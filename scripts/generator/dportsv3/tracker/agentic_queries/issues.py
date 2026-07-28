"""Issue reads for the tracker's Sentry-model endpoints.

The `issues` table already carries its own rollups (`times_seen`,
`first_seen_at`, `last_seen_at`, `latest_bundle_id`), maintained by the
artifact-store writer (WS3), so these reads are plain SELECTs plus the
occurrence join — no aggregation. They feed `issue_state` (WS5): the
issues list/detail and the worklist-by-issue.

Default order is systemic-first (`times_seen` DESC) then most-recent
(`last_seen_at` DESC), matching how the worklist floats a repeatedly
failing problem to the top.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from typing import Any

from dportsv3.tracker.agentic_queries._util import _row_dict, _maybe


def list_issues(
    conn: sqlite3.Connection,
    *,
    target: str | None = None,
    origin: str | None = None,
    states: Iterable[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Issues (with their persisted rollups), newest-problem-first.

    Filters are all optional and ANDed: ``target`` / ``origin`` exact
    match, ``states`` an allow-list of lifecycle states.
    """
    sql = "SELECT * FROM issues"
    clauses: list[str] = []
    params: list[Any] = []
    if target is not None:
        clauses.append("target = ?")
        params.append(target)
    if origin is not None:
        clauses.append("origin = ?")
        params.append(origin)
    state_list = list(states) if states is not None else None
    if state_list:
        clauses.append(f"state IN ({','.join('?' * len(state_list))})")
        params.extend(state_list)
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY times_seen DESC, last_seen_at DESC, issue_key ASC LIMIT ?"
    params.append(max(1, int(limit)))
    return [_row_dict(row) for row in conn.execute(sql, params).fetchall()]


def occurrences_for_issue(
    conn: sqlite3.Connection, issue_key: str,
) -> list[dict[str, Any]]:
    """The occurrences (bundles) of one issue, newest-first."""
    rows = conn.execute(
        "SELECT * FROM bundles WHERE issue_key = ? "
        "ORDER BY ts_utc DESC, bundle_id DESC",
        (issue_key,),
    ).fetchall()
    return [_row_dict(r) for r in rows]


def get_issue(
    conn: sqlite3.Connection, issue_key: str,
) -> dict[str, Any] | None:
    """One issue row plus its ``occurrences`` (newest-first), or None."""
    issue = _maybe(
        conn.execute(
            "SELECT * FROM issues WHERE issue_key = ?", (issue_key,)
        ).fetchone()
    )
    if issue is None:
        return None
    issue["occurrences"] = occurrences_for_issue(conn, issue_key)
    return issue


def issue_for_bundle(
    conn: sqlite3.Connection, bundle_id: str,
) -> dict[str, Any] | None:
    """The issue a bundle belongs to (joined via ``issue_key``), or None
    when the bundle has no issue. Used by the runner's mute check."""
    return _maybe(
        conn.execute(
            "SELECT i.* FROM issues i "
            "JOIN bundles b ON b.issue_key = i.issue_key "
            "WHERE b.bundle_id = ?",
            (bundle_id,),
        ).fetchone()
    )


def issues_with_occurrences(
    conn: sqlite3.Connection,
    *,
    target: str | None = None,
    origin: str | None = None,
    states: Iterable[str] | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """`list_issues` with each issue's ``occurrences`` attached — the
    feed `issue_state.build_issue_worklist` consumes.

    Occurrences for the whole result set are fetched in one query (keyed
    by ``issue_key IN (...)``) rather than N per-issue round-trips.
    """
    issues = list_issues(
        conn, target=target, origin=origin, states=states, limit=limit
    )
    if not issues:
        return issues
    keys = [i["issue_key"] for i in issues]
    rows = conn.execute(
        f"SELECT * FROM bundles WHERE issue_key IN ({','.join('?' * len(keys))}) "
        "ORDER BY ts_utc DESC, bundle_id DESC",
        keys,
    ).fetchall()
    by_key: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        d = _row_dict(r)
        by_key.setdefault(d["issue_key"], []).append(d)
    for issue in issues:
        issue["occurrences"] = by_key.get(issue["issue_key"], [])
    return issues
