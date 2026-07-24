"""Job reads + token usage for the tracker's agentic endpoints."""

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


# The "inflight" filter bucket mirrors the dashboard inflight count
# (agentic_status): the actively-working set minus queued (which is
# its own "pending" bucket). Derived from the single canonical
# lifecycle constant so the filtered list and the dashboard count
# can't disagree. `triaged` is intentionally excluded here too — a
# triaged job is still reachable via an exact ?state=triaged match.
_INFLIGHT_BUCKET: tuple[str, ...] = tuple(
    s for s in ACTIVE_WORK_STATE_VALUES if s != "queued"
)

_STATE_BUCKETS: dict[str, tuple[str, ...]] = {
    # Bucket aliases for filter UX: a user picks "inflight" and gets
    # every job in any inflight-ish lifecycle state.
    "pending":  ("queued",),
    "inflight": _INFLIGHT_BUCKET,
    "done":     ("done",),
    "dead":     ("dead",),
    "escalated": ("escalated",),
    # Legacy API/filter alias: keep accepting failed, but make it mean
    # actual dead jobs instead of conflating operator-escalated work.
    "failed":   ("dead",),
}


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
        if state in _STATE_BUCKETS:
            bucket = _STATE_BUCKETS[state]
            placeholders = ",".join("?" * len(bucket))
            clauses.append(f"state IN ({placeholders})")
            params.extend(bucket)
        else:
            # Allow direct typed-state filtering too (queued, triaging, etc.)
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


def list_jobs_for_bundle(
    conn: sqlite3.Connection, bundle_id: str,
) -> list[dict[str, Any]]:
    """Return all jobs whose ``bundle_id`` FK references ``bundle_id``.

    Joins on the normalized ``jobs.bundle_id`` column. Ordered
    newest-first by created_ts_utc.
    """
    rows = conn.execute(
        """SELECT * FROM jobs
           WHERE bundle_id = ?
           ORDER BY created_ts_utc DESC, job_id DESC""",
        (bundle_id,),
    ).fetchall()
    return [_row_dict(r) for r in rows]


def active_job_for_port(
    conn: sqlite3.Connection,
    origin: str,
    target: str | None = None,
) -> dict[str, Any] | None:
    """Return the most-recent open job for (origin, target), if any.

    Step 9 manual-queue blocker indicator: when an operator submits
    fresh context, the runner only re-enqueues triage if no job is
    already in flight for the same origin/target. This surfaces "yes,
    a job is in flight — wait for it" vs. "queue is clear" on the UI.

    Uses the same canonical actively-working set as the runner's
    retriage guard (lifecycle.ACTIVE_WORK_STATES), so the UI
    indicator and the runner's actual enqueue decision agree — and
    so a resting `triaged` job doesn't pin the indicator to
    "in flight" forever.
    """
    open_states = ACTIVE_WORK_STATE_VALUES
    placeholders = ",".join("?" * len(open_states))
    sql = (
        f"SELECT * FROM jobs WHERE origin = ? AND state IN ({placeholders})"
    )
    params: list[Any] = [origin, *open_states]
    if target is not None:
        sql += " AND target = ?"
        params.append(target)
    sql += " ORDER BY created_ts_utc DESC LIMIT 1"
    return _maybe(conn.execute(sql, params).fetchone())


def token_usage_for_job(
    conn: sqlite3.Connection, job_id: str,
) -> dict[str, Any]:
    """Aggregate token usage from ``llm_turn`` activity_log rows.

    Returns a structured summary card for the job detail page:

    - ``prompt_tokens``, ``completion_tokens``, ``total_tokens``: sums
    - ``llm_turns``: count of llm_turn events for this job
    - ``largest_turn``: the single turn with the largest prompt;
      includes ``turn``, ``prompt_tokens``, ``tools_requested``
    - ``has_data``: False when there are no llm_turn rows yet
      (older jobs predating the telemetry, or fresh jobs that
      haven't hit their first LLM call). Operators see "no data"
      rather than zeros.

    Sums are computed from ``extra_json`` rather than the message
    text — that's the structured source of truth for the data.
    """
    # ``stage LIKE '%llm_turn'`` matches both the canonical
    # 'llm_turn' written by PatchEventDispatcher and any
    # prefix-namespaced variant (e.g. 'convert:llm_turn' from
    # earlier convert-flow builds). Defends the token card against
    # stage-name drift across job types.
    rows = conn.execute(
        """SELECT extra_json FROM activity_log
           WHERE job_id = ? AND stage LIKE '%llm_turn'
           ORDER BY id ASC""",
        (job_id,),
    ).fetchall()
    if not rows:
        return {
            "has_data": False,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "billable_tokens": 0,
            "llm_turns": 0,
            "largest_turn": None,
        }
    prompt_sum = 0
    completion_sum = 0
    total_sum = 0
    cached_sum = 0
    largest: dict[str, Any] | None = None
    for row in rows:
        raw = row[0] if not hasattr(row, "keys") else row["extra_json"]
        if not raw:
            continue
        try:
            extra = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(extra, dict):
            continue
        p = int(extra.get("prompt_tokens") or 0)
        c = int(extra.get("completion_tokens") or 0)
        t = int(extra.get("total_tokens") or 0)
        # cached_tokens is absent on pre-H4 rows → treated as 0, so
        # billable degrades to total (the old, conservative number).
        cached = int(extra.get("cached_tokens") or 0)
        prompt_sum += p
        completion_sum += c
        total_sum += t
        cached_sum += cached
        if largest is None or p > largest["prompt_tokens"]:
            largest = {
                "turn": extra.get("turn"),
                "attempt": extra.get("attempt"),
                "prompt_tokens": p,
                "completion_tokens": c,
                "total_tokens": t,
                "tools_requested": extra.get("tools_requested") or [],
            }
    # billable = uncached prompt + completion (what the budget enforces
    # on and what the run actually costs). Clamp the prompt-minus-cached
    # term at 0 to defend against any provider over-reporting cached.
    billable_sum = max(0, prompt_sum - cached_sum) + completion_sum
    return {
        "has_data": True,
        "prompt_tokens": prompt_sum,
        "completion_tokens": completion_sum,
        "total_tokens": total_sum,
        "cached_tokens": cached_sum,
        "billable_tokens": billable_sum,
        "llm_turns": len(rows),
        "largest_turn": largest,
    }


def token_usage_for_port(
    conn: sqlite3.Connection,
    origin: str,
    target: str | None = None,
) -> dict[str, Any]:
    """Lifetime token usage across every job for (origin, target).

    Step 9 bundle-page card: sum ``llm_turn`` activity rows whose job
    matches the origin/target. The bundle being viewed is one
    failure for the port; this aggregate is "what has the agent
    burned trying to fix this port across all attempts."

    Same return shape as :func:`token_usage_for_job` but with an
    extra ``jobs`` count instead of ``largest_turn`` (the per-turn
    detail is more useful one job at a time on the job page).
    """
    clauses = ["j.origin = ?", "al.stage = ?"]
    params: list[Any] = [origin, "llm_turn"]
    if target is not None:
        clauses.append("j.target = ?")
        params.append(target)
    sql = (
        "SELECT al.extra_json, al.job_id FROM activity_log AS al "
        "JOIN jobs AS j ON j.job_id = al.job_id WHERE "
        + " AND ".join(clauses)
    )
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return {
            "has_data": False,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "llm_turns": 0,
            "jobs": 0,
        }
    prompt_sum = 0
    completion_sum = 0
    total_sum = 0
    jobs_seen: set[str] = set()
    for row in rows:
        raw = row[0] if not hasattr(row, "keys") else row["extra_json"]
        job_id = row[1] if not hasattr(row, "keys") else row["job_id"]
        if job_id:
            jobs_seen.add(str(job_id))
        if not raw:
            continue
        try:
            extra = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(extra, dict):
            continue
        prompt_sum += int(extra.get("prompt_tokens") or 0)
        completion_sum += int(extra.get("completion_tokens") or 0)
        total_sum += int(extra.get("total_tokens") or 0)
    return {
        "has_data": True,
        "prompt_tokens": prompt_sum,
        "completion_tokens": completion_sum,
        "total_tokens": total_sum,
        "llm_turns": len(rows),
        "jobs": len(jobs_seen),
    }


def job_events_for_job(
    conn: sqlite3.Connection,
    job_id: str,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Lifecycle transition rows for one job, oldest first."""
    rows = conn.execute(
        """SELECT id, ts, from_state, to_state, event_name, actor, detail_json
           FROM job_events
           WHERE job_id = ?
           ORDER BY id ASC
           LIMIT ?""",
        (job_id, max(1, int(limit))),
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _row_dict(row)
        raw = item.get("detail_json")
        if raw:
            try:
                item["detail"] = json.loads(raw)
            except (TypeError, ValueError):
                item["detail"] = raw
        else:
            item["detail"] = None
        items.append(item)
    return items


def port_attempt_summary(
    conn: sqlite3.Connection,
    *,
    target: str | None,
    origin: str | None,
    window_hours: int,
    max_attempts: int,
) -> dict[str, Any] | None:
    """Recent bundle failures for a target/origin retry-cap window."""
    if not origin:
        return None
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=max(0, int(window_hours)))
    ).isoformat()
    row = conn.execute(
        """SELECT
             SUM(CASE WHEN result = 'failure' AND last_seen_at >= ?
                      THEN 1 ELSE 0 END) AS recent_failures,
             MAX(last_seen_at) AS last_attempt_at
           FROM bundles
           WHERE origin = ?
             AND (target = ? OR (? IS NULL AND target IS NULL))""",
        (cutoff, origin, target, target),
    ).fetchone()
    return {
        "target": target,
        "origin": origin,
        "window_hours": int(window_hours),
        "max_attempts": int(max_attempts),
        "recent_failures": int((row[0] if row else 0) or 0),
        "last_attempt_at": row[1] if row else None,
    }
