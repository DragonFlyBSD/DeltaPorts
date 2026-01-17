"""Save command - save current port state as new diff."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.utils import get_logger


def cmd_save(config: Config, args: Namespace) -> int:
    """Execute the save command."""
    log = get_logger(__name__)
    
    origin = PortOrigin.parse(args.port)
    quarterly = getattr(args, 'target', None)
    
    log.info(f"Saving diff for {origin}")
    
    # Get paths
    merged_port = config.get_merged_port_path(str(origin))
    fbsd_port = config.get_freebsd_port_path(str(origin), quarterly or "")
    overlay_path = config.get_overlay_port_path(str(origin))
    
    if not merged_port.exists():
        log.error(f"Merged port not found: {merged_port}")
        return 1
    
    if not fbsd_port.exists():
        log.error(f"FreeBSD port not found: {fbsd_port}")
        return 1
    
    # Generate diff
    try:
        result = subprocess.run(
            ["diff", "-ruN", str(fbsd_port), str(merged_port)],
            capture_output=True,
            text=True,
            timeout=60,
        )
        
        diff_content = result.stdout
        
        if not diff_content.strip():
            log.info("No differences found")
            return 0
        
        # Determine output path
        diffs_dir = overlay_path / "diffs"
        if quarterly:
            diffs_dir = diffs_dir / f"@{quarterly}"
        
        diffs_dir.mkdir(parents=True, exist_ok=True)
        
        # Save diff
        diff_file = diffs_dir / "port.diff"
        diff_file.write_text(diff_content)
        
        log.info(f"Saved diff to {diff_file}")
        log.info(f"Diff size: {len(diff_content)} bytes")
        
        # Update overlay.toml if needed
        overlay_toml = overlay_path / "overlay.toml"
        if overlay_toml.exists():
            content = overlay_toml.read_text()
            if "diffs = false" in content:
                content = content.replace("diffs = false", "diffs = true")
                overlay_toml.write_text(content)
                log.info("Updated overlay.toml: diffs = true")
        
        return 0
        
    except subprocess.TimeoutExpired:
        log.error("Diff command timed out")
        return 1
    except Exception as e:
        log.error(f"Error generating diff: {e}")
        return 1
