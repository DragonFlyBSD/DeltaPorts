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
    DropFile, DropMkDirective, DropPatch, DropTargetBlock,
    ReplaceInDopsBlock, ReplaceInPatch,
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
    return _append_overlay(t, "replace_in_patch", [stmt], scope=intent.scope)


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
    before_overlay = original
    patch_file = t.port_path(intent.target)
    before_patch = patch_file.read_text() if patch_file.is_file() else None
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
    before_state: dict[Path, str | None] = {overlay: before_overlay}
    # Delete the patch file on disk for BOTH install shapes. Symmetric
    # cleanup: dropping the install directive without dropping the file
    # leaves an orphan that blocks a subsequent add_patch with
    # "patch already exists" (devel_jwasm-20260602-204312Z trap). The
    # rationale that justifies deletion for file_materialize — "the
    # patch bytes would still sit on disk and confuse the next person
    # to read the port" — applies identically to patch_apply.
    if shape in ("file_materialize", "patch_apply"):
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
            before_state[patch_file] = before_patch
    return EditResult(
        ok=True, intent_type="drop_patch",
        paths_changed=paths_changed,
        substrate_diff=t.diff_from_before(before_state),
    )


def _resolve_from_dupe(t, target: str) -> str | None:
    """Find the most recently modified file matching ``target``'s
    basename in either the WRKSRC (intent flow, since the genpatch
    wrapper cd's into WRKSRC and runs the script there) or the
    workspace-relative ``.genpatch-out`` (legacy / test fallback).

    The intent-flow path is the canonical one for runtime: the
    `genpatch` wrapper produces `patch-<wrksrc-rel>` files inside
    WRKSRC under that branch. The legacy fallback handles tests
    that place files at known workspace-relative locations without
    a real extract.

    Returns the file's text contents, or ``None`` if nothing matches.
    """
    from pathlib import Path  # noqa: PLC0415
    basename = Path(target).name
    candidates: list[Path] = []

    # Intent-flow location: WRKSRC, set by worker.apply_intent when
    # the cache has a hit. The genpatch wrapper writes here under
    # the cache-hit branch.
    wrksrc = getattr(t, "wrksrc", None)
    if wrksrc:
        ws = Path(wrksrc)
        if ws.is_dir():
            for cand in ws.rglob(basename):
                if cand.is_file() and cand.name.startswith("patch-"):
                    candidates.append(cand)

    # Legacy / test-fixture location: workspace-relative
    # `.genpatch-out`. Kept so tests can stage files without
    # mocking out a full WRKSRC tree.
    candidates.append(t.workspace / ".genpatch-out" / basename)
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
    overlay = t.port_path(_DOPS_FILE)
    before_overlay = overlay.read_text() if overlay.is_file() else None
    try:
        target.write_text(diff_content)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="add_patch",
            error=f"write failed for {intent.target}: {exc}",
        )
    # Now append the dops directive that installs it.
    stmt_result = _append_overlay(t, "add_patch",
                                  [f"patch apply {intent.target}"],
                                  scope=intent.scope)
    if not stmt_result.ok:
        # Roll back the file write so the half-applied state doesn't
        # confuse the next intent.
        try:
            target.unlink()
        except OSError:
            pass
        return stmt_result
    return EditResult(
        ok=True, intent_type="add_patch",
        paths_changed=[
            str(target.relative_to(t.workspace)),
            str(overlay.relative_to(t.workspace)),
        ],
        substrate_diff=t.diff_from_before({
            target: None, overlay: before_overlay,
        }),
    )


def add_file(t, intent: AddFile):
    """Render add_file in dops mode.

    For kind=resource: write content + emit
    ``file copy <dest> -> <dest>``.
    For kind=materialize: emit ``file materialize <src> -> <dst>``.
    """
    from .translator import EditResult
    # Path safety BEFORE kind-dispatch. The resource branch already
    # got escape protection via port_path() on its file write; the
    # materialize branch (which only appends a directive, no file
    # IO) skipped it and would happily write
    # ``file materialize ... -> ../../etc/foo`` into overlay.dops.
    # Hoist the check so both kinds are covered.
    try:
        t.port_path(intent.dest)
    except IntentError as exc:
        return EditResult(
            ok=False, intent_type="add_file", error=str(exc),
        )
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
        overlay = t.port_path(_DOPS_FILE)
        before_overlay = overlay.read_text() if overlay.is_file() else None
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
        stmt_result = _append_overlay(t, "add_file", [stmt], scope=intent.scope)
        if not stmt_result.ok:
            try:
                target.unlink()
            except OSError:
                pass
            return stmt_result
        return EditResult(
            ok=True, intent_type="add_file",
            paths_changed=[
                str(target.relative_to(t.workspace)),
                str(overlay.relative_to(t.workspace)),
            ],
            substrate_diff=t.diff_from_before({
                target: None, overlay: before_overlay,
            }),
        )
    if intent.kind == "materialize":
        # `file materialize <src> -> <dst>` per the dops grammar.
        stmt = f"file materialize {intent.source} -> {intent.dest}"
        return _append_overlay(t, "add_file", [stmt], scope=intent.scope)
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


def _mk_directive_matches(line: str, kind: str, key: str, value: str | None) -> bool:
    """True iff ``line`` parses to an ``mk <kind> <key> [...]`` directive
    matching the drop intent — compared on the parsed ``MkOpNode``, not
    on byte spelling.

    Defers to the engine parser as the single source of truth. The
    on-disk value form is incidental: convert emits whitespace-free
    values bare (``mk add USES alias``, migration/convert.py) while
    ``change_makefile`` quotes them (``mk add USES "alias"``); the
    parser decodes both to the same ``token`` field, so this matcher
    treats them identically — which is the whole point, since the
    engine does too. A trailing ``on-missing`` clause lands in its own
    AST field and is correctly ignored here.

    Match rules by ``kind`` mirror the renderers' semantics:
    - ``add`` / ``remove`` → ``var`` and ``token`` must both match.
    - ``set`` → ``var`` matches; the value is ignored (an agent
      shouldn't have to echo the exact on-disk value to drop a set).
    - ``unset`` → ``var`` matches (no value to compare).
    """
    from dportsv3.engine.api import parse_dsl  # noqa: PLC0415
    from dportsv3.engine.ast import MkOpNode  # noqa: PLC0415

    result = parse_dsl(line)
    if not result.ok or result.ast is None:
        return False
    stmts = result.ast.statements
    if len(stmts) != 1:
        return False
    node = stmts[0]
    if not isinstance(node, MkOpNode):
        return False
    if node.action != kind or node.var != key:
        return False
    if kind in ("add", "remove"):
        return node.token == value
    return True  # set (value ignored) / unset (no value)


def change_makefile(t, intent: ChangeMakefile):
    """``mk <action> VAR ["value"]`` per the dops grammar.

    The previous form (``mk.var.set var=K value=V``, dot-separated
    with named args) was invented — the actual engine parser at
    ``engine/parser.py:343`` expects space-separated tokens
    ``mk set|unset|add|remove VAR "value"``. The intent's ``op``
    field uses ``set`` / ``append`` / ``remove`` / ``unset``;
    ``append`` maps to dops ``add`` (the parser's name for "append
    to a list-shaped variable"). ``set`` takes a quoted STRING
    value; ``add`` / ``remove`` take a token (quoted for safety).
    ``unset`` takes NO value — it emits ``mk unset VAR`` and the
    engine deletes the variable's assignment line from the composed
    Makefile (symmetric inverse of ``mk set``, useful for dropping
    an upstream assignment that's wrong for our target).

    Each ``op`` emits a single line and never touches lines from
    prior intents. Re-emitting ``op=set FOO "x"`` twice produces
    two ``mk set FOO "x"`` lines on disk; the engine plays both in
    declaration order (last-wins) and the composed Makefile is
    correct. Cleanup of redundant lines is the agent's explicit
    responsibility via the Family A delete intents
    (intent-surface-gaps-plan.md) — no implicit prefilter does it
    here. Step 38e removed the pre-existing
    ``_strip_existing_mk_set`` prefilter for two reasons: (1) it
    was scope-blind and would have corrupted multi-target overlays
    once 38d enabled per-target emission, and (2) it baked
    cross-intent state mutation into a renderer's body, violating
    the "each intent does exactly one thing" principle.
    """
    if intent.op == "unset":
        stmt = f"mk unset {intent.key}"
    else:
        action = {"set": "set", "append": "add", "remove": "remove"}[intent.op]
        stmt = f"mk {action} {intent.key} {_quote_dops_string(intent.value)}"
    return _append_overlay(
        t, "change_makefile", [stmt], scope=intent.scope,
    )


def _resolve_drop_scope(t, scope, intent_type):
    """Resolve a delete-renderer's scope.

    Returns ``(resolved_scope, None)`` on success or
    ``(None, EditResult)`` carrying an ``ok=False`` refusal. Shared by
    the Family A delete renderers. ``@current`` resolves to
    ``t.target`` (refused when empty — a runner-side bug we surface
    rather than silently widening to ``@any``); literal scopes are
    validated against the engine grammar. Mirrors the resolution block
    in ``_append_overlay`` but returns a refusal instead of writing.
    """
    from .translator import EditResult
    if scope == "@current":
        if not t.target:
            return None, EditResult(
                ok=False, intent_type=intent_type,
                error=(
                    "intent requested scope=@current but the runner did "
                    "not populate an env target (t.target is empty). This "
                    "is a calling-context bug; retrying will not help — "
                    "escalate."
                ),
            )
        resolved = t.target
    else:
        resolved = scope
    from dportsv3.common.validation import is_scoped_target  # noqa: PLC0415
    if not is_scoped_target(resolved):
        return None, EditResult(
            ok=False, intent_type=intent_type,
            error=(
                f"invalid scope: {resolved!r} (expected @any, @main, or "
                f"@YYYYQ[1-4])"
            ),
        )
    return resolved, None


def drop_mk_directive(t, intent: DropMkDirective):
    """Remove a single ``mk set/unset/add/remove VAR`` line from
    overlay.dops (Step 39a).

    Symmetric delete for ``change_makefile``: that renderer appends an
    ``mk`` line; this one strips the matching line. Closes the
    accumulate-then-counter-op thrash where an agent emits an
    ``mk add`` (via ``change_makefile op=append``) and, realizing it
    was wrong, had no way to take it back — leaving an add+remove pair
    on disk. Now it emits ``drop_mk_directive(kind=add, ...)`` and the
    prior line is gone.

    The match is scope-filtered: only lines under the resolved
    ``scope`` section are considered (``@current`` resolves to
    ``t.target``). Line-shape matching by ``kind``:

    - ``unset`` → exact ``mk unset KEY`` (``value`` ignored)
    - ``set``   → ``mk set KEY`` with any value (``value`` ignored)
    - ``add`` / ``remove`` → exact ``mk <kind> KEY "<value>"`` (the
      ``value`` token must match, quoted the same way
      ``change_makefile`` emits it)

    Refusals (``ok=False``): overlay missing, ``scope=@current`` with
    no env target, invalid scope, zero matches (the line not existing
    signals the agent's model is wrong — don't silently no-op), or
    multiple matches at the same scope (ambiguous; disambiguate via
    scope or hand-edit). No implicit invariant repair — this renderer
    only removes lines, never reorders sections.
    """
    from .translator import EditResult
    overlay = t.port_path(_DOPS_FILE)
    if not overlay.is_file():
        return EditResult(
            ok=False, intent_type="drop_mk_directive",
            error=f"{_DOPS_FILE} does not exist; nothing to remove from",
        )
    before_overlay = overlay.read_text()

    resolved_scope, scope_err = _resolve_drop_scope(
        t, intent.scope, "drop_mk_directive",
    )
    if scope_err is not None:
        return scope_err

    if intent.kind == "unset":
        shape_desc = f"mk unset {intent.key}"
    elif intent.kind == "set":
        shape_desc = f"mk set {intent.key} ..."
    else:  # add / remove
        shape_desc = f"mk {intent.kind} {intent.key} {intent.value}"

    def _matches(s: str) -> bool:
        return _mk_directive_matches(
            s, intent.kind, intent.key, intent.value,
        )

    new, count = _strip_scoped_line(
        before_overlay, resolved_scope, _matches,
    )
    if count == 0:
        return EditResult(
            ok=False, intent_type="drop_mk_directive",
            error=(
                f"no `{shape_desc}` line found in {_DOPS_FILE} under "
                f"scope {resolved_scope}"
            ),
        )
    if count > 1:
        return EditResult(
            ok=False, intent_type="drop_mk_directive",
            error=(
                f"{count} `{shape_desc}` lines match under scope "
                f"{resolved_scope}; ambiguous — refusing. Disambiguate "
                f"via scope or edit {_DOPS_FILE} manually."
            ),
        )
    try:
        overlay.write_text(new)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="drop_mk_directive",
            error=f"write failed for {_DOPS_FILE}: {exc}",
        )
    return EditResult(
        ok=True, intent_type="drop_mk_directive",
        paths_changed=[str(overlay.relative_to(t.workspace))],
        substrate_diff=t.diff_from_before({overlay: before_overlay}),
    )


def drop_file(t, intent: DropFile):
    """Remove a ``file copy`` / ``file materialize`` install directive
    for ``intent.target`` from overlay.dops (Step 39b).

    Symmetric delete for ``add_file``. Matches either install shape
    the grammar emits, scope-filtered:

    - ``file copy <src> -> <target>`` (add_file kind=resource)
    - ``file materialize <src> -> <target>`` (add_file kind=materialize)

    Non-overlapping with ``drop_patch``: patch-shaped destinations
    (``dragonfly/patch-*``) are refused here and routed to
    ``drop_patch`` (which already matches ``patch apply`` and
    ``file materialize ... -> dragonfly/patch-*``). Keeping the two
    intents path-partitioned gives the agent a clean rule: patch →
    drop_patch, anything else → drop_file.

    On a unique match the directive line is stripped AND the on-disk
    file at ``ports/<origin>/<target>`` is deleted — mirroring
    ``drop_patch``. Dropping only the directive would orphan bytes
    that block a later ``add_file`` with "already exists." A delete
    failure rolls the overlay edit back so no half-applied state
    survives.

    Refusals (``ok=False``): overlay missing, ``dragonfly/patch-*``
    target (use drop_patch), ``scope=@current`` with no env target,
    invalid scope, zero matches, or multiple matches at the same
    scope (ambiguous).
    """
    from .translator import EditResult
    if intent.target.startswith("dragonfly/patch-"):
        return EditResult(
            ok=False, intent_type="drop_file",
            error=(
                f"drop_file refuses target={intent.target!r}: patch-shaped "
                f"destinations (dragonfly/patch-*) are owned by drop_patch. "
                f"Use drop_patch to remove a patch install."
            ),
        )
    overlay = t.port_path(_DOPS_FILE)
    if not overlay.is_file():
        return EditResult(
            ok=False, intent_type="drop_file",
            error=f"{_DOPS_FILE} does not exist; nothing to remove from",
        )
    before_overlay = overlay.read_text()

    resolved_scope, scope_err = _resolve_drop_scope(
        t, intent.scope, "drop_file",
    )
    if scope_err is not None:
        return scope_err

    def _matches(s: str) -> bool:
        for verb in ("file copy ", "file materialize "):
            if s.startswith(verb):
                _, _, rest = s.partition(verb)
                if "->" in rest:
                    _src, _, dest = rest.partition("->")
                    if dest.strip() == intent.target:
                        return True
        return False

    new, count = _strip_scoped_line(before_overlay, resolved_scope, _matches)
    if count == 0:
        return EditResult(
            ok=False, intent_type="drop_file",
            error=(
                f"no `file copy ... -> {intent.target}` or "
                f"`file materialize ... -> {intent.target}` line found in "
                f"{_DOPS_FILE} under scope {resolved_scope}"
            ),
        )
    if count > 1:
        return EditResult(
            ok=False, intent_type="drop_file",
            error=(
                f"{count} install directives for {intent.target!r} match "
                f"under scope {resolved_scope}; ambiguous — refusing. "
                f"Disambiguate via scope or edit {_DOPS_FILE} manually."
            ),
        )
    resource = t.port_path(intent.target)
    before_resource = resource.read_text() if resource.is_file() else None
    try:
        overlay.write_text(new)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="drop_file",
            error=f"write failed for {_DOPS_FILE}: {exc}",
        )
    paths_changed = [str(overlay.relative_to(t.workspace))]
    before_state: dict[Path, str | None] = {overlay: before_overlay}
    if before_resource is not None:
        try:
            resource.unlink()
        except OSError as exc:
            overlay.write_text(before_overlay)  # roll back the line strip
            return EditResult(
                ok=False, intent_type="drop_file",
                error=(
                    f"could not delete resource file {intent.target}: {exc}"
                ),
            )
        paths_changed.append(str(resource.relative_to(t.workspace)))
        before_state[resource] = before_resource
    return EditResult(
        ok=True, intent_type="drop_file",
        paths_changed=paths_changed,
        substrate_diff=t.diff_from_before(before_state),
    )


def drop_target_block(t, intent: DropTargetBlock):
    """Remove an entire ``mk target set|append <name> <<TAG ... TAG``
    heredoc block from overlay.dops (Step 39c).

    Block-level delete. Where ``drop_mk_directive`` strips a single
    ``mk`` line and ``replace_in_dops_block`` edits *inside* a body,
    this removes the whole block — opening line, body, and closing
    tag. Closes the gap where convert produces a structurally valid
    but semantically wrong target recipe that should be removed
    wholesale rather than patched line-by-line.

    Scope-filtered: the engine accepts same-name blocks under
    different ``target`` sections, so the locator considers only
    blocks whose enclosing scope equals the resolved ``scope``
    (``@current`` resolves to ``t.target``). Adjacent blank lines are
    left untouched (no implicit cleanup — cosmetic blanks don't affect
    compose).

    Refusals (``ok=False``): overlay missing, ``scope=@current`` with
    no env target, invalid scope, corrupt overlay (a matching block
    opens but never closes), zero matches (the block not existing
    signals the agent's model is wrong — don't silently no-op), or
    multiple matches at the same scope (ambiguous; disambiguate via
    scope or hand-edit).
    """
    from .translator import EditResult
    overlay = t.port_path(_DOPS_FILE)
    if not overlay.is_file():
        return EditResult(
            ok=False, intent_type="drop_target_block",
            error=f"{_DOPS_FILE} does not exist; nothing to remove from",
        )
    before_overlay = overlay.read_text()

    resolved_scope, scope_err = _resolve_drop_scope(
        t, intent.scope, "drop_target_block",
    )
    if scope_err is not None:
        return scope_err

    extents, err = _find_mk_target_blocks(
        before_overlay, intent.block_name, resolved_scope,
    )
    if err:
        return EditResult(
            ok=False, intent_type="drop_target_block", error=err,
        )
    if len(extents) == 0:
        return EditResult(
            ok=False, intent_type="drop_target_block",
            error=(
                f"no `mk target set/append {intent.block_name} <<...` block "
                f"found in {_DOPS_FILE} under scope {resolved_scope}"
            ),
        )
    if len(extents) > 1:
        return EditResult(
            ok=False, intent_type="drop_target_block",
            error=(
                f"{len(extents)} `mk target ... {intent.block_name}` blocks "
                f"match under scope {resolved_scope}; ambiguous — refusing. "
                f"Disambiguate via scope or edit {_DOPS_FILE} manually."
            ),
        )
    open_idx, close_idx = extents[0]
    lines = before_overlay.splitlines()
    kept = lines[:open_idx] + lines[close_idx + 1:]
    suffix = "\n" if before_overlay.endswith("\n") else ""
    new = "\n".join(kept) + suffix
    try:
        overlay.write_text(new)
    except OSError as exc:
        return EditResult(
            ok=False, intent_type="drop_target_block",
            error=f"write failed for {_DOPS_FILE}: {exc}",
        )
    return EditResult(
        ok=True, intent_type="drop_target_block",
        paths_changed=[str(overlay.relative_to(t.workspace))],
        substrate_diff=t.diff_from_before({overlay: before_overlay}),
    )


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
    # Refuse no-op replacements (find == replace). Without this
    # guard the call returns ok=True with an empty substrate_diff
    # and the agent reads that as progress — but nothing changed.
    # Observed on archivers/liblz4 2026-05-26 where the agent
    # degraded its find/replace pair across attempts until both
    # were identical, then kept logging "ok" intents while the
    # build was already green from an earlier (real) intent.
    if intent.find == intent.replace:
        return EditResult(
            ok=False, intent_type="replace_in_dops_block",
            error=(
                "no-op: find and replace are identical. If you "
                "meant to confirm a prior intent already landed, "
                "check the substrate; don't re-emit."
            ),
        )
    overlay = t.port_path(_DOPS_FILE)
    if not overlay.is_file():
        return EditResult(
            ok=False, intent_type="replace_in_dops_block",
            error=f"{_DOPS_FILE} does not exist; nothing to edit",
        )
    text = overlay.read_text()
    before_overlay = text
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
        substrate_diff=t.diff_from_before({overlay: before_overlay}),
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


def _find_mk_target_blocks(
    text: str, block_name: str, scope: str,
) -> tuple[list[tuple[int, int]], str]:
    """Locate every ``mk target set|append <block_name> <<TAG ... TAG``
    block within the ``scope`` section.

    Returns ``(extents, error)``. ``extents`` is a list of
    ``(open_idx, close_idx)`` inclusive line-index pairs into
    ``text.splitlines()`` — one per matching block, in document order.
    ``error`` is a non-empty string only when the overlay is
    structurally corrupt (a heredoc opens but never closes); on a clean
    parse it is ``""`` regardless of match count, leaving the
    zero/one/many decision to the caller.

    Scope-aware by design: the engine accepts same-name blocks under
    different ``target`` sections (no duplicate-name check in
    ``semantic.py``), so only blocks whose enclosing section scope
    equals ``scope`` are returned. Section/heredoc tracking mirrors
    ``_strip_scoped_line``; the open-line parse and tag extraction
    mirror ``_replace_in_mk_target_block``. ``rename`` is skipped (it
    has no body).
    """
    lines = text.splitlines()
    n = len(lines)
    extents: list[tuple[int, int]] = []
    current_scope = "@any"
    i = 0
    while i < n:
        stripped = lines[i].strip()
        if stripped.startswith("target "):
            tok = stripped[len("target "):].strip()
            if tok:
                current_scope = tok
            i += 1
            continue
        if stripped.startswith("mk target ") and "<<" in stripped:
            rest = stripped[len("mk target "):]
            parts = rest.split(None, 2)
            tag = stripped.split("<<", 1)[1].strip().strip("'\"")
            close_idx: int | None = None
            if tag:
                for j in range(i + 1, n):
                    if lines[j].strip() == tag:
                        close_idx = j
                        break
            if close_idx is None:
                return [], (
                    f"heredoc block opens at line {i + 1} (<<{tag}) but has "
                    f"no closing line in {_DOPS_FILE} — overlay is corrupt"
                )
            if (
                len(parts) >= 2
                and parts[0] != "rename"
                and parts[1] == block_name
                and current_scope == scope
            ):
                extents.append((i, close_idx))
            i = close_idx + 1
            continue
        i += 1
    return extents, ""


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
    return _append_overlay(
        t, "bump_portrevision", [stmt], scope=intent.scope,
    )


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def _append_overlay(t, intent_type: str, statements: Iterable[str],
                    scope: str | None = None):
    """Append one or more dops statements to ports/<origin>/overlay.dops.

    Creates the file with a minimal header if it didn't already
    exist. Returns an EditResult with the diff scoped to overlay.dops.

    ``scope`` is Step 38d-3's hook for target-scoped emission:

    - ``None`` (default): dumb-append at EOF under whatever the
      file's most recent ``target`` directive was. Post-38d-6 every
      renderer passes an explicit scope, so this path is only
      exercised by ad-hoc test invocations; kept for that reason.
    - ``"@current"``: resolves to ``t.target`` at apply time. If
      ``t.target`` is None or empty, the call is refused with an
      actionable error pointing at the runner's responsibility to
      populate the env-target cache (Step 38a).
    - ``"@any"`` / ``"@main"`` / ``"@YYYYQ[1-4]"``: literal scope
      passed through to ``_ensure_target_scope`` for placement.

    When scope is provided (post-resolution), the function dispatches
    through ``_ensure_target_scope`` instead of the dumb-append loop.
    Invariant checking (Step 38c) runs first regardless.
    """
    from .translator import EditResult
    overlay = t.port_path(_DOPS_FILE)
    existed = overlay.is_file()
    before_overlay = overlay.read_text() if existed else None
    original = before_overlay if existed else _initial_overlay_header(t)
    # Step 38c: refuse writes when the existing overlay already
    # violates the @any-first invariant. No auto-repair — the
    # operator must resolve the malformed layout deliberately. Clean
    # overlays (which includes every convert output and every
    # `_initial_overlay_header`-seeded file) pass the check trivially.
    invariant_err = _check_target_scope_order(original)
    if invariant_err is not None:
        return EditResult(
            ok=False, intent_type=intent_type, error=invariant_err,
        )
    # Step 38d-3: scope resolution. Translate the agent-facing
    # ``@current`` alias to the concrete ``@YYYYQX`` from
    # ``t.target`` (populated by the runner via
    # ``worker.set_env_target``). Refuse on inconsistencies rather
    # than silently falling back to ``@any`` — a missing cache
    # entry is a runner-side bug we want to surface, not paper over.
    resolved_scope: str | None = None
    if scope is not None:
        if scope == "@current":
            if not t.target:
                return EditResult(
                    ok=False, intent_type=intent_type,
                    error=(
                        f"intent requested scope=@current but the "
                        f"runner did not populate an env target "
                        f"(t.target is empty). This indicates a "
                        f"calling-context bug: the cache should be "
                        f"set at job start by "
                        f"worker.set_env_target. Retrying will not "
                        f"help — escalate."
                    ),
                )
            resolved_scope = t.target
        else:
            resolved_scope = scope
        # Validate against the engine's grammar so a malformed scope
        # never reaches the substrate (catches typos in schema enums
        # and ad-hoc callers that hand-construct scope strings).
        from dportsv3.common.validation import is_scoped_target  # noqa: PLC0415
        if not is_scoped_target(resolved_scope):
            return EditResult(
                ok=False, intent_type=intent_type,
                error=(
                    f"invalid scope: {resolved_scope!r} (expected "
                    f"@any, @main, or @YYYYQ[1-4])"
                ),
            )
    new = original
    if resolved_scope is None:
        # Backward-compatible dumb-append path. Pre-38d-6 renderers
        # land here; their statements end up under whatever the
        # file's last `target` directive was — in practice always
        # `target @any` from `_initial_overlay_header`.
        if not new.endswith("\n"):
            new += "\n"
        for stmt in statements:
            new += stmt.rstrip() + "\n"
    else:
        # Scope-aware placement via 38b's helper. Handles section
        # location, blank-line preservation, and the legacy
        # @any-no-match case (38d-2).
        new = _ensure_target_scope(new, resolved_scope, list(statements))
    try:
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_text(new)
    except OSError as exc:
        if existed and before_overlay is not None:
            try:
                overlay.write_text(before_overlay)
            except OSError:
                pass
        return EditResult(
            ok=False, intent_type=intent_type,
            error=f"write failed for {_DOPS_FILE}: {exc}",
        )
    return EditResult(
        ok=True, intent_type=intent_type,
        paths_changed=[str(overlay.relative_to(t.workspace))],
        substrate_diff=t.diff_from_before({overlay: before_overlay}),
    )


def _initial_overlay_header(t) -> str:
    """Minimal header for a freshly-created overlay.dops.

    ``target @any`` matches every env's compose target. The prior
    default `@main` was inherited from an early convert convention
    and made the overlay silently dead on every env whose target
    != @main: compose's apply.py:296 filters ops whose scope isn't
    in ``{"@any", target}`` and marks them ``status="skipped"`` with
    ``I_APPLY_TARGET_MISMATCH``. The summary line then reads
    ``applied=0``, and the agent (correctly observing nothing
    changed) chases a non-existent compose bug instead of the real
    failure. Convert hit this on archivers/liblz4 2026-05-26 and
    flipped to @any (commits d71f605c206 + 47846e7a392); the
    edit-intent translator's twin default was missed at the time.
    """
    return (
        f"target @any\n"
        f"port {t.origin}\n"
        f"type port\n"
        f"reason \"agent edits via edit-intent DSL\"\n"
        f"\n"
    )


def _check_target_scope_order(overlay_text: str) -> str | None:
    """Walk `target` directives top-to-bottom and verify the
    `@any`-first structural invariant.

    Returns ``None`` if the invariant holds (the common case). Returns
    an actionable error message string if a ``target @any`` directive
    appears after a non-``@any`` directive — that ordering inverts
    "specific overrides general" semantics because the engine applies
    ops in declaration order, and the late ``@any`` op runs *after*
    matching ``@Q`` ops on a ``@Q`` build.

    The checker is intentionally narrow:

    - Multiple consecutive ``target @any`` directives are redundant
      but not a violation (the engine treats them as a no-op
      re-bind).
    - Ordering between ``@Q`` directives doesn't matter (no conflict
      — each ``@Q`` filters to a different build), so no constraint.
    - Comma-separated multi-target directives are treated as
      ``@Q``-equivalent if they don't contain ``@any``; the engine
      rejects mixing ``@any`` with explicit selectors at semantic
      time, so we don't have to handle that case here.
    - Overlays with no ``target`` directive at all default to
      ``@any`` per ``semantic.py:358``; no violation possible.

    Step 38c's design choice: never auto-repair. If an overlay
    already violates the invariant (operator hand-edit, legacy
    convert output, etc.), the renderer refuses with this error so
    the operator can fix it deliberately rather than have the
    engine silently propagate a corrupted layout.
    """
    seen_non_any: tuple[int, str] | None = None
    for i, line in enumerate(overlay_text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("target "):
            continue
        scope_str = stripped[len("target "):].strip()
        if not scope_str:
            continue
        # Comma-separated multi-target: split and inspect each token.
        # If `@any` appears, the engine itself rejects mixing — we
        # match that classification here by treating it as @any.
        scopes = [s.strip() for s in scope_str.split(",")]
        is_any_only = scopes == ["@any"]
        if is_any_only:
            if seen_non_any is not None:
                prev_line, prev_scope = seen_non_any
                return (
                    f"overlay.dops violates the @any-first invariant: "
                    f"`target @any` at line {i} appears after "
                    f"`target {prev_scope}` at line {prev_line}. The "
                    f"engine applies ops in declaration order; an "
                    f"@any op placed after a @Q section silently "
                    f"overrides @Q on that build. Resolve by editing "
                    f"overlay.dops manually (move @any directives + "
                    f"their ops to precede any non-@any sections), or "
                    f"re-run `dportsv3 dev-env reset-port` to start "
                    f"fresh."
                )
        else:
            if seen_non_any is None:
                seen_non_any = (i, scope_str)
    return None


def _ensure_target_scope(
    overlay_text: str, scope: str, statements: list[str],
) -> str:
    """Append statements under the ``target <scope>`` section.

    ``scope`` must be a resolved engine-valid scope string (e.g.
    ``"@any"``, ``"@main"``, ``"@2026Q2"``). The caller — typically an
    intent renderer in Step 38d — resolves the agent-facing
    ``"@current"`` alias to ``translator.target`` before invocation;
    this helper does not know about the alias.

    Placement rules:

    - If a ``target <scope>`` directive already exists in the overlay,
      ``statements`` are appended at the tail of its section (right
      before the next ``target`` directive, or EOF). Trailing blank
      lines inside the section are skipped so the blank-line
      separator stays attached to the next ``target`` block.
    - If no matching directive exists, a fresh
      ``target <scope>\\n<statements>`` block is appended at EOF,
      preceded by a single blank line for visual separation.

    Step 38c will add a renderer-side guard that refuses writes which
    would violate the ``@any-first`` structural invariant. This helper
    assumes a well-formed input and places statements without
    rearranging existing sections.

    Known limitations (deferred to 38c if they surface):

    - Exact-string match on the target directive's scope token. A
      comma-separated multi-target directive (``target @2026Q4,@2026Q1``)
      is not matched even if ``scope`` is one of the targets.
      Convert-produced overlays use single scopes; the intent flow
      never emits multi-target directives.
    - Line-based scan, not AST. A ``target ...`` substring that
      appears inside a ``mk target set NAME <<TAG ... TAG`` heredoc
      body would currently false-match. The renderers don't emit
      ``target`` directives inside heredoc bodies; this isn't
      reachable in practice with today's intent surface.
    """
    has_trailing_nl = overlay_text.endswith("\n")
    lines = overlay_text.split("\n")
    if has_trailing_nl and lines and lines[-1] == "":
        lines.pop()

    # Locate `target <scope>` directives via a simple line-based scan.
    # The dops grammar puts ``target`` at column 0 (no leading
    # whitespace), but we tolerate ``\s+target`` for robustness against
    # operator-edited files.
    target_positions: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("target "):
            continue
        scope_str = stripped[len("target "):].strip()
        if scope_str:
            target_positions.append((i, scope_str))

    # Locate the section matching `scope` (exact-string match).
    matching_section_idx: int | None = None
    for idx, (_line_no, t) in enumerate(target_positions):
        if t == scope:
            matching_section_idx = idx
            break

    statement_lines = [s.rstrip() for s in statements]

    if matching_section_idx is not None:
        # Section exists.
        if matching_section_idx + 1 < len(target_positions):
            # Next-section case: insert before the following `target`
            # directive, walking back past blank lines so the
            # separator stays attached to the next block.
            insert_pos = target_positions[matching_section_idx + 1][0]
            while insert_pos > 0 and lines[insert_pos - 1].strip() == "":
                insert_pos -= 1
        else:
            # EOF case: append at end. Do NOT walk back blanks — the
            # trailing blank line from `_initial_overlay_header`
            # (between port/type/reason metadata and the first
            # operation) must be preserved. Walking back would eat
            # that separator on the first statement added to a
            # fresh overlay (38d-1 fix).
            insert_pos = len(lines)
        new_lines = lines[:insert_pos] + statement_lines + lines[insert_pos:]
    elif not statement_lines:
        # No matching section AND nothing to insert — emit no directive.
        # Prevents a buggy renderer with edge-case logic from silently
        # appending bare ``target @X`` lines (valid grammar, but
        # operationally useless and visual noise in the overlay).
        new_lines = list(lines)
    elif scope == "@any" and target_positions:
        # @any-no-match with existing non-@any sections: a legacy or
        # operator-hand-edited overlay has e.g. `target @main` as its
        # first directive but no `target @any`. Appending @any at EOF
        # would land it after the @Q sections, violating the
        # @any-first invariant and silently overriding @Q on @Q
        # builds. Instead insert a fresh @any block at the top of the
        # operations, just before the first existing `target`
        # directive (38d-2 fix).
        insert_pos = target_positions[0][0]
        block = [f"target {scope}"] + statement_lines + [""]
        new_lines = lines[:insert_pos] + block + lines[insert_pos:]
    else:
        # No matching section. Append a fresh block at EOF, preceded
        # by a blank-line separator.
        new_lines = list(lines)
        if new_lines and new_lines[-1].strip() != "":
            new_lines.append("")
        new_lines.append(f"target {scope}")
        new_lines.extend(statement_lines)

    result = "\n".join(new_lines)
    if has_trailing_nl or new_lines:
        result += "\n"
    return result


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


def _strip_scoped_line(text, scope, predicate):
    """Remove top-level lines satisfying ``predicate`` within the
    ``scope`` section.

    Shared by the Family A delete renderers (``drop_mk_directive``,
    ``drop_file``). Walks ``text`` tracking the current ``target``
    section (default ``@any`` per ``semantic.py:358`` for the prologue
    before any directive) and heredoc state. A line is removed only
    when all hold:

    - the current section's scope token exactly equals ``scope``
      (exact-string match, same limitation as ``_ensure_target_scope``:
      comma-separated multi-target directives don't match a single
      scope), AND
    - the line is NOT inside an ``mk target set/append NAME <<TAG``
      heredoc body (recipe text must never be matched as a top-level
      directive), AND
    - ``predicate(stripped_line)`` is True.

    Returns ``(new_text, removed_count)``. ``removed_count`` lets the
    caller distinguish zero-match (refuse), unique-match (apply), and
    ambiguous multi-match (refuse) without re-scanning.
    """
    out: list[str] = []
    removed = 0
    current_scope = "@any"
    heredoc_tag: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if heredoc_tag is not None:
            if stripped == heredoc_tag:
                heredoc_tag = None
            out.append(line)
            continue
        if stripped.startswith("target "):
            tok = stripped[len("target "):].strip()
            if tok:
                current_scope = tok
            out.append(line)
            continue
        if stripped.startswith("mk target ") and "<<" in stripped:
            tag = stripped.split("<<", 1)[1].strip().strip("'\"")
            heredoc_tag = tag or None
            out.append(line)
            continue
        if current_scope == scope and predicate(stripped):
            removed += 1
            continue
        out.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return ("\n".join(out) + suffix, removed)
