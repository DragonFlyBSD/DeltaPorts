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
    fetch_artifact,
    finish_build,
    get_activity,
    get_build,
    get_bundle,
    get_diff,
    get_failures,
    get_job,
    get_status,
    list_bundles,
    list_jobs,
    list_port_bundles,
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
    if action == "get-bundle":
        return _cmd_get_bundle(args)
    if action == "list-bundles":
        return _cmd_list_bundles(args)
    if action == "get-job":
        return _cmd_get_job(args)
    if action == "list-jobs":
        return _cmd_list_jobs(args)
    if action == "get-activity":
        return _cmd_get_activity(args)
    if action == "fetch-artifact":
        return _cmd_fetch_artifact(args)

    print(f"Unknown tracker action: {action}", file=sys.stderr)
    return 1


def _resolve_state_db_path(args: Namespace) -> Path:
    """Resolve the state.db path with the precedence:
      1. --db PATH (explicit operator override)
      2. DPORTSV3_STATE_DB env var
      3. $PWD/state.db (fall-back default)

    Tracker reads + writes the same file artifact-store writes. The
    operator is responsible for ensuring the path matches whatever
    artifact-store was started with (typically --logs-root
    /build/synth/logs → /build/synth/logs/evidence/state.db).
    """
    if args.db is not None:
        return Path(args.db)
    env_db = os.environ.get("DPORTSV3_STATE_DB")
    if env_db:
        return Path(env_db)
    return Path.cwd() / "state.db"


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

    db_path = _resolve_state_db_path(args)
    app = create_app(db_path)
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


# --------------------------------------------------------------------
# Agentic-side read handlers (get-bundle / list-bundles / get-job /
# list-jobs / get-activity / fetch-artifact). Used by operators and
# the analyzer subagent.
# --------------------------------------------------------------------


def _cmd_get_bundle(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = get_bundle(server, str(args.bundle_id))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json(payload, pretty=True)
    else:
        for line in _format_bundle(payload):
            print(line)
    return 0


def _cmd_list_bundles(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        if getattr(args, "origin", None):
            # /api/ports/{origin} returns the origin-scoped list; preferred
            # when the caller knows the origin since it sorts newest-first
            # and accepts the same target filter.
            payload = list_port_bundles(
                server, str(args.origin),
                target=str(args.target) if getattr(args, "target", None) else None,
                limit=int(args.limit),
            )
        else:
            payload = list_bundles(
                server,
                target=str(args.target) if getattr(args, "target", None) else None,
                origin=None,
                limit=int(args.limit),
            )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json({"bundles": payload}, pretty=True)
    else:
        if not payload:
            print("No matching bundles.")
        for row in payload:
            print(
                f"{row.get('bundle_id', '-')}  "
                f"{row.get('origin', '-'):<28}  "
                f"{row.get('target', '-') or '-':<8}  "
                f"{row.get('result', '-'):<8}  "
                f"{row.get('resolution', '-') or '-':<22}  "
                f"{row.get('last_seen_at', '-')}"
            )
    return 0


def _cmd_get_job(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = get_job(server, str(args.job_id))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json(payload, pretty=True)
    else:
        for line in _format_job(payload):
            print(line)
    return 0


def _cmd_list_jobs(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = list_jobs(
            server,
            state=str(args.state) if getattr(args, "state", None) else None,
            target=str(args.target) if getattr(args, "target", None) else None,
            limit=int(args.limit),
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json({"jobs": payload}, pretty=True)
    else:
        if not payload:
            print("No matching jobs.")
        for row in payload:
            print(
                f"{row.get('job_id', '-')}  "
                f"{row.get('state', '-'):<10}  "
                f"{row.get('origin', '-'):<28}  "
                f"{row.get('target', '-') or '-':<8}  "
                f"{row.get('updated_at', '-')}"
            )
    return 0


def _cmd_get_activity(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        payload = get_activity(
            server,
            job_id=str(args.job_id) if getattr(args, "job_id", None) else None,
            target=str(args.target) if getattr(args, "target", None) else None,
            stage_filter=str(args.stage_filter)
            if getattr(args, "stage_filter", None) else None,
            since_id=int(args.since_id),
            limit=int(args.limit),
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if bool(args.json):
        emit_json({"activity": payload}, pretty=True)
    else:
        if not payload:
            print("No matching activity rows.")
        for row in payload:
            stage = row.get("stage", "-")
            message = row.get("message", "")
            ts = row.get("ts", row.get("created_at", "-"))
            print(f"{ts}  {stage:<24}  {message}")
    return 0


def _cmd_fetch_artifact(args: Namespace) -> int:
    try:
        server = _resolve_server_url(args)
        data = fetch_artifact(
            server, str(args.bundle_id), str(args.relpath),
        )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # Write raw bytes to stdout so callers can pipe binary artifacts
    # (logs.gz etc.) without text-mode mangling.
    sys.stdout.buffer.write(data)
    return 0


def _format_bundle(b: dict[str, Any]) -> list[str]:
    lines = [
        f"Bundle:     {b.get('bundle_id', '-')}",
        f"Origin:     {b.get('origin', '-')}",
        f"Target:     {b.get('target', '-') or '-'}",
        f"Result:     {b.get('result', '-')}",
        f"Resolution: {b.get('resolution', '-') or '-'}",
        f"Last seen:  {b.get('last_seen_at', '-')}",
    ]
    if b.get("verification_status"):
        lines.append(
            f"Verified:   {b['verification_status']} at "
            f"{b.get('verification_at', '-')}"
        )
    artifacts = b.get("artifacts") or []
    lines.append(f"Artifacts:  {len(artifacts)}")
    for a in artifacts:
        size = a.get("size")
        size_str = f"{size}B" if isinstance(size, int) else "?"
        lines.append(f"  - {a.get('relpath', '?'):<40} {size_str:>10}")
    return lines


def _format_job(j: dict[str, Any]) -> list[str]:
    lines = [
        f"Job:        {j.get('job_id', '-')}",
        f"State:      {j.get('state', '-')}",
        f"Origin:     {j.get('origin', '-')}",
        f"Target:     {j.get('target', '-') or '-'}",
        f"Bundle:     {j.get('bundle_id', '-') or '-'}",
        f"Updated:    {j.get('updated_at', '-')}",
    ]
    if j.get("retire_reason"):
        lines.append(f"Retired:    {j['retire_reason']}")
    if j.get("last_transition_at"):
        lines.append(f"Last txn:   {j['last_transition_at']}")
    return lines


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
