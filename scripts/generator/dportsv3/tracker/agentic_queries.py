"""SQL helpers for the tracker's agentic-read endpoints.

These queries read the state.db tables originally owned by the
(now-retired) state-server: ``runs``, ``bundles``, ``jobs``, ``events``,
``activity_log``, ``runner_status``, ``artifact_refs``.

Target filtering: ``bundles``, ``jobs``, ``runs`` carry a nullable
``target`` column added in step 5. Filter is applied as an equality
match when supplied. ``NULL``-target rows surface only when no filter
is set — they're legacy or filed by a writer that didn't know its
target.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from dportsv3.agent.lifecycle import ACTIVE_WORK_STATE_VALUES


def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {str(key): row[key] for key in row.keys()}


def _maybe(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return _row_dict(row) if row is not None else None


def _decode_extra_json(item: dict[str, Any]) -> dict[str, Any]:
    raw = item.get("extra_json")
    if raw:
        try:
            item["extra"] = json.loads(raw)
        except (TypeError, ValueError):
            item["extra"] = raw
    else:
        item["extra"] = None
    return item


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


def runner_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Singleton runner_status row, or a defaulted shape if unset."""
    row = conn.execute(
        "SELECT * FROM runner_status WHERE id = 1"
    ).fetchone()
    if row is None:
        return {"status": "unknown"}
    return _row_dict(row)


def get_active_env(conn: sqlite3.Connection) -> str | None:
    """Return the operator-selected active dev-env, or None if unset.

    Singleton row in ``tracker_active_env``. Source of truth for the
    runner's per-job-dispatch env resolution (precedence step 2) and
    for the verify-fix CLI's fallback when ``--env`` is omitted.
    """
    row = conn.execute(
        "SELECT env_name FROM tracker_active_env WHERE singleton = 1"
    ).fetchone()
    if row is None:
        return None
    val = row["env_name"]
    return val if isinstance(val, str) and val else None


def set_active_env(conn: sqlite3.Connection, env_name: str | None) -> None:
    """Upsert the active dev-env. ``None`` clears it.

    No server-side validation against the envs that actually exist —
    the runner / CLI surface a clear error on use if the name doesn't
    resolve. Validation here would couple the tracker to filesystem
    state it can't reliably read (tracker runs unprivileged).
    """
    from datetime import datetime, timezone  # noqa: PLC0415
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO tracker_active_env (singleton, env_name, set_at)
           VALUES (1, ?, ?)
           ON CONFLICT(singleton) DO UPDATE SET
             env_name = excluded.env_name,
             set_at   = excluded.set_at""",
        (env_name, now),
    )
    conn.commit()


def env_health_statuses(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Latest persisted health probe per dev-env."""
    rows = conn.execute(
        """SELECT env, status, probed_at, operator_action, detail_json, updated_at
           FROM env_health_status
           ORDER BY env ASC"""
    ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = _row_dict(row)
        raw = item.get("detail_json")
        checks: list[dict[str, Any]] = []
        if raw:
            try:
                detail = json.loads(raw)
                item["detail"] = detail
                checks = detail.get("checks") or [] if isinstance(detail, dict) else []
            except (TypeError, ValueError):
                item["detail"] = raw
        else:
            item["detail"] = None
        item["checks"] = checks
        items.append(item)
    return items


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


def bundles_for_run(
    conn: sqlite3.Connection, run_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM bundles WHERE run_id = ? ORDER BY ts_utc DESC LIMIT ?",
        (run_id, max(1, int(limit))),
    ).fetchall()
    return [_row_dict(row) for row in rows]


def list_manual_requests(
    conn: sqlite3.Connection,
    open_only: bool = True,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return rows from ``user_context_requests`` for the manual queue UI.

    Joins ``user_context`` so the operator can see when context has
    already been provided and at what rev. ``open_only`` filters to
    rows where the runner hasn't yet picked up the latest context
    (``last_context_rev_handled < user_context.context_rev``) OR where
    the request has no context yet (``user_context`` row absent or
    ``context_rev`` = 0).

    Rows are sorted oldest-pending first — operator queue discipline.
    """
    sql = """
        SELECT
            ucr.run_id              AS run_id,
            ucr.origin              AS origin,
            ucr.bundle_id           AS bundle_id,
            ucr.classification      AS classification,
            ucr.confidence          AS confidence,
            ucr.iteration           AS iteration,
            ucr.max_iterations      AS max_iterations,
            ucr.requested_at        AS requested_at,
            ucr.status              AS status,
            ucr.last_context_rev_handled
                                    AS last_context_rev_handled,
            COALESCE(uc.context_rev, 0)
                                    AS context_rev,
            uc.context_text         AS context_text,
            uc.updated_at           AS context_updated_at,
            b.target                AS target,
            (SELECT retire_reason FROM jobs
                WHERE origin = ucr.origin
                  AND target IS b.target
                  AND retire_reason IS NOT NULL
                ORDER BY last_seen_at DESC LIMIT 1)
                                    AS latest_retire_reason,
            (SELECT COUNT(*) FROM jobs
                WHERE origin = ucr.origin
                  AND target IS b.target
                  AND type = 'patch')
                                    AS patch_attempts
        FROM user_context_requests AS ucr
        LEFT JOIN user_context AS uc
          ON uc.run_id = ucr.run_id AND uc.origin = ucr.origin
        LEFT JOIN bundles AS b
          ON b.bundle_id = ucr.bundle_id
    """
    params: list[Any] = []
    if open_only:
        # "open" = the row is in ``pending`` status, which by
        # construction means operator action is awaited. The status
        # column is set to ``pending`` on every triage MANUAL/retry-
        # cap escalation, and flipped to ``retriage_enqueued`` when
        # the runner sweep picks up a new operator context.
        #
        # The prior filter required ``context_rev > last_handled``
        # OR ``(both = 0)``, which excluded rows in the
        # "operator-submitted-context, runner-processed-it, agent-
        # re-escalated" state — the queue went empty while the
        # bundle sat in ``escalated_manual`` with operator action
        # implicitly required. Dropping that predicate so a pending
        # status alone makes a row visible; the runner's own sweep
        # gate (``process_user_context_updates``) keeps the
        # ``rev > handled`` check, so no infinite re-triage loops.
        sql += " WHERE ucr.status = 'pending'"
    sql += " ORDER BY ucr.requested_at ASC LIMIT ?"
    params.append(max(1, int(limit)))
    return [_row_dict(row) for row in conn.execute(sql, params).fetchall()]


def get_manual_request(
    conn: sqlite3.Connection,
    run_id: str,
    origin: str,
) -> dict[str, Any] | None:
    """Return the most-recent ``user_context_requests`` row for
    ``(run_id, origin)`` joined with any provided context and the
    bundle metadata. ``None`` if no such request exists."""
    row = conn.execute(
        """SELECT
               ucr.run_id              AS run_id,
               ucr.origin              AS origin,
               ucr.bundle_id           AS bundle_id,
               ucr.classification      AS classification,
               ucr.confidence          AS confidence,
               ucr.iteration           AS iteration,
               ucr.max_iterations      AS max_iterations,
               ucr.requested_at        AS requested_at,
               ucr.status              AS status,
               ucr.last_context_rev_handled
                                       AS last_context_rev_handled,
               COALESCE(uc.context_rev, 0)
                                       AS context_rev,
               uc.context_text         AS context_text,
               uc.updated_at           AS context_updated_at,
               b.target                AS target,
               (SELECT retire_reason FROM jobs
                   WHERE origin = ucr.origin
                     AND target IS b.target
                     AND retire_reason IS NOT NULL
                   ORDER BY last_seen_at DESC LIMIT 1)
                                       AS latest_retire_reason,
               (SELECT COUNT(*) FROM jobs
                   WHERE origin = ucr.origin
                     AND target IS b.target
                     AND type = 'patch')
                                       AS patch_attempts
           FROM user_context_requests AS ucr
           LEFT JOIN user_context AS uc
             ON uc.run_id = ucr.run_id AND uc.origin = ucr.origin
           LEFT JOIN bundles AS b
             ON b.bundle_id = ucr.bundle_id
           WHERE ucr.run_id = ? AND ucr.origin = ?
           ORDER BY ucr.requested_at DESC
           LIMIT 1""",
        (run_id, origin),
    ).fetchone()
    return _row_dict(row) if row is not None else None


def discard_manual_request(
    conn: sqlite3.Connection,
    run_id: str,
    origin: str,
    reason: str = "",
) -> bool:
    """Mark all open requests for ``(run_id, origin)`` as discarded.

    Affects every ``user_context_requests`` row matching the pair
    (there can be multiple, one per bundle iteration). Emits a
    ``manual_request_discarded`` event so the activity log surfaces
    operator intent.

    Returns True if at least one row was updated.
    """
    now = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """UPDATE user_context_requests
           SET status = 'discarded'
           WHERE run_id = ? AND origin = ?
             AND status != 'discarded'""",
        (run_id, origin),
    )
    changed = cur.rowcount > 0
    if changed:
        conn.execute(
            """INSERT INTO events (ts, type, data_json)
               VALUES (?, ?, ?)""",
            (now, "manual_request_discarded",
             json.dumps({"run_id": run_id, "origin": origin,
                         "reason": reason or None, "discarded_at": now})),
        )
    conn.commit()
    return changed


def upsert_user_context_text(
    conn: sqlite3.Connection,
    run_id: str,
    origin: str,
    context_text: str,
    submitted_by: str | None = None,
) -> int:
    """Set/replace the operator's hint text for ``(run_id, origin)``.

    Bumps ``context_rev`` by 1 on every write so the runner's
    ``process_user_context_updates`` loop picks it up. Mirrors the
    artifact-store's ``/v1/user-context`` write path; tracker writes
    directly because it shares ``state.db``. Emits a
    ``user_context_updated`` event for activity-log visibility.

    Step 29b: every write also appends an immutable row to
    ``user_context_history`` carrying this round's text +
    ``submitted_by``. ``user_context`` keeps overwriting (its
    callers expect a single current row); the history table is
    the audit/render source for ``manual_handoff.md``.

    Returns the new ``context_rev``.
    """
    now = datetime.now(timezone.utc).isoformat()
    row = conn.execute(
        "SELECT context_rev FROM user_context WHERE run_id = ? AND origin = ?",
        (run_id, origin),
    ).fetchone()
    if row:
        new_rev = int(row[0]) + 1
        conn.execute(
            """UPDATE user_context
               SET context_text = ?, updated_at = ?, context_rev = ?
               WHERE run_id = ? AND origin = ?""",
            (context_text, now, new_rev, run_id, origin),
        )
    else:
        new_rev = 1
        conn.execute(
            """INSERT INTO user_context
               (run_id, origin, context_text, updated_at, context_rev)
               VALUES (?, ?, ?, ?, ?)""",
            (run_id, origin, context_text, now, new_rev),
        )
    conn.execute(
        """INSERT INTO user_context_history
           (run_id, origin, context_rev, submitted_at, text, submitted_by)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (run_id, origin, new_rev, now, context_text, submitted_by),
    )
    conn.execute(
        """INSERT INTO events (ts, type, data_json)
           VALUES (?, ?, ?)""",
        (now, "user_context_updated",
         json.dumps({"run_id": run_id, "origin": origin,
                     "context_rev": new_rev, "updated_at": now})),
    )
    # Fresh operator context overrides a prior discard — the operator
    # changed their mind. Flip any discarded request rows back to
    # 'pending' so the runner sweep picks the new rev up.
    conn.execute(
        """UPDATE user_context_requests
           SET status = 'pending'
           WHERE run_id = ? AND origin = ? AND status = 'discarded'""",
        (run_id, origin),
    )
    conn.commit()
    return new_rev


def list_user_context_history(
    conn: sqlite3.Connection,
    run_id: str,
    origin: str,
) -> list[dict[str, Any]]:
    """Step 29b: return every operator-submitted context round for
    ``(run_id, origin)``, ordered oldest → newest.

    Each row carries ``context_rev``, ``submitted_at``, ``text``,
    ``submitted_by``. Returns ``[]`` if no rounds were submitted.
    ``manual_handoff.build_handoff_ctx`` consumes this to render
    the operator-context section.
    """
    rows = conn.execute(
        """SELECT context_rev, submitted_at, text, submitted_by
           FROM user_context_history
           WHERE run_id = ? AND origin = ?
           ORDER BY context_rev ASC, id ASC""",
        (run_id, origin),
    ).fetchall()
    return [
        {
            "context_rev": int(r["context_rev"]),
            "submitted_at": r["submitted_at"],
            "text": r["text"],
            "submitted_by": r["submitted_by"],
        }
        for r in rows
    ]


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


def is_origin_skipped(
    conn: sqlite3.Connection, target: str, origin: str,
) -> dict[str, Any] | None:
    """Return the open skip-flag row for (target, origin), or None.

    "Open" = ``cleared_at IS NULL``. The schema's partial-unique index
    guarantees at most one open row per pair, so this is a point
    lookup. Caller treats None as "not skipped, proceed normally."
    """
    row = conn.execute(
        """SELECT id, target, origin, set_by, set_at, reason, bundle_id,
                  cleared_at, cleared_by
           FROM origin_skip_flags
           WHERE target = ? AND origin = ? AND cleared_at IS NULL
           LIMIT 1""",
        (target, origin),
    ).fetchone()
    return _maybe(row)


def set_origin_skip(
    conn: sqlite3.Connection, *,
    target: str,
    origin: str,
    set_by: str,
    reason: str,
    bundle_id: str | None = None,
) -> int:
    """Open a skip lock on (target, origin). Returns the row id.

    Raises ``sqlite3.IntegrityError`` if a lock is already open for
    this pair (the partial-unique index enforces this). Callers
    invoke ``is_origin_skipped`` first and convert that into a 409
    at the HTTP layer rather than swallowing the integrity error
    here — the caller has more context about how to surface the
    conflict.
    """
    ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO origin_skip_flags
           (target, origin, set_by, set_at, reason, bundle_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (target, origin, set_by, ts, reason, bundle_id),
    )
    return int(cur.lastrowid or 0)


def clear_origin_skip(
    conn: sqlite3.Connection, *,
    target: str,
    origin: str,
    cleared_by: str,
) -> bool:
    """Close the open skip lock for (target, origin). Returns True
    if a row was cleared, False if no open lock existed (no-op)."""
    ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """UPDATE origin_skip_flags
           SET cleared_at = ?, cleared_by = ?
           WHERE target = ? AND origin = ? AND cleared_at IS NULL""",
        (ts, cleared_by, target, origin),
    )
    return cur.rowcount > 0


# ---------------------------------------------------------------------
# Step 11d-1: bundle_review_requests
# ---------------------------------------------------------------------


def insert_review_request(
    conn: sqlite3.Connection, *,
    bundle_id: str,
    provider: str,
    status: str = "created",
    provider_pr_id: str | None = None,
    url: str | None = None,
    branch: str | None = None,
    title: str | None = None,
    error: str | None = None,
    operator: str | None = None,
    error_signature: str | None = None,
    diff_sha256: str | None = None,
) -> int:
    """Append one ``bundle_review_requests`` row. Returns row id.

    Raises ``sqlite3.IntegrityError`` if the partial-unique index
    ``uq_brr_open_branch`` blocks a duplicate open delivery for the
    same ``(provider, branch)`` — caller (``deliver`` in
    ``delivery.orchestrator``) catches this and reconciles to the
    existing row rather than orphaning the upstream PR.
    """
    ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO bundle_review_requests
           (bundle_id, provider, provider_pr_id, url, branch, title,
            status, created_at, error, operator, error_signature,
            diff_sha256)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (bundle_id, provider, provider_pr_id, url, branch, title,
         status, ts, error, operator, error_signature, diff_sha256),
    )
    return int(cur.lastrowid or 0)


def latest_review_request_for_bundle(
    conn: sqlite3.Connection, bundle_id: str,
) -> dict[str, Any] | None:
    """Most-recent ``bundle_review_requests`` row for one bundle,
    or None. Drives the bundle detail page's "Delivery" card."""
    row = conn.execute(
        """SELECT id, bundle_id, provider, provider_pr_id, url, branch,
                  title, status, created_at, last_synced_at, error,
                  operator, error_signature, note, diff_sha256
           FROM bundle_review_requests
           WHERE bundle_id = ?
           ORDER BY id DESC LIMIT 1""",
        (bundle_id,),
    ).fetchone()
    return _maybe(row)


def find_open_review_request(
    conn: sqlite3.Connection, *,
    provider: str,
    branch: str,
) -> dict[str, Any] | None:
    """Idempotency lookup: return the open delivery row for
    ``(provider, branch)`` if one exists, else None.

    "Open" matches the partial-unique index condition: status NOT
    IN ('closed', 'merged', 'create_failed'). Caller uses this to
    decide between create-new and patch-existing-body.

    Keyed on branch (not error_signature) because the branch is what
    the provider keys on for find-or-create — matching that key means
    "provider returned updated" ↔ "we have an open row" stays in
    lockstep. The default branch template encodes (origin, target,
    signature_short) so genuine same-port re-deliveries still
    converge.
    """
    row = conn.execute(
        """SELECT id, bundle_id, provider, provider_pr_id, url, branch,
                  title, status, created_at, last_synced_at, error,
                  operator, error_signature, note, diff_sha256
           FROM bundle_review_requests
           WHERE provider = ? AND branch = ?
             AND status NOT IN ('closed', 'merged', 'create_failed')
           ORDER BY id DESC LIMIT 1""",
        (provider, branch),
    ).fetchone()
    return _maybe(row)


def update_review_request_status(
    conn: sqlite3.Connection, *,
    request_id: int,
    status: str,
    error: str | None = None,
    note: str | None = None,
    provider_pr_id: str | None = None,
    url: str | None = None,
    branch: str | None = None,
    diff_sha256: str | None = None,
) -> bool:
    """Move a delivery row's status. Used for transitions like
    ``created`` → ``closed``/``merged`` (operator action), or
    ``created`` → ``updated`` on idempotency hits, or to attach
    PR-side data when the provider-create returns asynchronously.

    Returns True if a row was updated, False if no row matched
    ``request_id``. Always bumps ``last_synced_at``.

    ``note`` is the operator-supplied annotation for manual
    status updates (11d-5 / Finding 7 of the review). Lives in
    its own column rather than being co-located with ``error`` —
    the latter is for create-time failures only.
    """
    ts = datetime.now(timezone.utc).isoformat()
    # Build the SET clause dynamically so we don't blow away
    # fields the caller didn't pass.
    sets = ["status = ?", "last_synced_at = ?"]
    args: list[object] = [status, ts]
    if error is not None:
        sets.append("error = ?")
        args.append(error)
    if note is not None:
        sets.append("note = ?")
        args.append(note)
    if provider_pr_id is not None:
        sets.append("provider_pr_id = ?")
        args.append(provider_pr_id)
    if url is not None:
        sets.append("url = ?")
        args.append(url)
    if branch is not None:
        sets.append("branch = ?")
        args.append(branch)
    if diff_sha256 is not None:
        sets.append("diff_sha256 = ?")
        args.append(diff_sha256)
    args.append(request_id)
    cur = conn.execute(
        f"UPDATE bundle_review_requests SET {', '.join(sets)} "
        f"WHERE id = ?",
        tuple(args),
    )
    return cur.rowcount > 0
