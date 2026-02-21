"""Prune command - remove ports that no longer exist in FreeBSD."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.quarterly import validate_target
from dports.utils import get_logger, list_ports


def cmd_prune(config: Config, args: Namespace) -> int:
    """Execute the prune command."""
    log = get_logger(__name__)

    target = validate_target(args.target)
    dry_run = getattr(args, "dry_run", False)

    log.info(f"Pruning removed ports for {target}")

    # Get list of FreeBSD ports
    fbsd_ports = set(list_ports(config.paths.freebsd_ports))

    # Get list of merged ports
    merged_ports = set(list_ports(config.paths.merged_output))

    # Find ports to remove
    to_remove = merged_ports - fbsd_ports

    if not to_remove:
        log.info("No ports to prune")
        return 0

    log.info(f"Found {len(to_remove)} ports to prune")

    for origin in sorted(to_remove):
        port_path = config.paths.merged_output / origin

        if dry_run:
            log.info(f"Would remove: {origin}")
        else:
            log.info(f"Removing: {origin}")
            try:
                shutil.rmtree(port_path)
            except OSError as e:
                log.error(f"Failed to remove {origin}: {e}")

    return 0
