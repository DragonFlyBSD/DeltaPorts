"""Special command - apply special/ directory patches."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.special import apply_special_patches, list_special_contents
from dports.utils import get_logger


def cmd_special(config: Config, args: Namespace) -> int:
    """Execute the special command."""
    log = get_logger(__name__)
    
    quarterly = args.target
    dry_run = getattr(args, 'dry_run', False)
    
    if dry_run:
        log.info("Dry run - showing special/ contents")
        contents = list_special_contents(config)
        
        for dirname, items in contents.items():
            log.info(f"\nspecial/{dirname}/")
            if items["files"]:
                log.info(f"  Files: {', '.join(items['files'])}")
            if items["diffs"]:
                log.info(f"  Diffs: {', '.join(items['diffs'])}")
        
        return 0
    
    log.info(f"Applying special/ patches for {quarterly}")
    
    results = apply_special_patches(config, quarterly, dry_run=dry_run)
    
    total = 0
    for dirname, applied in results.items():
        if applied:
            log.info(f"special/{dirname}/: {len(applied)} items applied")
            for item in applied:
                log.debug(f"  {item}")
            total += len(applied)
    
    log.info(f"Applied {total} special items")
    return 0
