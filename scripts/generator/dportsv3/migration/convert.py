"""Converter MVP for legacy overlay artifacts to overlay.dops."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dportsv3.engine.api import build_plan, check_dsl, parse_dsl

_ASSIGN_RE = re.compile(r"^([A-Z0-9_]+)\s*(\+?=|\?=|:=|!=)\s*(.*)$")
_TARGET_LINE_RE = re.compile(r"^([A-Za-z0-9_.-]+):\s*$")


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\t", "\\t").replace("\n", "\\n")
    return f'"{escaped}"'


def _parse_makefile_dragonfly(path: Path) -> tuple[list[str], list[str]]:
    """Return generated dops ops and unsupported reasons."""
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        return [], [f"read_error:{exc}"]

    ops: list[str] = []
    errors: list[str] = []
    i = 0
    heredoc_index = 0

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
            errors.append("conditional_block_present")
            continue

        assign = _ASSIGN_RE.match(line)
        if assign:
            name = assign.group(1)
            op = assign.group(2)
            value = assign.group(3).strip()
            if op in {"=", "?=", ":=", "!="}:
                ops.append(f"mk set {name} {_quote(value)}")
            elif op == "+=":
                if value:
                    token = value if re.match(r"^[^\s]+$", value) else _quote(value)
                    ops.append(f"mk add {name} {token}")
            else:
                errors.append(f"unsupported_assignment_op:{op}")
            continue

        target = _TARGET_LINE_RE.match(line)
        if target:
            target_name = target.group(1)
            recipe: list[str] = []
            while i < len(lines) and (
                lines[i].startswith("\t") or not lines[i].strip()
            ):
                recipe.append(lines[i])
                i += 1
            heredoc_index += 1
            tag = f"MK{heredoc_index}"
            ops.append(f"mk target set {target_name} <<'{tag}'")
            ops.extend(recipe)
            ops.append(tag)
            continue

        errors.append(f"unsupported_line:{line}")

    return ops, errors


def _render_dops(origin: str, ops: list[str]) -> str:
    header = [
        "target @main",
        f"port {origin}",
        "type port",
        'reason "auto-converted from Makefile.DragonFly"',
        "",
    ]
    return "\n".join(header + ops + [""])


def convert_record(
    record: dict[str, Any],
    *,
    repo_root: Path,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Attempt conversion for one classified record."""
    origin = str(record.get("origin", ""))
    port_path = repo_root / "ports" / origin
    dops_path = port_path / "overlay.dops"
    bucket = str(record.get("bucket", ""))

    result: dict[str, Any] = {
        "origin": origin,
        "bucket": bucket,
        "status": "blocked",
        "parse_ok": False,
        "check_ok": False,
        "plan_ok": False,
        "deterministic_ok": False,
        "classified": bool(bucket),
        "errors": [],
        "dry_run": dry_run,
        "output_path": str(dops_path),
    }

    if bucket == "stale":
        result["status"] = "stale"
        return result
    if bucket == "fallback-only":
        result["status"] = "fallback"
        return result
    if bucket != "auto-safe":
        result["status"] = "blocked"
        result["errors"].append("bucket_not_auto_safe")
        return result

    if dops_path.exists():
        source = dops_path.read_text()
        parsed = parse_dsl(source, dops_path)
        checked = check_dsl(source, dops_path)
        planned = build_plan(source, dops_path)
        planned_again = build_plan(source, dops_path)
        planned_dict = planned.plan.to_dict() if planned.plan is not None else None
        planned_again_dict = (
            planned_again.plan.to_dict() if planned_again.plan is not None else None
        )
        result["parse_ok"] = parsed.ok
        result["check_ok"] = checked.ok
        result["plan_ok"] = planned.ok
        result["deterministic_ok"] = (
            planned.ok
            and planned_again.ok
            and planned_dict is not None
            and planned_again_dict is not None
            and planned_dict == planned_again_dict
        )
        result["status"] = "converted"
        result["errors"] = [
            d.code
            for d in parsed.diagnostics + checked.diagnostics + planned.diagnostics
        ]
        return result

    mk_path = port_path / "Makefile.DragonFly"
    if not mk_path.exists():
        result["status"] = "blocked"
        result["errors"].append("missing_makefile_dragonfly")
        return result

    ops, conversion_errors = _parse_makefile_dragonfly(mk_path)
    if conversion_errors:
        result["status"] = "blocked"
        result["errors"] = conversion_errors
        return result

    source = _render_dops(origin, ops)
    parsed = parse_dsl(source, dops_path)
    checked = check_dsl(source, dops_path)
    planned = build_plan(source, dops_path)
    planned_again = build_plan(source, dops_path)
    planned_dict = planned.plan.to_dict() if planned.plan is not None else None
    planned_again_dict = (
        planned_again.plan.to_dict() if planned_again.plan is not None else None
    )

    result["parse_ok"] = parsed.ok
    result["check_ok"] = checked.ok
    result["plan_ok"] = planned.ok
    result["deterministic_ok"] = (
        planned.ok
        and planned_again.ok
        and planned_dict is not None
        and planned_again_dict is not None
        and planned_dict == planned_again_dict
    )
    result["errors"] = [
        d.code for d in parsed.diagnostics + checked.diagnostics + planned.diagnostics
    ]

    if (
        result["parse_ok"]
        and result["check_ok"]
        and result["plan_ok"]
        and result["deterministic_ok"]
    ):
        result["status"] = "converted"
        if not dry_run:
            dops_path.write_text(source)
    else:
        result["status"] = "failed"

    return result
