"""Shared aggregation helpers."""

from __future__ import annotations

from collections.abc import Iterable


def count_by(items: Iterable[str]) -> dict[str, int]:
    """Count occurrences and return sorted dictionary by key."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return dict(sorted(counts.items()))
