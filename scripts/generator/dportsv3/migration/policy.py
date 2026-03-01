"""Forward policy checks for migration program."""

from __future__ import annotations

from typing import Any


def evaluate_forward_policy(
    records: list[dict[str, Any]],
    *,
    touched_origins: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate dops-first policy for classified records."""
    global_violations: list[dict[str, str]] = []
    touched_violations: list[dict[str, str]] = []
    by_origin = {str(r.get("origin", "")): r for r in records}

    for record in records:
        origin = str(record.get("origin", ""))
        if not origin:
            continue

        bucket = str(record.get("bucket", "")).strip()
        has_overlay_dops = bool(record.get("has_overlay_dops", False))
        legacy_overlay = bool(record.get("legacy_overlay", False))

        if legacy_overlay and not bucket:
            global_violations.append(
                {
                    "origin": origin,
                    "type": "unclassified_legacy_overlay",
                    "message": "legacy overlay record is not classified",
                }
            )

        if (
            legacy_overlay
            and not has_overlay_dops
            and bucket not in {"fallback-only", "stale", "review-needed"}
        ):
            global_violations.append(
                {
                    "origin": origin,
                    "type": "legacy_without_dops_policy_exception",
                    "message": "legacy overlay is missing dops and has no fallback/review classification",
                }
            )

    for origin in sorted(set(touched_origins or [])):
        record = by_origin.get(origin)
        if record is None:
            touched_violations.append(
                {
                    "origin": origin,
                    "type": "touched_origin_missing_inventory",
                    "message": "touched origin not found in inventory",
                }
            )
            continue

        bucket = str(record.get("bucket", "")).strip()
        has_overlay_dops = bool(record.get("has_overlay_dops", False))
        legacy_overlay = bool(record.get("legacy_overlay", False))
        if legacy_overlay and not has_overlay_dops and bucket != "fallback-only":
            touched_violations.append(
                {
                    "origin": origin,
                    "type": "touched_origin_not_dops_first",
                    "message": "touched legacy overlay is not dops-first and not marked fallback-only",
                }
            )

    violations = [*global_violations, *touched_violations]

    by_type: dict[str, int] = {}
    for row in violations:
        key = row["type"]
        by_type[key] = by_type.get(key, 0) + 1

    return {
        "policy_version": "v1",
        "pass": len(violations) == 0,
        "violation_count": len(violations),
        "violations": violations,
        "global_violations": global_violations,
        "touched_violations": touched_violations,
        "summary": {
            "global_violation_count": len(global_violations),
            "touched_violation_count": len(touched_violations),
            "by_type": dict(sorted(by_type.items())),
        },
    }
