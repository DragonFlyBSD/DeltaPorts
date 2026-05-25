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
        # Wrote-by-this-transaction tracking. Used by the half-
        # migration invariant (§9.3): if a transaction has already
        # written dops-flavored ops, refuse subsequent compat-flavored
        # writes, and vice versa.
        self._touched_dops: bool = False
        self._touched_compat_makefile: bool = False
        # subprocess.run shim — tests inject a fake.
        self._git = git or _real_git

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
            return EditResult(
                ok=False, intent_type=(
                    getattr(raw, "type", None)
                    or (raw.get("type") if isinstance(raw, dict) else "")
                    or "unknown"
                ),
                error=str(exc),
            )

        self._check_half_migration_invariant(intent)

        # Dispatch on the intent type, mode-aware.
        renderer = self._renderer_for(intent)
        try:
            result = renderer(intent)
        except IntentError as exc:
            return EditResult(
                ok=False, intent_type=intent.type, error=str(exc),
            )
        # Track for the invariant.
        if result.ok:
            self._record_flavor(intent)
        return result

    # ----- invariants -------------------------------------------------

    def _check_half_migration_invariant(self, intent: Intent) -> None:
        """Design §9.3: no transaction may write both
        compat-Makefile.DragonFly edits and dops-statement edits.

        Specifically reject:
          - compat-mode change_makefile against Makefile.DragonFly,
            *after* the transaction has already touched overlay.dops
          - any dops-flavored intent (in compat mode) when
            Makefile.DragonFly has been touched
          - the convert_to_dops intent when any compat-flavored
            intent has already landed
        """
        # convert_to_dops is mode-restricted at the renderer level;
        # the invariant check fires only after a compat write tried
        # to coexist with it.
        if isinstance(intent, ConvertToDops):
            if self._touched_compat_makefile:
                raise IntentError(
                    "half-migration invariant: convert_to_dops "
                    "cannot follow a compat-mode write in the same "
                    "transaction",
                    intent={"type": "convert_to_dops"},
                )
        if isinstance(intent, ChangeMakefile):
            # Compat Makefile.DragonFly write after a dops write?
            if (intent.path.endswith("Makefile.DragonFly")
                    and self._touched_dops):
                raise IntentError(
                    "half-migration invariant: cannot edit "
                    f"{intent.path!r} after dops-flavored writes "
                    "in this transaction",
                )

    def _record_flavor(self, intent: Intent) -> None:
        # In dops mode, every successful apply touches overlay.dops.
        if self.mode == "dops":
            self._touched_dops = True
        elif self.mode == "compat":
            if isinstance(intent, ChangeMakefile) and intent.path.endswith(
                "Makefile.DragonFly"
            ):
                self._touched_compat_makefile = True

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
