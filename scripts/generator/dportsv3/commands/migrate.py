"""Migration command handlers for dportsv3."""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path
from typing import Any

from dportsv3.common.io import (
    emit_json,
    read_json_list,
    read_lines_file,
)
from dportsv3.migration.batch import run_batch
from dportsv3.migration.classify import classify_inventory
from dportsv3.migration.convert import convert_record
from dportsv3.migration.dashboard import build_migration_dashboard
from dportsv3.migration.inventory import scan_inventory
from dportsv3.migration.policy import evaluate_forward_policy
from dportsv3.migration.progress import evaluate_completion
from dportsv3.migration.touched import extract_touched_origins
from dportsv3.migration.waves import build_wave_report, select_wave


def _handle_inventory(args: Namespace) -> int:
    root = Path(args.root)
    try:
        records = scan_inventory(root)
    except ValueError as exc:
        print(f"Inventory scan error: {exc}", file=sys.stderr)
        return 2

    payload: dict[str, Any] = {
        "root": str(root),
        "record_total": len(records),
        "records": records,
    }
    emit_json(payload, pretty=bool(args.json))
    return 0


def _handle_classify(args: Namespace) -> int:
    records, error = read_json_list(Path(args.inventory), label="Inventory")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if records is None:
        return 2

    classified = classify_inventory(records)
    payload: dict[str, Any] = {
        "record_total": len(classified),
        "classified": classified,
    }
    emit_json(payload, pretty=bool(args.json))
    return 0


def _handle_convert(args: Namespace) -> int:
    records, error = read_json_list(Path(args.classified), label="Classified")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if records is None:
        return 2

    origin = str(args.origin)
    matches = [r for r in records if str(r.get("origin", "")) == origin]
    if not matches:
        print(f"Origin not found in classified inventory: {origin}", file=sys.stderr)
        return 2

    result = convert_record(
        matches[0], repo_root=Path(args.root), dry_run=bool(args.dry_run)
    )
    emit_json(result, pretty=bool(args.json))
    if bool(args.strict) and result.get("status") in {"failed", "blocked"}:
        return 2
    return 0


def _handle_batch(args: Namespace) -> int:
    records, error = read_json_list(Path(args.classified), label="Classified")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if records is None:
        return 2

    report = run_batch(
        records,
        repo_root=Path(args.root),
        buckets=list(args.bucket or []),
        targets=list(args.target or []),
        categories=list(args.category or []),
        max_ports=int(args.max_ports),
        dry_run=bool(args.dry_run),
    )
    emit_json(report, pretty=bool(args.json))
    if bool(args.strict) and not report.get("report", {}).get("gate_pass", False):
        return 2
    return 0


def _handle_policy_check(args: Namespace) -> int:
    records, error = read_json_list(Path(args.classified), label="Classified")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if records is None:
        return 2

    touched: list[str] | None = None
    if args.touched is not None:
        touched, load_error = read_lines_file(Path(args.touched))
        if load_error is not None:
            print(load_error, file=sys.stderr)
            return 1

    result = evaluate_forward_policy(records, touched_origins=touched)
    emit_json(result, pretty=bool(args.json))
    if bool(args.strict) and not result.get("pass", False):
        return 2
    return 0


def _handle_progress(args: Namespace) -> int:
    records, error = read_json_list(Path(args.classified), label="Classified")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if records is None:
        return 2

    results_payload: list[dict[str, Any]] | None = None
    if args.results is not None:
        results_payload, load_error = read_json_list(
            Path(args.results), label="Results"
        )
        if load_error is not None:
            print(load_error, file=sys.stderr)
            return 1
        if results_payload is None:
            return 2

    report = evaluate_completion(records, conversion_results=results_payload)
    emit_json(report, pretty=bool(args.json))
    if bool(args.strict) and not report.get("operationally_complete", False):
        return 2
    return 0


def _handle_dashboard(args: Namespace) -> int:
    records, error = read_json_list(Path(args.classified), label="Classified")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if records is None:
        return 2

    results_payload: list[dict[str, Any]] | None = None
    if args.results is not None:
        results_payload, load_error = read_json_list(
            Path(args.results), label="Results"
        )
        if load_error is not None:
            print(load_error, file=sys.stderr)
            return 1
        if results_payload is None:
            return 2

    touched_origins: list[str] = []
    if args.touched is not None:
        touched_lines, load_error = read_lines_file(Path(args.touched))
        if load_error is not None:
            print(load_error, file=sys.stderr)
            return 1
        touched_origins.extend(touched_lines or [])

    if args.changed_files is not None:
        changed_lines, load_error = read_lines_file(Path(args.changed_files))
        if load_error is not None:
            print(load_error, file=sys.stderr)
            return 1
        touched_origins.extend(extract_touched_origins(changed_lines or []))

    strict = bool(args.strict)
    strict_policy = bool(args.strict_policy) or strict
    strict_progress = bool(args.strict_progress) or strict

    report = build_migration_dashboard(
        records,
        conversion_results=results_payload,
        touched_origins=sorted(set(touched_origins)),
        strict_policy=strict_policy,
        strict_progress=strict_progress,
        metadata={"root": str(args.root)},
    )
    emit_json(report, pretty=bool(args.json))
    if not report.get("gates", {}).get("ci_pass", False):
        return 2
    return 0


def _handle_wave_plan(args: Namespace) -> int:
    payload, error = read_json_list(Path(args.inventory), label="Inventory")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if payload is None:
        return 2

    try:
        report = select_wave(
            payload,
            buckets=list(args.bucket or []),
            targets=list(args.target or []),
            categories=list(args.category or []),
            max_ports=int(args.max_ports),
            dry_run=bool(args.dry_run),
        )
    except ValueError as exc:
        print(f"Wave selection error: {exc}", file=sys.stderr)
        return 2

    emit_json(report, pretty=bool(args.json))
    return 0


def _handle_wave_report(args: Namespace) -> int:
    payload, error = read_json_list(Path(args.results), label="Results")
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if payload is None:
        return 2

    try:
        report = build_wave_report(payload)
    except ValueError as exc:
        print(f"Wave report error: {exc}", file=sys.stderr)
        return 2

    emit_json(report, pretty=bool(args.json))
    if bool(args.strict) and not report["gate_pass"]:
        return 2
    return 0


def cmd_migrate(args: Namespace) -> int:
    """Dispatch migration subcommands."""
    action = args.migrate_action
    if action == "inventory":
        return _handle_inventory(args)
    if action == "classify":
        return _handle_classify(args)
    if action == "convert":
        return _handle_convert(args)
    if action == "batch":
        return _handle_batch(args)
    if action == "policy-check":
        return _handle_policy_check(args)
    if action == "progress":
        return _handle_progress(args)
    if action == "dashboard":
        return _handle_dashboard(args)
    if action == "wave-plan":
        return _handle_wave_plan(args)
    if action == "wave-report":
        return _handle_wave_report(args)

    print(f"Unknown migrate action: {action}", file=sys.stderr)
    return 1
