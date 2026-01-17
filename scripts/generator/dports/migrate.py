"""
Migration tools for DPorts v1 to v2.

Provides tools to migrate existing port customizations from v1 format
(implicit detection) to v2 format (explicit overlay.toml manifests).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterator
    from dports.config import Config

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from dports.models import PortOrigin, OverlayManifest
from dports.quarterly import list_quarterly_overrides
from dports.utils import DPortsError, get_logger


class MigrationError(DPortsError):
    """Error during migration."""
    pass


def detect_v1_customizations(port_path: Path) -> dict[str, bool]:
    """
    Detect v1-style customizations in a port directory.
    
    Args:
        port_path: Path to the port overlay directory
        
    Returns:
        Dict with detected customization types
    """
    return {
        "makefile_dragonfly": (port_path / "Makefile.DragonFly").exists(),
        "diffs": (port_path / "diffs").exists() and any((port_path / "diffs").iterdir()),
        "dragonfly_dir": (port_path / "dragonfly").exists() and any((port_path / "dragonfly").iterdir()),
        "extra_patches": (port_path / "files").exists() and any(
            f.name.startswith("extra-patch-") for f in (port_path / "files").iterdir() if f.is_file()
        ),
    }


def generate_overlay_toml(
    origin: PortOrigin,
    customizations: dict[str, bool],
    quarterly_overrides: list[str] | None = None,
    description: str = "",
) -> str:
    """
    Generate overlay.toml content for a port.
    
    Args:
        origin: Port origin
        customizations: Dict of detected customizations
        quarterly_overrides: List of quarterly-specific overrides
        description: Optional description
        
    Returns:
        TOML content as string
    """
    lines = [
        "# DPorts v2 overlay manifest",
        f"# Port: {origin}",
        "",
    ]
    
    if description:
        lines.append(f'description = "{description}"')
        lines.append("")
    
    # Add customization flags
    lines.append("# Customization types present")
    
    if customizations.get("makefile_dragonfly"):
        lines.append("makefile_dragonfly = true")
    
    if customizations.get("diffs"):
        lines.append("diffs = true")
    
    if customizations.get("dragonfly_dir"):
        lines.append("dragonfly_dir = true")
    
    if customizations.get("extra_patches"):
        lines.append("extra_patches = true")
    
    # Add quarterly overrides if any
    if quarterly_overrides:
        lines.append("")
        lines.append("# Quarterly-specific patch directories")
        overrides_str = ", ".join(f'"{q}"' for q in sorted(quarterly_overrides))
        lines.append(f"quarterly_overrides = [{overrides_str}]")
    
    lines.append("")
    return "\n".join(lines)


def migrate_port(
    config: Config,
    origin: PortOrigin,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Migrate a single port from v1 to v2 format.
    
    Args:
        config: DPorts configuration
        origin: Port to migrate
        dry_run: If True, don't write files
        
    Returns:
        Tuple of (success, message)
    """
    log = get_logger(__name__)
    port_path = config.get_overlay_port_path(str(origin))
    
    if not port_path.exists():
        return False, f"Port directory not found: {port_path}"
    
    # Check if already migrated
    overlay_toml = port_path / "overlay.toml"
    if overlay_toml.exists():
        return True, "Already migrated (overlay.toml exists)"
    
    # Detect customizations
    customizations = detect_v1_customizations(port_path)
    
    # Check if there's anything to migrate
    if not any(customizations.values()):
        return False, "No customizations detected"
    
    # Detect quarterly overrides
    quarterly_overrides = list_quarterly_overrides(port_path)
    
    # Generate overlay.toml
    content = generate_overlay_toml(
        origin,
        customizations,
        quarterly_overrides,
    )
    
    log.info(f"Migrating {origin}")
    log.debug(f"Customizations: {customizations}")
    log.debug(f"Quarterly overrides: {quarterly_overrides}")
    
    if not dry_run:
        overlay_toml.write_text(content)
        log.info(f"Created {overlay_toml}")
    
    return True, f"Created overlay.toml with {sum(customizations.values())} customization types"


def discover_unmigrated_ports(config: Config) -> Iterator[tuple[PortOrigin, dict[str, bool]]]:
    """
    Find all ports that need migration.
    
    Yields:
        Tuples of (origin, customizations) for unmigrated ports
    """
    ports_base = config.paths.delta / "ports"
    
    for category_dir in ports_base.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue
        
        for port_dir in category_dir.iterdir():
            if not port_dir.is_dir() or port_dir.name.startswith("."):
                continue
            
            # Skip if already migrated
            if (port_dir / "overlay.toml").exists():
                continue
            
            # Check for customizations
            customizations = detect_v1_customizations(port_dir)
            
            if any(customizations.values()):
                origin = PortOrigin(category_dir.name, port_dir.name)
                yield origin, customizations


def migrate_all_ports(
    config: Config,
    dry_run: bool = False,
) -> tuple[int, int, list[str]]:
    """
    Migrate all ports from v1 to v2 format.
    
    Args:
        config: DPorts configuration
        dry_run: If True, don't write files
        
    Returns:
        Tuple of (success_count, skip_count, errors)
    """
    log = get_logger(__name__)
    success = 0
    skipped = 0
    errors = []
    
    for origin, customizations in discover_unmigrated_ports(config):
        try:
            ok, msg = migrate_port(config, origin, dry_run)
            if ok:
                success += 1
            else:
                skipped += 1
                log.debug(f"Skipped {origin}: {msg}")
        except Exception as e:
            errors.append(f"{origin}: {e}")
            log.error(f"Error migrating {origin}: {e}")
    
    log.info(f"Migration complete: {success} migrated, {skipped} skipped, {len(errors)} errors")
    return success, skipped, errors
