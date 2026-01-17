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
from dports.transform import (
    transform_gids_file,
    transform_uids_file,
    transform_moved_file,
    transform_tools_file,
)


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


# =============================================================================
# Infrastructure Merge Functions
# =============================================================================

def merge_infrastructure(
    config: Config,
    quarterly: str,
    dry_run: bool = False,
) -> dict[str, bool]:
    """
    Merge all infrastructure files (Mk, Templates, Tools, treetop).
    
    This handles:
    - Mk/ and Templates/ - copied from FreeBSD, then patched
    - Tools/ - copied with perl shebang fix
    - Keywords/ - straight copy
    - Treetop files (UIDs, GIDs, MOVED, Makefile) - copied and transformed
    
    Args:
        config: DPorts configuration
        quarterly: Target quarterly (for FreeBSD path resolution)
        dry_run: If True, don't make changes
        
    Returns:
        Dict mapping component name to success status
    """
    log = get_logger(__name__)
    results: dict[str, bool] = {}
    
    fports = config.get_freebsd_ports_path(quarterly)
    merged = config.paths.merged_output
    delta = config.paths.delta
    
    # Merge Tools/ with perl shebang fix
    results["Tools"] = _merge_tools(fports, merged, dry_run, log)
    
    # Merge Keywords/ (straight copy)
    results["Keywords"] = _merge_keywords(fports, merged, dry_run, log)
    
    # Merge Mk/ and Templates/ with patches
    results["Mk"] = _merge_mk_templates(fports, merged, delta, "Mk", dry_run, log)
    results["Templates"] = _merge_mk_templates(fports, merged, delta, "Templates", dry_run, log)
    
    # Merge treetop files (UIDs, GIDs, MOVED, etc.)
    results["treetop"] = _merge_treetop(fports, merged, delta, dry_run, log)
    
    return results


def _merge_tools(fports: Path, merged: Path, dry_run: bool, log) -> bool:
    """Merge Tools/ with perl path fixes."""
    log.info("Merging Tools/...")
    
    if dry_run:
        return True
    
    src = fports / "Tools"
    dst = merged / "Tools"
    
    if not src.exists():
        log.warning(f"Tools/ not found in {fports}")
        return False
    
    try:
        if dst.exists():
            shutil.rmtree(dst)
        dst.mkdir(parents=True)
        
        for f in src.rglob('*'):
            rel = f.relative_to(src)
            d = dst / rel
            
            if f.is_dir():
                d.mkdir(parents=True, exist_ok=True)
            else:
                try:
                    content = f.read_text()
                    content = transform_tools_file(content)
                    d.parent.mkdir(parents=True, exist_ok=True)
                    d.write_text(content)
                    d.chmod(f.stat().st_mode)
                except UnicodeDecodeError:
                    # Binary file, just copy
                    shutil.copy2(f, d)
        
        return True
    except Exception as e:
        log.error(f"Failed to merge Tools/: {e}")
        return False


def _merge_keywords(fports: Path, merged: Path, dry_run: bool, log) -> bool:
    """Copy Keywords/ directory."""
    log.info("Merging Keywords/...")
    
    if dry_run:
        return True
    
    src = fports / "Keywords"
    dst = merged / "Keywords"
    
    if not src.exists():
        log.warning(f"Keywords/ not found in {fports}")
        return False
    
    try:
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        return True
    except Exception as e:
        log.error(f"Failed to merge Keywords/: {e}")
        return False


def _merge_mk_templates(
    fports: Path, merged: Path, delta: Path, dirname: str, dry_run: bool, log
) -> bool:
    """Merge Mk/ or Templates/ with patches."""
    log.info(f"Merging {dirname}/...")
    
    if dry_run:
        return True
    
    src = fports / dirname
    dst = merged / dirname
    
    if not src.exists():
        log.warning(f"{dirname}/ not found in {fports}")
        return False
    
    try:
        # Copy base
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        
        # Remove bsd.gcc.mk from Mk/ (not used on DragonFly)
        if dirname == "Mk":
            gcc_mk = dst / "bsd.gcc.mk"
            if gcc_mk.exists():
                gcc_mk.unlink()
        
        # Apply patches from special/<dirname>/diffs/
        diffs = delta / "special" / dirname / "diffs"
        if diffs.exists():
            for diff_file in sorted(diffs.glob("*.diff")):
                success = apply_patch(diff_file, dst)
                if not success:
                    log.warning(f"Patch failed: {diff_file.name}")
            
            # Cleanup .orig files
            for orig in dst.rglob("*.orig"):
                patched = orig.with_suffix('')
                if patched.exists():
                    import os
                    os.utime(patched, (orig.stat().st_atime, orig.stat().st_mtime))
                orig.unlink()
        
        # Copy replacements from special/<dirname>/replacements/
        replacements = delta / "special" / dirname / "replacements"
        if replacements.exists():
            for item in replacements.rglob("*"):
                if item.is_file():
                    rel = item.relative_to(replacements)
                    target = dst / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(item, target)
                    log.debug(f"Replaced {rel}")
        
        return True
    except Exception as e:
        log.error(f"Failed to merge {dirname}/: {e}")
        return False


def _merge_treetop(fports: Path, merged: Path, delta: Path, dry_run: bool, log) -> bool:
    """Merge top-level files (UIDs, GIDs, MOVED)."""
    log.info("Merging top-level files...")
    
    if dry_run:
        return True
    
    try:
        # GIDs - insert extra DragonFly groups
        gids_src = fports / "GIDs"
        if gids_src.exists():
            content = gids_src.read_text()
            content = transform_gids_file(content)
            (merged / "GIDs").write_text(content)
        
        # UIDs - insert extra DragonFly users
        uids_src = fports / "UIDs"
        if uids_src.exists():
            content = uids_src.read_text()
            content = transform_uids_file(content)
            (merged / "UIDs").write_text(content)
        
        # MOVED - filter out old entries (pre-2012)
        moved_src = fports / "MOVED"
        if moved_src.exists():
            content = moved_src.read_text()
            content = transform_moved_file(content)
            dst = merged / "MOVED"
            dst.write_text(content)
            shutil.copystat(moved_src, dst)
        
        # Apply treetop patches
        diffs = delta / "special" / "treetop" / "diffs"
        if diffs.exists():
            for diff_file in sorted(diffs.glob("*.diff")):
                success = apply_patch(diff_file, merged)
                if not success:
                    log.warning(f"Treetop patch failed: {diff_file.name}")
            
            # Cleanup .orig files
            for orig in merged.glob("*.orig"):
                orig.unlink()
        
        return True
    except Exception as e:
        log.error(f"Failed to merge treetop: {e}")
        return False
