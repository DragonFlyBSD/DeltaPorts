"""State command - manage build state database."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.state import BuildState, import_from_status_files
from dports.models import BuildStatus
from dports.utils import get_logger


def cmd_state(config: Config, args: Namespace) -> int:
    """Execute the state command."""
    log = get_logger(__name__)
    
    state_cmd = getattr(args, 'state_cmd', 'show')
    
    if state_cmd == "show":
        return _state_show(config, args)
    elif state_cmd == "clear":
        return _state_clear(config, args)
    elif state_cmd == "import":
        return _state_import(config, args)
    elif state_cmd == "export":
        return _state_export(config, args)
    else:
        log.error(f"Unknown state subcommand: {state_cmd}")
        return 1


def _state_show(config: Config, args: Namespace) -> int:
    """Show current build state."""
    log = get_logger(__name__)
    
    state = BuildState(config)
    state.load()
    
    # Count by status
    counts = {s: 0 for s in BuildStatus}
    for port_state in state.iter_all():
        counts[port_state.status] += 1
    
    log.info("Build state summary:")
    for status, count in counts.items():
        if count > 0:
            log.info(f"  {status.value}: {count}")
    
    return 0


def _state_clear(config: Config, args: Namespace) -> int:
    """Clear build state."""
    log = get_logger(__name__)
    
    state = BuildState(config)
    state.clear()
    state.save()
    
    log.info("Build state cleared")
    return 0


def _state_import(config: Config, args: Namespace) -> int:
    """Import state from STATUS files."""
    log = get_logger(__name__)
    
    quarterly = args.target
    log.info(f"Importing state from STATUS files for {quarterly}")
    
    state = import_from_status_files(config, quarterly)
    state.save()
    
    log.info("State imported successfully")
    return 0


def _state_export(config: Config, args: Namespace) -> int:
    """Export state to STATUS files."""
    log = get_logger(__name__)
    
    quarterly = args.target
    log.info(f"Exporting state to STATUS files for {quarterly}")
    
    state = BuildState(config)
    state.load()
    
    count = 0
    for port_state in state.iter_all():
        if port_state.quarterly == quarterly or not port_state.quarterly:
            status_path = config.paths.merged_output / str(port_state.origin) / "STATUS"
            status_path.parent.mkdir(parents=True, exist_ok=True)
            
            content = f"{port_state.status.value}\n{port_state.version}\n"
            status_path.write_text(content)
            count += 1
    
    log.info(f"Exported {count} STATUS files")
    return 0
