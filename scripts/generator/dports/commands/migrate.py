"""Migrate command - migrate v1 port configuration to v2 overlay.toml."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.migrate import migrate_port, migrate_all_ports, discover_unmigrated_ports
from dports.utils import get_logger


def cmd_migrate(config: Config, args: Namespace) -> int:
    """Execute the migrate command."""
    log = get_logger(__name__)
    
    dry_run = getattr(args, 'dry_run', False)
    
    if args.port == "all" or args.port is None:
        log.info("Migrating all ports to v2 format")
        
        if dry_run:
            # Just list what would be migrated
            count = 0
            for origin, customizations in discover_unmigrated_ports(config):
                log.info(f"Would migrate: {origin}")
                for k, v in customizations.items():
                    if v:
                        log.debug(f"  {k}")
                count += 1
            log.info(f"Total: {count} ports to migrate")
            return 0
        
        success, skipped, errors = migrate_all_ports(config, dry_run=dry_run)
        
        log.info(f"Migration complete: {success} migrated, {skipped} skipped")
        
        if errors:
            for err in errors:
                log.error(f"  {err}")
            return 1
        return 0
    else:
        origin = PortOrigin.parse(args.port)
        log.info(f"Migrating {origin} to v2 format")
        
        ok, msg = migrate_port(config, origin, dry_run=dry_run)
        
        if ok:
            log.info(f"Migration successful: {msg}")
            return 0
        else:
            log.error(f"Migration failed: {msg}")
            return 1
