"""Classification rules for dportsv3 migration inventory."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dportsv3.common.text import safe_read_text

_TARGET_LINE_RE = re.compile(r"^([A-Za-z0-9_.-]+):\s*$")
_ASSIGN_RE = re.compile(r"^([A-Z0-9_]+)\s*(\+?=|\?=|:=|!=)\s*(.*)$")


def _is_makefile_dragonfly_auto_safe(path: Path) -> tuple[bool, str]:
    text = safe_read_text(path)
    if not text.strip():
        return True, "empty_makefile_dragonfly"

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        i += 1

        if not line or line.startswith("#"):
            continue

        if (
            line.startswith(".if")
            or line.startswith(".elif")
            or line.startswith(".else")
        ):
            return False, "conditional_block_present"

        target_match = _TARGET_LINE_RE.match(line)
        if target_match:
            while i < len(lines) and (
                lines[i].startswith("\t") or not lines[i].strip()
            ):
                i += 1
            continue

        if _ASSIGN_RE.match(line):
            continue

        return False, "unsupported_line_pattern"

    return True, "supported_makefile_dragonfly_pattern"


def classify_inventory(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classify inventory into migration buckets."""
    classified: list[dict[str, Any]] = []

    for record in records:
        row = dict(record)
        reasons: list[str] = []
        bucket = "review-needed"

        if bool(row.get("stale", False)):
            bucket = "stale"
            reasons.append("stale_overlay")
        elif bool(row.get("has_diffs", False)):
            bucket = "fallback-only"
            reasons.append("raw_diffs_present")
        elif bool(row.get("has_newport", False)):
            bucket = "review-needed"
            reasons.append("newport_present")
        elif bool(row.get("has_makefile_dragonfly", False)):
            mk_path = Path(str(row.get("path", ""))) / "Makefile.DragonFly"
            safe, reason = _is_makefile_dragonfly_auto_safe(mk_path)
            if safe:
                bucket = "auto-safe"
            else:
                bucket = "review-needed"
            reasons.append(reason)
        elif bool(row.get("has_overlay_dops", False)):
            bucket = "auto-safe"
            reasons.append("already_dops")
        else:
            bucket = "fallback-only"
            reasons.append("unknown_overlay_shape")

        row["bucket"] = bucket
        row["classification_reasons"] = reasons
        classified.append(row)

    classified.sort(key=lambda r: r["origin"])
    return classified
