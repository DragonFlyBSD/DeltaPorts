"""Check command - validate overlay configuration and patches."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.validate import validate_port, validate_all_ports
from dports.quarterly import validate_target
from dports.utils import get_logger, ensure_git_branch


def cmd_check(config: Config, args: Namespace) -> int:
    """Execute the check command."""
    log = get_logger(__name__)

    target = validate_target(args.target)

    try:
        ensure_git_branch(config.paths.freebsd_ports, target)
    except Exception as e:
        log.error(str(e))
        return 1

    if args.port == "all" or args.port is None:
        log.info("Validating all ports")
        results = validate_all_ports(config, target)

        valid = sum(1 for r in results.values() if r.valid)
        invalid = sum(1 for r in results.values() if not r.valid)

        log.info(f"Validation complete: {valid} valid, {invalid} invalid")

        if invalid > 0:
            for origin, result in results.items():
                if not result.valid:
                    log.error(f"{origin}:")
                    for err in result.errors:
                        log.error(f"  ERROR: {err}")
                    for warn in result.warnings:
                        log.warning(f"  WARNING: {warn}")
            return 1
        return 0
    else:
        origin = PortOrigin.parse(args.port)
        log.info(f"Validating {origin}")

        result = validate_port(config, origin, target)

        if result.valid:
            log.info(f"Port {origin} is valid")
            for warn in result.warnings:
                log.warning(f"  WARNING: {warn}")
            return 0
        else:
            log.error(f"Port {origin} has errors:")
            for err in result.errors:
                log.error(f"  {err}")
            for warn in result.warnings:
                log.warning(f"  {warn}")
            return 1
