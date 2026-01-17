"""
Special directory handling for DPorts v2.

Handles the special/ directory which contains patches for:
- Mk/ - FreeBSD ports framework makefiles
- Templates/ - Port templates
- treetop/ - Top-level ports tree files

Unlike regular ports, special/ uses a simple convention:
- Files are copied directly (no overlay.toml needed)
- Diffs use underscore naming: Mk/diffs/bsd.port.mk.diff
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dports.config import Config

from dports.utils import DPortsError, cpdup, apply_patch, get_logger


class SpecialError(DPortsError):
    """Error handling special directories."""
    pass


# Known special directory names
SPECIAL_DIRS = ["Mk", "Templates", "treetop"]


def apply_special_patches(
    config: Config,
    quarterly: str,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """
    Apply all special directory patches.
    
    Args:
        config: DPorts configuration
        quarterly: Target quarterly (for path resolution)
        dry_run: If True, don't make changes
        
    Returns:
        Dict mapping directory name to list of applied patches
    """
    log = get_logger(__name__)
    results: dict[str, list[str]] = {}
    
    special_base = config.paths.delta / "special"
    
    for dirname in SPECIAL_DIRS:
        special_dir = special_base / dirname
        if not special_dir.exists():
            continue
        
        applied = []
        
        # Get target directory in merged output
        if dirname == "treetop":
            # treetop files go to the root of the ports tree
            target_dir = config.paths.merged_output
        else:
            target_dir = config.paths.merged_output / dirname
        
        # Copy base files from special/<dir>/ (excluding diffs/)
        log.info(f"Processing special/{dirname}")
        
        for item in special_dir.iterdir():
            if item.name == "diffs":
                continue
            
            if item.is_file():
                dst = target_dir / item.name
                log.debug(f"Copying {item} -> {dst}")
                if not dry_run:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, dst)
                applied.append(f"copy:{item.name}")
            elif item.is_dir():
                dst = target_dir / item.name
                log.debug(f"Copying directory {item} -> {dst}")
                if not dry_run:
                    if dst.exists():
                        shutil.rmtree(dst)
                    shutil.copytree(item, dst)
                applied.append(f"copy:{item.name}/")
        
        # Apply diffs
        diffs_dir = special_dir / "diffs"
        if diffs_dir.exists():
            for diff_file in sorted(diffs_dir.glob("*.diff")):
                # Parse target from diff filename
                # e.g., bsd.port.mk.diff -> Mk/bsd.port.mk
                target_name = diff_file.stem  # Remove .diff
                
                # Handle underscore convention for subdirs
                # e.g., Scripts_Makefile.diff -> Mk/Scripts/Makefile
                target_path = _parse_diff_target(target_name, dirname)
                
                log.info(f"Applying {diff_file.name} to {target_path}")
                if not dry_run:
                    full_target = config.paths.merged_output / target_path
                    if full_target.exists():
                        success = apply_patch(diff_file, full_target.parent, filename=full_target.name)
                        if success:
                            applied.append(f"patch:{diff_file.name}")
                        else:
                            log.warning(f"Failed to apply {diff_file.name}")
                    else:
                        log.warning(f"Target not found: {full_target}")
                else:
                    applied.append(f"patch:{diff_file.name}")
        
        results[dirname] = applied
    
    return results


def _parse_diff_target(diff_name: str, special_dir: str) -> Path:
    """
    Parse a diff filename into target path.
    
    Uses underscore convention for subdirectories:
    - bsd.port.mk -> Mk/bsd.port.mk
    - Scripts_Makefile -> Mk/Scripts/Makefile
    
    Args:
        diff_name: Diff filename without .diff extension
        special_dir: Name of special directory (Mk, Templates, treetop)
        
    Returns:
        Relative path to target file
    """
    # Split on underscores to get path components
    parts = diff_name.split("_")
    
    if special_dir == "treetop":
        # treetop files are at root level
        return Path("_".join(parts))
    
    if len(parts) > 1:
        # Has subdirectory component
        subdir = parts[0]
        filename = "_".join(parts[1:])
        return Path(special_dir) / subdir / filename
    
    # Simple filename
    return Path(special_dir) / diff_name


def list_special_contents(config: Config) -> dict[str, dict[str, list[str]]]:
    """
    List contents of special directories.
    
    Returns:
        Dict mapping directory name to {"files": [...], "diffs": [...]}
    """
    results: dict[str, dict[str, list[str]]] = {}
    special_base = config.paths.delta / "special"
    
    for dirname in SPECIAL_DIRS:
        special_dir = special_base / dirname
        if not special_dir.exists():
            continue
        
        contents: dict[str, list[str]] = {"files": [], "diffs": []}
        
        for item in special_dir.iterdir():
            if item.name == "diffs":
                diffs_dir = item
                for diff_file in sorted(diffs_dir.glob("*.diff")):
                    contents["diffs"].append(diff_file.name)
            elif item.is_file():
                contents["files"].append(item.name)
            elif item.is_dir():
                contents["files"].append(f"{item.name}/")
        
        results[dirname] = contents
    
    return results
