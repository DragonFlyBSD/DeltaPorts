"""Bundle reads + artifact refs for the tracker's agentic endpoints."""

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


def bundles_for_run(
    conn: sqlite3.Connection, run_id: str, limit: int = 200
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM bundles WHERE run_id = ? ORDER BY ts_utc DESC LIMIT ?",
        (run_id, max(1, int(limit))),
    ).fetchall()
    return [_row_dict(row) for row in rows]
