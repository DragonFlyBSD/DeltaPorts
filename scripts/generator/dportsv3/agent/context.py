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

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol, runtime_checkable


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
    kedb_dir: Path | None = None
    # Pre-loaded data the caller computes once and hands to sections
    # that need it.
    port_history: object | None = None  # decision.PortHistory or None
    sibling_bundle_ids: list[str] = field(default_factory=list)
    prior_triage_bundle_ids: list[str] = field(default_factory=list)
    prior_patch_bundle_ids: list[str] = field(default_factory=list)
    user_context_text: str | None = None
    kedb_text: str | None = None
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
class KEDBSection:
    """Known Error Database content. Pre-loaded into ctx.kedb_text."""
    name: str = "kedb"
    priority: int = 20

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.kedb_text:
            return None
        return _trailing_blank(ctx.kedb_text)


@dataclass
class UserContextSection:
    """Run-scoped user context. Pre-loaded into ctx.user_context_text."""
    name: str = "user_context"
    priority: int = 30

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.user_context_text:
            return None
        return f"## User Context (run-scoped)\n{ctx.user_context_text}\n"


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

    def render(self, ctx: ContextCtx) -> str | None:
        if ctx.read_bundle_text is None:
            return None
        errors = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "logs/errors.txt")
        if not errors:
            return None
        return f"## Build Errors\n{errors}\n"


@dataclass
class PortFilesSection:
    """``## Port Files`` header + Makefile, pkg-plist, distinfo subsections.

    The header always renders (even with no files); the subsections
    only render when their respective files exist in the bundle.
    """
    name: str = "port_files"
    priority: int = 60

    def render(self, ctx: ContextCtx) -> str | None:
        if ctx.read_bundle_text is None:
            return "## Port Files\n"  # header alone with trailing blank
        lines = ["## Port Files"]

        makefile = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "port/Makefile")
        if makefile:
            lines.extend(["### Makefile", "```makefile", makefile, "```", ""])

        plist = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "port/pkg-plist")
        if plist:
            lines.extend(["### pkg-plist", "```", plist, "```", ""])

        distinfo = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "port/distinfo")
        if distinfo:
            lines.extend(["### distinfo", "```", distinfo, "```", ""])

        return "\n".join(lines)


@dataclass
class ExistingPatchesSection:
    """``### Existing Patches`` listing — diff fences per patch file."""
    name: str = "existing_patches"
    priority: int = 70

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
            lines.extend([f"#### {name}", "```diff", content, "```", ""])
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
            lines.extend([f"### Bundle {sib_id}", "```", sib_errors, "```", ""])
        return "\n".join(lines)


@dataclass
class PriorTriagesSection:
    """``## Prior Triages (most recent 2)`` — pre-loaded historical
    bundles' triage.md + rebuild_proof.json."""
    name: str = "prior_triages"
    priority: int = 90

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.prior_triage_bundle_ids or ctx.read_bundle_text is None:
            return None
        lines = ["## Prior Triages (most recent 2)"]
        emitted_any = False
        for past_bundle in ctx.prior_triage_bundle_ids[:2]:
            section_lines = [f"### Bundle {past_bundle}"]
            had_content = False
            for relpath, title, code_block in [
                ("analysis/triage.md", "Triage", None),
                ("analysis/rebuild_proof.json", "Rebuild Proof", "json"),
            ]:
                content = ctx.read_bundle_text(None, past_bundle, relpath)
                if not content:
                    continue
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
    KEDBSection(),
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

    def render(self, ctx: ContextCtx) -> str | None:
        if ctx.read_bundle_text is None:
            return None
        triage = ctx.read_bundle_text(ctx.bundle_dir, ctx.bundle_id, "analysis/triage.md")
        if not triage:
            return None
        return f"## Triage Summary\n{triage}\n"


@dataclass
class PriorAttemptsSection:
    """``## Prior Attempts (most recent 3)`` — historical patch
    bundles' patch_plan.json + patch.log + rebuild_status.txt.

    Pre-loaded by the caller into ``ctx.prior_patch_bundle_ids``;
    section reads each bundle's artifacts via ``ctx.read_bundle_text``.
    """
    name: str = "prior_attempts"
    priority: int = 50

    def render(self, ctx: ContextCtx) -> str | None:
        if not ctx.prior_patch_bundle_ids or ctx.read_bundle_text is None:
            return None
        lines = ["## Prior Attempts (most recent 3)"]
        emitted_any = False
        for past_bundle in ctx.prior_patch_bundle_ids[:3]:
            section_lines = [f"### Bundle {past_bundle}"]
            had_content = False
            for relpath, title, code_block in [
                ("analysis/patch_plan.json", "Patch Plan", "json"),
                ("analysis/patch.log", "Patch Log", None),
                ("analysis/rebuild_status.txt", "Rebuild Status", None),
            ]:
                content = ctx.read_bundle_text(None, past_bundle, relpath)
                if not content:
                    continue
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
# Reused sections: Snippets, UserContext, KEDB, Metadata, BuildErrors,
# PortFiles, ExistingPatches. New: AutomationContext, TriageSummary,
# PriorAttempts, PatchPromptFooter. SiblingBundles is parameterized
# (with_intro=False for the patch variant).
PATCH_SECTIONS: tuple[ContextSection, ...] = (
    SnippetsRoundSection(priority=10),
    AutomationContextSection(),
    TriageSummarySection(),
    SiblingBundlesSection(priority=40, with_intro=False),
    PriorAttemptsSection(),
    UserContextSection(priority=60),
    KEDBSection(priority=70),
    MetadataSection(priority=80),
    BuildErrorsSection(priority=90),
    PortFilesSection(priority=100),
    ExistingPatchesSection(priority=110),
    PatchPromptFooterSection(),
)
