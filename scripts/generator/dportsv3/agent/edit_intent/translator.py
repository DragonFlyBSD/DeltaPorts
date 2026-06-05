"""Translator + transaction engine (Step 25b).

Public surface:

    Translator(workspace, origin, mode).apply(intent_dict) -> EditResult

``workspace`` is a Path: the dev-env's writable DeltaPorts root
(typically ``<env>/writable/work/DeltaPorts``). The translator
edits files under ``<workspace>/ports/<origin>/`` only.

``mode`` is ``"dops"`` — the only supported mode after Step C.
The patch agent operates only on dops-converted substrate
(enforced upstream at worker.apply_intent). Compat-mode editing
and the convert-mode intent (convert_to_dops) were both removed
as part of dops-only consolidation; the convert agent uses
direct put_file, not apply_intent.

Per-intent atomicity: each ``apply`` either applies the intent in
full and returns ok=True, or rolls back any partial substrate
ops and returns ok=False with an error message. The intent log
accumulator (IntentLog in log.py) is the caller's responsibility
to populate from EditResult.

See ``docs/edit-intent-design.md`` §3, §4, §9 for grammar,
transaction semantics, and validator rules.
"""

from __future__ import annotations

import difflib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from .grammar import (
    AddFile,
    AddPatch,
    BumpPortrevision,
    ChangeMakefile,
    DropFile,
    DropMkDirective,
    DropTargetBlock,
    DropPatch,
    Intent,
    ReplaceInDopsBlock,
    ReplaceInPatch,
)
from .validator import IntentError, parse_intent


Mode = Literal["dops"]


@dataclass
class EditResult:
    """Outcome of one intent application."""
    ok: bool
    intent_type: str
    paths_changed: list[str] = field(default_factory=list)
    substrate_diff: str = ""
    error: str | None = None


class Translator:
    """Per-transaction translator. Construct once per agent run."""

    def __init__(self, workspace: Path, origin: str, mode: Mode,
                 *,
                 wrksrc: str | None = None,
                 target: str | None = None,
                 git: Callable[..., subprocess.CompletedProcess] | None = None):
        if mode != "dops":
            raise ValueError(
                f"invalid mode: {mode!r} (post-Step-C: only 'dops' "
                f"is supported; compat and convert modes were "
                f"removed)"
            )
        self.workspace = Path(workspace)
        self.origin = origin
        self.mode = mode
        # `wrksrc` is the extracted source root for this transaction.
        # `add_patch(from_dupe=True)`'s lookup walks this directory
        # for the patch genpatch produced. Optional — legacy callers
        # (and tests) that don't have a wrksrc fall back to the
        # workspace-relative `.genpatch-out` lookup.
        self.wrksrc = wrksrc
        # `target` is the env's compose target (e.g. `@2026Q2`, `@any`,
        # `@main`). Step 38a plumbing: stored on the translator so
        # downstream renderers (Step 38b) can emit per-target-scoped
        # statements without each having to consult the runner. Optional
        # for backward compatibility — renderers ignore it pre-38b,
        # and tests that don't exercise scope can leave it None.
        self.target = target
        self.port_dir = self.workspace / "ports" / origin
        # subprocess.run shim — tests inject a fake.
        self._git = git or _real_git
        # The half-migration invariant lives at the
        # worker.apply_intent boundary: worker.assess_dops returns
        # action='surface_invariant' when the substrate is in a
        # mixed state (Makefile.DragonFly + overlay.dops together),
        # and apply_intent refuses before constructing the
        # Translator. Post-Step-C: only mode='dops' is supported;
        # compat-mode and convert-mode were both removed as
        # unreachable dead code.

    # ----- public API -------------------------------------------------

    def apply(self, raw: dict[str, Any] | str | Intent) -> EditResult:
        """Validate + render + apply one intent atomically.

        On any failure, partial substrate ops are rolled back via
        each renderer's own inverse logic; the EditResult records
        ok=False + error.
        """
        try:
            if isinstance(raw, (dict, str)):
                intent = parse_intent(raw)
            else:
                intent = raw
        except IntentError as exc:
            # parse_intent attaches the raw intent dict to the
            # exception when it could resolve a type field; use that
            # for the EditResult's intent_type so the LLM sees the
            # type it tried to emit, not a generic "unknown".
            attached = (exc.intent or {}) if isinstance(exc.intent, dict) else {}
            return EditResult(
                ok=False,
                intent_type=(
                    attached.get("type")
                    or (raw.get("type") if isinstance(raw, dict) else None)
                    or "unknown"
                ),
                error=str(exc),
            )

        # Dispatch on the intent type, mode-aware. The half-migration
        # invariant is enforced upstream at worker.apply_intent via
        # assess_dops; the Translator itself only runs after the
        # substrate has been validated.
        renderer = self._renderer_for(intent)
        try:
            return renderer(intent)
        except IntentError as exc:
            return EditResult(
                ok=False, intent_type=intent.type, error=str(exc),
            )

    # ----- dispatch ---------------------------------------------------

    def _renderer_for(self, intent: Intent) -> Callable[[Intent], EditResult]:
        # Step C: dops-only renderers. The patch agent's surface is
        # mode="dops"; every renderer lives in _dops.
        from . import _dops as _mod  # noqa: PLC0415

        if isinstance(intent, ReplaceInPatch):
            return lambda i: _mod.replace_in_patch(self, i)
        if isinstance(intent, DropPatch):
            return lambda i: _mod.drop_patch(self, i)
        if isinstance(intent, AddPatch):
            return lambda i: _mod.add_patch(self, i)
        if isinstance(intent, AddFile):
            return lambda i: _mod.add_file(self, i)
        if isinstance(intent, ChangeMakefile):
            return lambda i: _mod.change_makefile(self, i)
        if isinstance(intent, BumpPortrevision):
            return lambda i: _mod.bump_portrevision(self, i)
        if isinstance(intent, ReplaceInDopsBlock):
            return lambda i: _mod.replace_in_dops_block(self, i)
        if isinstance(intent, DropMkDirective):
            return lambda i: _mod.drop_mk_directive(self, i)
        if isinstance(intent, DropFile):
            return lambda i: _mod.drop_file(self, i)
        if isinstance(intent, DropTargetBlock):
            return lambda i: _mod.drop_target_block(self, i)
        return lambda i: EditResult(
            ok=False, intent_type=getattr(i, "type", "unknown"),
            error=f"no renderer for intent: {i!r}",
        )

    # ----- helpers used by renderers ---------------------------------

    def port_path(self, relpath: str) -> Path:
        """Resolve a port-subtree relpath. Refuses paths escaping
        the port directory."""
        if relpath.startswith("/") or ".." in relpath.split("/"):
            raise IntentError(
                f"intent target {relpath!r} must be a relative path "
                "under ports/<origin>/, not absolute or with '..'",
            )
        return self.port_dir / relpath

    def diff_from_before(self, before: dict[Path, str | None]) -> str:
        """Per-intent unified diff over the given paths.

        ``before`` maps absolute Paths to their pre-intent contents
        (None means the file did not exist). The current on-disk
        state is read after writes and diffed against ``before``;
        the result captures **only what this intent changed**, not
        the cumulative working-tree state.

        The prior implementation used ``git diff HEAD -- ...``,
        which reports cumulative state since HEAD. After N intents
        on the same file every intent's ``substrate_diff`` shows
        the same cumulative diff — making it impossible to read
        the intent log forensically and giving the agent false
        "I made progress" signals for no-op intents (archivers/liblz4
        2026-05-26). difflib is stdlib-only and matches the
        agent-visible diff shape git produced.
        """
        if not before:
            return ""
        chunks: list[str] = []
        for path, old in before.items():
            if path.is_file():
                try:
                    new = path.read_text()
                except OSError:
                    new = ""
            else:
                new = None
            if old == new:
                continue
            rel = str(path.relative_to(self.workspace))
            from_label = "a/" + rel if old is not None else "/dev/null"
            to_label = "b/" + rel if new is not None else "/dev/null"
            old_lines = (old or "").splitlines(keepends=True)
            new_lines = (new or "").splitlines(keepends=True)
            diff = difflib.unified_diff(
                old_lines, new_lines, fromfile=from_label, tofile=to_label,
            )
            text = "".join(diff)
            if text:
                chunks.append(text)
        return "".join(chunks)

    def git_diff(self, *paths: Path) -> str:
        """Capture a unified diff over the given paths, covering all
        three change shapes: tracked-modified, tracked-deleted,
        untracked-new.

        Strategy: ``git add --intent-to-add`` for paths that exist in
        the working tree (makes new files visible), then
        ``git diff HEAD -- ...`` (captures additions, modifications,
        and deletions in one command), then ``git reset`` to drop the
        intent-to-add entries.

        Doing intent-to-add on a tracked-deleted path corrupts the
        diff (git replaces the deletion with a "phantom" entry),
        which is why the existence check is required.
        """
        if not paths:
            return ""
        rels = [str(p.relative_to(self.workspace)) for p in paths]
        to_add = [rel for rel, p in zip(rels, paths) if p.exists()]
        if to_add:
            self._git("-C", str(self.workspace),
                      "add", "--intent-to-add", "--", *to_add, check=False)
        try:
            p = self._git("-C", str(self.workspace),
                          "diff", "HEAD", "--", *rels,
                          capture_output=True, text=True, check=False)
            return p.stdout
        finally:
            if to_add:
                self._git("-C", str(self.workspace),
                          "reset", "--", *to_add, check=False)


def _real_git(*args: str, **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], **kwargs)
