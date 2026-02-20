"""Migrate command - migrate v1 port configuration to v2 overlay.toml."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.migrate import (
    migrate_port,
    migrate_all_ports,
    discover_all_ports,
    parse_status_file,
    detect_v1_customizations,
    has_customizations,
)
from dports.utils import get_logger


def cmd_migrate(config: Config, args: Namespace) -> int:
    """Execute the migrate command."""
    log = get_logger(__name__)
    
    dry_run = getattr(args, 'dry_run', False)
    
    # Get output paths
    output_base = Path(getattr(args, 'output', None) or config.paths.delta / "migrated_ports")
    state_output = Path(getattr(args, 'state_output', None) or config.paths.delta / "state" / "builds.json")
    
    if args.port == "all" or args.port is None:
        log.info("Migrating all ports to v2 format")
        log.info(f"Output directory: {output_base}")
        log.info(f"State file: {state_output}")
        
        if dry_run:
            # Show summary of what would be migrated
            from dports.models import PortType
            
            counts = {
                "PORT_with_custom": 0,
                "PORT_without_custom": 0,
                "MASK": 0,
                "DPORT": 0,
                "LOCK": 0,
            }
            
            for origin, port_path in discover_all_ports(config):
                status_data = parse_status_file(port_path / "STATUS")
                customizations = detect_v1_customizations(port_path)
                
                if status_data.port_type == PortType.PORT:
                    if has_customizations(customizations):
                        counts["PORT_with_custom"] += 1
                        log.debug(f"Would migrate: {origin} (PORT with customizations)")
                    else:
                        counts["PORT_without_custom"] += 1
                elif status_data.port_type == PortType.MASK:
                    counts["MASK"] += 1
                    log.debug(f"Would migrate: {origin} (MASK)")
                elif status_data.port_type == PortType.DPORT:
                    counts["DPORT"] += 1
                    log.debug(f"Would migrate: {origin} (DPORT)")
                elif status_data.port_type == PortType.LOCK:
                    counts["LOCK"] += 1
                    log.debug(f"Would migrate: {origin} (LOCK)")
            
            total = sum(counts.values())
            to_migrate = total - counts["PORT_without_custom"]
            
            log.info(f"Summary:")
            log.info(f"  PORT with customizations: {counts['PORT_with_custom']}")
            log.info(f"  PORT without customizations: {counts['PORT_without_custom']} (builds.json only)")
            log.info(f"  MASK: {counts['MASK']}")
            log.info(f"  DPORT: {counts['DPORT']}")
            log.info(f"  LOCK: {counts['LOCK']}")
            log.info(f"  Total: {total} ports")
            log.info(f"  Would create directories for: {to_migrate} ports")
            return 0
        
        migrated, skipped, total, errors = migrate_all_ports(
            config,
            output_base=output_base,
            state_output=state_output,
            dry_run=dry_run,
        )
        
        log.info(f"Migration complete:")
        log.info(f"  Migrated (created directories): {migrated}")
        log.info(f"  Skipped (builds.json only): {skipped}")
        log.info(f"  Total ports: {total}")
        
        if errors:
            log.error(f"  Errors: {len(errors)}")
            for err in errors[:10]:  # Show first 10 errors
                log.error(f"    {err}")
            if len(errors) > 10:
                log.error(f"    ... and {len(errors) - 10} more")
            return 1
        return 0
    else:
        origin = PortOrigin.parse(args.port)
        log.info(f"Migrating {origin} to v2 format")
        
        result = migrate_port(config, origin, output_base=output_base, dry_run=dry_run)
        
        if result.migrated:
            log.info(f"Migration successful: {result.message}")
            log.info(f"  Type: {result.status_data.port_type.value}")
            if result.status_data.last_attempt:
                log.info(f"  Last attempt: {result.status_data.last_attempt}")
            if result.status_data.last_success:
                log.info(f"  Last success: {result.status_data.last_success}")
            if result.customizations:
                customs = [k for k, v in result.customizations.items() if v]
                if customs:
                    log.info(f"  Customizations: {', '.join(customs)}")
            return 0
        else:
            log.warning(f"Port not migrated: {result.message}")
            log.info(f"  Type: {result.status_data.port_type.value}")
            if result.status_data.last_attempt:
                log.info(f"  Last attempt: {result.status_data.last_attempt}")
            return 0  # Not an error, just no customizations
