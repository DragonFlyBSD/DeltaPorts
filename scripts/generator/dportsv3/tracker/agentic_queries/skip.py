"""Origin skip-flag reads/writes for the tracker's agentic endpoints."""

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
