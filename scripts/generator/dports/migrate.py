"""
Migration tools for DPorts v1 to v2.

Provides tools to migrate existing port customizations from v1 format
(STATUS files) to v2 format (overlay.toml manifests + builds.json).

v1 STATUS file format:
    Line 1: PORT|MASK|DPORT|LOCK
    Line 2: Last attempt: <version> (optional)
    Line 3: Last success: <version> (optional)
    Line 2+: # <reason> (for MASK ports)

v2 output:
    - overlay.toml per port with customizations
    - state/builds.json with version tracking for ALL ports
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterator
    from dports.config import Config

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from dports.models import PortOrigin, OverlayManifest, PortType
from dports.quarterly import list_quarterly_overrides
from dports.utils import DPortsError, get_logger


class MigrationError(DPortsError):
    """Error during migration."""
    pass


@dataclass
class StatusFileData:
    """Parsed data from a v1 STATUS file."""
    
    port_type: PortType = PortType.PORT
    last_attempt: str = ""
    last_success: str = ""
    reason: str = ""  # For MASK ports
    
    @property
    def build_status(self) -> str:
        """Determine build status from versions."""
        if not self.last_attempt:
            return "unknown"
        if self.last_success == self.last_attempt:
            return "success"
        if self.last_success:
            return "failed"  # Had success before but last attempt failed
        return "failed"


@dataclass
class PortMigrationResult:
    """Result of migrating a single port."""
    
    origin: PortOrigin
    status_data: StatusFileData
    migrated: bool = False  # True if copied to migrated_ports/
    message: str = ""
    customizations: dict[str, bool] = field(default_factory=dict)


def parse_status_file(status_path: Path) -> StatusFileData:
    """
    Parse a v1 STATUS file.
    
    Args:
        status_path: Path to the STATUS file
        
    Returns:
        StatusFileData with parsed information
        
    Example STATUS files:
        PORT
        Last attempt: 1.26.3_3,3
        Last success: 1.26.3_3,3
        
        MASK
        # FreeBSD documentation is available online
        
        DPORT
        
        LOCK
        Last attempt: 25.3.2.7_1,4
        Last success: 25.3.2.7_1,4
    """
    result = StatusFileData()
    
    if not status_path.exists():
        return result
    
    lines = status_path.read_text().strip().split("\n")
    if not lines:
        return result
    
    # Line 1: Port type
    type_str = lines[0].strip().upper()
    try:
        result.port_type = PortType(type_str.lower())
    except ValueError:
        # Default to PORT for unknown types
        result.port_type = PortType.PORT
    
    # Parse remaining lines
    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        
        if line.startswith("Last attempt:"):
            result.last_attempt = line.split(":", 1)[1].strip()
        elif line.startswith("Last success:"):
            result.last_success = line.split(":", 1)[1].strip()
        elif line.startswith("#"):
            # Reason comment (for MASK ports)
            reason = line[1:].strip()
            if result.reason:
                result.reason += " " + reason
            else:
                result.reason = reason
    
    return result


def detect_v1_customizations(port_path: Path) -> dict[str, bool]:
    """
    Detect v1-style customizations in a port directory.
    
    Args:
        port_path: Path to the port overlay directory
        
    Returns:
        Dict with detected customization types
    """
    def dir_has_files(path: Path) -> bool:
        """Check if directory exists and has files."""
        if not path.exists() or not path.is_dir():
            return False
        try:
            return any(path.iterdir())
        except PermissionError:
            return False
    
    return {
        "makefile_dragonfly": (port_path / "Makefile.DragonFly").exists(),
        "diffs": dir_has_files(port_path / "diffs"),
        "dragonfly_dir": dir_has_files(port_path / "dragonfly"),
        "extra_patches": (port_path / "files").exists() and any(
            f.name.startswith("extra-patch-") 
            for f in (port_path / "files").iterdir() 
            if f.is_file()
        ) if (port_path / "files").exists() else False,
        "newport": dir_has_files(port_path / "newport"),
    }


def has_customizations(customizations: dict[str, bool]) -> bool:
    """Check if any customizations are present."""
    return any(customizations.values())


def copy_port_directory(
    src: Path,
    dst: Path,
    exclude: set[str] | None = None,
) -> None:
    """
    Copy a port directory, excluding specified files.
    
    Args:
        src: Source port directory
        dst: Destination directory
        exclude: Set of filenames to exclude (e.g., {"STATUS"})
    """
    if exclude is None:
        exclude = {"STATUS"}
    
    # Ensure destination parent exists
    dst.parent.mkdir(parents=True, exist_ok=True)
    
    # Remove existing destination if present
    if dst.exists():
        shutil.rmtree(dst)
    
    # Create destination
    dst.mkdir(parents=True, exist_ok=True)
    
    # Copy contents
    for item in src.iterdir():
        if item.name in exclude:
            continue
        
        dst_item = dst / item.name
        if item.is_dir():
            shutil.copytree(item, dst_item)
        else:
            shutil.copy2(item, dst_item)


def generate_overlay_toml(
    origin: PortOrigin,
    port_type: PortType,
    customizations: dict[str, bool],
    reason: str = "",
    quarterly_overrides: list[str] | None = None,
) -> str:
    """
    Generate overlay.toml content for a port.
    
    Args:
        origin: Port origin
        port_type: Type of port (PORT, MASK, DPORT, LOCK)
        customizations: Dict of detected customizations
        reason: Reason/description (especially for MASK)
        quarterly_overrides: List of quarterly-specific overrides
        
    Returns:
        TOML content as string
    """
    lines = []
    
    # Header comment
    lines.append(f"# DPorts v2 overlay manifest for {origin}")
    lines.append("")
    
    # [overlay] section
    lines.append("[overlay]")
    
    # Reason/description
    if reason:
        # Escape quotes in reason
        safe_reason = reason.replace('"', '\\"')
        lines.append(f'reason = "{safe_reason}"')
    else:
        lines.append('reason = "TODO: add description"')
    
    # Type (only if not PORT, since PORT is default)
    if port_type != PortType.PORT:
        lines.append(f'type = "{port_type.value}"')
    
    lines.append("")
    
    # For MASK ports, add [status] section
    if port_type == PortType.MASK:
        lines.append("[status]")
        if reason:
            safe_reason = reason.replace('"', '\\"')
            lines.append(f'ignore = "{safe_reason}"')
        else:
            lines.append('ignore = "masked"')
        lines.append("")
    
    # Customization flags (for PORT type with customizations)
    if port_type == PortType.PORT and has_customizations(customizations):
        lines.append("# Customization types present")
        
        if customizations.get("makefile_dragonfly"):
            lines.append("makefile_dragonfly = true")
        
        if customizations.get("diffs"):
            lines.append("diffs = true")
        
        if customizations.get("dragonfly_dir"):
            lines.append("dragonfly_dir = true")
        
        if customizations.get("extra_patches"):
            lines.append("extra_patches = true")
        
        lines.append("")
    
    # DPORT type - note about newport
    if port_type == PortType.DPORT:
        lines.append("# newport/ directory contains the complete port")
        lines.append("newport = true")
        lines.append("")
    
    # Add quarterly overrides if any
    if quarterly_overrides:
        lines.append("# Quarterly-specific patch directories")
        overrides_str = ", ".join(f'"{q}"' for q in sorted(quarterly_overrides))
        lines.append(f"quarterly_overrides = [{overrides_str}]")
        lines.append("")
    
    return "\n".join(lines)


def migrate_port(
    config: Config,
    origin: PortOrigin,
    output_base: Path,
    dry_run: bool = False,
) -> PortMigrationResult:
    """
    Migrate a single port from v1 to v2 format.
    
    Args:
        config: DPorts configuration
        origin: Port to migrate
        output_base: Base directory for migrated ports (e.g., migrated_ports/)
        dry_run: If True, don't write files
        
    Returns:
        PortMigrationResult with details of the operation
    """
    log = get_logger(__name__)
    port_path = config.get_overlay_port_path(str(origin))
    
    result = PortMigrationResult(
        origin=origin,
        status_data=StatusFileData(),
    )
    
    if not port_path.exists():
        result.message = f"Port directory not found: {port_path}"
        return result
    
    # Parse STATUS file
    status_path = port_path / "STATUS"
    result.status_data = parse_status_file(status_path)
    
    # Detect customizations
    result.customizations = detect_v1_customizations(port_path)
    
    port_type = result.status_data.port_type
    
    # Determine if we need to create a directory in migrated_ports/
    # Rules:
    # - PORT with customizations: yes
    # - PORT without customizations: no (just track in builds.json)
    # - MASK: yes (need overlay.toml with ignore)
    # - DPORT: yes (need newport/ and overlay.toml)
    # - LOCK: yes (need overlay.toml only, no content)
    
    needs_migration_dir = (
        port_type == PortType.MASK or
        port_type == PortType.DPORT or
        port_type == PortType.LOCK or
        (port_type == PortType.PORT and has_customizations(result.customizations))
    )
    
    if not needs_migration_dir:
        result.migrated = False
        result.message = "No customizations - tracking in builds.json only"
        return result
    
    # Create output directory
    output_path = output_base / str(origin)
    
    log.info(f"Migrating {origin} ({port_type.value})")
    
    if not dry_run:
        # Copy port contents (excluding STATUS)
        if port_type == PortType.LOCK:
            # LOCK ports only need overlay.toml, no content
            output_path.mkdir(parents=True, exist_ok=True)
        else:
            copy_port_directory(port_path, output_path)
        
        # Generate and write overlay.toml
        quarterly_overrides = list_quarterly_overrides(port_path) if port_type == PortType.PORT else []
        
        overlay_content = generate_overlay_toml(
            origin=origin,
            port_type=port_type,
            customizations=result.customizations,
            reason=result.status_data.reason,
            quarterly_overrides=quarterly_overrides,
        )
        
        overlay_path = output_path / "overlay.toml"
        overlay_path.write_text(overlay_content)
        log.debug(f"Created {overlay_path}")
    
    result.migrated = True
    result.message = f"Migrated as {port_type.value}"
    
    return result


def discover_all_ports(config: Config) -> Iterator[tuple[PortOrigin, Path]]:
    """
    Discover all ports in the v1 ports/ directory.
    
    Yields:
        Tuples of (origin, port_path) for all ports
    """
    ports_base = config.paths.delta / "ports"
    
    for category_dir in sorted(ports_base.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue
        
        for port_dir in sorted(category_dir.iterdir()):
            if not port_dir.is_dir() or port_dir.name.startswith("."):
                continue
            
            origin = PortOrigin(category_dir.name, port_dir.name)
            yield origin, port_dir


def generate_builds_json(
    results: list[PortMigrationResult],
    output_path: Path,
    dry_run: bool = False,
) -> None:
    """
    Generate builds.json from migration results.
    
    Args:
        results: List of migration results for all ports
        output_path: Path to write builds.json
        dry_run: If True, don't write file
    """
    log = get_logger(__name__)
    
    builds_data = {
        "version": 1,
        "migrated_from": "v1_status_files",
        "migrated_at": datetime.utcnow().isoformat() + "Z",
        "ports": {},
    }
    
    for result in results:
        port_key = str(result.origin)
        status = result.status_data
        
        port_data = {
            "type": status.port_type.value,
            "status": status.build_status,
        }
        
        if status.last_attempt:
            port_data["last_attempt"] = status.last_attempt
        if status.last_success:
            port_data["last_success"] = status.last_success
        if status.reason:
            port_data["reason"] = status.reason
        
        builds_data["ports"][port_key] = port_data
    
    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(builds_data, f, indent=2, sort_keys=False)
        log.info(f"Created {output_path} with {len(results)} ports")


def migrate_all_ports(
    config: Config,
    output_base: Path,
    state_output: Path,
    dry_run: bool = False,
) -> tuple[int, int, int, list[str]]:
    """
    Migrate all ports from v1 to v2 format.
    
    Args:
        config: DPorts configuration
        output_base: Base directory for migrated ports (e.g., migrated_ports/)
        state_output: Path for builds.json (e.g., state/builds.json)
        dry_run: If True, don't write files
        
    Returns:
        Tuple of (migrated_count, skipped_count, total_count, errors)
    """
    log = get_logger(__name__)
    
    results: list[PortMigrationResult] = []
    errors: list[str] = []
    migrated = 0
    skipped = 0
    
    for origin, port_path in discover_all_ports(config):
        try:
            result = migrate_port(config, origin, output_base, dry_run)
            results.append(result)
            
            if result.migrated:
                migrated += 1
            else:
                skipped += 1
                
        except Exception as e:
            errors.append(f"{origin}: {e}")
            log.error(f"Error migrating {origin}: {e}")
            # Create a minimal result for builds.json
            results.append(PortMigrationResult(
                origin=origin,
                status_data=parse_status_file(port_path / "STATUS"),
                migrated=False,
                message=f"Error: {e}",
            ))
    
    # Generate builds.json
    generate_builds_json(results, state_output, dry_run)
    
    total = len(results)
    log.info(f"Migration complete: {migrated} migrated, {skipped} skipped, {total} total, {len(errors)} errors")
    
    return migrated, skipped, total, errors


# Legacy function for backward compatibility
def discover_unmigrated_ports(config: Config) -> Iterator[tuple[PortOrigin, dict[str, bool]]]:
    """
    Find all ports that need migration (legacy function).
    
    Yields:
        Tuples of (origin, customizations) for unmigrated ports
    """
    for origin, port_path in discover_all_ports(config):
        # Skip if already has overlay.toml
        if (port_path / "overlay.toml").exists():
            continue
        
        # Check for customizations
        customizations = detect_v1_customizations(port_path)
        
        if has_customizations(customizations):
            yield origin, customizations
