"""Merge command - merge FreeBSD ports with DragonFly overlays."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.merge import merge_port, merge_all_ports
from dports.utils import get_logger


def cmd_merge(config: Config, args: Namespace) -> int:
    """Execute the merge command."""
    log = get_logger(__name__)
    
    quarterly = args.target
    dry_run = getattr(args, 'dry_run', False)
    
    if args.port == "all" or args.port is None:
        log.info(f"Merging all ports for {quarterly}")
        results = merge_all_ports(config, quarterly, dry_run=dry_run)
        
        success = sum(1 for r in results if r.success)
        failed = sum(1 for r in results if not r.success)
        
        log.info(f"Merge complete: {success} succeeded, {failed} failed")
        
        if failed > 0:
            for r in results:
                if not r.success:
                    log.error(f"  {r.origin}: {r.message}")
            return 1
        return 0
    else:
        origin = PortOrigin.parse(args.port)
        log.info(f"Merging {origin} for {quarterly}")
        
        result = merge_port(config, origin, quarterly, dry_run=dry_run)
        
        if result.success:
            log.info(f"Merge successful: {result.message}")
            if result.applied_diffs:
                log.info(f"  Applied diffs: {', '.join(result.applied_diffs)}")
            return 0
        else:
            log.error(f"Merge failed: {result.message}")
            for err in result.errors:
                log.error(f"  {err}")
            return 1
