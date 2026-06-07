"""Manual handoff artifact generation.

Step 3 of the post-implementation manual-escalation plan
(``docs/agentic-consolidation-plan.md``).

When a job escalates to manual — by triage MANUAL tier, retry cap,
patch budget exhaustion, or patch gave-up — the runner writes a
single ``analysis/manual_handoff.md`` artifact summarizing what the
agent did, why it stopped, and what input the operator needs to
provide so the loop can resume.

This module ships two pieces:

- ``render_handoff(ctx)`` — pure markdown builder over ``HandoffCtx``.
  No I/O. Trivial to unit-test by constructing a context directly.
- ``build_handoff_ctx(...)`` — reads the bundle artifacts the patch
  flow currently writes (``analysis/triage.md``, ``patch_audit.json``,
  ``changes.diff``, ``errors.txt``) via the caller-supplied
  ``read_bundle_text`` callable, and assembles a ``HandoffCtx``.

Persistence is the caller's job (the runner has the
``artifact_store_put`` helper); this module never touches the network
or the filesystem.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Reason taxonomy
# ---------------------------------------------------------------------------

REASON_MANUAL_TIER = "manual_tier"
REASON_RETRY_CAP = "retry_cap"
REASON_PATCH_BUDGET = "patch_budget_exhausted"
REASON_PATCH_GAVE_UP = "patch_gave_up"
# Step 37-4: patch fixed the build (rebuild_ok=true) but punted on
# one or more deferred patches from convert. Surfaces as MANUAL so
# the operator can review the escalated subset; the patch agent's
# regenerated / dropped verdicts are recorded on PatchResult.
REASON_PATCH_ESCALATED_VERDICTS = "patch_escalated_verdicts"
# M4: triage terminated without reaching a routing decision (bundle
# materialization / LLM call / policy load / orchestrator precheck
# failed). Usually an infra signal, not an agent give-up.
REASON_TRIAGE_FAILED = "triage_failed"

VALID_REASONS = frozenset({
    REASON_MANUAL_TIER,
    REASON_RETRY_CAP,
    REASON_PATCH_BUDGET,
    REASON_PATCH_GAVE_UP,
    REASON_PATCH_ESCALATED_VERDICTS,
    REASON_TRIAGE_FAILED,
})

_REASON_LABELS = {
    REASON_MANUAL_TIER:   "triage classified as MANUAL",
    REASON_RETRY_CAP:     "retry cap reached",
    REASON_PATCH_BUDGET:  "patch budget exhausted",
    REASON_PATCH_GAVE_UP: "patch agent gave up",
    REASON_PATCH_ESCALATED_VERDICTS:
        "patch fixed build but escalated deferred patches",
    REASON_TRIAGE_FAILED: "triage failed to run",
}


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class HandoffCtx:
    """Everything ``render_handoff`` needs. Caller pre-loads it."""
    origin: str
    target: str = ""
    reason: str = REASON_MANUAL_TIER
    reason_detail: str = ""
    bundle_id: str = ""
    # Triage signals
    classification: str = ""
    confidence: str = ""
    suggested_fix: str = ""
    # Retry-cap signals
    recent_failures: int = 0
    max_attempts: int = 0
    window_hours: int = 0
    # Last patch attempt
    patch_attempts: int = 0
    patch_status: str = ""
    tokens_used: int = 0
    files_touched: list[str] = field(default_factory=list)
    changes_diff_summary: str = ""
    # Last failing build
    errors_tail: str = ""
    # Step 29c: operator-context history, oldest → newest. Each
    # entry is a dict with keys: context_rev, submitted_at, text,
    # submitted_by (any of submitted_by may be None). When the
    # list is empty the rendered handoff omits the section.
    operator_context_history: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


_MAX_DIFF_BYTES = 4000
_MAX_ERRORS_LINES = 40
_MAX_FILES_LIST = 20


def render_handoff(ctx: HandoffCtx) -> str:
    """Render ``ctx`` as the ``analysis/manual_handoff.md`` body."""
    lines: list[str] = []
    lines.append("# Manual Handoff")
    lines.append("")
    lines.append(f"- **Origin:** `{ctx.origin}`")
    lines.append(f"- **Target:** `{ctx.target or '(none)'}`")
    lines.append(f"- **Bundle:** `{ctx.bundle_id or '(unknown)'}`")
    lines.append(
        f"- **Reason:** {_REASON_LABELS.get(ctx.reason, ctx.reason)}"
    )
    if ctx.reason_detail:
        lines.append(f"- **Detail:** {ctx.reason_detail}")
    lines.append("")

    if ctx.classification or ctx.confidence or ctx.suggested_fix:
        lines.append("## Triage")
        lines.append("")
        lines.append(f"- Classification: `{ctx.classification or 'unknown'}`")
        lines.append(f"- Confidence: `{ctx.confidence or 'unknown'}`")
        lines.append("")
        if ctx.suggested_fix:
            lines.append("### Suggested Fix")
            lines.append("")
            lines.append(ctx.suggested_fix.strip())
            lines.append("")

    if ctx.recent_failures or ctx.max_attempts:
        lines.append("## Attempt History")
        lines.append("")
        lines.append(f"- Recent failures: {ctx.recent_failures}")
        if ctx.max_attempts:
            window = f" in last {ctx.window_hours}h" if ctx.window_hours else ""
            lines.append(f"- Cap: {ctx.max_attempts}{window}")
        lines.append("")

    if ctx.patch_attempts or ctx.patch_status or ctx.tokens_used:
        lines.append("## Last Patch Attempt")
        lines.append("")
        lines.append(f"- Status: `{ctx.patch_status or 'unknown'}`")
        lines.append(f"- Attempts: {ctx.patch_attempts}")
        lines.append(f"- Tokens used: {ctx.tokens_used}")
        lines.append("")
        if ctx.files_touched:
            lines.append("### Files Touched")
            lines.append("")
            for fp in ctx.files_touched[:_MAX_FILES_LIST]:
                lines.append(f"- `{fp}`")
            extra = len(ctx.files_touched) - _MAX_FILES_LIST
            if extra > 0:
                lines.append(f"- … and {extra} more")
            lines.append("")

    if ctx.changes_diff_summary:
        lines.append("## Changes Diff (summary)")
        lines.append("")
        lines.append("```diff")
        lines.append(ctx.changes_diff_summary.rstrip())
        lines.append("```")
        lines.append("")

    if ctx.errors_tail:
        lines.append("## Last Failing Build (tail)")
        lines.append("")
        lines.append("```")
        lines.append(ctx.errors_tail.rstrip())
        lines.append("```")
        lines.append("")

    if ctx.operator_context_history:
        lines.append("## Operator Context")
        lines.append("")
        for idx, entry in enumerate(ctx.operator_context_history, start=1):
            submitted_at = entry.get("submitted_at") or "(unknown time)"
            submitted_by = entry.get("submitted_by") or ""
            heading = f"### Round {idx} — {submitted_at}"
            if submitted_by:
                heading += f" (operator: {submitted_by})"
            lines.append(heading)
            lines.append("")
            text = (entry.get("text") or "").rstrip()
            if text:
                lines.append(text)
            lines.append("")

    lines.append("## Operator Question")
    lines.append("")
    lines.append(_question(ctx))
    lines.append("")
    return "\n".join(lines)


def _question(ctx: HandoffCtx) -> str:
    if ctx.reason == REASON_MANUAL_TIER:
        if ctx.suggested_fix:
            return (
                f"Triage classified this as MANUAL "
                f"(classification=`{ctx.classification or 'unknown'}`, "
                f"confidence=`{ctx.confidence or 'unknown'}`). "
                "A Suggested Fix is included above — confirm the approach "
                "or provide a different angle, then click "
                "“Try again with this context”."
            )
        return (
            f"Triage classified this as MANUAL "
            f"(classification=`{ctx.classification or 'unknown'}`) "
            "with no concrete fix path. What approach should the agent take?"
        )
    if ctx.reason == REASON_RETRY_CAP:
        return (
            f"The agent attempted {ctx.recent_failures} times "
            f"in the last {ctx.window_hours or '?'}h and kept failing. "
            "Has the build broken upstream, or should the approach change?"
        )
    if ctx.reason == REASON_PATCH_BUDGET:
        return (
            f"The patch agent exhausted its token budget after "
            f"{ctx.patch_attempts} attempt(s) ({ctx.tokens_used} tokens). "
            "Should the budget be raised, or the approach changed? "
            "Look at the diff above — was the agent on the right track?"
        )
    if ctx.reason == REASON_PATCH_GAVE_UP:
        return (
            f"The patch agent gave up after {ctx.patch_attempts} attempt(s). "
            "What context would help the next attempt? "
            "For example: a known good FreeBSD-side fix, a specific file to "
            "look at, or an instruction to convert a static patch to a "
            "semantic `dops` / `REINPLACE_CMD` operation."
        )
    if ctx.reason == REASON_TRIAGE_FAILED:
        detail = (ctx.reason_detail or "").strip()
        detail_line = f" Failure: {detail}." if detail else ""
        return (
            "Triage never reached a routing decision — it failed before "
            f"classifying the build.{detail_line} This usually points at "
            "infrastructure (LLM endpoint, bundle artifacts, or the dev-env) "
            "rather than the port itself. Check the env/endpoint, then retry."
        )
    return "Provide context for the agent to retry."


# ---------------------------------------------------------------------------
# Context assembly from bundle artifacts
# ---------------------------------------------------------------------------


ReadBundleText = Callable[[Path | None, "str | None", str], "str | None"]


def build_handoff_ctx(
    *,
    origin: str,
    target: str = "",
    reason: str,
    reason_detail: str = "",
    bundle_id: str = "",
    bundle_dir: Path | None = None,
    read_bundle_text: ReadBundleText | None = None,
    decision_extra: dict | None = None,
    patch_result: object | None = None,
    operator_context_history: list[dict] | None = None,
) -> HandoffCtx:
    """Assemble a ``HandoffCtx`` from the bundle artifacts available now.

    Arguments fall back gracefully: missing ``read_bundle_text`` or
    missing artifacts just leave the corresponding fields empty.
    """
    decision_extra = decision_extra or {}

    classification = str(decision_extra.get("classification", "") or "").strip()
    confidence = str(decision_extra.get("confidence", "") or "").strip()
    recent_failures = int(decision_extra.get("recent_failures", 0) or 0)
    max_attempts = int(decision_extra.get("max_attempts", 0) or 0)
    window_hours = int(decision_extra.get("window_hours", 0) or 0)

    suggested_fix = ""
    if read_bundle_text is not None:
        triage_text = read_bundle_text(
            bundle_dir, bundle_id or None, "analysis/triage.md",
        )
        if triage_text:
            suggested_fix = _extract_section(triage_text, "Suggested Fix")
            if not classification:
                classification = _extract_inline_value(triage_text, "Classification")
            if not confidence:
                confidence = _extract_inline_value(triage_text, "Confidence")

    patch_attempts = 0
    patch_status = ""
    tokens_used = 0
    if patch_result is not None:
        patch_attempts = len(getattr(patch_result, "attempts", []) or [])
        patch_status = str(getattr(patch_result, "status", "") or "")
        usage = getattr(patch_result, "usage", None)
        if usage is not None:
            tokens_used = int(getattr(usage, "total_tokens", 0) or 0)
    elif read_bundle_text is not None:
        audit_text = read_bundle_text(
            bundle_dir, bundle_id or None, "analysis/patch_audit.json",
        )
        if audit_text:
            try:
                audit = json.loads(audit_text)
                patch_attempts = len(audit.get("attempts") or [])
                patch_status = str(audit.get("status") or "")
                tu = audit.get("tokens_used") or {}
                if isinstance(tu, dict):
                    tokens_used = int(tu.get("total", 0) or 0)
            except Exception:
                pass

    files_touched: list[str] = []
    diff_summary = ""
    errors_tail = ""
    if read_bundle_text is not None:
        diff_text = read_bundle_text(
            bundle_dir, bundle_id or None, "analysis/changes.diff",
        )
        if diff_text:
            files_touched = _parse_diff_files(diff_text)
            diff_summary = _truncate_bytes(diff_text, _MAX_DIFF_BYTES)
        # The dsynth hook uploads the distilled build log to
        # ``logs/errors.txt`` (scripts/dsynth-hooks/hook_pkg_failure:89);
        # context.py:233 reads the same path. Match it so the "Last
        # Failing Build (tail)" section actually has content.
        err_text = read_bundle_text(
            bundle_dir, bundle_id or None, "logs/errors.txt",
        )
        if err_text:
            errors_tail = _tail_lines(err_text, _MAX_ERRORS_LINES)

    return HandoffCtx(
        origin=origin,
        target=target,
        reason=reason,
        reason_detail=reason_detail,
        bundle_id=bundle_id,
        classification=classification,
        confidence=confidence,
        suggested_fix=suggested_fix,
        recent_failures=recent_failures,
        max_attempts=max_attempts,
        window_hours=window_hours,
        patch_attempts=patch_attempts,
        patch_status=patch_status,
        tokens_used=tokens_used,
        files_touched=files_touched,
        changes_diff_summary=diff_summary,
        errors_tail=errors_tail,
        operator_context_history=list(operator_context_history or []),
    )


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _extract_section(text: str, heading: str) -> str:
    """Return body of ``## <heading>`` (case-insensitive) up to the next
    ``## `` heading or EOF. Empty string if not found."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def _extract_inline_value(text: str, heading: str) -> str:
    """Return the first non-empty line under ``## <heading>``."""
    body = _extract_section(text, heading)
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped.lower()
    return ""


def _parse_diff_files(diff: str) -> list[str]:
    """Pull file paths from a unified diff's ``+++ b/...`` lines."""
    out: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            if path and path != "/dev/null" and path not in seen:
                seen.add(path)
                out.append(path)
    return out


def _truncate_bytes(s: str, max_bytes: int) -> str:
    raw = s.encode("utf-8", errors="replace")
    if len(raw) <= max_bytes:
        return s.rstrip()
    cut = raw[:max_bytes].decode("utf-8", errors="ignore").rstrip()
    return cut + "\n... [truncated]"


def _tail_lines(s: str, lines: int) -> str:
    arr = s.splitlines()
    return "\n".join(arr[-lines:])
