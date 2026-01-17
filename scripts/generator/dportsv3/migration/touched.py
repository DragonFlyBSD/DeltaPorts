"""Helpers to derive touched origin sets from changed file paths."""

from __future__ import annotations


def extract_touched_origins(changed_paths: list[str]) -> list[str]:
    """Return sorted unique `category/name` origins under ports/ changes."""
    origins: set[str] = set()
    for raw in changed_paths:
        path = raw.strip().replace("\\", "/")
        if not path:
            continue
        while path.startswith("./"):
            path = path[2:]
        parts = path.split("/")
        if len(parts) < 3:
            continue
        if parts[0] != "ports":
            continue
        category = parts[1].strip()
        port = parts[2].strip()
        if not category or not port:
            continue
        if category.startswith(".") or port.startswith("."):
            continue
        origins.add(f"{category}/{port}")
    return sorted(origins)
