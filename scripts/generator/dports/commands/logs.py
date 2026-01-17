"""Logs command - show or manage merge logs."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.utils import get_logger


def cmd_logs(config: Config, args: Namespace) -> int:
    """Execute the logs command."""
    log = get_logger(__name__)
    
    log_dir = config.paths.logs
    clear = getattr(args, 'clear', False)
    
    if clear:
        if log_dir.exists():
            shutil.rmtree(log_dir)
            log.info(f"Cleared logs at {log_dir}")
        else:
            log.info("No logs to clear")
        return 0
    
    if args.port:
        origin = PortOrigin.parse(args.port)
        port_log = log_dir / origin.category / f"{origin.name}.log"
        
        if port_log.exists():
            print(port_log.read_text())
        else:
            log.info(f"No logs found for {origin}")
        return 0
    
    # List all logs
    if not log_dir.exists():
        log.info("No logs found")
        return 0
    
    log_files = list(log_dir.rglob("*.log"))
    
    if not log_files:
        log.info("No logs found")
        return 0
    
    log.info(f"Found {len(log_files)} log files in {log_dir}")
    
    for log_file in sorted(log_files)[:20]:  # Show first 20
        rel = log_file.relative_to(log_dir)
        size = log_file.stat().st_size
        log.info(f"  {rel} ({size} bytes)")
    
    if len(log_files) > 20:
        log.info(f"  ... and {len(log_files) - 20} more")
    
    return 0
