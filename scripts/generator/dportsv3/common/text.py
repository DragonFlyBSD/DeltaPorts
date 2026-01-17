"""Shared text file helpers."""

from __future__ import annotations

from pathlib import Path


def safe_read_text(path: Path) -> str:
    """Read text file returning empty string on read failure."""
    try:
        return path.read_text()
    except OSError:
        return ""
