"""Manual-queue + user-context reads/writes for the tracker's agentic endpoints."""

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
