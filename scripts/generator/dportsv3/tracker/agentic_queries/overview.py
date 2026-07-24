"""Status / overview aggregates for the tracker's agentic endpoints."""

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


def agentic_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Global aggregate counts for /api/agentic-status.

    Groups the typed lifecycle states into operator-facing buckets:
        pending  = queued
        inflight = lifecycle.ACTIVE_WORK_STATES minus queued
                   (claimed | triaging | patching | converting |
                    verifying | verifying_fix)
        done     = done
        dead     = dead
        escalated = escalated

    ``triaged`` is intentionally in NO bucket: a triaged triage job
    has finished and handed its work to a spawned patch/convert job
    (which is itself counted), so counting it in ``inflight`` would
    double-count one origin's work. See ACTIVE_WORK_STATES.
    """
    bundles = conn.execute("SELECT count(*) FROM bundles").fetchone()[0]
    inflight_states = tuple(
        s for s in ACTIVE_WORK_STATE_VALUES if s != "queued"
    )
    placeholders = ",".join("?" for _ in inflight_states)
    rows = conn.execute(
        f"""SELECT
             SUM(CASE WHEN state = 'queued' THEN 1 ELSE 0 END) AS pending,
             SUM(CASE WHEN state IN ({placeholders}) THEN 1 ELSE 0 END) AS inflight,
             SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END) AS done,
             SUM(CASE WHEN state = 'dead' THEN 1 ELSE 0 END) AS dead,
             SUM(CASE WHEN state = 'escalated' THEN 1 ELSE 0 END) AS escalated
           FROM jobs""",
        inflight_states,
    ).fetchone()
    runs = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    # Step 9 — surface the open manual-queue depth on the dashboard
    # so operators see "5 ports waiting for me" without first
    # clicking through. The status column carries the operator-action
    # signal directly: ``pending`` = operator action awaited (whether
    # because they haven't submitted context yet, or because they
    # submitted some and the agent re-escalated anyway);
    # ``retriage_enqueued`` = runner mid-flight; ``discarded`` =
    # terminal. The list/count uses the same predicate as
    # ``list_manual_requests`` so dashboard and queue agree.
    manual_pending = conn.execute(
        "SELECT count(*) FROM user_context_requests "
        "WHERE status = 'pending'"
    ).fetchone()[0]
    # Step 20f — convert-job progress by state. open=queued/claimed/converting,
    # done/dead/escalated mirror the global rollup so the operator can read
    # progress at a glance.
    convert_rows = conn.execute(
        """SELECT
             SUM(CASE WHEN state IN ('queued','claimed','converting') THEN 1 ELSE 0 END) AS open,
             SUM(CASE WHEN state = 'done' THEN 1 ELSE 0 END) AS done,
             SUM(CASE WHEN state = 'dead' THEN 1 ELSE 0 END) AS dead,
             SUM(CASE WHEN state = 'escalated' THEN 1 ELSE 0 END) AS escalated
           FROM jobs WHERE type = 'convert'"""
    ).fetchone()
    return {
        "bundles": bundles,
        "runs": runs,
        "manual_pending": int(manual_pending or 0),
        "jobs": {
            "pending": int(rows[0] or 0),
            "inflight": int(rows[1] or 0),
            "done": int(rows[2] or 0),
            "dead": int(rows[3] or 0),
            "escalated": int(rows[4] or 0),
        },
        "convert": {
            "open":      int(convert_rows[0] or 0),
            "done":      int(convert_rows[1] or 0),
            "dead":      int(convert_rows[2] or 0),
            "escalated": int(convert_rows[3] or 0),
        },
    }


def runner_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Singleton runner_status row, or a defaulted shape if unset."""
    row = conn.execute(
        "SELECT * FROM runner_status WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"status": "unknown"}
    return _row_dict(row)


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
