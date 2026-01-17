"""Shared validation helpers for targets and policy flags."""

from __future__ import annotations

import re

TARGET_MAIN_OR_QUARTER_PATTERN = re.compile(r"^@(main|\d{4}Q[1-4])$")
TARGET_WITH_ANY_PATTERN = re.compile(r"^@(any|main|\d{4}Q[1-4])$")

ON_MISSING_VALUES = frozenset({"error", "warn", "noop"})


def is_compose_target(value: str) -> bool:
    """Return whether value is a valid compose target selector."""
    return TARGET_MAIN_OR_QUARTER_PATTERN.match(value) is not None


def is_scoped_target(value: str) -> bool:
    """Return whether value is a valid DSL/apply target selector."""
    return TARGET_WITH_ANY_PATTERN.match(value) is not None


def compose_target_branch(value: str) -> str | None:
    """Resolve compose target selector to git branch name."""
    if not is_compose_target(value):
        return None
    return value[1:]


def normalize_on_missing(value: str | None) -> str | None:
    """Normalize on-missing token, returning None when invalid."""
    if value is None:
        return None
    candidate = value.strip().lower()
    if candidate in ON_MISSING_VALUES:
        return candidate
    return None
