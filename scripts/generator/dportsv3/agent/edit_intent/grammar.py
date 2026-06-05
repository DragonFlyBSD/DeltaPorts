"""Intent grammar — dataclass per intent type (Step 25b).

Wire-format is JSON: each intent is a dict with a ``type`` field
discriminating the variant, plus type-specific fields. These
dataclasses are the parsed in-memory representation.

See ``docs/edit-intent-design.md`` §3 for the full grammar spec.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union


INTENT_TYPES: tuple[str, ...] = (
    "replace_in_patch",
    "drop_patch",
    "add_patch",
    "add_file",
    "change_makefile",
    "bump_portrevision",
    "replace_in_dops_block",
    "drop_mk_directive",
    "drop_file",
    "drop_target_block",
)


@dataclass(frozen=True)
class ReplaceInPatch:
    """Edit a single hunk inside an existing patch file (§3.2.1)."""
    type: Literal["replace_in_patch"]
    target: str           # relpath under ports/<origin>/
    find: str
    replace: str
    occurrence: int = 1   # 1-based; which match to replace if find is non-unique
    scope: Literal["@any", "@current"] = "@any"  # Step 38d-4


@dataclass(frozen=True)
class DropPatch:
    """Declare an existing patch obsolete and remove it (§3.2.2)."""
    type: Literal["drop_patch"]
    target: str
    reason: str


@dataclass(frozen=True)
class AddPatch:
    """Introduce a new patch for an upstream file (§3.2.3).

    Either ``diff`` is supplied directly, or ``from_dupe=True`` and
    the translator picks up the most recent matching file from
    ``<env>/writable/work/<wrksrc>/.genpatch-out/``.
    """
    type: Literal["add_patch"]
    target: str
    diff: str | None = None
    from_dupe: bool = False
    scope: Literal["@any", "@current"] = "@any"  # Step 38d-4


@dataclass(frozen=True)
class AddFile:
    """Add a port-local file or stage from upstream tree (§3.2.4).

    kind="resource" requires ``content``; kind="materialize" requires
    ``source`` (a path in the upstream/dragonfly source tree).
    """
    type: Literal["add_file"]
    dest: str
    kind: Literal["resource", "materialize"]
    content: str | None = None
    source: str | None = None
    scope: Literal["@any", "@current"] = "@any"  # Step 38d-4


@dataclass(frozen=True)
class ChangeMakefile:
    """Edit a Makefile variable (§3.2.5).

    ``op=unset`` deletes the variable's assignment from the
    composed Makefile (symmetric inverse of ``mk set``). The
    ``value`` field is ignored on unset; defaulting it to the
    empty string lets parse_intent accept payloads that omit
    ``value`` for unset (JSON-schema enforces presence for the
    other ops). Field order keeps ``op`` last so the defaulted
    ``value`` is still keyword-compatible with the wire format.
    """
    type: Literal["change_makefile"]
    path: str             # relpath, e.g. "Makefile.DragonFly" or "Makefile"
    key: str              # var name
    op: Literal["set", "append", "remove", "unset"]
    value: str = ""       # ignored when op="unset"
    scope: Literal["@any", "@current"] = "@any"  # Step 38d-4


@dataclass(frozen=True)
class BumpPortrevision:
    """Increment PORTREVISION (§3.2.6)."""
    type: Literal["bump_portrevision"]
    scope: Literal["@any", "@current"] = "@any"  # Step 38d-4


@dataclass(frozen=True)
class ReplaceInDopsBlock:
    """Edit text inside an ``mk target set <name> <<MK ... MK``
    heredoc body in overlay.dops (Step C-4).

    Closes the gap surfaced by archivers/liblz4 2026-05-26 where
    convert produced a structurally valid overlay containing a
    ``mk target set dfly-patch`` block with internally broken
    sed-target paths. No other intent reaches heredoc bodies:
    drop_patch is line-level on top-level statements;
    replace_in_patch operates on patch files; change_makefile
    edits variable assignments. This intent is the surgical
    text-replace inside one named target block.
    """
    type: Literal["replace_in_dops_block"]
    block_name: str
    find: str
    replace: str
    occurrence: int = 1


@dataclass(frozen=True)
class DropMkDirective:
    """Remove a single ``mk set/unset/add/remove VAR`` line from
    overlay.dops (Step 39a).

    Symmetric delete for ``change_makefile``: where that intent
    *emits* an ``mk`` line, this one *removes* the matching line.
    ``kind`` selects the dops line shape; ``value`` is required for
    ``add`` / ``remove`` (must match the line's token) and ignored
    for ``set`` / ``unset`` (which match by ``key`` alone). The
    scope filter is applied before the match so the agent can
    target a specific build line's section.
    """
    type: Literal["drop_mk_directive"]
    kind: Literal["set", "unset", "add", "remove"]
    key: str
    value: str = ""   # required for add/remove; ignored for set/unset
    scope: Literal["@any", "@current"] = "@any"  # Step 38d-4


@dataclass(frozen=True)
class DropFile:
    """Remove a ``file copy`` / ``file materialize`` install directive
    from overlay.dops (Step 39b).

    Symmetric delete for ``add_file``. Distinct from ``drop_patch``,
    which owns patch-shaped destinations (``dragonfly/patch-*``):
    ``drop_file`` handles everything else (port-local resources,
    generated files) and refuses ``dragonfly/patch-*`` targets so the
    two intents never overlap. Deletes the on-disk resource file too,
    mirroring ``drop_patch`` — dropping only the directive would
    orphan bytes that block a later ``add_file``.
    """
    type: Literal["drop_file"]
    target: str           # the `-> <target>` destination relpath
    reason: str
    scope: Literal["@any", "@current"] = "@any"  # Step 38d-4


@dataclass(frozen=True)
class DropTargetBlock:
    """Remove an entire ``mk target set|append <name> <<TAG ... TAG``
    heredoc block from overlay.dops (Step 39c).

    Block-level delete. Distinct from its neighbours:
    ``drop_mk_directive`` removes a single ``mk`` line;
    ``replace_in_dops_block`` edits *inside* a block body;
    ``drop_target_block`` removes the whole block (open line, body,
    close tag). Closes the gap where convert produced a structurally
    valid but semantically wrong target recipe that should be removed
    rather than patched line-by-line. Scope-aware: the engine accepts
    same-name blocks across different ``target`` sections, so the
    match is filtered by ``scope`` before removal.
    """
    type: Literal["drop_target_block"]
    block_name: str
    reason: str
    scope: Literal["@any", "@current"] = "@any"  # Step 38d-4


Intent = Union[
    ReplaceInPatch,
    DropPatch,
    AddPatch,
    AddFile,
    ChangeMakefile,
    BumpPortrevision,
    ReplaceInDopsBlock,
    DropMkDirective,
    DropFile,
    DropTargetBlock,
]


# Type → dataclass map. Used by parse_intent in validator.py to
# dispatch from the wire-format ``type`` field.
INTENT_DATACLASSES: dict[str, type] = {
    "replace_in_patch":      ReplaceInPatch,
    "drop_patch":            DropPatch,
    "add_patch":             AddPatch,
    "add_file":              AddFile,
    "change_makefile":       ChangeMakefile,
    "bump_portrevision":     BumpPortrevision,
    "replace_in_dops_block": ReplaceInDopsBlock,
    "drop_mk_directive":     DropMkDirective,
    "drop_file":             DropFile,
    "drop_target_block":     DropTargetBlock,
}
