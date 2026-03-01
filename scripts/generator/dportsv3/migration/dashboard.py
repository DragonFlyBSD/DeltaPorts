"""Migration dashboard aggregation for CI policy enforcement."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from dportsv3.common.metrics import count_by
from dportsv3.migration.models import primary_target, record_category
from dportsv3.migration.policy import evaluate_forward_policy
from dportsv3.migration.progress import evaluate_completion


def _classification_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: list[str] = []
    targets: list[str] = []
    categories: list[str] = []
    legacy_unclassified = 0

    for row in records:
        bucket = str(row.get("bucket", "")).strip()
        target = primary_target(row)
        category = record_category(row)

        if bucket:
            buckets.append(bucket)
        if target:
            targets.append(target)
        if category:
            categories.append(category)

        if bool(row.get("legacy_overlay", False)) and not bucket:
            legacy_unclassified += 1

    return {
        "record_total": len(records),
        "legacy_unclassified": legacy_unclassified,
        "by_bucket": count_by(buckets),
        "by_target": count_by(targets),
        "by_category": count_by(categories),
    }


def build_migration_dashboard(
    classified_records: list[dict[str, Any]],
    *,
    conversion_results: list[dict[str, Any]] | None = None,
    touched_origins: list[str] | None = None,
    strict_policy: bool = True,
    strict_progress: bool = False,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a stable migration dashboard payload for CI artifacts."""
    policy = evaluate_forward_policy(
        classified_records, touched_origins=touched_origins
    )
    progress = evaluate_completion(
        classified_records,
        conversion_results=conversion_results,
    )
    summary = _classification_summary(classified_records)

    policy_pass = bool(policy.get("pass", False))
    progress_pass = bool(progress.get("operationally_complete", False))
    ci_pass = (policy_pass or not strict_policy) and (
        progress_pass or not strict_progress
    )

    return {
        "dashboard_version": "v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metadata": metadata or {},
        "classification": summary,
        "policy": policy,
        "progress": progress,
        "gates": {
            "strict_policy": strict_policy,
            "strict_progress": strict_progress,
            "policy_pass": policy_pass,
            "progress_pass": progress_pass,
            "ci_pass": ci_pass,
        },
        "touched_origins": sorted(set(touched_origins or [])),
    }
