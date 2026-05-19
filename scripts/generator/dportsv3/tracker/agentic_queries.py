"""SQL helpers for the agentic-read endpoints added in Phase 4 step 5.

These queries read the state.db tables originally owned by state-server
(``runs``, ``bundles``, ``jobs``, ``events``, ``activity_log``,
``runner_status``, ``artifact_refs``). Tracker (step 5) absorbs them so
state-server can be retired in step 8.

Target filtering: ``bundles``, ``jobs``, ``runs`` carry a nullable
``target`` column added in step 5. Filter is applied as an equality
match when supplied. ``NULL``-target rows surface only when no filter
is set — they're legacy or filed by a writer that didn't know its
target.
"""

from __future__ import annotations

import sqlite3
from typing import Any


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {str(key): row[key] for key in row.keys()}


def _maybe(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return _row_dict(row) if row is not None else None


def agentic_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Global aggregate counts for /api/agentic-status."""
    bundles = conn.execute("SELECT count(*) FROM bundles").fetchone()[0]
    jobs_pending = conn.execute(
        "SELECT count(*) FROM jobs WHERE state = 'pending'"
    ).fetchone()[0]
    jobs_inflight = conn.execute(
        "SELECT count(*) FROM jobs WHERE state = 'inflight'"
    ).fetchone()[0]
    jobs_done = conn.execute(
        "SELECT count(*) FROM jobs WHERE state = 'done'"
    ).fetchone()[0]
    jobs_failed = conn.execute(
        "SELECT count(*) FROM jobs WHERE state = 'failed'"
    ).fetchone()[0]
    runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    return {
        "bundles": bundles,
        "runs": runs,
        "jobs": {
            "pending": jobs_pending,
            "inflight": jobs_inflight,
            "done": jobs_done,
            "failed": jobs_failed,
        },
    }


def recent_activity(
    conn: sqlite3.Connection,
    limit: int = 10,
    target: str | None = None,
) -> list[dict[str, Any]]:
    """Most recent activity_log rows, newest first.

    activity_log itself has no target column — filter is applied via
    a join to the originating job's target when target is supplied.
    Rows whose job_id doesn't resolve are dropped under filter.
    """
    if target is None:
        rows = conn.execute(
            "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?",
            (max(1, int(limit)),),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT activity_log.*
               FROM activity_log
               JOIN jobs ON jobs.job_id = activity_log.job_id
               WHERE jobs.target = ?
               ORDER BY activity_log.id DESC
               LIMIT ?""",
            (target, max(1, int(limit))),
        ).fetchall()
    return [_row_dict(row) for row in rows]


def runner_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Singleton runner_status row, or a defaulted shape if unset."""
    row = conn.execute(
        "SELECT * FROM runner_status WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"status": "unknown"}
    return _row_dict(row)


def list_runs(
    conn: sqlite3.Connection,
    target: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM runs"
    params: list[Any] = []
    if target is not None:
        sql += " WHERE target = ?"
        params.append(target)
    sql += " ORDER BY ts_start DESC, run_id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    return [_row_dict(row) for row in conn.execute(sql, params).fetchall()]


def get_run(conn: sqlite3.Connection, run_id: str) -> dict[str, Any] | None:
    return _maybe(
        conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    )


def list_jobs(
    conn: sqlite3.Connection,
    state: str | None = None,
    target: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM jobs"
    clauses: list[str] = []
    params: list[Any] = []
    if state is not None:
        clauses.append("state = ?")
        params.append(state)
    if target is not None:
        clauses.append("target = ?")
        params.append(target)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY created_ts_utc DESC, job_id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    return [_row_dict(row) for row in conn.execute(sql, params).fetchall()]


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    return _maybe(
        conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    )


def list_bundles(
    conn: sqlite3.Connection,
    target: str | None = None,
    origin: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM bundles"
    clauses: list[str] = []
    params: list[Any] = []
    if target is not None:
        clauses.append("target = ?")
        params.append(target)
    if origin is not None:
        clauses.append("origin = ?")
        params.append(origin)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY ts_utc DESC, bundle_id DESC LIMIT ?"
    params.append(max(1, int(limit)))
    return [_row_dict(row) for row in conn.execute(sql, params).fetchall()]


def get_bundle(conn: sqlite3.Connection, bundle_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM bundles WHERE bundle_id = ?", (bundle_id,)
    ).fetchone()
    if row is None:
        return None
    bundle = _row_dict(row)
    artifacts = conn.execute(
        """SELECT relpath, backend, sha256, fs_path, kind, size, created_at
           FROM artifact_refs
           WHERE bundle_id = ?
           ORDER BY relpath ASC""",
        (bundle_id,),
    ).fetchall()
    bundle["artifacts"] = [_row_dict(r) for r in artifacts]
    return bundle


def get_artifact_ref(
    conn: sqlite3.Connection, bundle_id: str, relpath: str
) -> dict[str, Any] | None:
    return _maybe(
        conn.execute(
            """SELECT backend, sha256, fs_path, kind, size
               FROM artifact_refs
               WHERE bundle_id = ? AND relpath = ?""",
            (bundle_id, relpath),
        ).fetchone()
    )


def list_port_bundles(
    conn: sqlite3.Connection,
    origin: str,
    target: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM bundles WHERE origin = ?"
    params: list[Any] = [origin]
    if target is not None:
        sql += " AND target = ?"
        params.append(target)
    sql += " ORDER BY ts_utc DESC LIMIT ?"
    params.append(max(1, int(limit)))
    return [_row_dict(row) for row in conn.execute(sql, params).fetchall()]


def distinct_targets(conn: sqlite3.Connection) -> list[str]:
    """Sorted list of non-NULL targets seen across bundles/jobs/runs.

    Used to populate target-selector dropdowns on the HTML views.
    """
    rows = conn.execute(
        """SELECT DISTINCT target FROM (
             SELECT target FROM bundles
             UNION SELECT target FROM jobs
             UNION SELECT target FROM runs
           )
           WHERE target IS NOT NULL AND target <> ''
           ORDER BY target ASC"""
    ).fetchall()
    return [str(row[0]) for row in rows]


def activity_for_job(
    conn: sqlite3.Connection, job_id: str, limit: int = 50
) -> list[dict[str, Any]]:
    """Activity-log rows for one job_id, newest first."""
    rows = conn.execute(
        "SELECT * FROM activity_log WHERE job_id = ? ORDER BY id DESC LIMIT ?",
        (job_id, max(1, int(limit))),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def bundles_for_run(
    conn: sqlite3.Connection, run_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM bundles WHERE run_id = ? ORDER BY ts_utc DESC LIMIT ?",
        (run_id, max(1, int(limit))),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def events_since(
    conn: sqlite3.Connection,
    last_id: int = 0,
    target: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return events with ``id > last_id``, oldest first.

    Used by the SSE endpoint to tail events. Target filter is best-effort:
    an event's ``data_json`` carries ``target`` when the originating
    write knew it (post-step-5). Pre-step-5 events have no target and
    surface only when no filter is set.
    """
    rows = conn.execute(
        "SELECT id, ts, type, data_json FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
        (int(last_id), max(1, int(limit))),
    ).fetchall()
    items = [_row_dict(row) for row in rows]
    if target is None:
        return items
    out: list[dict[str, Any]] = []
    import json

    for item in items:
        raw = item.get("data_json")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if payload.get("target") == target:
            out.append(item)
    return out
