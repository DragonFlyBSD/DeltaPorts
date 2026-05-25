"""Translator + transaction engine (Step 25b).

Public surface:

    Translator(workspace, origin, mode).apply(intent_dict) -> EditResult

``workspace`` is a Path: the dev-env's writable DeltaPorts root
(typically ``<env>/writable/work/DeltaPorts``). The translator
edits files under ``<workspace>/ports/<origin>/`` only.

``mode`` is one of ``"compat" | "dops" | "convert"``. Resolved by
the caller (in production: from ``classify_dops`` at BEGIN time).
Convert mode is for the convert agent and accepts the
``convert_to_dops`` intent; compat and dops modes reject it.

Per-intent atomicity: each ``apply`` either applies the intent in
full and returns ok=True, or rolls back any partial substrate
ops and returns ok=False with an error message. The intent log
accumulator (IntentLog in log.py) is the caller's responsibility
to populate from EditResult.

See ``docs/edit-intent-design.md`` §3, §4, §9 for grammar,
transaction semantics, and validator rules.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from .grammar import (
    AddFile,
    AddPatch,
    BumpPortrevision,
    ChangeMakefile,
    ConvertToDops,
    DropPatch,
    Intent,
    ReplaceInPatch,
)
from .validator import IntentError, parse_intent


Mode = Literal["compat", "dops", "convert"]


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
                 *, git: Callable[..., subprocess.CompletedProcess] | None = None):
        if mode not in ("compat", "dops", "convert"):
            raise ValueError(f"invalid mode: {mode!r}")
        self.workspace = Path(workspace)
        self.origin = origin
        self.mode = mode
        self.port_dir = self.workspace / "ports" / origin
        # subprocess.run shim — tests inject a fake.
        self._git = git or _real_git
        # NOTE on the half-migration invariant: design §9.3 originally
        # specified an in-transaction tracker (touched_dops vs
        # touched_compat_makefile). It turned out to be unreachable in
        # production because (a) mode is fixed at construction and
        # (b) renderers dispatch by mode, so a compat-mode Translator
        # can't ever produce dops-flavored writes and vice versa. The
        # real guard lives at the worker.apply_intent boundary:
        # worker.assess_dops returns action='surface_invariant' when
        # the substrate is in a mixed state, and apply_intent refuses
        # before constructing the Translator. See worker.apply_intent
        # docstring + the test at test_refuses_substrate_in_half_
        # migrated_state.

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
        # Import-on-demand to keep cycles benign + allow per-mode
        # renderer files to live alongside this module.
        if self.mode in ("compat", "convert"):
            from . import _compat as _mod
        else:
            from . import _dops as _mod

        # Per-intent dispatch table per mode.
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
        if isinstance(intent, ConvertToDops):
            # Restricted: only the convert agent (mode=="convert")
            # may emit this intent.
            if self.mode != "convert":
                return lambda i: EditResult(
                    ok=False, intent_type="convert_to_dops",
                    error=(
                        "convert_to_dops intent rejected: only the "
                        "convert agent (mode='convert') may emit it"
                    ),
                )
            from . import _convert as _conv_mod  # noqa: PLC0415
            return lambda i: _conv_mod.convert_to_dops(self, i)
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
