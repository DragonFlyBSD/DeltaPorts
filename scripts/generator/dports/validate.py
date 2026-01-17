"""
Validation for DPorts v2.

Provides comprehensive validation of:
- overlay.toml manifests
- Diff patch files
- File structure integrity
- Quarterly override consistency
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dports.config import Config

from dports.models import PortOrigin, ValidationResult
from dports.overlay import Overlay, OverlayError
from dports.utils import DPortsError, get_logger


class ValidationError(DPortsError):
    """Validation error."""
    pass


def validate_port(
    config: Config,
    origin: PortOrigin,
    quarterly: str | None = None,
) -> ValidationResult:
    """
    Validate a port's overlay configuration.
    
    Checks:
    - overlay.toml exists and is valid
    - Declared customizations exist
    - Diff files are valid unified diff format
    - No orphaned files
    
    Args:
        config: DPorts configuration
        origin: Port to validate
        quarterly: Optional quarterly context
        
    Returns:
        ValidationResult
    """
    log = get_logger(__name__)
    result = ValidationResult(origin=origin, valid=True)
    
    port_path = config.get_overlay_port_path(str(origin))
    
    if not port_path.exists():
        result.add_error(f"Overlay directory not found: {port_path}")
        return result
    
    # Load and validate overlay
    try:
        overlay = Overlay(port_path, origin)
        if not overlay.exists():
            result.add_error("No overlay.toml found")
            return result
        
        result.checked_manifest = True
        
        # Delegate to overlay's validation
        overlay_result = overlay.validate(quarterly)
        result.errors.extend(overlay_result.errors)
        result.warnings.extend(overlay_result.warnings)
        result.valid = overlay_result.valid
        
    except OverlayError as e:
        result.add_error(str(e))
        return result
    
    # Validate diff files
    diffs_dir = port_path / "diffs"
    if diffs_dir.exists():
        for diff_file in diffs_dir.rglob("*.diff"):
            if diff_file.parent.name.startswith("@"):
                # Quarterly-specific diff
                pass
            
            errors = validate_diff_file(diff_file)
            for error in errors:
                result.add_warning(f"{diff_file.name}: {error}")
        
        result.checked_diffs = True
    
    # Check for orphaned files
    orphans = find_orphaned_files(port_path)
    for orphan in orphans:
        result.add_warning(f"Orphaned file not in manifest: {orphan}")
    
    return result


def validate_diff_file(diff_path: Path) -> list[str]:
    """
    Validate a diff file format.
    
    Checks:
    - File is readable
    - Contains valid unified diff headers
    - Hunks have proper format
    
    Args:
        diff_path: Path to diff file
        
    Returns:
        List of error messages (empty if valid)
    """
    errors = []
    
    try:
        content = diff_path.read_text()
    except Exception as e:
        return [f"Cannot read file: {e}"]
    
    if not content.strip():
        return ["Empty diff file"]
    
    # Check for unified diff headers
    has_header = False
    lines = content.split("\n")
    
    for i, line in enumerate(lines):
        if line.startswith("---") or line.startswith("+++"):
            has_header = True
        elif line.startswith("@@"):
            # Validate hunk header
            if not re.match(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", line):
                errors.append(f"Line {i+1}: Invalid hunk header")
    
    if not has_header:
        errors.append("Missing unified diff header (--- / +++)")
    
    return errors


def validate_diff_applies(
    diff_path: Path,
    target_dir: Path,
    target_file: str | None = None,
) -> tuple[bool, str]:
    """
    Check if a diff applies cleanly (dry-run).
    
    Args:
        diff_path: Path to diff file
        target_dir: Directory to apply in
        target_file: Optional specific target file
        
    Returns:
        Tuple of (applies_cleanly, message)
    """
    cmd = ["patch", "--dry-run", "-p0", "-i", str(diff_path)]
    
    if target_file:
        # Extract the target file from the diff and verify
        pass
    
    try:
        result = subprocess.run(
            cmd,
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=30,
        )
        
        if result.returncode == 0:
            return True, "Applies cleanly"
        else:
            return False, result.stderr or result.stdout
            
    except subprocess.TimeoutExpired:
        return False, "Patch command timed out"
    except Exception as e:
        return False, str(e)


def find_orphaned_files(port_path: Path) -> list[str]:
    """
    Find files not referenced by the overlay manifest.
    
    Args:
        port_path: Path to port overlay
        
    Returns:
        List of orphaned file paths relative to port_path
    """
    orphans = []
    
    # Known directories/files that should be in manifest
    known_items = {
        "overlay.toml",
        "Makefile.DragonFly",
        "diffs",
        "dragonfly",
        "files",
    }
    
    for item in port_path.iterdir():
        if item.name.startswith("."):
            continue
        if item.name not in known_items:
            orphans.append(item.name)
    
    return orphans


def validate_all_ports(
    config: Config,
    quarterly: str | None = None,
) -> dict[str, ValidationResult]:
    """
    Validate all ports with overlays.
    
    Args:
        config: DPorts configuration
        quarterly: Optional quarterly context
        
    Returns:
        Dict mapping port origin to ValidationResult
    """
    from dports.overlay import discover_overlays
    
    results = {}
    ports_base = config.paths.delta / "ports"
    
    for overlay in discover_overlays(ports_base):
        result = validate_port(config, overlay.origin, quarterly)
        results[str(overlay.origin)] = result
    
    return results
