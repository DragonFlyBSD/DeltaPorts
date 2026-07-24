"""Activity-log + event reads for the tracker's agentic endpoints."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from dportsv3.agent.lifecycle import ACTIVE_WORK_STATE_VALUES
from dportsv3.tracker.agentic_queries._util import (
    _row_dict,
    _maybe,
    _decode_extra_json,
)


def recent_activity_for_bundle(
    conn: sqlite3.Connection,
    bundle_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Activity rows tagged with a specific bundle_id.

    Tracker-side endpoints (accept, delivery, etc.) write rows
    with ``bundle_id`` populated but no ``job_id`` because they
    don't originate from a runner job. This query surfaces them
    on the bundle detail page where the job-scoped activity
    ribbon can't see them.
    """
    rows = conn.execute(
        "SELECT * FROM activity_log WHERE bundle_id = ? "
        "ORDER BY id DESC LIMIT ?",
        (bundle_id, max(1, int(limit))),
    ).fetchall()
    return [_decode_extra_json(_row_dict(row)) for row in rows]


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
    return [_decode_extra_json(_row_dict(row)) for row in rows]


def activity_for_job(
    conn: sqlite3.Connection,
    job_id: str,
    limit: int = 50,
    since_id: int = 0,
    stage_filter: str | None = None,
) -> list[dict[str, Any]]:
    """Activity-log rows for one ``job_id``.

    Newest first by default (the static initial render).

    With ``since_id > 0``, returns rows with ``id > since_id`` in
    **oldest-first** order — the polling shape, so the client can
    prepend each new row at the top of an existing newest-first table.

    ``stage_filter`` (Step 9b):
    - ``"llm_turn"`` → rows where ``stage`` matches ``%llm_turn``
      (catches the canonical name plus any prefixed variant like
      ``convert:llm_turn`` written by earlier convert-flow builds)
    - ``"tool"``      → rows where ``stage LIKE 'tool:%'``
    - any other value or ``None`` → no filter
    """
    clauses = ["job_id = ?"]
    params: list[Any] = [job_id]
    if stage_filter == "llm_turn":
        clauses.append("stage LIKE ?")
        params.append("%llm_turn")
    elif stage_filter == "tool":
        clauses.append("stage LIKE ?")
        params.append("tool:%")
    if since_id and since_id > 0:
        clauses.append("id > ?")
        params.append(int(since_id))
        order = "ASC"
    else:
        order = "DESC"
    params.append(max(1, int(limit)))
    sql = (
        "SELECT * FROM activity_log WHERE "
        + " AND ".join(clauses)
        + f" ORDER BY id {order} LIMIT ?"
    )
    rows = conn.execute(sql, params).fetchall()
    return [_decode_extra_json(_row_dict(row)) for row in rows]


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


# ---------------------------------------------------------------------
# Step 28a: origin skip flags
# ---------------------------------------------------------------------
