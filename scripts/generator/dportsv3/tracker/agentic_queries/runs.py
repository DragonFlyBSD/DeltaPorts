"""Build-run reads for the tracker's agentic endpoints."""

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
