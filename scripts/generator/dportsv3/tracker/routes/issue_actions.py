"""Issue-level operator-action routes: mute / unmute / resolve / reopen.

The occurrence-level fix actions (accept/reject/…) stay on the bundle;
these act on the fingerprinted *problem*. Each endpoint checks the
authoritative gate (`issue_state.issue_action_allowed`) then writes the
transition under one `BEGIN IMMEDIATE`, the same single-writer discipline
the bundle actions and the merge reconciler use.

The transition bodies are module-level functions taking a write
connection so they can be unit-tested without a live app; each writes the
new state, its audit timestamps, and one issue event.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from dportsv3.tracker import issue_state
from dportsv3.tracker.agentic_queries import get_issue
from dportsv3.tracker.routes._common import HTTPException


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _recompute_open_state(write_conn: sqlite3.Connection, issue_key: str) -> str:
    """Whether an issue returning to the worklist is ``regressed`` or just
    ``unresolved``.

    Regressed is the literal "was fixed, then came back" signal: the issue
    has a ``resolved_at`` and at least one occurrence after it. A muted
    issue was always open when muted (the gate forbids muting a resolved
    one), so this exactly reconstructs its pre-mute open state.
    """
    row = write_conn.execute(
        "SELECT resolved_at FROM issues WHERE issue_key = ?", (issue_key,)
    ).fetchone()
    resolved_at = (row["resolved_at"] if row is not None else None)
    if resolved_at:
        later = write_conn.execute(
            "SELECT 1 FROM bundles WHERE issue_key = ? AND ts_utc > ? LIMIT 1",
            (issue_key, resolved_at),
        ).fetchone()
        if later is not None:
            return issue_state.ISSUE_REGRESSED
    return issue_state.ISSUE_UNRESOLVED


def mute_issue(write_conn: sqlite3.Connection, issue_key: str, *,
               now: str, actor: str) -> str:
    """Silence an issue: drops it from surfacing and (WS8) stops
    auto-triage. Returns the new state (``muted``)."""
    write_conn.execute(
        "UPDATE issues SET state = 'muted', muted_at = ?, muted_by = ?, "
        "updated_at = ? WHERE issue_key = ?",
        (now, actor, now, issue_key),
    )
    from dportsv3.artifact_store import emit_event  # noqa: PLC0415
    emit_event(write_conn, "issue_muted",
               {"issue_key": issue_key, "actor": actor})
    return issue_state.ISSUE_MUTED


def unmute_issue(write_conn: sqlite3.Connection, issue_key: str, *,
                 now: str, actor: str) -> str:
    """Return a muted issue to the worklist, recomputing unresolved vs
    regressed from its occurrences. Returns the new state."""
    new_state = _recompute_open_state(write_conn, issue_key)
    write_conn.execute(
        "UPDATE issues SET state = ?, muted_at = NULL, muted_by = NULL, "
        "updated_at = ? WHERE issue_key = ?",
        (new_state, now, issue_key),
    )
    from dportsv3.artifact_store import emit_event  # noqa: PLC0415
    emit_event(write_conn, "issue_unmuted",
               {"issue_key": issue_key, "actor": actor, "new_state": new_state})
    return new_state


def resolve_issue(write_conn: sqlite3.Connection, issue_key: str, *,
                  now: str, actor: str) -> str:
    """Manually mark an issue resolved (operator judgement, no merge).
    Returns the new state (``resolved``)."""
    write_conn.execute(
        "UPDATE issues SET state = 'resolved', resolved_at = ?, "
        "updated_at = ? WHERE issue_key = ?",
        (now, now, issue_key),
    )
    from dportsv3.artifact_store import emit_event  # noqa: PLC0415
    emit_event(write_conn, "issue_resolved",
               {"issue_key": issue_key, "actor": actor, "source": "manual"})
    return issue_state.ISSUE_RESOLVED


def reopen_issue(write_conn: sqlite3.Connection, issue_key: str, *,
                 now: str, actor: str) -> str:
    """Reopen a resolved issue (operator says it's not actually fixed):
    back to ``unresolved``, resolution history cleared. Returns the new
    state."""
    write_conn.execute(
        "UPDATE issues SET state = 'unresolved', resolved_at = NULL, "
        "regressed_at = NULL, reopened_at = ?, reopened_by = ?, "
        "updated_at = ? WHERE issue_key = ?",
        (now, actor, now, issue_key),
    )
    from dportsv3.artifact_store import emit_event  # noqa: PLC0415
    emit_event(write_conn, "issue_reopened",
               {"issue_key": issue_key, "actor": actor})
    return issue_state.ISSUE_UNRESOLVED


_ACTIONS = {
    "mute": mute_issue,
    "unmute": unmute_issue,
    "resolve": resolve_issue,
    "reopen": reopen_issue,
}


def register(app, ctx):
    _conn = ctx.conn

    def _do(action: str, issue_key: str, body: dict[str, Any] | None) -> dict[str, Any]:
        with _conn() as conn:
            issue = get_issue(conn, issue_key)
        if issue is None:
            raise HTTPException(status_code=404,
                                detail=f"Unknown issue: {issue_key}")
        state = issue.get("state")
        if not issue_state.issue_action_allowed(action, state):
            raise HTTPException(
                status_code=409,
                detail=f"Cannot {action} an issue in state {state!r}",
            )
        actor = str((body or {}).get("actor") or "operator")
        now = _now()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        write_conn.execute("PRAGMA busy_timeout=5000")
        try:
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                new_state = _ACTIONS[action](
                    write_conn, issue_key, now=now, actor=actor)
                write_conn.execute("COMMIT")
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
        finally:
            write_conn.close()
        return {"ok": True, "issue_key": issue_key,
                "state": new_state, "actor": actor}

    @app.post("/api/issues/{issue_key}/mute")
    def api_issue_mute(issue_key: str, body: dict[str, Any] | None = None):
        return _do("mute", issue_key, body)

    @app.post("/api/issues/{issue_key}/unmute")
    def api_issue_unmute(issue_key: str, body: dict[str, Any] | None = None):
        return _do("unmute", issue_key, body)

    @app.post("/api/issues/{issue_key}/resolve")
    def api_issue_resolve(issue_key: str, body: dict[str, Any] | None = None):
        return _do("resolve", issue_key, body)

    @app.post("/api/issues/{issue_key}/reopen")
    def api_issue_reopen(issue_key: str, body: dict[str, Any] | None = None):
        return _do("reopen", issue_key, body)
