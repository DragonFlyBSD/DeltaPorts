"""Batch conversion runner for migration program."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from dportsv3.migration.convert import convert_record
from dportsv3.migration.waves import build_wave_report, select_wave


def run_batch(
    records: list[dict[str, Any]],
    *,
    repo_root: Path,
    buckets: list[str] | None = None,
    targets: list[str] | None = None,
    categories: list[str] | None = None,
    max_ports: int = 100,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Run batch conversion for selected records and return report."""
    wave = select_wave(
        records,
        buckets=buckets,
        targets=targets,
        categories=categories,
        max_ports=max_ports,
        dry_run=dry_run,
    )

    selected_by_origin = {row["origin"]: row for row in records}
    selected_records = [selected_by_origin[item["origin"]] for item in wave["selected"]]

    results = [
        convert_record(record, repo_root=repo_root, dry_run=dry_run)
        for record in selected_records
    ]
    report = build_wave_report(results)

    status_counts = report.get("status_counts", {})
    converted = int(status_counts.get("converted", 0))
    blocked = int(status_counts.get("blocked", 0))
    fallback = int(status_counts.get("fallback", 0))
    failed = int(status_counts.get("failed", 0))

    return {
        "dry_run": dry_run,
        "wave": wave,
        "report": report,
        "artifacts": {
            "converted_count": converted,
            "blocked_count": blocked,
            "fallback_count": fallback,
            "failed_count": failed,
            "stale_count": int(status_counts.get("stale", 0)),
        },
        "results": results,
    }
