"""Diff command - show diff between FreeBSD and merged port."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.quarterly import validate_target
from dports.utils import get_logger


def cmd_diff(config: Config, args: Namespace) -> int:
    """Execute the diff command."""
    log = get_logger(__name__)

    origin = PortOrigin.parse(args.port)
    target = validate_target(args.target)

    # Get paths
    merged_port = config.get_merged_port_path(str(origin))
    fbsd_port = config.get_freebsd_port_path(str(origin), target)

    if not merged_port.exists():
        log.error(f"Merged port not found: {merged_port}")
        return 1

    if not fbsd_port.exists():
        log.error(f"FreeBSD port not found: {fbsd_port}")
        return 1

    # Generate diff
    try:
        result = subprocess.run(
            ["diff", "-ruN", str(fbsd_port), str(merged_port)],
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.stdout:
            print(result.stdout)
        else:
            log.info("No differences found")

        return 0

    except subprocess.TimeoutExpired:
        log.error("Diff command timed out")
        return 1
    except Exception as e:
        log.error(f"Error generating diff: {e}")
        return 1
