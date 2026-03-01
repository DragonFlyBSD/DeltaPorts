"""Shared IO helpers for dportsv3 commands and tools."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_text_file(path: Path) -> tuple[str | None, str | None]:
    """Read one text file with standardized user-facing errors."""
    if not path.exists():
        return None, f"Input file not found: {path}"
    if not path.is_file():
        return None, f"Input path is not a file: {path}"
    try:
        return path.read_text(), None
    except OSError as exc:
        return None, f"Failed to read input file: {path} ({exc})"


def read_lines_file(path: Path) -> tuple[list[str] | None, str | None]:
    """Read one text file as trimmed non-empty lines."""
    text, error = read_text_file(path)
    if error is not None:
        return None, error
    if text is None:
        return None, f"Failed to read input file: {path}"
    lines = [line.strip() for line in text.splitlines()]
    return [line for line in lines if line], None


def read_json_file(path: Path) -> tuple[Any | None, str | None]:
    """Read one JSON file with standardized user-facing errors."""
    text, error = read_text_file(path)
    if error is not None:
        return None, error
    if text is None:
        return None, f"Failed to read input file: {path}"
    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        return None, f"Invalid JSON in input file: {path} ({exc})"


def read_json_object(
    path: Path,
    *,
    object_label: str = "JSON",
) -> tuple[dict[str, Any] | None, str | None]:
    """Read one JSON object payload with standardized type checks."""
    payload, error = read_json_file(path)
    if error is not None:
        return None, error
    if not isinstance(payload, dict):
        return None, f"{object_label} must be an object"
    return payload, None


def read_json_list(
    path: Path,
    *,
    label: str,
    key_candidates: tuple[str, ...] = ("records", "classified", "results"),
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Read a list of objects from JSON payload or key candidates."""
    payload, error = read_json_file(path)
    if error is not None:
        return None, error

    if isinstance(payload, dict):
        for key in key_candidates:
            candidate = payload.get(key)
            if isinstance(candidate, list):
                payload = candidate
                break

    if not isinstance(payload, list):
        return None, f"{label} JSON must be a list of records"
    if not all(isinstance(item, dict) for item in payload):
        return None, f"{label} JSON list must contain only objects"
    return payload, None


def emit_json(payload: dict[str, Any], *, pretty: bool) -> None:
    """Emit JSON payload to stdout using stable key ordering."""
    if pretty:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return
    print(json.dumps(payload, sort_keys=True))


def write_json_file(path: Path, payload: dict[str, Any]) -> str | None:
    """Write one JSON object file and return error string on failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    except OSError as exc:
        return f"Failed to write output file: {path} ({exc})"
    return None
