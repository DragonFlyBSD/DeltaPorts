"""Progress evaluation for migration completion thresholds."""

from __future__ import annotations

from typing import Any


def evaluate_completion(
    records: list[dict[str, Any]],
    *,
    conversion_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Evaluate completion thresholds for migration program."""
    result_map = {str(row.get("origin", "")): row for row in conversion_results or []}

    in_scope = [
        r
        for r in records
        if bool(r.get("legacy_overlay", False))
        or bool(r.get("has_overlay_dops", False))
    ]

    classified = [r for r in in_scope if str(r.get("bucket", "")).strip()]
    auto_safe = [r for r in in_scope if r.get("bucket") == "auto-safe"]

    auto_safe_converted = 0
    unaccounted: list[str] = []

    for record in in_scope:
        origin = str(record.get("origin", ""))
        bucket = str(record.get("bucket", "")).strip()
        has_dops = bool(record.get("has_overlay_dops", False))
        status = str(result_map.get(origin, {}).get("status", "")).strip()

        converted = has_dops or status == "converted"
        if bucket == "auto-safe" and converted:
            auto_safe_converted += 1

        if not converted and bucket not in {"fallback-only", "stale", "review-needed"}:
            unaccounted.append(origin)

    thresholds = {
        "all_in_scope_classified": len(classified) == len(in_scope),
        "all_auto_safe_converted": auto_safe_converted == len(auto_safe),
        "no_unaccounted_remaining": len(unaccounted) == 0,
    }

    classified_ratio = (len(classified) / len(in_scope)) if in_scope else 1.0
    auto_safe_ratio = (auto_safe_converted / len(auto_safe)) if auto_safe else 1.0
    accounted_ratio = (
        ((len(in_scope) - len(unaccounted)) / len(in_scope)) if in_scope else 1.0
    )

    return {
        "progress_version": "v1",
        "in_scope_total": len(in_scope),
        "classified_total": len(classified),
        "auto_safe_total": len(auto_safe),
        "auto_safe_converted": auto_safe_converted,
        "unaccounted_origins": sorted(unaccounted),
        "ratios": {
            "classification_coverage": classified_ratio,
            "auto_safe_conversion": auto_safe_ratio,
            "accounted_coverage": accounted_ratio,
        },
        "thresholds": thresholds,
        "operationally_complete": all(thresholds.values()),
    }
