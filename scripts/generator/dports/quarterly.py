"""
Target branch utilities for DPorts v2.

v2 target model supports only:
- main
- YYYYQ[1-4]

The module name remains quarterly.py for compatibility, but semantics are
target-branch oriented.
"""

from __future__ import annotations

import re
from pathlib import Path

from dports.utils import DPortsError


TARGET_MAIN = "main"
QUARTERLY_PATTERN = re.compile(r"^(\d{4})Q([1-4])$")


class TargetError(DPortsError):
    """Invalid target branch specification."""


# Compatibility alias
QuarterlyError = TargetError


def normalize_target(value: str) -> str:
    """
    Normalize and validate a target identifier.

    Accepted inputs:
    - main (case-insensitive)
    - YYYYQ[1-4] (year + uppercase Q + quarter)
    """
    if value is None:
        raise TargetError("Target cannot be empty")

    candidate = value.strip()
    if not candidate:
        raise TargetError("Target cannot be empty")

    if candidate.lower() == TARGET_MAIN:
        return TARGET_MAIN

    quarterly = candidate.upper()
    if QUARTERLY_PATTERN.match(quarterly):
        return quarterly

    raise TargetError(
        f"Invalid target: {value!r} (expected 'main' or 'YYYYQ[1-4]' such as 2025Q2)"
    )


def validate_target(value: str) -> str:
    """Validate and return normalized target value."""
    return normalize_target(value)


def is_valid_target(value: str) -> bool:
    """Return True if value is a valid target branch identifier."""
    try:
        normalize_target(value)
        return True
    except TargetError:
        return False


def is_quarterly_target(value: str) -> bool:
    """Return True if target is a YYYYQ[1-4] quarterly target."""
    try:
        target = normalize_target(value)
    except TargetError:
        return False
    return target != TARGET_MAIN


def target_dirname(target: str) -> str:
    """Return overlay directory name for target (e.g. @main, @2025Q2)."""
    return f"@{validate_target(target)}"


def parse_target_dirname(name: str) -> str | None:
    """
    Parse directory name like @main or @2025Q2.

    Returns normalized target string, or None if invalid/not target directory.
    """
    if not name.startswith("@"):
        return None

    try:
        return normalize_target(name[1:])
    except TargetError:
        return None


def list_target_overrides(component_dir: Path) -> list[str]:
    """
    List normalized target names in a component directory.

    A component directory is typically one of:
    - diffs/
    - dragonfly/
    """
    if not component_dir.exists() or not component_dir.is_dir():
        return []

    targets: list[str] = []
    for entry in component_dir.iterdir():
        if not entry.is_dir() or not entry.name.startswith("@"):
            continue
        parsed = parse_target_dirname(entry.name)
        if parsed is not None:
            targets.append(parsed)

    return sorted(set(targets))


def find_invalid_target_dirs(component_dir: Path) -> list[str]:
    """Return @-prefixed directory names that are not valid targets."""
    if not component_dir.exists() or not component_dir.is_dir():
        return []

    invalid: list[str] = []
    for entry in component_dir.iterdir():
        if entry.is_dir() and entry.name.startswith("@"):
            if parse_target_dirname(entry.name) is None:
                invalid.append(entry.name)

    return sorted(invalid)


def get_target_diffs_dir(overlay_path: Path, target: str) -> Path | None:
    """Return diffs/@<target> path if it exists."""
    q_dir = overlay_path / "diffs" / target_dirname(target)
    return q_dir if q_dir.exists() else None


# Compatibility wrappers used by migration-era code paths
def validate_quarterly(value: str) -> str:
    return validate_target(value)


def is_valid_quarterly(value: str) -> bool:
    return is_valid_target(value)


def get_quarterly_diffs_dir(overlay_path: Path, quarterly: str) -> Path | None:
    return get_target_diffs_dir(overlay_path, quarterly)


def list_quarterly_overrides(overlay_path: Path) -> list[str]:
    return list_target_overrides(overlay_path / "diffs")
