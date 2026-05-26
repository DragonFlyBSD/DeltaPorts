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
    DropPatch, ReplaceInDopsBlock, ReplaceInPatch,
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

    Validates target is a safe port-subtree relpath BEFORE
    appending — without this guard a malicious or buggy intent
    could write ``text replace-once file ../escape.c ...`` to
    overlay.dops, which compose would happily try to apply at
    materialize time.
    """
    from .translator import EditResult
    # Path safety. Reuses port_path's escape-check; we don't need
    # the resolved path, just the validation.
    try:
        t.port_path(intent.target)
    except IntentError as exc:
        return EditResult(
            ok=False, intent_type="replace_in_patch", error=str(exc),
        )
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


def _resolve_from_dupe(t, target: str) -> str | None:
    """Find the most recently modified file matching ``target``'s
    basename in the env's genpatch output directory.

    The env layout for the genpatch output isn't fully resolved
    without consulting the dev-env state (the WRKSRC path depends
    on the port's distfile layout). Conventional location is
    ``<workspace>/.genpatch-out/<basename>``; the helper also walks
    the workspace tree for sibling ``.genpatch-out`` directories
    so tests can place the file freely. Returns the file's text
    contents, or ``None`` if nothing matches.
    """
    from pathlib import Path  # noqa: PLC0415
    basename = Path(target).name
    candidates = [t.workspace / ".genpatch-out" / basename]
    work = t.workspace
    if work.is_dir():
        for sub in work.rglob(".genpatch-out"):
            cand = sub / basename
            if cand.is_file():
                candidates.append(cand)
    matches = [c for c in candidates if c.is_file()]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0].read_text()


def add_patch(t, intent: AddPatch):
    """Write the patch file + append `patch apply <target>` to overlay.dops."""
    from .translator import EditResult
    # Write the patch file. In dops mode the patch lives on disk
    # next to overlay.dops; the dops directive is the install
    # declaration. The file content comes from intent.diff or, if
    # from_dupe=True, from the env's genpatch output.
    target = t.port_path(intent.target)
    if target.exists():
        return EditResult(
            ok=False, intent_type="add_patch",
            error=f"patch already exists: {intent.target}",
        )
    if intent.from_dupe:
        diff_content = _resolve_from_dupe(t, intent.target)
        if diff_content is None:
            return EditResult(
                ok=False, intent_type="add_patch",
                error=(
                    f"from_dupe: no file matching basename "
                    f"{intent.target.rsplit('/', 1)[-1]!r} found in "
                    f"env's genpatch output dir"
                ),
            )
    else:
        diff_content = intent.diff or ""
    if not diff_content.strip():
        return EditResult(
            ok=False, intent_type="add_patch",
            error="add_patch requires non-empty diff content",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.write_text(diff_content)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="add_patch",
            error=f"write failed for {intent.target}: {exc}",
        )
    # Now append the dops directive that installs it.
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


def replace_in_dops_block(t, intent: ReplaceInDopsBlock):
    """Edit text inside an ``mk target set <name>`` heredoc body.

    Step C-4. Closes the gap that thrashed archivers/liblz4 on
    2026-05-26: convert produced a structurally valid overlay
    whose heredoc-bodied target recipe was internally broken,
    and no existing intent could reach into the heredoc to fix
    it. The agent could only ``add`` more directives via
    change_makefile / add_file, which corrupted the overlay
    further. This intent surgically replaces one occurrence of
    ``find`` with ``replace`` inside the named block's body —
    nothing outside the block is touched.

    The block delimiter is the line ``mk target set <block_name> <<TAG``
    (or ``mk target append/remove/rename ... <<TAG``). The body
    runs until a line matching exactly ``TAG`` (the heredoc tag
    chosen by the convert agent — typically ``MK``, ``MK1``, etc.).

    Refusals (ok=False):
    - block not found by name
    - find string not present in the block body
    - occurrence requested exceeds matches
    - block body is unbounded (no closing tag) — corrupt overlay
    """
    from .translator import EditResult
    overlay = t.port_path(_DOPS_FILE)
    if not overlay.is_file():
        return EditResult(
            ok=False, intent_type="replace_in_dops_block",
            error=f"{_DOPS_FILE} does not exist; nothing to edit",
        )
    text = overlay.read_text()
    new_text, found, why = _replace_in_mk_target_block(
        text, intent.block_name, intent.find, intent.replace,
        intent.occurrence,
    )
    if not found:
        return EditResult(
            ok=False, intent_type="replace_in_dops_block",
            error=why,
        )
    try:
        overlay.write_text(new_text)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="replace_in_dops_block",
            error=f"write failed for {_DOPS_FILE}: {exc}",
        )
    return EditResult(
        ok=True, intent_type="replace_in_dops_block",
        paths_changed=[str(overlay.relative_to(t.workspace))],
        substrate_diff=t.git_diff(overlay),
    )


def _replace_in_mk_target_block(
    text: str, block_name: str, find: str, replace: str,
    occurrence: int,
) -> tuple[str, bool, str]:
    """Replace ``find`` with ``replace`` inside the body of the
    ``mk target set|append|remove|rename <block_name>`` heredoc
    block.

    Returns (new_text, found, reason). ``found`` is True on
    success; reason is empty. On failure, ``new_text`` equals the
    input and ``reason`` names the specific shape problem so the
    agent can react.

    Heredoc body extraction is tag-based: ``<<TAG`` opens, a line
    matching exactly ``TAG`` closes. Tabs/spaces around the tag
    are tolerated on the open form (``mk target set foo <<MK``);
    the close line is checked stripped.
    """
    lines = text.splitlines(keepends=True)
    open_idx: int | None = None
    tag: str | None = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        # `mk target <action> <block_name> ...` shape.
        # Tolerate `set`, `append`, `remove`, `rename` after `mk target`.
        if not stripped.startswith("mk target "):
            continue
        rest = stripped[len("mk target "):]
        # Expect: <action> <name> ... <<TAG
        parts = rest.split(None, 2)
        if len(parts) < 2:
            continue
        # rename has a different shape: `rename <old> -> <new>`. Not
        # our concern — it doesn't have a body.
        if parts[0] == "rename":
            continue
        if parts[1] != block_name:
            continue
        # Find the heredoc tag — token after `<<`.
        heredoc_pos = stripped.find("<<")
        if heredoc_pos < 0:
            # Block without a body (single-line variant); no body
            # to edit.
            continue
        tag_part = stripped[heredoc_pos + 2:].strip()
        # Allow quoted ('<<'TAG') and unquoted (<<TAG). Strip one
        # outer pair of single quotes if present.
        if tag_part.startswith("'") and tag_part.endswith("'") and len(tag_part) >= 2:
            tag_part = tag_part[1:-1]
        if not tag_part:
            continue
        open_idx = i
        tag = tag_part
        break
    if open_idx is None or tag is None:
        return (text, False, (
            f"no `mk target set/append/remove {block_name} <<...` "
            f"heredoc block found in overlay.dops"
        ))
    # Find the close line.
    close_idx: int | None = None
    for j in range(open_idx + 1, len(lines)):
        if lines[j].strip() == tag:
            close_idx = j
            break
    if close_idx is None:
        return (text, False, (
            f"heredoc block {block_name!r} opens with <<{tag} but "
            f"has no closing line — overlay.dops is corrupt"
        ))
    # Body is lines (open_idx+1, close_idx) exclusive of the tag
    # lines.
    body_lines = lines[open_idx + 1:close_idx]
    body = "".join(body_lines)
    # Replace nth occurrence in body.
    matches = []
    start = 0
    while True:
        pos = body.find(find, start)
        if pos < 0:
            break
        matches.append(pos)
        start = pos + 1
    if not matches:
        return (text, False, (
            f"find string not present in block {block_name!r}: "
            f"{find[:80]!r}"
        ))
    if occurrence < 1 or occurrence > len(matches):
        return (text, False, (
            f"occurrence {occurrence} requested but block "
            f"{block_name!r} has {len(matches)} match(es) of "
            f"{find[:40]!r}"
        ))
    pos = matches[occurrence - 1]
    new_body = body[:pos] + replace + body[pos + len(find):]
    new_text = (
        "".join(lines[:open_idx + 1])
        + new_body
        + "".join(lines[close_idx:])
    )
    return (new_text, True, "")


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
