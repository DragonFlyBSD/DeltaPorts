"""Step 11a — proposed-fix artifact generation.

When the patch agent succeeds (``rebuild_ok=true``), write a one-page
``analysis/proposed_fix.md`` to the bundle. The artifact is
operator-facing: it captures what the agent did, the cost, and the
recipe to land the fix in the operator's own DeltaPorts clone.

Mirrors ``manual_handoff.py``:

- ``render_proposed_fix(ctx)`` — pure markdown builder over
  ``ProposedFixCtx``. No I/O. Trivial to unit-test.
- ``build_proposed_fix_ctx(...)`` — reads bundle artifacts
  (``patch.md``, ``patch_audit.json``, ``changes.diff``) via the
  caller-supplied ``read_bundle_text`` callable and assembles a
  ``ProposedFixCtx``.

Persistence is the caller's job (runner has ``artifact_store_put``);
this module never touches the network or the filesystem.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


# ---------------------------------------------------------------------------
# Context
# ---------------------------------------------------------------------------


@dataclass
class ProposedFixCtx:
    """Everything ``render_proposed_fix`` needs. Caller pre-loads it."""
    origin: str
    target: str = ""
    bundle_id: str = ""

    # Agent metadata
    model: str = ""
    status: str = "success"
    attempts_total: int = 0
    attempts_max: int = 0
    rebuild_ok: bool = True

    # Cost — patch attempt(s) only.
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Triage cost, read separately from analysis/triage.json. The
    # operator-facing "true" cost is triage + patch.
    triage_prompt_tokens: int = 0
    triage_completion_tokens: int = 0
    triage_total_tokens: int = 0

    # Diff
    diff_bytes: int = 0
    files_touched: list[str] = field(default_factory=list)

    # Triage / agent narrative
    summary: str = ""           # one-line distilled from patch.md
    classification: str = ""
    confidence: str = ""

    # Optional: tracker base URL — when set we render a curl recipe
    # that fetches the diff from the artifact API.
    tracker_url: str = ""


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


_MAX_FILES_LIST = 20


def render_proposed_fix(ctx: ProposedFixCtx) -> str:
    """Render ``ctx`` as the ``analysis/proposed_fix.md`` body."""
    lines: list[str] = []
    lines.append(f"# Proposed Fix — `{ctx.origin}`")
    lines.append("")
    lines.append(
        f"The agent built and verified a fix for `{ctx.origin}` "
        f"on target `{ctx.target or '(none)'}`. Review the diff, "
        f"verify independently, and land it in your DeltaPorts clone."
    )
    lines.append("")

    # Summary first — what the operator most wants to see.
    if ctx.summary:
        lines.append("## Summary")
        lines.append("")
        lines.append(ctx.summary.strip())
        lines.append("")

    # Bundle metadata block.
    lines.append("## Bundle")
    lines.append("")
    lines.append(f"- **Origin:** `{ctx.origin}`")
    lines.append(f"- **Target:** `{ctx.target or '(none)'}`")
    lines.append(f"- **Bundle:** `{ctx.bundle_id or '(unknown)'}`")
    lines.append(
        f"- **Status:** `{ctx.status}`"
        + (f" ({ctx.attempts_total} of {ctx.attempts_max} attempts used)"
           if ctx.attempts_max else
           f" ({ctx.attempts_total} attempts)" if ctx.attempts_total else "")
    )
    if ctx.classification or ctx.confidence:
        lines.append(
            f"- **Triage:** `{ctx.classification or 'unknown'}` "
            f"(confidence: `{ctx.confidence or 'unknown'}`)"
        )
    lines.append("")

    # Cost — concrete numbers operators can use to estimate scale.
    # Triage and patch are separate LLM jobs; the "true" full-run
    # cost is the sum.
    combined_total = ctx.total_tokens + ctx.triage_total_tokens
    lines.append("## Cost")
    lines.append("")
    lines.append(f"- Model: `{ctx.model or 'unknown'}`")
    lines.append(f"- Patch — prompt: {ctx.prompt_tokens:,}, "
                 f"completion: {ctx.completion_tokens:,}, "
                 f"total: {ctx.total_tokens:,}")
    if ctx.triage_total_tokens:
        lines.append(f"- Triage — prompt: {ctx.triage_prompt_tokens:,}, "
                     f"completion: {ctx.triage_completion_tokens:,}, "
                     f"total: {ctx.triage_total_tokens:,}")
        lines.append(f"- **Combined total (triage + patch): "
                     f"{combined_total:,}**")
    else:
        lines.append(f"- Total tokens: {ctx.total_tokens:,}")
    lines.append("")

    # Files touched — surfaced from the diff.
    if ctx.files_touched:
        lines.append("## Files touched")
        lines.append("")
        for fp in ctx.files_touched[:_MAX_FILES_LIST]:
            lines.append(f"- `{fp}`")
        extra = len(ctx.files_touched) - _MAX_FILES_LIST
        if extra > 0:
            lines.append(f"- … and {extra} more")
        lines.append("")

    # Apply recipe.
    lines.append("## Apply this fix")
    lines.append("")
    lines.append(
        f"The complete diff lives at `analysis/changes.diff` "
        f"({ctx.diff_bytes:,} bytes). Recipe to land it in your "
        f"own DeltaPorts clone:"
    )
    lines.append("")
    lines.append("```sh")
    if ctx.tracker_url and ctx.bundle_id:
        url = (
            f"{ctx.tracker_url.rstrip('/')}/api/bundles/"
            f"{ctx.bundle_id}/artifacts/analysis/changes.diff"
        )
        lines.append(f"curl -sS {url} > /tmp/proposed-fix.diff")
    else:
        lines.append(
            "# Fetch the diff from the bundle's analysis/changes.diff"
        )
        lines.append("# (use the bundle viewer's 'raw' link to download).")
    lines.append("cd /path/to/your/DeltaPorts")
    lines.append("git apply --3way /tmp/proposed-fix.diff")
    lines.append("git diff   # review the change")
    lines.append(f"git add ports/{ctx.origin}/")
    lines.append(
        "git commit -s -m "
        f"\"{ctx.origin}: fix dsynth build"
        + (f" under {ctx.target}" if ctx.target else "")
        + "\""
    )
    lines.append("```")
    lines.append("")
    lines.append(
        "`-s` adds your `Signed-off-by`; the agent itself does not sign. "
        "Review the diff before committing — the agent has been verified "
        "to produce a passing `dsynth_build`, but operator judgment "
        "remains the gate."
    )
    lines.append("")

    # Independent verification.
    lines.append("## Verify independently")
    lines.append("")
    lines.append(
        "Once Step 11b ships, `dportsv3 dev-env verify-fix "
        f"{ctx.bundle_id or '<bundle_id>'}` provisions a clean env, "
        "applies the diff, and runs `dsynth_build` to confirm "
        "reproducibility. For now you can do the same manually:"
    )
    lines.append("")
    lines.append("```sh")
    lines.append("# In a fresh dev-env (NOT the env the agent ran in):")
    lines.append(
        f"dportsv3 dev-env exec <fresh-env> -- "
        f"dsynth -S -y -p \"$DPORTS_DSYNTH_PROFILE\" "
        f"build {ctx.origin}"
    )
    lines.append("```")
    lines.append("")

    # Audit trail.
    lines.append("## Audit trail")
    lines.append("")
    lines.append("Other artifacts in this bundle:")
    lines.append("")
    lines.append("- `analysis/patch.md` — agent's full reasoning")
    lines.append("- `analysis/changes.diff` — the diff being proposed")
    lines.append("- `analysis/patch_audit.json` — token usage + attempt log")
    lines.append("- `analysis/rebuild_proof.json` — dsynth_build success record")
    lines.append("- `analysis/tool_trace.jsonl` — per-turn event stream")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context assembly from bundle artifacts
# ---------------------------------------------------------------------------


ReadBundleText = Callable[[Path | None, "str | None", str], "str | None"]


def build_proposed_fix_ctx(
    *,
    origin: str,
    target: str = "",
    bundle_id: str = "",
    bundle_dir: Path | None = None,
    read_bundle_text: ReadBundleText | None = None,
    patch_result: object | None = None,
    model: str = "",
    classification: str = "",
    confidence: str = "",
    attempts_max: int = 0,
    tracker_url: str = "",
) -> ProposedFixCtx:
    """Assemble a ``ProposedFixCtx`` from bundle artifacts + patch_result.

    ``patch_result`` is the harness's ``PatchResult`` (preferred). If
    unavailable, we fall back to reading ``analysis/patch_audit.json``
    from the bundle. Missing artifacts degrade gracefully — fields
    default to safe empty values.
    """
    # Cost + attempt info — from patch_result if we have it, else
    # from the audit artifact written by the runner.
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    attempts_total = 0
    rebuild_ok = True
    status = "success"

    if patch_result is not None:
        usage = getattr(patch_result, "usage", None)
        if usage is not None:
            prompt_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        attempts = getattr(patch_result, "attempts", []) or []
        attempts_total = len(attempts)
        status = str(getattr(patch_result, "status", "") or "success")
        if attempts:
            last = attempts[-1]
            rebuild_ok = bool(getattr(last, "rebuild_ok", True))
    elif read_bundle_text is not None:
        audit_text = read_bundle_text(
            bundle_dir, bundle_id or None, "analysis/patch_audit.json",
        )
        if audit_text:
            try:
                audit = json.loads(audit_text)
                status = str(audit.get("status") or "success")
                tu = audit.get("tokens_used") or {}
                if isinstance(tu, dict):
                    prompt_tokens = int(tu.get("prompt", 0) or 0)
                    completion_tokens = int(tu.get("completion", 0) or 0)
                    total_tokens = int(tu.get("total", 0) or 0)
                attempts_total = len(audit.get("attempts") or [])
            except Exception:
                pass

    # Summary — distil from patch.md's "## Patch Summary" section.
    summary = ""
    if read_bundle_text is not None:
        patch_md = read_bundle_text(
            bundle_dir, bundle_id or None, "analysis/patch.md",
        )
        if patch_md:
            summary = _extract_first_paragraph(patch_md, "Patch Summary")

        # Triage backfill runs independently of patch.md presence —
        # a bundle can have triage.md but no patch.md yet (e.g. the
        # write_proposed_fix path runs before patch.md is persisted).
        if not classification or not confidence:
            triage_md = read_bundle_text(
                bundle_dir, bundle_id or None, "analysis/triage.md",
            )
            if triage_md:
                if not classification:
                    classification = _extract_inline_value(
                        triage_md, "Classification",
                    )
                if not confidence:
                    confidence = _extract_inline_value(
                        triage_md, "Confidence",
                    )

    # Triage cost — read separately so proposed_fix.md surfaces the
    # full run cost (triage + patch). The patch attempt loop only
    # knows its own usage; without this, proposed_fix.md misreports
    # the cost as patch-only.
    triage_prompt_tokens = 0
    triage_completion_tokens = 0
    triage_total_tokens = 0
    if read_bundle_text is not None:
        triage_json = read_bundle_text(
            bundle_dir, bundle_id or None, "analysis/triage.json",
        )
        if triage_json:
            try:
                tdoc = json.loads(triage_json)
                ttu = tdoc.get("tokens_used") or {}
                if isinstance(ttu, dict):
                    triage_prompt_tokens = int(ttu.get("prompt", 0) or 0)
                    triage_completion_tokens = int(ttu.get("completion", 0) or 0)
                    triage_total_tokens = int(ttu.get("total", 0) or 0)
            except Exception:
                pass

    # Files touched + diff size — from changes.diff.
    files_touched: list[str] = []
    diff_bytes = 0
    if read_bundle_text is not None:
        diff_text = read_bundle_text(
            bundle_dir, bundle_id or None, "analysis/changes.diff",
        )
        if diff_text:
            diff_bytes = len(diff_text.encode("utf-8"))
            files_touched = _parse_diff_files(diff_text)

    return ProposedFixCtx(
        origin=origin,
        target=target,
        bundle_id=bundle_id,
        model=model,
        status=status,
        attempts_total=attempts_total,
        attempts_max=attempts_max,
        rebuild_ok=rebuild_ok,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        triage_prompt_tokens=triage_prompt_tokens,
        triage_completion_tokens=triage_completion_tokens,
        triage_total_tokens=triage_total_tokens,
        diff_bytes=diff_bytes,
        files_touched=files_touched,
        summary=summary,
        classification=classification,
        confidence=confidence,
        tracker_url=tracker_url,
    )


# ---------------------------------------------------------------------------
# Small helpers (mirror manual_handoff's parsing patterns)
# ---------------------------------------------------------------------------


def _extract_first_paragraph(text: str, heading: str) -> str:
    """Return the first non-empty paragraph under ``## <heading>``
    (case-insensitive). Caps at a few sentences to stay one-line-ish."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return ""
    body = m.group(1).strip()
    # First paragraph (until blank line).
    para = body.split("\n\n", 1)[0].strip()
    # Cap length so the artifact stays scannable.
    if len(para) > 500:
        para = para[:500].rstrip() + "…"
    return para


def _extract_inline_value(text: str, heading: str) -> str:
    """First non-empty line under ``## <heading>``."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)",
        re.MULTILINE | re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(text)
    if not m:
        return ""
    for line in m.group(1).splitlines():
        s = line.strip()
        if s:
            return s.lower()
    return ""


def _parse_diff_files(diff: str) -> list[str]:
    """File paths from a unified diff's ``+++ b/...`` lines."""
    out: list[str] = []
    seen: set[str] = set()
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:].strip()
            if path and path != "/dev/null" and path not in seen:
                seen.add(path)
                out.append(path)
    return out
