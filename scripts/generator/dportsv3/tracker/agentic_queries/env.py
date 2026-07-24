"""Active-env config + env-health reads for the tracker's agentic endpoints."""

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
