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
    "convert_to_dops",
)


@dataclass(frozen=True)
class ReplaceInPatch:
    """Edit a single hunk inside an existing patch file (§3.2.1)."""
    type: Literal["replace_in_patch"]
    target: str           # relpath under ports/<origin>/
    find: str
    replace: str
    occurrence: int = 1   # 1-based; which match to replace if find is non-unique


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


@dataclass(frozen=True)
class ChangeMakefile:
    """Edit a Makefile variable (§3.2.5)."""
    type: Literal["change_makefile"]
    path: str             # relpath, e.g. "Makefile.DragonFly" or "Makefile"
    key: str              # var name
    value: str
    op: Literal["set", "append", "remove"]


@dataclass(frozen=True)
class BumpPortrevision:
    """Increment PORTREVISION (§3.2.6)."""
    type: Literal["bump_portrevision"]


@dataclass(frozen=True)
class ConvertToDops:
    """Convert-agent-only: lift compat → dops atomically (§3.2.7).

    The patch agent cannot emit this intent; the validator rejects
    it in any non-convert transaction.
    """
    type: Literal["convert_to_dops"]


Intent = Union[
    ReplaceInPatch,
    DropPatch,
    AddPatch,
    AddFile,
    ChangeMakefile,
    BumpPortrevision,
    ConvertToDops,
]


# Type → dataclass map. Used by parse_intent in validator.py to
# dispatch from the wire-format ``type`` field.
INTENT_DATACLASSES: dict[str, type] = {
    "replace_in_patch":  ReplaceInPatch,
    "drop_patch":        DropPatch,
    "add_patch":         AddPatch,
    "add_file":          AddFile,
    "change_makefile":   ChangeMakefile,
    "bump_portrevision": BumpPortrevision,
    "convert_to_dops":   ConvertToDops,
}
