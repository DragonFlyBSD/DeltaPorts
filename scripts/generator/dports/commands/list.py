"""List command - list ports with various filters."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.overlay import Overlay
from dports.quarterly import validate_target
from dports.utils import get_logger, list_ports, list_delta_ports


def cmd_list(config: Config, args: Namespace) -> int:
    """Execute the list command."""
    log = get_logger(__name__)

    customized_only = getattr(args, "customized", False)
    target = getattr(args, "target", None)
    output_format = getattr(args, "format", "simple")

    normalized_target = validate_target(target) if target else None

    ports_base = config.paths.delta / "ports"

    if customized_only:
        # List only ports with customizations
        ports = []
        for origin_str in list_delta_ports(ports_base):
            origin = PortOrigin.parse(origin_str)
            overlay = Overlay(config.get_overlay_port_path(origin_str), origin)

            has_makefile = False
            has_diffs = False
            has_dragonfly = False
            available_targets: list[str] = []

            if overlay.exists():
                try:
                    manifest = overlay.manifest
                    has_makefile = manifest.has_makefile_dragonfly
                    has_diffs = manifest.has_diffs
                    has_dragonfly = manifest.has_dragonfly_dir
                except Exception:
                    pass

            # Fallback inference when manifest is missing/invalid
            if not (has_makefile or has_diffs or has_dragonfly):
                has_makefile = (overlay.path / "Makefile.DragonFly").exists() or any(
                    overlay.path.glob("Makefile.DragonFly.@*")
                )
                has_diffs = (overlay.path / "diffs").is_dir()
                has_dragonfly = (overlay.path / "dragonfly").is_dir()

            available_targets = overlay.get_available_targets()

            if normalized_target and normalized_target not in available_targets:
                continue

            ports.append(
                {
                    "origin": str(origin),
                    "customizations": {
                        "makefile_dragonfly": has_makefile,
                        "diffs": has_diffs,
                        "dragonfly_dir": has_dragonfly,
                    },
                    "targets": available_targets,
                }
            )
    else:
        # List all ports
        all_ports = list_ports(config.paths.merged_output)
        customized = set(list_delta_ports(ports_base))

        ports = [{"origin": p, "customized": p in customized} for p in all_ports]

    # Output
    if output_format == "json":
        print(json.dumps(ports, indent=2))
    elif output_format == "table":
        if customized_only:
            print(f"{'Origin':<40} {'MkDF':>4} {'Diffs':>5} {'DF/':>3} {'Targets'}")
            print("-" * 70)
            for p in ports:
                c = p["customizations"]
                q = ",".join(p["targets"]) or "-"
                print(
                    f"{p['origin']:<40} {'Y' if c['makefile_dragonfly'] else '-':>4} "
                    f"{'Y' if c['diffs'] else '-':>5} {'Y' if c['dragonfly_dir'] else '-':>3} {q}"
                )
        else:
            print(f"{'Origin':<40} {'Customized':>10}")
            print("-" * 52)
            for p in ports:
                print(f"{p['origin']:<40} {'Yes' if p['customized'] else 'No':>10}")
    else:  # simple
        for p in ports:
            if isinstance(p, dict):
                print(p.get("origin", p))
            else:
                print(p)

    return 0
