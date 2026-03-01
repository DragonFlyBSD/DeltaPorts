"""Compose reporting and summarization helpers."""

from __future__ import annotations

from typing import Any


def _diagnostic_code(message: str) -> str:
    if ":" not in message:
        return message.strip()
    return message.split(":", 1)[0].strip()


def _diagnostic_origin(message: str) -> str | None:
    if ": " not in message:
        return None
    rest = message.split(": ", 1)[1]
    if ":" not in rest:
        return None
    origin = rest.split(":", 1)[0].strip()
    if "/" not in origin:
        return None
    return origin


def _patch_name_from_compat_error(message: str) -> str | None:
    marker = "patch failed ("
    if marker not in message:
        return None
    tail = message.split(marker, 1)[1]
    if "):" not in tail:
        return None
    return tail.split("):", 1)[0].strip()


def build_compose_report_overview(
    payload: dict[str, Any], *, top: int = 10
) -> dict[str, Any]:
    from collections import Counter

    stages = payload.get("stages", [])
    ports = payload.get("ports", [])

    error_codes: Counter[str] = Counter()
    warning_codes: Counter[str] = Counter()
    error_origins: Counter[str] = Counter()
    patch_failures: Counter[str] = Counter()
    mode_counts: Counter[str] = Counter()
    stale_origins: set[str] = set()

    for stage in stages:
        stage_name = str(stage.get("name", ""))
        for item in list(stage.get("errors", [])):
            text = str(item)
            code = _diagnostic_code(text)
            error_codes[code] += 1
            origin = _diagnostic_origin(text)
            if origin is not None:
                error_origins[origin] += 1
            if code == "E_COMPOSE_STALE_OVERLAY" and origin is not None:
                stale_origins.add(origin)
            if stage_name == "apply_compat_ops":
                patch = _patch_name_from_compat_error(text)
                if patch is not None:
                    patch_failures[patch] += 1

        for item in list(stage.get("warnings", [])):
            text = str(item)
            code = _diagnostic_code(text)
            warning_codes[code] += 1
            if code == "I_COMPOSE_STALE_OVERLAY_PRUNE_CANDIDATE":
                origin = _diagnostic_origin(text)
                if origin is not None:
                    stale_origins.add(origin)

    for port in ports:
        mode = str(port.get("mode", ""))
        if mode:
            mode_counts[mode] += 1

    prune_stage = next(
        (stage for stage in stages if str(stage.get("name")) == "prune_stale_overlays"),
        None,
    )
    pruned = 0
    if isinstance(prune_stage, dict):
        delta_removed = list(prune_stage.get("metadata", {}).get("delta_removed", []))
        output_removed = list(prune_stage.get("metadata", {}).get("output_removed", []))
        pruned = len(set(str(item) for item in delta_removed + output_removed))

    hints: list[str] = []
    if error_codes.get("E_COMPOSE_STALE_OVERLAY", 0) > 0:
        hints.append("rerun with --prune-stale-overlays to auto-remove stale overlays")
    if error_codes.get("E_COMPOSE_COMPAT_FAILED", 0) > 0:
        hints.append("review apply_compat_ops failures by origin and patch name")

    return {
        "ok": bool(payload.get("ok", False)),
        "target": str(payload.get("target", "")),
        "output_path": str(payload.get("output_path", "")),
        "top_error_codes": [
            {"code": code, "count": count}
            for code, count in error_codes.most_common(top)
        ],
        "top_warning_codes": [
            {"code": code, "count": count}
            for code, count in warning_codes.most_common(top)
        ],
        "top_error_origins": [
            {"origin": origin, "count": count}
            for origin, count in error_origins.most_common(top)
        ],
        "top_failed_patches": [
            {"patch": patch, "count": count}
            for patch, count in patch_failures.most_common(top)
        ],
        "mode_counts": dict(sorted(mode_counts.items())),
        "stale": {
            "count": len(stale_origins),
            "origins": sorted(stale_origins)[:top],
            "pruned": pruned,
        },
        "hints": hints,
    }


def format_compose_overview(overview: dict[str, Any]) -> list[str]:
    lines: list[str] = []

    def _format_pairs(items: list[dict[str, Any]], key: str) -> str:
        return ", ".join(f"{row[key]}={row['count']}" for row in items)

    top_errors = list(overview.get("top_error_codes", []))
    if top_errors:
        lines.append(f"top_error_codes: {_format_pairs(top_errors, 'code')}")

    top_warnings = list(overview.get("top_warning_codes", []))
    if top_warnings:
        lines.append(f"top_warning_codes: {_format_pairs(top_warnings, 'code')}")

    top_origins = list(overview.get("top_error_origins", []))
    if top_origins:
        lines.append(
            "top_error_origins: "
            + ", ".join(f"{row['origin']}({row['count']})" for row in top_origins)
        )

    top_patches = list(overview.get("top_failed_patches", []))
    if top_patches:
        lines.append(
            "top_failed_patches: "
            + ", ".join(f"{row['patch']}({row['count']})" for row in top_patches)
        )

    stale = dict(overview.get("stale", {}))
    if stale.get("count", 0) > 0:
        lines.append(
            f"stale: count={stale.get('count', 0)} pruned={stale.get('pruned', 0)}"
        )

    mode_counts = dict(overview.get("mode_counts", {}))
    if mode_counts:
        lines.append(
            "modes: "
            + ", ".join(f"{key}={value}" for key, value in mode_counts.items())
        )

    for hint in list(overview.get("hints", [])):
        lines.append(f"hint: {hint}")

    return lines


def format_compose_result(result: Any) -> list[str]:
    """Render concise human-friendly compose summary."""
    lines = [
        (
            f"Compose {'succeeded' if result.ok else 'failed'} for "
            f"{result.target} -> {result.output_path}"
        )
    ]
    for stage in result.stages:
        state = "ok" if stage.success else "fail"
        lines.append(
            f"[{state}] {stage.name}: changed={stage.changed} skipped={stage.skipped} warnings={len(stage.warnings)} errors={len(stage.errors)}"
        )
    lines.append(
        (
            "summary: "
            f"ports={result.summary['port_total']} "
            f"ops={result.summary['total_ops']} "
            f"applied={result.summary['applied_ops']} "
            f"fallback={result.summary['fallback_patch_count']} "
            f"errors={result.summary['errors']}"
        )
    )
    overview = build_compose_report_overview(result.to_dict(), top=5)
    lines.extend(format_compose_overview(overview))
    return lines
