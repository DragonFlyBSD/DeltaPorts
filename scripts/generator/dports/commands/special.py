"""Special command - apply infrastructure merge stage."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.special import merge_infrastructure
from dports.quarterly import validate_target
from dports.utils import get_logger, ensure_git_branch


def cmd_special(config: Config, args: Namespace) -> int:
    """Execute the special command."""
    log = get_logger(__name__)

    target = validate_target(args.target)
    dry_run = getattr(args, "dry_run", False)

    try:
        ensure_git_branch(config.paths.freebsd_ports, target)
    except Exception as e:
        log.error(str(e))
        return 1

    log.info(f"Applying infrastructure merge for {target}")

    results = merge_infrastructure(config, target, dry_run=dry_run)
    ok = [name for name, success in results.items() if success]
    failed = [name for name, success in results.items() if not success]

    for name in ok:
        log.info(f"  {name}: ok")
    for name in failed:
        log.error(f"  {name}: failed")

    if failed:
        return 1
    return 0
