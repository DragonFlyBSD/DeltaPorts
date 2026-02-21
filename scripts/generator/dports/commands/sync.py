"""Sync command - sync FreeBSD ports tree for a target branch."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.quarterly import validate_target
from dports.utils import get_logger, sync_git_branch


def cmd_sync(config: Config, args: Namespace) -> int:
    """Execute the sync command."""
    log = get_logger(__name__)

    target = validate_target(args.target)
    repo = config.paths.freebsd_ports
    log.info(f"Syncing FreeBSD ports tree to target {target}")

    try:
        sync_git_branch(repo, target)
    except Exception as e:
        log.error(str(e))
        return 1

    log.info(f"FreeBSD ports tree synced: {repo} @ {target}")
    return 0
