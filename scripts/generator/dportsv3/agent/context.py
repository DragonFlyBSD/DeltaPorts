"""Composable context assembly for LLM payloads.

Phase 4 Step 1 of the agentic framework. Layer 4 of
``agentic-framework-design.md``.

The two ``build_*_payload`` functions in the runner were walls of
``parts.append(...)`` calls that hard-coded section ordering and
inclusion logic. This module replaces them with composable
``ContextSection`` objects that the ``ContextAssembler`` renders in
priority order.

Phase 4 enforces **strict byte-for-byte parity** with the legacy
output. This module ships the protocol + driver only; concrete
section classes land in Steps 2 (triage) and 3 (patch).

Design decisions:

- **Sections are pure functions over typed data.** Anything that
  needs to be queried (DB rows, bundle artifacts) is pre-loaded by
  the caller into the ``ContextCtx``. Sections never do I/O during
  render. Makes them trivial to unit-test and predictable to compose.
- **Section priority is render order.** Integer priority; lower
  renders first. Reproduces today's hard-coded order by assigning
  priorities. The assembler never reorders beyond that.
- **None or "" → skip silently.** A section that has nothing to say
  returns ``None`` (or empty); it drops out of the payload as if it
  weren't in the list. No extra blank line.
- **Exceptions bubble.** A section that raises crashes the assembler.
  This is a bug-not-edge-case; observability beats silent fallback.

Future phases may add aggregate-budget enforcement (drop lowest-
priority sections when total exceeds N bytes); that's not in Phase 4.
"""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable


# Per-file cap (chars, not tokens — chars are deterministic and cheap
# to bound; tokens are model-dependent). Triage on big-port classes
# (python, perl, etc.) was inlining 500 KB+ pkg-plist files whole,
# producing 250 K-token prompts where the classifier needed at most
# the failing log + a snippet of plist. Patch agent has the ``get_file``
# tool to fetch the full content when it actually needs it; triage has
# snippet rounds. Either way, the unbounded inline was wasteful.
# Override via DP_HARNESS_CONTEXT_FILE_CAP (chars). Floored at 2048 —
# below that the head+tail split has no room for meaningful context
# around the truncation marker. Smaller values are clamped silently.
def _default_file_cap() -> int:
    try:
        return max(2048, int(os.environ.get(
            "DP_HARNESS_CONTEXT_FILE_CAP", "32768",
        )))
    except (TypeError, ValueError):
        return 32768


def _truncate_head_tail(text: str, cap: int) -> str:
    """Return ``text`` if under ``cap``, else a head+tail snippet with
    an explicit truncation marker showing the original byte count and
    the elided range. Head and tail get half the cap each (so the
    rendered output stays inside the cap modulo the marker line).

    ``cap=0`` means "resolve from DP_HARNESS_CONTEXT_FILE_CAP at call
    time" — this is the default for the section-level field so the
    env var can be set after module import (e.g. by tests, or by
    a runtime override).
    """
    if cap == 0:
        cap = _default_file_cap()
    if cap <= 0 or len(text) <= cap:
        return text
    half = max(1024, cap // 2)
    head = text[:half]
    tail = text[-half:]
    elided = len(text) - len(head) - len(tail)
    marker = (
        f"\n[... truncated {elided} of {len(text)} chars; "
        f"showing first {len(head)} + last {len(tail)} ...]\n"
    )
    return head + marker + tail


# I/O callables sections need but can't import directly (avoiding a
# cycle with runner.py). The caller binds these into the ctx.
ReadBundleText = Callable[[Path | None, str | None, str], str | None]
BundleArtifactList = Callable[[str], list[str]]
SnippetFeedback = Callable[[Path, int], str]
SnippetContent = Callable[[Path, int], str]


@dataclass
class ContextCtx:
    """Render-time context passed to every section.

    Fields are populated by the caller before render. Pre-loading
    (rather than letting sections query) keeps render deterministic
    and unit-testable without a live DB or filesystem.
    """
    bundle_dir: Path | None = None
    bundle_id: str | None = None
    job: dict = field(default_factory=dict)
    playbooks_dir: Path | None = None
    # Pre-loaded data the caller computes once and hands to sections
    # that need it.
    port_history: object | None = None  # decision.PortHistory or None
    sibling_bundle_ids: list[str] = field(default_factory=list)
    prior_triage_bundle_ids: list[str] = field(default_factory=list)
    prior_patch_bundle_ids: list[str] = field(default_factory=list)
    user_context_text: str | None = None
    # Step 29e: full operator-context history (oldest → newest) for
    # the bundle's (run_id, origin). Each entry is a dict with keys
    # ``context_rev`` / ``submitted_at`` / ``text`` / ``submitted_by``
    # — matching what list_user_context_history returns. When non-
    # empty, ``UserContextSection`` renders all rounds verbatim
    # instead of just ``user_context_text``; the model sees
    # continuity across operator submissions.
    user_context_history: list[dict] = field(default_factory=list)
    playbooks_text: str | None = None
    # Automation-context inputs the patch flow pre-loads.
    prior_failure_count: int = 0
    window_hours: int = 0
    max_attempts_cap: int = 0
    # I/O callables — caller binds runner-side helpers so sections
    # stay pure (no import cycle, easy to stub in tests).
    read_bundle_text: ReadBundleText | None = None
    bundle_artifact_list: BundleArtifactList | None = None
    snippet_feedback: SnippetFeedback | None = None
    snippet_content: SnippetContent | None = None
    # Optional DB conn for sections that genuinely cannot avoid I/O
    # (e.g. an enumeration where the section list itself depends on
    # query results). Phase-4 sections shouldn't need this; reserved.
    db_conn: sqlite3.Connection | None = None


@runtime_checkable
class ContextSection(Protocol):
    """A piece of the rendered payload.

    Implementations should be dataclasses or simple classes that
    expose:
    - ``name``: short identifier for logging/debugging.
    - ``priority``: integer; lower renders first. Use distinct
      values per section (e.g. 10, 20, 30...) so reorders by
      insertion later don't conflict.
    - ``render(ctx)``: returns the section's markdown chunk
      (including header) or ``None`` to skip.

    Sections must not mutate ``ctx``.
    """
    name: str
    priority: int

    def render(self, ctx: ContextCtx) -> str | None: ...


def render_payload(sections: list[ContextSection], ctx: ContextCtx) -> str:
    """Assemble the payload by rendering each section in priority order.

    Mirrors ``"\\n".join(parts)`` semantics of the legacy code:
    sections that return ``None`` or empty string drop out; the rest
    are joined with a single newline.

    Sections are sorted in-place-ish (a stable sort over a fresh
    list) so callers can pass them in any order. Within the same
    priority value, insertion order is preserved.

    Sections that want a blank line after their content should end
    their render output with a trailing ``"\\n"``. Combined with the
    join's added newline, that produces ``"...content\\n\\nnext"`` —
    a single blank line between sections — which is what the legacy
    ``parts.append(""); parts.append(next)`` pattern emitted.
    """
    ordered = sorted(sections, key=lambda s: s.priority)
    rendered: list[str] = []
    for section in ordered:
        chunk = section.render(ctx)
        if chunk is None or chunk == "":
            continue
        rendered.append(chunk)
    return "\n".join(rendered)


# -----------------------------------------------------------------------------
# Concrete section classes for the triage payload.
#
# Each section's render output mirrors the legacy ``parts.append(...)``
# sequence, including the trailing blank line (encoded as a trailing
# ``"\n"`` in the render output) where the legacy code did
# ``parts.append("")``. The join in render_payload supplies the
# inter-section newline that turns trailing-``\n`` into the blank-line
# separator.
# -----------------------------------------------------------------------------


def _trailing_blank(content: str) -> str:
    """Ensure a section's content ends with a trailing newline so the
    join's added "\\n" produces a blank-line separator. Idempotent."""
    return content if content.endswith("\n") else content + "\n"


@dataclass
class SnippetsRoundSection:
    """Snippet-extractor feedback + extracted content for follow-up
    rounds. Only renders when the job's ``snippet_round`` > 0 and
    the bundle_dir is on-disk."""
    name: str = "snippets_round"
    priority: int = 10

    def render(self, ctx: ContextCtx) -> str | None:
        if ctx.bundle_dir is None:
            return None
        job = ctx.job or {}
        snippet_round = int(job.get("snippet_round", "0") or "0")
        has_snippets = job.get("has_snippets", "false") == "true"
        if not (has_snippets and snippet_round > 0):
            return None
        if ctx.snippet_feedback is None or ctx.snippet_content is None:
            return None

        chunks: list[str] = []
        feedback = ctx.snippet_feedback(ctx.bundle_dir, snippet_round)
        if feedback:
            chunks.append(feedback + "\n")
        content = ctx.snippet_content(ctx.bundle_dir, snippet_round)
        if content:
            chunks.append(content + "\n")
        if not chunks:
            return None
        return "\n".join(chunks)


@dataclass
class PlaybooksSection:
    """Agent playbook library content. Pre-loaded into
    ctx.playbooks_text by the runner via
    ``dportsv3.agent.playbooks.load_playbooks``."""
    name: str = "playbooks"
    priority: int = 20

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.playbooks_text:
            return None
        return _trailing_blank(ctx.playbooks_text)


@dataclass
class UserContextSection:
    """Run-scoped user context. Pre-loaded into ctx.user_context_text
    (single current text) and ctx.user_context_history (every round
    in submission order).

    When history is non-empty, renders each round as a separate
    block so the model sees continuity ("consider what I said
    before" only makes sense with prior rounds visible). When
    history is empty but a current text exists, falls back to the
    single-block legacy shape — covers ports that have the text
    but no history rows (pre-29b submissions) and direct
    test seeds that set ``user_context_text`` only.
    """
    name: str = "user_context"
    priority: int = 30

    def render(self, ctx: ContextCtx) -> str | None:
        history = ctx.user_context_history or []
        if history:
            lines = ["## User Context (run-scoped)"]
            for idx, entry in enumerate(history, start=1):
                submitted_at = entry.get("submitted_at") or "(unknown time)"
                submitted_by = entry.get("submitted_by") or ""
                heading = f"### Round {idx} — {submitted_at}"
                if submitted_by:
                    heading += f" (operator: {submitted_by})"
                lines.append(heading)
                text = (entry.get("text") or "").rstrip()
                if text:
                    lines.append(text)
                lines.append("")
            return "\n".join(lines)
        if ctx.user_context_text:
            return f"## User Context (run-scoped)\n{ctx.user_context_text}\n"
        return None


@dataclass
class MetadataSection:
    """Bundle ``meta.txt`` content."""
    name: str = "metadata"
    priority: int = 40

    def render(self, ctx: ContextCtx) -> str | None:
        if ctx.read_bundle_text is None:
            return None
        meta = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "meta.txt")
        if not meta:
            return None
        return f"## Metadata\n{meta}\n"


@dataclass
class BuildErrorsSection:
    """Distilled ``logs/errors.txt`` content."""
    name: str = "build_errors"
    priority: int = 50
    # 0 = resolve from env at render time. Tests can override per-instance.
    max_chars: int = 0

    def render(self, ctx: ContextCtx) -> str | None:
        if ctx.read_bundle_text is None:
            return None
        errors = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "logs/errors.txt")
        if not errors:
            return None
        return f"## Build Errors\n{_truncate_head_tail(errors, self.max_chars)}\n"


@dataclass
class PortFilesSection:
    """``## Port Files`` header + Makefile, pkg-plist, distinfo subsections.

    The header always renders (even with no files); the subsections
    only render when their respective files exist in the bundle.
    Per-file head+tail cap protects against pkg-plist explosion on
    large ports (python311's plist is 533 KB — that file alone was
    pushing triage prompts past 250 K tokens).
    """
    name: str = "port_files"
    priority: int = 60
    # 0 = resolve from env at render time. Tests can override per-instance.
    max_chars: int = 0

    def render(self, ctx: ContextCtx) -> str | None:
        if ctx.read_bundle_text is None:
            return "## Port Files\n"  # header alone with trailing blank
        lines = ["## Port Files"]

        makefile = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "port/Makefile")
        if makefile:
            lines.extend(["### Makefile", "```makefile",
                          _truncate_head_tail(makefile, self.max_chars),
                          "```", ""])

        plist = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "port/pkg-plist")
        if plist:
            lines.extend(["### pkg-plist", "```",
                          _truncate_head_tail(plist, self.max_chars),
                          "```", ""])

        # distinfo is tiny by construction (checksums for a few
        # distfiles); no cap needed.
        distinfo = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "port/distinfo")
        if distinfo:
            lines.extend(["### distinfo", "```", distinfo, "```", ""])

        return "\n".join(lines)


@dataclass
class ExistingPatchesSection:
    """``### Existing Patches`` listing — diff fences per patch file."""
    name: str = "existing_patches"
    priority: int = 70
    # 0 = resolve from env at render time. Tests can override per-instance.
    max_chars: int = 0

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.bundle_id or ctx.bundle_artifact_list is None or ctx.read_bundle_text is None:
            return None
        relpaths = [p for p in ctx.bundle_artifact_list(ctx.bundle_id)
                    if p.startswith("port/files/patch-")]
        if not relpaths:
            return None
        lines = ["### Existing Patches"]
        for rel in sorted(relpaths):
            content = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, rel)
            if not content:
                continue
            name = Path(rel).name
            lines.extend([f"#### {name}", "```diff",
                          _truncate_head_tail(content, self.max_chars),
                          "```", ""])
        if len(lines) == 1:
            # Header but no patches successfully read — mirror legacy
            # which still emitted the header in that case.
            return "### Existing Patches\n"
        return "\n".join(lines)


@dataclass
class SiblingBundlesSection:
    """``## Sibling Pending Failures (this batch)`` — same-origin
    bundles queued before the current job ran, capped at 3.

    Triage and patch payloads differ slightly: triage includes an
    introductory paragraph explaining the section; patch jumps
    straight to the per-bundle subsections. The ``with_intro`` flag
    parameterizes that difference.
    """
    name: str = "sibling_bundles"
    priority: int = 80
    with_intro: bool = True
    # 0 = resolve from env at render time. Tests can override per-instance.
    max_chars: int = 0

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.sibling_bundle_ids or ctx.read_bundle_text is None:
            return None
        lines = ["## Sibling Pending Failures (this batch)"]
        if self.with_intro:
            intro = (
                "These bundles failed for the same origin and were queued "
                "before this triage ran. Treat them as additional evidence "
                "for the same underlying issue."
            )
            lines.append(intro)
            lines.append("")
        for sib_id in ctx.sibling_bundle_ids[:3]:
            sib_errors = ctx.read_bundle_text(None, sib_id, "logs/errors.txt")
            if not sib_errors:
                continue
            lines.extend([f"### Bundle {sib_id}", "```",
                          _truncate_head_tail(sib_errors, self.max_chars),
                          "```", ""])
        return "\n".join(lines)


@dataclass
class PriorTriagesSection:
    """``## Prior Triages (most recent 2)`` — pre-loaded historical
    bundles' triage outputs *and* the patch agent's evidence from
    those bundles. Step 29d added the patch artifacts so the
    triage model can see what was already tried, not just what
    the prior triage said.

    Pulled per bundle (oldest → newest in the input list, but the
    list is already most-recent-first):
      - analysis/triage.md         — prior classification + reasoning
      - analysis/rebuild_proof.json — terminal proof or synthetic
      - analysis/patch.md          — patch agent narrative (clipped)
      - analysis/changes.diff      — what edits the patch agent made

    Char caps are tighter than the patch flow's
    ``PriorAttemptsSection`` because triage budget is leaner.
    Missing files are silently skipped — pre-29d bundles render
    exactly as before (just triage + rebuild_proof).
    """
    name: str = "prior_triages"
    priority: int = 90
    max_patch_chars: int = 2000
    max_diff_chars: int = 3000

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.prior_triage_bundle_ids or ctx.read_bundle_text is None:
            return None
        lines = ["## Prior Triages (most recent 2)"]
        emitted_any = False
        for past_bundle in ctx.prior_triage_bundle_ids[:2]:
            section_lines = [f"### Bundle {past_bundle}"]
            had_content = False
            for relpath, title, code_block, cap in [
                ("analysis/triage.md", "Triage", None, None),
                ("analysis/rebuild_proof.json", "Rebuild Proof", "json", None),
                ("analysis/patch.md", "Patch Report", None,
                 self.max_patch_chars),
                ("analysis/changes.diff", "Changes Diff", "diff",
                 self.max_diff_chars),
            ]:
                content = ctx.read_bundle_text(None, past_bundle, relpath)
                if not content:
                    continue
                if cap is not None and len(content) > cap:
                    content = content[:cap] + (
                        f"\n[...truncated to {cap} chars...]\n"
                    )
                had_content = True
                section_lines.append(f"#### {title}")
                if code_block:
                    section_lines.extend([f"```{code_block}", content, "```"])
                else:
                    section_lines.append(content)
                section_lines.append("")
            if had_content:
                lines.extend(section_lines)
                emitted_any = True
        if not emitted_any:
            return None
        return "\n".join(lines)


@dataclass
class TriagePromptFooterSection:
    """Closing instruction for the triage agent. Always renders.

    No trailing newline — this is the last section."""
    name: str = "triage_prompt_footer"
    priority: int = 100

    def render(self, ctx: ContextCtx) -> str | None:
        return "---\nAnalyze this build failure and provide your triage report."


# The default triage section roster. ``build_triage_payload`` passes
# this list to ``render_payload`` after binding I/O callables in ctx.
TRIAGE_SECTIONS: tuple[ContextSection, ...] = (
    SnippetsRoundSection(),
    PlaybooksSection(),
    UserContextSection(),
    MetadataSection(),
    BuildErrorsSection(),
    PortFilesSection(),
    ExistingPatchesSection(),
    SiblingBundlesSection(),
    PriorTriagesSection(),
    TriagePromptFooterSection(),
)


# -----------------------------------------------------------------------------
# Patch-payload-specific sections.
# -----------------------------------------------------------------------------


@dataclass
class AutomationContextSection:
    """The ``## Automation Context`` block that anchors the patch
    agent in the loop. Reads ``ctx.job`` (iteration / max_iterations /
    tier) and the pre-loaded ``ctx.prior_failure_count`` /
    ``window_hours`` / ``max_attempts_cap``.
    """
    name: str = "automation_context"
    priority: int = 20

    def render(self, ctx: ContextCtx) -> str | None:
        job = ctx.job or {}
        iteration = int(job.get("iteration", "1") or "1")
        max_iterations = int(job.get("max_iterations", "3") or "3")
        tier_ctx = job.get("tier", "") or "?"
        body = (
            f"- You are the patch agent in an automated DragonFly ports fix loop.\n"
            f"- This is iteration {iteration}/{max_iterations} for this patch job (tier={tier_ctx}).\n"
            f"- The same origin has produced {ctx.prior_failure_count} failure bundle(s) "
            f"in the last {ctx.window_hours} hour(s); the runner caps at "
            f"{ctx.max_attempts_cap} before forcing MANUAL.\n"
            f"- Your goal: either make dsynth_build report rebuild_ok=true, or "
            f"emit your best proposed fix with `Rebuild Status: gave-up` and a "
            f"concrete next-step recommendation in Patch Log. Either is a valid "
            f"outcome — burning the budget without trying anything is not.\n"
            f"- The Triage Summary below contains a `Suggested Fix` section. "
            f"**Apply it first.** Only explore further if the suggested fix has "
            f"already been tried (check Prior Attempts) or doesn't work."
        )
        return f"## Automation Context\n{body}\n"


@dataclass
class TriageSummarySection:
    """Embeds the bundle's ``analysis/triage.md`` so the patch agent
    has the classification, root cause, and suggested fix at hand."""
    name: str = "triage_summary"
    priority: int = 30
    # 0 = resolve from env at render time. Triage outputs are usually
    # small (~1-2 KB), but cap defensively for the same reason as the
    # other inline-file sections.
    max_chars: int = 0

    def render(self, ctx: ContextCtx) -> str | None:
        if ctx.read_bundle_text is None:
            return None
        triage = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "analysis/triage.md")
        if not triage:
            return None
        return f"## Triage Summary\n{_truncate_head_tail(triage, self.max_chars)}\n"


@dataclass
class PriorAttemptsSection:
    """``## Prior Attempts (most recent 3)`` — historical patch bundles.

    Pre-loaded by the caller into ``ctx.prior_patch_bundle_ids``;
    section reads each bundle's current patch artifacts via
    ``ctx.read_bundle_text``. Legacy artifact names are retained as a
    fallback for older bundles.
    """
    name: str = "prior_attempts"
    priority: int = 50
    max_bundles: int = 3
    max_patch_chars: int = 4000
    max_diff_chars: int = 6000
    max_trace_events: int = 12

    def _clip(self, value: str, limit: int) -> str:
        if len(value) <= limit:
            return value
        return value[:limit] + f"\n[...truncated to {limit} chars...]\n"

    def _audit_summary(self, content: str) -> str:
        try:
            data = json.loads(content)
        except ValueError:
            return self._clip(content, 2000)
        if not isinstance(data, dict):
            return self._clip(content, 2000)
        lines = []
        for key in ("status", "model", "via"):
            if data.get(key) is not None:
                lines.append(f"- {key}: {data[key]}")
        usage = data.get("tokens_used")
        if isinstance(usage, dict):
            lines.append(
                "- tokens: "
                f"prompt={usage.get('prompt', 0)} "
                f"completion={usage.get('completion', 0)} "
                f"total={usage.get('total', 0)}"
            )
        attempts = data.get("attempts")
        if isinstance(attempts, list):
            lines.append(f"- attempts: {len(attempts)}")
            for attempt in attempts[:5]:
                if isinstance(attempt, dict):
                    lines.append(
                        "  - "
                        f"attempt={attempt.get('attempt', '?')} "
                        f"tokens={attempt.get('tokens', '?')} "
                        f"rebuild_ok={attempt.get('rebuild_ok', '?')}"
                    )
            if attempts and isinstance(attempts[-1], dict):
                last_rebuild_ok = attempts[-1].get("rebuild_ok", "?")
                lines.append(f"- last_rebuild_ok: {last_rebuild_ok}")
        return "\n".join(lines) if lines else self._clip(content, 2000)

    def _tool_trace_summary(self, content: str) -> str:
        events: list[dict] = []
        for line in content.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if not events:
            return self._clip(content, 2000)
        lines = []
        for event in events[-self.max_trace_events:]:
            event_type = event.get("type", "?")
            if event_type == "attempt_start":
                used = event.get("tokens_used_so_far", 0)
                budget = event.get("budget", "?")
                lines.append(
                    f"- attempt_start {event.get('attempt', '?')}: "
                    f"tokens={used}/{budget}"
                )
                continue
            if event_type == "attempt_end":
                lines.append(
                    f"- attempt_end {event.get('attempt', '?')}: "
                    f"rebuild_ok={event.get('rebuild_ok', '?')} "
                    f"tokens={event.get('tokens', '?')}"
                )
                continue
            if event_type == "tool_call":
                args = event.get("args") if isinstance(event.get("args"), dict) else {}
                result = (
                    event.get("result")
                    if isinstance(event.get("result"), dict)
                    else {}
                )
                status = "?"
                if result.get("ok") is True:
                    status = "ok"
                elif result.get("ok") is False:
                    status = "fail"
                subject = (
                    args.get("origin") or args.get("path") or args.get("relpath") or ""
                )
                lines.append(f"- tool {event.get('tool', '?')} {status}: {subject}")
        return "\n".join(lines)

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.prior_patch_bundle_ids or ctx.read_bundle_text is None:
            return None
        lines = ["## Prior Attempts (most recent 3)"]
        emitted_any = False
        emitted_count = 0
        for past_bundle in ctx.prior_patch_bundle_ids:
            if emitted_count >= self.max_bundles:
                break
            section_lines = [f"### Bundle {past_bundle}"]
            had_content = False
            for relpath, title, code_block, transform in [
                (
                    "analysis/patch.md",
                    "Patch Report",
                    None,
                    lambda s: self._clip(s, self.max_patch_chars),
                ),
                (
                    "analysis/patch_audit.json",
                    "Patch Audit Summary",
                    None,
                    self._audit_summary,
                ),
                (
                    "analysis/changes.diff",
                    "Changes Diff",
                    "diff",
                    lambda s: self._clip(s, self.max_diff_chars),
                ),
                (
                    "analysis/tool_trace.jsonl",
                    "Tool Trace Summary",
                    None,
                    self._tool_trace_summary,
                ),
                (
                    "analysis/patch_plan.json",
                    "Legacy Patch Plan",
                    "json",
                    lambda s: s,
                ),
                ("analysis/patch.log", "Legacy Patch Log", None, lambda s: s),
                (
                    "analysis/rebuild_status.txt",
                    "Legacy Rebuild Status",
                    None,
                    lambda s: s,
                ),
            ]:
                content = ctx.read_bundle_text(None, past_bundle, relpath)
                if not content:
                    continue
                had_content = True
                section_lines.append(f"#### {title}")
                content = transform(content)
                if code_block:
                    section_lines.extend([f"```{code_block}", content, "```"])
                else:
                    section_lines.append(content)
                section_lines.append("")
            if had_content:
                lines.extend(section_lines)
                emitted_any = True
                emitted_count += 1
        if not emitted_any:
            return None
        return "\n".join(lines)


@dataclass
class DeferredFromConvertSection:
    """Step 37-3: surfaces the framework patches that the convert
    handler dropped from overlay.dops (see DeferredPatch on the
    bundle's ConvertResult). For each entry the patch agent decides
    whether the original intent is still relevant against current
    upstream, then emits a per-patch verdict.

    Reads typed ``ConvertResult`` from the bundle's
    ``analysis/convert_result.json`` via ``load_phase_result``. Renders
    nothing when convert wrote no result, no deferred patches, or
    schema mismatch (graceful degrade).
    """
    name: str = "deferred_from_convert"
    priority: int = 35  # between TriageSummary (30) and SiblingBundles (40)
    max_diff_chars: int = 8000  # cap per-patch diff inline

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.bundle_id and not ctx.bundle_dir:
            return None
        try:
            from dportsv3.agent.phase_result import (  # noqa: PLC0415
                ConvertResult, load_phase_result,
            )
        except Exception:
            return None
        try:
            cr = load_phase_result(
                ctx.bundle_dir, ctx.bundle_id, "convert", ConvertResult,
            )
        except Exception:
            # Schema mismatch / parse error → degrade silently.
            return None
        if cr is None or not cr.deferred_patches:
            return None

        lines = [
            "## Deferred from Convert",
            (
                "Convert produced a valid overlay.dops but dropped the "
                "framework patches listed below — compose rejected each "
                "one's hunks against current upstream. Treat each entry "
                "as INTENT (what the patch was doing) rather than "
                "AUTHORITY (the literal diff). For each one, decide "
                "whether the original intent is still relevant against "
                "the current upstream tree, then emit a per-patch "
                "verdict in your Patch Plan's `deferred_verdicts` field."
            ),
            (
                "Three outcomes per patch:"
            ),
            (
                "- `regenerated` — the intent still applies; edit "
                "`overlay.dops` directly to achieve it against current "
                "upstream."
            ),
            (
                "- `dropped` — the intent is no longer relevant (e.g. "
                "upstream already removed the lines); no edit, "
                "rationale=one sentence."
            ),
            (
                "- `escalated` — you can't determine relevance or how "
                "to regenerate; rationale=what blocks you."
            ),
            "",
        ]
        for dp in cr.deferred_patches:
            content = dp.original_content or ""
            if len(content) > self.max_diff_chars:
                content = (
                    content[: self.max_diff_chars]
                    + f"\n[... truncated to {self.max_diff_chars} chars ...]\n"
                )
            lines.extend([
                f"### {dp.path} → {dp.target_file}",
                f"Reject summary: {dp.reject_summary}",
                "Original content:",
                "```diff",
                content,
                "```",
                "",
            ])
        return "\n".join(lines)


@dataclass
class PatchPromptFooterSection:
    """Closing instruction for the patch agent. Always renders.

    The legacy code emits five lines without a trailing blank line.
    No trailing newline in render output (it's the last section)."""
    name: str = "patch_prompt_footer"
    priority: int = 120

    def render(self, ctx: ContextCtx) -> str | None:
        return (
            "---\n"
            "Use the dports tools to apply fixes in the shared workspace and rebuild the target origin.\n"
            "Return a report with these exact sections:\n"
            "- ## Patch Log\n"
            "- ## Rebuild Status\n"
            "- ## Patch Plan (JSON) with a ```json block\n"
            "- ## Rebuild Proof (JSON) with a ```json block"
        )


# Default patch section roster. ``build_patch_payload`` binds I/O
# callables into ctx and passes this list to ``render_payload``.
#
# Reused sections: Snippets, UserContext, Playbooks, Metadata, BuildErrors,
# PortFiles, ExistingPatches. New: AutomationContext, TriageSummary,
# PriorAttempts, PatchPromptFooter. SiblingBundles is parameterized
# (with_intro=False for the patch variant).
PATCH_SECTIONS: tuple[ContextSection, ...] = (
    SnippetsRoundSection(priority=10),
    AutomationContextSection(),
    TriageSummarySection(),
    DeferredFromConvertSection(),  # priority=35
    SiblingBundlesSection(priority=40, with_intro=False),
    PriorAttemptsSection(),
    UserContextSection(priority=60),
    PlaybooksSection(priority=70),
    MetadataSection(priority=80),
    BuildErrorsSection(priority=90),
    PortFilesSection(priority=100),
    ExistingPatchesSection(priority=110),
    PatchPromptFooterSection(),
)
