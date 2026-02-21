"""Validation for DPorts v2 branch-scoped overlays."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dports.config import Config

from dports.models import PortOrigin, ValidationResult
from dports.overlay import Overlay, OverlayError
from dports.quarterly import validate_target
from dports.selection import overlay_candidates
from dports.utils import DPortsError


class ValidationError(DPortsError):
    """Validation error."""


def validate_port(
    config: Config,
    origin: PortOrigin,
    target: str,
) -> ValidationResult:
    """Validate one port overlay against a target branch."""
    normalized_target = validate_target(target)
    result = ValidationResult(origin=origin, valid=True)

    port_path = config.get_overlay_port_path(str(origin))
    if not port_path.exists():
        result.add_error(f"Overlay directory not found: {port_path}")
        return result

    try:
        overlay = Overlay(port_path, origin)
        if not overlay.exists():
            result.add_error("No overlay.toml found")
            return result

        result.checked_manifest = True
        overlay_result = overlay.validate(normalized_target)
        result.errors.extend(overlay_result.errors)
        result.warnings.extend(overlay_result.warnings)
        result.valid = overlay_result.valid
    except OverlayError as e:
        result.add_error(str(e))
        return result

    if result.valid and overlay.manifest.has_diffs:
        for diff_file in overlay.get_diffs_for_target(normalized_target):
            errors = validate_diff_file(diff_file)
            for error in errors:
                result.add_warning(f"{diff_file.relative_to(port_path)}: {error}")
        result.checked_diffs = True

    return result


def validate_diff_file(diff_path: Path) -> list[str]:
    """Validate a patch file has basic unified diff structure."""
    errors = []

    try:
        content = diff_path.read_text()
    except Exception as e:
        return [f"Cannot read file: {e}"]

    if not content.strip():
        return ["Empty diff file"]

    has_header = False
    for i, line in enumerate(content.split("\n"), start=1):
        if line.startswith("---") or line.startswith("+++"):
            has_header = True
        elif line.startswith("@@"):
            if not re.match(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@", line):
                errors.append(f"Line {i}: Invalid hunk header")

    if not has_header:
        errors.append("Missing unified diff header (--- / +++)")

    return errors


def validate_diff_applies(
    diff_path: Path,
    target_dir: Path,
    target_file: str | None = None,
) -> tuple[bool, str]:
    """Check if a diff applies cleanly (patch --dry-run)."""
    cmd = ["patch", "--dry-run", "-p0", "-i", str(diff_path)]

    if target_file:
        cmd.extend(["--", target_file])

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
        return False, result.stderr or result.stdout
    except subprocess.TimeoutExpired:
        return False, "Patch command timed out"
    except Exception as e:
        return False, str(e)


def validate_all_ports(
    config: Config,
    target: str,
) -> dict[str, ValidationResult]:
    """Validate all overlay candidates for a target branch."""

    normalized_target = validate_target(target)
    results: dict[str, ValidationResult] = {}
    for origin in overlay_candidates(config):
        result = validate_port(config, origin, normalized_target)
        results[str(origin)] = result

    return results
