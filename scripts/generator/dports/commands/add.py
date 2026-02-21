"""Add command - add a new port customization."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.migrate import generate_overlay_toml_v2
from dports.utils import get_logger


def cmd_add(config: Config, args: Namespace) -> int:
    """Execute the add command."""
    log = get_logger(__name__)

    origin = PortOrigin.parse(args.port)
    log.info(f"Adding customization for {origin}")

    overlay_path = config.get_overlay_port_path(str(origin))

    # Check if already exists
    if overlay_path.exists():
        log.warning(f"Overlay already exists at {overlay_path}")
        return 1

    # Create directory structure
    overlay_path.mkdir(parents=True, exist_ok=True)

    # Create minimal overlay.toml
    content = generate_overlay_toml_v2(
        origin=origin,
        reason=f"DragonFly customizations for {origin}",
        components={
            "makefile_dragonfly": False,
            "diffs": False,
            "dragonfly_dir": False,
            "extra_patches": False,
        },
    )

    (overlay_path / "overlay.toml").write_text(content)

    log.info(f"Created {overlay_path}/overlay.toml")
    log.info("Edit the overlay.toml and add your customizations:")
    log.info("  - Create Makefile.DragonFly.@main for Makefile modifications")
    log.info("  - Create diffs/@main/ for patch files")
    log.info("  - Create dragonfly/@main/ for overlay files")

    return 0
