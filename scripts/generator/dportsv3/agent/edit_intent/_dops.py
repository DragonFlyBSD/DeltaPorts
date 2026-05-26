"""Dops-mode renderers (Step 25b).

Each renderer translates one intent into one or more statements
appended to ``ports/<origin>/overlay.dops``. For intents that also
carry a payload file (add_patch with diff content), the file is
written to its substrate location AND the dops statement
references it — that's the canonical compat/dops dichotomy: the
file is identical on disk, only the metadata (Makefile.DragonFly
vs overlay.dops) differs.

Per design §11.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .grammar import (
    AddFile, AddPatch, BumpPortrevision, ChangeMakefile,
    DropPatch, ReplaceInPatch,
)
from .validator import IntentError


_DOPS_FILE = "overlay.dops"


# --------------------------------------------------------------------
# Public renderers (one per intent type)
# --------------------------------------------------------------------


def replace_in_patch(t, intent: ReplaceInPatch):
    """``text replace-once file <target> from "X" to "Y"`` per dops grammar.

    Note the hyphen in ``replace-once`` (not underscore) and the
    absence of dots — the prior shape ``text.replace_once file=...``
    was invalid grammar that the engine parser rejected, silently
    corrupting overlays (archivers/liblz4 2026-05-26).
    """
    from .translator import EditResult
    stmt = _stmt_text_replace_once(intent.target, intent.find, intent.replace)
    return _append_overlay(t, "replace_in_patch", [stmt])


def drop_patch(t, intent: DropPatch):
    """Remove a patch install for ``intent.target`` from overlay.dops.

    Handles both shapes the dops grammar emits for patch installs:

    - ``patch apply <target>`` (inline) — strips the line.
    - ``file materialize <src> -> <target>`` (materialized) —
      strips the line AND deletes the referenced patch file under
      ``ports/<origin>/<target>``. Without the file-delete the
      materialize itself was the only declaration of the patch's
      existence in the overlay, but the patch bytes would still
      sit on disk and confuse the next person to read the port.

    Refusal when no shape matches names both shapes so the agent
    doesn't get an ambiguous "no `patch apply` found" when the
    overlay uses ``file materialize`` (the gperf 2026-05-26 trap).
    """
    from .translator import EditResult
    overlay = t.port_path(_DOPS_FILE)
    if not overlay.is_file():
        return EditResult(
            ok=False, intent_type="drop_patch",
            error=f"{_DOPS_FILE} does not exist; nothing to remove from",
        )
    original = overlay.read_text()
    new, removed, shape = _strip_patch_apply_stmt(original, intent.target)
    if not removed:
        # Diagnostic improvement: if the overlay has an `mk target
        # set <name>` block whose body references the target, tell
        # the agent we can't edit heredoc bodies via drop_patch
        # (no intent type covers that shape) and point at
        # escalation. Stops the workaround thrash seen on
        # archivers/liblz4 2026-05-26 where the agent reached for
        # change_makefile + add_file as substitutes and corrupted
        # the overlay.
        mk_target_hint = ""
        if "mk target " in original and (
            intent.target.rsplit("/", 1)[-1] in original
        ):
            mk_target_hint = (
                f" — note: {_DOPS_FILE} contains an `mk target` "
                f"block referencing {intent.target!r}, which is a "
                f"heredoc-body patch (sed/REINPLACE_CMD inside the "
                f"target body). No intent type can edit heredoc "
                f"bodies; the agent should escalate to MANUAL "
                f"rather than reach for change_makefile or add_file "
                f"as workarounds (they will corrupt the overlay)."
            )
        return EditResult(
            ok=False, intent_type="drop_patch",
            error=(
                f"no `patch apply {intent.target}` or "
                f"`file materialize ... -> {intent.target}` statement "
                f"found in {_DOPS_FILE}{mk_target_hint}"
            ),
        )
    try:
        overlay.write_text(new)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="drop_patch",
            error=f"write failed for {_DOPS_FILE}: {exc}",
        )
    paths_changed = [str(overlay.relative_to(t.workspace))]
    diff_targets = [overlay]
    # For materialized patches, also delete the patch file itself.
    if shape == "file_materialize":
        patch_file = t.port_path(intent.target)
        if patch_file.is_file():
            try:
                patch_file.unlink()
            except OSError as exc:
                # Rollback the overlay edit so the half-applied state
                # doesn't confuse the next intent.
                overlay.write_text(original)
                return EditResult(
                    ok=False, intent_type="drop_patch",
                    error=(
                        f"could not delete patch file "
                        f"{intent.target}: {exc}"
                    ),
                )
            paths_changed.append(str(patch_file.relative_to(t.workspace)))
            diff_targets.append(patch_file)
    return EditResult(
        ok=True, intent_type="drop_patch",
        paths_changed=paths_changed,
        substrate_diff=t.git_diff(*diff_targets),
    )


def add_patch(t, intent: AddPatch):
    """Write the patch file + append `patch apply <target>` to overlay.dops."""
    from .translator import EditResult
    from . import _compat
    # In dops mode we still need the patch file on disk.
    # Delegate the file-write half to the compat renderer (which
    # writes the diff content); then append the dops statement.
    file_result = _compat.add_patch(t, intent)
    if not file_result.ok:
        return file_result
    target = t.port_path(intent.target)
    stmt_result = _append_overlay(t, "add_patch",
                                  [f"patch apply {intent.target}"])
    if not stmt_result.ok:
        # Roll back the file write so the half-applied state doesn't
        # confuse the next intent.
        try:
            target.unlink()
        except OSError:
            pass
        return stmt_result
    # Merge paths_changed; substrate_diff is the union (we re-run
    # git_diff over both).
    overlay = t.port_path(_DOPS_FILE)
    return EditResult(
        ok=True, intent_type="add_patch",
        paths_changed=[
            str(target.relative_to(t.workspace)),
            str(overlay.relative_to(t.workspace)),
        ],
        substrate_diff=t.git_diff(target, overlay),
    )


def add_file(t, intent: AddFile):
    """Render add_file in dops mode.

    For kind=resource: write content + emit
    ``file copy <dest> -> <dest>``.
    For kind=materialize: emit ``file materialize <src> -> <dst>``.
    """
    from .translator import EditResult
    # Refuse to create Makefile.DragonFly on a dops-mode port — that
    # would create the half-migrated state (dops_with_unmigrated_makefile_dragonfly)
    # that the substrate invariant later refuses on every apply_intent.
    # Observed self-induced deadlock on archivers/liblz4 2026-05-26:
    # agent called add_file(dest=Makefile.DragonFly), then immediately
    # hit substrate_invariant on the next intent and burned 4 attempts
    # against a wall it had built itself.
    dest_basename = intent.dest.rsplit("/", 1)[-1]
    if dest_basename.startswith("Makefile.DragonFly"):
        return EditResult(
            ok=False, intent_type="add_file",
            error=(
                f"add_file refused: creating {intent.dest!r} on a "
                f"dops-mode port would produce a half-migrated "
                f"state (Makefile.DragonFly + overlay.dops together) "
                f"that violates the substrate invariant. Use "
                f"change_makefile to express Makefile.DragonFly-shaped "
                f"variable edits as `mk` directives in overlay.dops "
                f"instead. If the port genuinely needs both forms "
                f"the conversion is incorrect — escalate to operator."
            ),
        )
    if intent.kind == "resource":
        target = t.port_path(intent.dest)
        if target.exists():
            return EditResult(
                ok=False, intent_type="add_file",
                error=f"dest already exists: {intent.dest}",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.write_text(intent.content or "")
        except OSError as exc:
            return EditResult(
                ok=False, intent_type="add_file",
                error=f"write failed for {intent.dest}: {exc}",
            )
        # `file copy <src> -> <dst>` per the dops grammar (arrow
        # token between operands, no dots, no named args).
        stmt = f"file copy {intent.dest} -> {intent.dest}"
        stmt_result = _append_overlay(t, "add_file", [stmt])
        if not stmt_result.ok:
            try:
                target.unlink()
            except OSError:
                pass
            return stmt_result
        overlay = t.port_path(_DOPS_FILE)
        return EditResult(
            ok=True, intent_type="add_file",
            paths_changed=[
                str(target.relative_to(t.workspace)),
                str(overlay.relative_to(t.workspace)),
            ],
            substrate_diff=t.git_diff(target, overlay),
        )
    if intent.kind == "materialize":
        # `file materialize <src> -> <dst>` per the dops grammar.
        stmt = f"file materialize {intent.source} -> {intent.dest}"
        return _append_overlay(t, "add_file", [stmt])
    return EditResult(
        ok=False, intent_type="add_file",
        error=f"unknown kind: {intent.kind!r}",
    )


def _quote_dops_string(s: str) -> str:
    """Escape ``s`` for use as a quoted string literal in dops.

    Matches the lexer's string-escape rules at engine/lexer.py:
    ``\\``→``\\\\``, ``"``→``\\"``, newline→``\\n``, tab→``\\t``.
    All other characters pass through.
    """
    out: list[str] = ['"']
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def change_makefile(t, intent: ChangeMakefile):
    """``mk <action> VAR "value"`` per the dops grammar.

    The previous form (``mk.var.set var=K value=V``, dot-separated
    with named args) was invented — the actual engine parser at
    ``engine/parser.py:343`` expects space-separated tokens
    ``mk set|unset|add|remove VAR "value"``. The intent's ``op``
    field uses ``set`` / ``append`` / ``remove``; ``append`` maps
    to dops ``add`` (the parser's name for "append to a list-shaped
    variable"). ``set`` takes a quoted STRING value; ``add`` /
    ``remove`` take a token (we quote them too for safety).
    """
    from .translator import EditResult
    action = {"set": "set", "append": "add", "remove": "remove"}[intent.op]
    stmt = f"mk {action} {intent.key} {_quote_dops_string(intent.value)}"
    return _append_overlay(t, "change_makefile", [stmt])


def bump_portrevision(t, intent: BumpPortrevision):
    """``mk set PORTREVISION "<n+1>"`` per the dops grammar.

    Hardcoded to "1" today — the actual increment needs to read
    the current PORTREVISION from the upstream Makefile (or from
    a prior dops `mk set PORTREVISION` statement). 25c can wire a
    smarter increment later; for now the agent emits "1" which is
    correct for a port that's never been revision-bumped before.
    """
    from .translator import EditResult
    stmt = 'mk set PORTREVISION "1"'
    return _append_overlay(t, "bump_portrevision", [stmt])


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def _append_overlay(t, intent_type: str, statements: Iterable[str]):
    """Append one or more dops statements to ports/<origin>/overlay.dops.

    Creates the file with a minimal header if it didn't already
    exist. Returns an EditResult with the diff scoped to overlay.dops.
    """
    from .translator import EditResult
    overlay = t.port_path(_DOPS_FILE)
    existed = overlay.is_file()
    if existed:
        original = overlay.read_text()
    else:
        original = _initial_overlay_header(t)
    new = original
    if not new.endswith("\n"):
        new += "\n"
    for stmt in statements:
        new += stmt.rstrip() + "\n"
    try:
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(new)
    except OSError as exc:
        # Roll back to original if it existed.
        if existed:
            try:
                overlay.write_text(original)
            except OSError:
                pass
        return EditResult(
            ok=False, intent_type=intent_type,
            error=f"write failed for {_DOPS_FILE}: {exc}",
        )
    return EditResult(
        ok=True, intent_type=intent_type,
        paths_changed=[str(overlay.relative_to(t.workspace))],
        substrate_diff=t.git_diff(overlay),
    )


def _initial_overlay_header(t) -> str:
    """Minimal header for a freshly-created overlay.dops.

    Matches the convention emitted by ``migration.convert``.
    """
    return (
        f"target @main\n"
        f"port {t.origin}\n"
        f"type port\n"
        f"reason \"agent edits via edit-intent DSL\"\n"
        f"\n"
    )


def _stmt_text_replace_once(target: str, find: str, replace: str) -> str:
    """Serialize a ``text replace-once`` statement per the dops grammar.

    Form: ``text replace-once file <path> from "X" to "Y"`` —
    space-separated tokens, hyphen in ``replace-once``, strings
    double-quoted with lexer-compatible escapes. The previous
    form (``text.replace_once file=... from=... to=...``) was
    invented; the engine parser rejects it.
    """
    return (
        f"text replace-once file {target} "
        f"from {_quote_dops_string(find)} "
        f"to {_quote_dops_string(replace)}"
    )


def _strip_patch_apply_stmt(text: str, target: str) -> tuple[str, bool, str]:
    """Remove a patch-install statement for ``target`` from dops text.

    Recognizes two shapes (convert produces the second; older
    hand-authored overlays produce the first):

    1. ``patch apply <target>`` — inline patch install
    2. ``file materialize <src> -> <target>`` — materialized
       patch install (only matched when the destination is the
       target AND the destination looks like a patch path,
       i.e. starts with ``dragonfly/patch-`` to avoid
       accidentally stripping non-patch ``file materialize``
       lines)

    Returns (new_text, removed, shape) where ``shape`` is
    ``"patch_apply"`` or ``"file_materialize"`` or ``""`` if
    nothing matched. Matches with optional leading whitespace;
    preserves all other lines.
    """
    out = []
    removed = False
    shape = ""
    patch_apply_needle = f"patch apply {target}"
    looks_like_patch = target.startswith("dragonfly/patch-")
    for line in text.splitlines():
        stripped = line.strip()
        if not removed:
            if stripped == patch_apply_needle:
                removed = True
                shape = "patch_apply"
                continue
            if looks_like_patch and stripped.startswith("file materialize "):
                # Parse "file materialize <src> -> <dest>". Tolerate
                # extra whitespace around the arrow.
                rest = stripped[len("file materialize "):]
                if "->" in rest:
                    src, _, dest = rest.partition("->")
                    if dest.strip() == target:
                        removed = True
                        shape = "file_materialize"
                        continue
        out.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return ("\n".join(out) + suffix, removed, shape)
