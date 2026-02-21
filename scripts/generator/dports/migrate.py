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
from dports.quarterly import list_quarterly_overrides, target_dirname, validate_target
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
        "extra_patches": (port_path / "files").exists()
        and any(
            f.name.startswith("extra-patch-")
            for f in (port_path / "files").iterdir()
            if f.is_file()
        )
        if (port_path / "files").exists()
        else False,
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
        port_type == PortType.MASK
        or port_type == PortType.DPORT
        or port_type == PortType.LOCK
        or (port_type == PortType.PORT and has_customizations(result.customizations))
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
        quarterly_overrides = (
            list_quarterly_overrides(port_path) if port_type == PortType.PORT else []
        )

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
            results.append(
                PortMigrationResult(
                    origin=origin,
                    status_data=parse_status_file(port_path / "STATUS"),
                    migrated=False,
                    message=f"Error: {e}",
                )
            )

    # Generate builds.json
    generate_builds_json(results, state_output, dry_run)

    total = len(results)
    log.info(
        f"Migration complete: {migrated} migrated, {skipped} skipped, {total} total, {len(errors)} errors"
    )

    return migrated, skipped, total, errors


# Legacy function for backward compatibility
def discover_unmigrated_ports(
    config: Config,
) -> Iterator[tuple[PortOrigin, dict[str, bool]]]:
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


# =============================================================================
# Branch-Scoped Layout Migration (v2)
# =============================================================================


@dataclass
class TargetMigrationResult:
    """Result of migrating one port to strict @target layout."""

    origin: PortOrigin
    changed: bool = False
    actions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def generate_overlay_toml_v2(
    origin: PortOrigin,
    reason: str = "",
    port_type: PortType = PortType.PORT,
    components: dict[str, bool] | None = None,
    ignore_reason: str = "",
) -> str:
    """Generate branch-scoped v2 overlay.toml content."""
    comps = {
        "makefile_dragonfly": False,
        "diffs": False,
        "dragonfly_dir": False,
        "extra_patches": False,
        "newport": False,
    }
    if components:
        comps.update({k: bool(v) for k, v in components.items()})

    safe_reason = (reason or f"DragonFly customizations for {origin}").replace(
        '"', '\\"'
    )
    lines = [f"# DPorts v2 overlay manifest for {origin}", "", "[overlay]"]
    lines.append(f'reason = "{safe_reason}"')
    if port_type != PortType.PORT:
        lines.append(f'type = "{port_type.value}"')
    lines.append("")

    if port_type == PortType.MASK:
        lines.append("[status]")
        mask_reason = (ignore_reason or reason or "masked").replace('"', '\\"')
        lines.append(f'ignore = "{mask_reason}"')
        lines.append("")

    lines.append("[components]")
    lines.append(
        f"makefile_dragonfly = {'true' if comps['makefile_dragonfly'] else 'false'}"
    )
    lines.append(f"diffs = {'true' if comps['diffs'] else 'false'}")
    lines.append(f"dragonfly_dir = {'true' if comps['dragonfly_dir'] else 'false'}")
    lines.append(f"extra_patches = {'true' if comps['extra_patches'] else 'false'}")
    lines.append(f"newport = {'true' if comps['newport'] else 'false'}")
    lines.append("")

    return "\n".join(lines)


def _same_file_content(a: Path, b: Path) -> bool:
    if not a.exists() or not b.exists() or not a.is_file() or not b.is_file():
        return False
    return a.read_bytes() == b.read_bytes()


def _move_file_with_collision(
    src: Path,
    dst: Path,
    dry_run: bool,
    actions: list[str],
    errors: list[str],
) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)

    if dst.exists():
        if dst.is_file() and _same_file_content(src, dst):
            actions.append(f"drop-duplicate:{src}")
            if not dry_run:
                src.unlink()
            return
        errors.append(
            f"Collision: {src} -> {dst} (destination exists with different content)"
        )
        return

    actions.append(f"move:{src} -> {dst}")
    if not dry_run:
        src.rename(dst)


def _merge_dir_into_target(
    src_dir: Path,
    dst_dir: Path,
    dry_run: bool,
    actions: list[str],
    errors: list[str],
) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for child in sorted(src_dir.iterdir()):
        target_child = dst_dir / child.name
        if child.is_dir():
            _merge_dir_into_target(child, target_child, dry_run, actions, errors)
            if not dry_run:
                try:
                    child.rmdir()
                except OSError:
                    pass
        else:
            _move_file_with_collision(child, target_child, dry_run, actions, errors)

    if not dry_run:
        try:
            src_dir.rmdir()
        except OSError:
            pass


def _detect_components(port_path: Path) -> dict[str, bool]:
    return {
        "makefile_dragonfly": any(port_path.glob("Makefile.DragonFly.@*")),
        "diffs": bool(list_quarterly_overrides(port_path)),
        "dragonfly_dir": bool(
            [
                d
                for d in (port_path / "dragonfly").iterdir()
                if d.is_dir() and d.name.startswith("@")
            ]
        )
        if (port_path / "dragonfly").is_dir()
        else False,
        "extra_patches": (port_path / "files").is_dir()
        and any(
            f.name.startswith("extra-patch-")
            for f in (port_path / "files").iterdir()
            if f.is_file()
        ),
        "newport": (port_path / "newport").is_dir(),
    }


def _normalize_overlay_manifest(
    port_path: Path,
    origin: PortOrigin,
    components: dict[str, bool],
    dry_run: bool,
    errors: list[str],
    actions: list[str],
) -> None:
    overlay_path = port_path / "overlay.toml"
    status_path = port_path / "STATUS"
    existing_text: str | None = None

    reason = f"DragonFly customizations for {origin}"
    port_type = PortType.PORT
    ignore_reason = ""

    status_data = parse_status_file(status_path) if status_path.exists() else None

    if overlay_path.exists():
        existing_text = overlay_path.read_text()
        try:
            parsed = tomllib.loads(existing_text)
            overlay_section = (
                parsed.get("overlay", {}) if isinstance(parsed, dict) else {}
            )
            status_section = (
                parsed.get("status", {}) if isinstance(parsed, dict) else {}
            )
            reason = overlay_section.get("reason", reason)

            type_raw = overlay_section.get("type", parsed.get("type", "port"))
            try:
                port_type = PortType(str(type_raw).lower())
            except Exception:
                port_type = PortType.PORT

            ignore_reason = status_section.get(
                "ignore", parsed.get("ignore_reason", "")
            )
            existing_components = (
                parsed.get("components", {}) if isinstance(parsed, dict) else {}
            )
            for key in list(components):
                components[key] = bool(
                    components[key]
                    or existing_components.get(key, False)
                    or parsed.get(key, False)
                )
        except Exception as e:
            errors.append(f"Failed to parse existing overlay.toml: {e}")
            return

    # STATUS is explicit source of type/reason when present in source tree.
    if status_data is not None:
        port_type = status_data.port_type

        if status_data.port_type == PortType.MASK and status_data.reason:
            if reason.startswith("DragonFly customizations for "):
                reason = status_data.reason
            if not ignore_reason:
                ignore_reason = status_data.reason

        if status_data.port_type == PortType.DPORT:
            components["newport"] = True

    content = generate_overlay_toml_v2(
        origin=origin,
        reason=reason,
        port_type=port_type,
        components=components,
        ignore_reason=ignore_reason,
    )

    if existing_text is not None and existing_text.strip() == content.strip():
        return

    actions.append(f"write:{overlay_path}")
    if not dry_run:
        overlay_path.write_text(content)


def migrate_port_layout_to_target(
    config: Config,
    origin: PortOrigin,
    target: str,
    dry_run: bool = False,
    delta_base: Path | None = None,
) -> TargetMigrationResult:
    """Migrate one overlay directory from legacy root layout to @target layout."""
    normalized_target = validate_target(target)
    result = TargetMigrationResult(origin=origin)

    base = delta_base or config.paths.delta
    port_path = base / "ports" / str(origin)
    if not port_path.exists() or not port_path.is_dir():
        result.errors.append(f"Port overlay path not found: {port_path}")
        return result

    target_suffix = target_dirname(normalized_target)

    # Makefile.DragonFly -> Makefile.DragonFly.@target
    root_mk = port_path / "Makefile.DragonFly"
    target_mk = port_path / f"Makefile.DragonFly.{target_suffix}"
    if root_mk.exists():
        _move_file_with_collision(
            root_mk, target_mk, dry_run, result.actions, result.errors
        )

    # diffs/*.diff|*.patch -> diffs/@target/
    diffs_dir = port_path / "diffs"
    if diffs_dir.is_dir():
        target_diffs = diffs_dir / target_suffix
        for entry in sorted(diffs_dir.iterdir()):
            if entry.is_file() and entry.suffix in {".diff", ".patch"}:
                _move_file_with_collision(
                    entry,
                    target_diffs / entry.name,
                    dry_run,
                    result.actions,
                    result.errors,
                )

    # dragonfly root content -> dragonfly/@target/
    dragonfly_dir = port_path / "dragonfly"
    if dragonfly_dir.is_dir():
        target_dragonfly = dragonfly_dir / target_suffix
        for entry in sorted(dragonfly_dir.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir() and entry.name.startswith("@"):
                continue

            dst = target_dragonfly / entry.name
            if entry.is_dir():
                if dst.exists() and dst.is_dir():
                    _merge_dir_into_target(
                        entry, dst, dry_run, result.actions, result.errors
                    )
                elif dst.exists():
                    result.errors.append(f"Collision: {entry} -> {dst}")
                else:
                    result.actions.append(f"move:{entry} -> {dst}")
                    if not dry_run:
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        entry.rename(dst)
            else:
                _move_file_with_collision(
                    entry, dst, dry_run, result.actions, result.errors
                )

    # Normalize manifest for strict v2 components
    components = _detect_components(port_path)
    _normalize_overlay_manifest(
        port_path,
        origin,
        components,
        dry_run,
        result.errors,
        result.actions,
    )

    result.changed = bool(result.actions)
    return result


def migrate_special_diffs_to_target(
    config: Config,
    target: str,
    dry_run: bool = False,
    delta_base: Path | None = None,
) -> tuple[int, list[str]]:
    """Migrate special/*/diffs root patches to diffs/@target."""
    normalized_target = validate_target(target)
    suffix = target_dirname(normalized_target)
    moved = 0
    errors: list[str] = []

    base = delta_base or config.paths.delta

    for comp in ("Mk", "Templates", "treetop"):
        diffs = base / "special" / comp / "diffs"
        if not diffs.is_dir():
            continue

        target_dir = diffs / suffix
        for f in sorted(diffs.iterdir()):
            if not f.is_file() or f.suffix not in {".diff", ".patch"}:
                continue

            dst = target_dir / f.name
            if dst.exists():
                if dst.is_file() and _same_file_content(f, dst):
                    if not dry_run:
                        f.unlink()
                    moved += 1
                    continue
                errors.append(f"special/{comp}: collision for {f.name}")
                continue

            if not dry_run:
                target_dir.mkdir(parents=True, exist_ok=True)
                f.rename(dst)
            moved += 1

    return moved, errors


def migrate_all_layouts_to_target(
    config: Config,
    target: str,
    dry_run: bool = False,
    delta_base: Path | None = None,
) -> tuple[int, int, list[str]]:
    """Migrate all candidate overlay ports to strict @target layout in-place."""
    from dports.selection import overlay_candidates_from_base

    migrated = 0
    unchanged = 0
    errors: list[str] = []
    base = delta_base or config.paths.delta
    ports_base = base / "ports"

    for origin in overlay_candidates_from_base(ports_base):
        result = migrate_port_layout_to_target(
            config,
            origin,
            target,
            dry_run=dry_run,
            delta_base=base,
        )
        if result.errors:
            errors.extend([f"{origin}: {e}" for e in result.errors])
        if result.changed:
            migrated += 1
        else:
            unchanged += 1

    return migrated, unchanged, errors


def default_output_tree_path(delta_base: Path, target: str) -> Path:
    """Return default destination tree path for out-of-place migration."""
    normalized = validate_target(target)
    return delta_base.parent / f"{delta_base.name}-migrated-{normalized}"


def prepare_output_tree(
    source_base: Path,
    output_base: Path,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Create an output tree for migration by copying ports/ and special/.

    Returns:
        (ok, message)
    """
    if output_base.resolve() == source_base.resolve():
        return False, "Output path cannot be the same as source path"

    if output_base.exists() and any(output_base.iterdir()):
        return False, f"Output directory is not empty: {output_base}"

    if dry_run:
        return True, f"Would create output tree at {output_base}"

    output_base.mkdir(parents=True, exist_ok=True)

    for name in ("ports", "special"):
        src = source_base / name
        if src.exists() and src.is_dir():
            shutil.copytree(src, output_base / name, dirs_exist_ok=False)

    return True, f"Created output tree at {output_base}"


def cleanup_status_only_dirs(
    delta_base: Path,
    dry_run: bool = False,
) -> int:
    """
    Remove overlay dirs under ports/ that contain only STATUS.

    Returns:
        Number of removed port directories.
    """
    removed = 0
    ports_base = delta_base / "ports"
    if not ports_base.exists() or not ports_base.is_dir():
        return 0

    for category_dir in sorted(ports_base.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue

        for port_dir in sorted(category_dir.iterdir()):
            if not port_dir.is_dir() or port_dir.name.startswith("."):
                continue

            entries = [e for e in port_dir.iterdir() if not e.name.startswith(".")]
            if (
                len(entries) == 1
                and entries[0].is_file()
                and entries[0].name == "STATUS"
            ):
                removed += 1
                if not dry_run:
                    shutil.rmtree(port_dir)

        if not dry_run:
            try:
                if not any(category_dir.iterdir()):
                    category_dir.rmdir()
            except OSError:
                pass

    return removed


def generate_builds_json_from_status(
    delta_base: Path,
    target: str,
    output_path: Path,
    dry_run: bool = False,
) -> int:
    """
    Consolidate STATUS files into BuildState-compatible builds.json format.

    Returns:
        Number of ports written.
    """
    normalized_target = validate_target(target)
    ports_base = delta_base / "ports"
    if not ports_base.exists() or not ports_base.is_dir():
        if dry_run:
            return 0
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {"version": 1, "updated": datetime.now().isoformat(), "ports": []},
                indent=2,
            )
        )
        return 0

    entries: list[dict[str, object]] = []

    for category_dir in sorted(ports_base.iterdir()):
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue
        for port_dir in sorted(category_dir.iterdir()):
            if not port_dir.is_dir() or port_dir.name.startswith("."):
                continue

            origin = PortOrigin(category=category_dir.name, name=port_dir.name)
            status = parse_status_file(port_dir / "STATUS")

            if status.port_type == PortType.MASK:
                build_status = "skipped"
            else:
                build_status = status.build_status

            version = status.last_attempt or status.last_success or ""

            entries.append(
                {
                    "origin": str(origin),
                    "status": build_status,
                    "last_build": None,
                    "last_success": None,
                    "version": version,
                    "target": normalized_target,
                    "notes": status.reason or "",
                }
            )

    data = {
        "version": 1,
        "updated": datetime.now().isoformat(),
        "ports": entries,
    }

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(data, f, indent=2)

    return len(entries)
