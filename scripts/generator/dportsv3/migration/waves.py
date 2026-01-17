"""Wave rollout planning and reporting helpers."""

from __future__ import annotations

from typing import Any

from dportsv3.common.metrics import count_by
from dportsv3.migration.models import MigrationWaveRecord


def _normalize_records(records: list[dict[str, Any]]) -> list[MigrationWaveRecord]:
    normalized: list[MigrationWaveRecord] = []
    for data in records:
        normalized.append(MigrationWaveRecord.from_dict(data))
    return normalized


def select_wave(
    records: list[dict[str, Any]],
    *,
    buckets: list[str] | None = None,
    targets: list[str] | None = None,
    categories: list[str] | None = None,
    max_ports: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Select a deterministic wave candidate set from inventory records."""
    if max_ports <= 0:
        raise ValueError("max_ports must be > 0")

    norm = _normalize_records(records)

    bucket_set = set(buckets or [])
    target_set = set(targets or [])
    category_set = set(categories or [])

    prelim = [
        r
        for r in norm
        if (not bucket_set or r.bucket in bucket_set)
        and (not category_set or r.category in category_set)
    ]

    excluded_by_target_count = 0
    filtered: list[tuple[MigrationWaveRecord, str, str]] = []
    for record in prelim:
        selection_reason = (
            "baseline_match"
            if record.target_mode == "baseline"
            else "explicit_target_match"
        )
        selected_target = record.target

        if target_set:
            if record.target_mode == "baseline":
                selected_target = sorted(target_set)[0]
                selection_reason = "baseline_match"
            else:
                matches = sorted(set(record.available_targets).intersection(target_set))
                if not matches:
                    excluded_by_target_count += 1
                    continue
                selected_target = matches[0]
                selection_reason = "explicit_target_match"

        filtered.append((record, selected_target, selection_reason))

    ordered = sorted(
        filtered,
        key=lambda row: (
            -row[0].churn,
            row[0].category,
            row[0].origin,
            row[1],
            row[0].bucket,
        ),
    )
    selected = ordered[:max_ports]
    baseline_selected_count = sum(
        1 for _, _, reason in selected if reason == "baseline_match"
    )
    explicit_selected_count = sum(
        1 for _, _, reason in selected if reason == "explicit_target_match"
    )

    return {
        "dry_run": dry_run,
        "selection_criteria": {
            "buckets": sorted(bucket_set),
            "targets": sorted(target_set),
            "categories": sorted(category_set),
            "max_ports": max_ports,
        },
        "candidate_total": len(norm),
        "filtered_total": len(filtered),
        "selected_total": len(selected),
        "selected": [
            {
                "origin": r.origin,
                "bucket": r.bucket,
                "target": selected_target,
                "target_mode": r.target_mode,
                "selection_reason": selection_reason,
                "category": r.category,
                "churn": r.churn,
            }
            for r, selected_target, selection_reason in selected
        ],
        "selection_counters": {
            "baseline_selected_count": baseline_selected_count,
            "explicit_selected_count": explicit_selected_count,
            "excluded_by_target_count": excluded_by_target_count,
        },
        "summary": {
            "by_bucket": count_by([r.bucket for r, _, _ in selected]),
            "by_target": count_by([target for _, target, _ in selected]),
            "by_category": count_by([r.category for r, _, _ in selected]),
        },
    }


def build_wave_report(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize a wave run and evaluate quality gates."""
    statuses: list[str] = []
    parse_failures = 0
    check_failures = 0
    plan_failures = 0
    determinism_failures = 0
    unclassified = 0

    normalized_rows: list[dict[str, Any]] = []
    for row in results:
        status = str(row.get("status", "")).strip()
        if not status:
            raise ValueError("result row missing status")

        parse_ok = bool(row.get("parse_ok", True))
        check_ok = bool(row.get("check_ok", True))
        plan_ok = bool(row.get("plan_ok", True))
        deterministic_ok = bool(row.get("deterministic_ok", True))
        classified = bool(row.get("classified", True))

        if not parse_ok:
            parse_failures += 1
        if not check_ok:
            check_failures += 1
        if not plan_ok:
            plan_failures += 1
        if not deterministic_ok:
            determinism_failures += 1
        if not classified:
            unclassified += 1

        statuses.append(status)
        normalized_rows.append(
            {
                "origin": row.get("origin", ""),
                "status": status,
                "parse_ok": parse_ok,
                "check_ok": check_ok,
                "plan_ok": plan_ok,
                "deterministic_ok": deterministic_ok,
                "classified": classified,
            }
        )

    by_status = count_by(statuses)
    hard_failures = by_status.get("failed", 0)

    gates = {
        "no_hard_failures": hard_failures == 0,
        "no_validation_failures": parse_failures == 0
        and check_failures == 0
        and plan_failures == 0,
        "deterministic_outputs": determinism_failures == 0,
        "no_unclassified_overlay": unclassified == 0,
    }
    gate_pass = all(gates.values())

    return {
        "total": len(results),
        "status_counts": by_status,
        "validation_failures": {
            "parse": parse_failures,
            "check": check_failures,
            "plan": plan_failures,
        },
        "determinism_failures": determinism_failures,
        "unclassified_count": unclassified,
        "gates": gates,
        "gate_pass": gate_pass,
        "rows": normalized_rows,
    }
