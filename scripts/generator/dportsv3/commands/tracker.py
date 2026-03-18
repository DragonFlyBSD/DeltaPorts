"""Tracker command handlers for dportsv3."""

from __future__ import annotations

import json
import os
import sys
import importlib
from importlib import util as importlib_util
from argparse import Namespace
from pathlib import Path
from typing import Any

from dportsv3.common.io import emit_json
from dportsv3.tracker.client import (
    compare_builds,
    enqueue_ports,
    finish_build,
    get_build,
    get_diff,
    get_failures,
    get_status,
    mark_port_building,
    record_result,
    start_build,
)


def cmd_tracker(args: Namespace) -> int:
    """Dispatch tracker subcommands."""
    action = getattr(args, "tracker_action", None)
    if action == "serve":
        return _cmd_serve(args)
    if action == "start-build":
        return _cmd_start_build(args)
    if action == "finish-build":
        return _cmd_finish_build(args)
    if action == "enqueue-ports":
        return _cmd_enqueue_ports(args)
    if action == "mark-building":
        return _cmd_mark_building(args)
    if action == "record-result":
        return _cmd_record_result(args)
    if action == "status":
        return _cmd_status(args)
    if action == "failures":
        return _cmd_failures(args)
    if action == "diff":
        return _cmd_diff(args)
    if action == "show-build":
        return _cmd_show_build(args)
    if action == "compare-builds":
        return _cmd_compare_builds(args)

    print(f"Unknown tracker action: {action}", file=sys.stderr)
    return 1


def _cmd_serve(args: Namespace) -> int:
    uvicorn_spec = importlib_util.find_spec("uvicorn")
    if uvicorn_spec is None:
        print(
            'Tracker server requires optional dependencies. Install with: pip install -e ".[tracker]"',
            file=sys.stderr,
        )
        return 1
    uvicorn = importlib.import_module("uvicorn")

    from dportsv3.tracker.server import create_app

    app = create_app(Path(args.db))
    uvicorn.run(app, host="0.0.0.0", port=int(args.port))
    return 0


def _cmd_start_build(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        run_id = start_build(server, target=str(args.target), build_type=str(args.type))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Started {args.type} build {run_id} for {args.target}")
    return 0


def _cmd_finish_build(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        finish_build(
            server,
            int(args.run),
            finished_at=str(args.finished_at)
            if getattr(args, "finished_at", None)
            else None,
            commit_sha=str(args.commit_sha)
            if getattr(args, "commit_sha", None)
            else None,
            commit_branch=str(args.commit_branch)
            if getattr(args, "commit_branch", None)
            else None,
            commit_pushed_at=str(args.commit_pushed_at)
            if getattr(args, "commit_pushed_at", None)
            else None,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Finished build {args.run}")
    return 0


def _cmd_enqueue_ports(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        ports_data = json.loads(Path(args.file).read_text(encoding="utf-8"))
        if not isinstance(ports_data, list):
            print("Ports file must contain a JSON array", file=sys.stderr)
            return 1
        count = enqueue_ports(
            server,
            int(args.run),
            ports_data,
            total_expected=int(args.total) if getattr(args, "total", None) else None,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Enqueued {count} ports for run {args.run}")
    return 0


def _cmd_mark_building(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        mark_port_building(server, int(args.run), str(args.origin))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Marked {args.origin} as building in run {args.run}")
    return 0


def _cmd_record_result(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        record_result(
            server,
            int(args.run),
            origin=str(args.origin),
            version=str(args.version),
            result=str(args.result),
            log_url=str(args.log_url) if getattr(args, "log_url", None) else None,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Recorded {args.result} for {args.origin} in run {args.run}")
    return 0


def _cmd_status(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = get_status(
            server,
            target=str(args.target) if getattr(args, "target", None) else None,
            origin=str(args.origin) if getattr(args, "origin", None) else None,
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json({"status": payload}, pretty=True)
    else:
        for line in _format_status_rows(payload):
            print(line)
    return 0


def _cmd_failures(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = get_failures(server, str(args.target))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json({"failures": payload}, pretty=True)
    else:
        print(f"Failures for {args.target}: {len(payload)}")
        for row in payload:
            print(f"- {row['origin']} {row['last_attempt_version']}")
    return 0


def _cmd_diff(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = get_diff(server, str(args.target_a), str(args.target_b))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json(payload, pretty=True)
    else:
        print(
            f"Diff {args.target_a} vs {args.target_b}: "
            f"only_a={len(payload['only_a'])} only_b={len(payload['only_b'])} differ={len(payload['differ'])}"
        )
        for row in payload["differ"]:
            print(
                f"- {row['origin']}: {row['result_a']} {row['version_a']} vs "
                f"{row['result_b']} {row['version_b']}"
            )
    return 0


def _cmd_show_build(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = get_build(server, int(args.run))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json(payload, pretty=True)
    else:
        for line in _format_build(payload):
            print(line)
    return 0


def _cmd_compare_builds(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = compare_builds(server, int(args.run_a), int(args.run_b))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json(payload, pretty=True)
    else:
        for line in _format_build_compare(payload):
            print(line)
    return 0


def _resolve_server_url(args: Namespace) -> str:
    server = getattr(args, "server", None) or os.environ.get("DPORTSV3_TRACKER_URL")
    if server:
        return str(server)
    raise RuntimeError(
        "Tracker server URL required: use --server or DPORTSV3_TRACKER_URL"
    )


def _format_status_rows(rows: list[dict[str, Any]]) -> list[str]:
    if not rows:
        return ["No matching status rows."]
    return [
        f"- {row['target']} {row['origin']}: {row['last_attempt_result']} {row['last_attempt_version']}"
        + _format_last_success_suffix(row)
        for row in rows
    ]


def _format_last_success_suffix(row: dict[str, Any]) -> str:
    if not row.get("last_success_version"):
        return ""
    return f" (last success {row['last_success_version']})"


def _format_build(payload: dict[str, Any]) -> list[str]:
    build = payload["build_run"]
    results = payload.get("results", [])
    header = (
        f"Build {build['id']}: {build['target']} {build['build_type']} "
        f"started {build['started_at']}"
    )
    lines = [header]
    if build.get("finished_at"):
        lines.append(f"finished: {build['finished_at']}")
    else:
        lines.append("finished: running")
    lines.append(
        "results: "
        f"{build.get('success_count', 0)} success, "
        f"{build.get('failure_count', 0)} failure, "
        f"{build.get('skipped_count', 0)} skipped, "
        f"{build.get('ignored_count', 0)} ignored"
    )
    if build.get("commit_sha"):
        lines.append(
            f"commit: {build['commit_sha']} branch={build.get('commit_branch') or '-'} "
            f"pushed_at={build.get('commit_pushed_at') or '-'}"
        )
    for row in results:
        suffix = f" log={row['log_url']}" if row.get("log_url") else ""
        lines.append(f"- {row['origin']} {row['version']} {row['result']}{suffix}")
    return lines


def _format_build_compare(payload: dict[str, Any]) -> list[str]:
    run_a = payload["run_a"]
    run_b = payload["run_b"]
    summary = payload["summary"]
    if (
        run_a["target"] == run_b["target"]
        and run_a["build_type"] == run_b["build_type"]
    ):
        title = (
            f"Comparing {run_a['target']} {run_a['build_type']} run {run_a['id']} "
            f"({run_a['started_at']}) vs run {run_b['id']} ({run_b['started_at']})"
        )
    else:
        title = (
            f"Comparing {run_a['target']} {run_a['build_type']} run {run_a['id']} "
            f"({run_a['started_at']}) vs {run_b['target']} {run_b['build_type']} "
            f"run {run_b['id']} ({run_b['started_at']})"
        )
    lines = [title]
    lines.append(f"New successes (fixes):      {summary['new_successes']:>4}")
    lines.append(f"New failures (regressions): {summary['new_failures']:>4}")
    if payload["new_failures"]:
        inline = ", ".join(
            f"{row['origin']} {row['version_b']}" for row in payload["new_failures"]
        )
        lines.append(f"  {inline}")
    lines.append(f"Still failing:              {summary['still_failing']:>4}")
    lines.append(f"Still succeeding:           {summary['still_succeeding']:>4}")
    lines.append(f"Added:                      {summary['added']:>4}")
    lines.append(f"Removed:                    {summary['removed']:>4}")
    lines.append(f"Version changes:            {summary['version_changes']:>4}")
    return lines
