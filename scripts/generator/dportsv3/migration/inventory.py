"""Inventory scanning for dportsv3 migration program."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dportsv3.common.text import safe_read_text

_TARGET_DIR_RE = re.compile(r"^@(?:any|main|\d{4}Q[1-4])$")
_TARGET_LINE_RE = re.compile(r"^([A-Za-z0-9_.-]+):\s*$")


def _extract_targets(port_dir: Path) -> list[str]:
    targets: set[str] = set()
    for path in port_dir.rglob("*"):
        name = path.name
        if path.is_dir() and _TARGET_DIR_RE.match(name):
            targets.add(name)
    return sorted(targets)


def _complexity_signals(
    port_dir: Path, has_makefile_dragonfly: bool
) -> tuple[list[str], int]:
    signals: set[str] = set()
    churn = 0

    if has_makefile_dragonfly:
        mk_path = port_dir / "Makefile.DragonFly"
        text = safe_read_text(mk_path)
        lines = [line.rstrip("\n") for line in text.splitlines()]
        churn += len(lines)
        if any(line.strip().startswith(".if") for line in lines):
            signals.add("conditional")
        if any(_TARGET_LINE_RE.match(line.strip()) for line in lines):
            signals.add("target_recipe")
        if any("+=" in line for line in lines):
            signals.add("token_add")

    diffs_dir = port_dir / "diffs"
    if diffs_dir.exists():
        signals.add("raw_diffs")
        churn += sum(1 for _ in diffs_dir.rglob("*") if _.is_file())

    newport_dir = port_dir / "newport"
    if newport_dir.exists():
        signals.add("newport")
        churn += sum(1 for _ in newport_dir.rglob("*") if _.is_file())

    if (port_dir / "overlay.dops").exists():
        signals.add("dops_present")

    return sorted(signals), churn


def scan_inventory(root: Path) -> list[dict[str, Any]]:
    """Scan ports tree and build migration inventory records."""
    ports_root = root / "ports"
    if not ports_root.exists() or not ports_root.is_dir():
        raise ValueError(f"ports root not found: {ports_root}")

    records: list[dict[str, Any]] = []

    for category_dir in sorted([p for p in ports_root.iterdir() if p.is_dir()]):
        category = category_dir.name
        for port_dir in sorted([p for p in category_dir.iterdir() if p.is_dir()]):
            origin = f"{category}/{port_dir.name}"

            has_makefile_dragonfly = (port_dir / "Makefile.DragonFly").exists()
            has_diffs = (port_dir / "diffs").exists()
            has_newport = (port_dir / "newport").exists()
            has_overlay_dops = (port_dir / "overlay.dops").exists()

            legacy_overlay = has_makefile_dragonfly or has_diffs or has_newport
            if not legacy_overlay and not has_overlay_dops:
                continue

            signals, churn = _complexity_signals(port_dir, has_makefile_dragonfly)
            explicit_targets = [
                target for target in _extract_targets(port_dir) if target != "@any"
            ]
            baseline_capable = not explicit_targets
            target_mode = "baseline" if baseline_capable else "explicit"
            available_targets = sorted(
                set(explicit_targets + (["@any"] if baseline_capable else []))
            )

            records.append(
                {
                    "origin": origin,
                    "category": category,
                    "path": str(port_dir),
                    "has_makefile_dragonfly": has_makefile_dragonfly,
                    "has_diffs": has_diffs,
                    "has_newport": has_newport,
                    "has_overlay_dops": has_overlay_dops,
                    "legacy_overlay": legacy_overlay,
                    "targets": available_targets,
                    "target_mode": target_mode,
                    "available_targets": available_targets,
                    "complexity_signals": signals,
                    "churn": churn,
                    "stale": False,
                }
            )

    records.sort(key=lambda r: r["origin"])
    return records
