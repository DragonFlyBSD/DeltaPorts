"""Status command - show port build status."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.state import BuildState
from dports.quarterly import validate_target
from dports.utils import get_logger


def cmd_status(config: Config, args: Namespace) -> int:
    """Execute the status command."""
    log = get_logger(__name__)

    state = BuildState(config)
    state.load()

    if args.port:
        origin = PortOrigin.parse(args.port)
        target = validate_target(args.target) if getattr(args, "target", None) else None
        if target:
            states = [state.get_for_target(origin, target)]
        else:
            states = state.get_all_for_origin(origin)

        states = [s for s in states if s is not None]
        if states:
            log.info(f"Port: {origin}")
            for port_state in states:
                log.info(f"  Target: {port_state.target or 'any'}")
                log.info(f"    Status: {port_state.status.value}")
                log.info(f"    Version: {port_state.version or 'unknown'}")
                if port_state.last_build:
                    log.info(f"    Last build: {port_state.last_build.isoformat()}")
                if port_state.notes:
                    log.info(f"    Notes: {port_state.notes}")
        else:
            log.info(f"No status recorded for {origin}")

        return 0
    else:
        # Show summary
        from dports.models import BuildStatus

        counts = {s: 0 for s in BuildStatus}
        for port_state in state.iter_all():
            counts[port_state.status] += 1

        total = sum(counts.values())
        log.info(f"Build status summary ({total} ports):")
        for status, count in counts.items():
            if count > 0:
                pct = (count / total * 100) if total > 0 else 0
                log.info(f"  {status.value:<10}: {count:>6} ({pct:5.1f}%)")

        return 0
