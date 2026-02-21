"""Origin selection helpers for compose/check/merge workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dports.config import Config
from dports.models import PortOrigin, SelectionMode
from dports.utils import list_delta_ports, list_overlay_dirs, list_ports


@dataclass
class OriginSelection:
    """Resolved origin selection and candidate index."""

    selected: list[PortOrigin]
    candidates: set[str]


def overlay_candidates(config: Config) -> list[PortOrigin]:
    """List overlay candidate origins."""
    ports_base = config.paths.delta / "ports"
    return overlay_candidates_from_base(ports_base)


def overlay_candidates_from_base(ports_base: Path) -> list[PortOrigin]:
    """List overlay candidate origins from an explicit ports/ base path."""
    return [PortOrigin.parse(origin) for origin in list_delta_ports(ports_base)]


def overlay_dirs(config: Config) -> list[PortOrigin]:
    """List all category/port dirs present under DeltaPorts ports/."""
    ports_base = config.paths.delta / "ports"
    return [PortOrigin.parse(origin) for origin in list_overlay_dirs(ports_base)]


def freebsd_ports(config: Config) -> list[PortOrigin]:
    """List all FreeBSD port origins from configured source tree."""
    return [
        PortOrigin.parse(origin) for origin in list_ports(config.paths.freebsd_ports)
    ]


def resolve_selection(
    config: Config,
    mode: SelectionMode,
    origin: PortOrigin | None = None,
) -> OriginSelection:
    """Resolve selected origins and candidate index for a selection mode."""
    candidates = {str(o) for o in overlay_candidates(config)}

    if mode == SelectionMode.SINGLE:
        if origin is None:
            raise ValueError("SelectionMode.SINGLE requires origin")
        return OriginSelection(selected=[origin], candidates=candidates)

    if mode == SelectionMode.OVERLAY_CANDIDATES:
        return OriginSelection(
            selected=[PortOrigin.parse(o) for o in sorted(candidates)],
            candidates=candidates,
        )

    if mode == SelectionMode.FULL_TREE:
        return OriginSelection(selected=freebsd_ports(config), candidates=candidates)

    raise ValueError(f"Unknown selection mode: {mode}")
