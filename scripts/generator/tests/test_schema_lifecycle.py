"""Schema smoke test for the Phase 1 lifecycle additions.

Phase 1 of the agentic framework introduces:

- a new ``job_events`` table for typed state-machine transitions
- two new columns on ``jobs``: ``last_transition_at``, ``retire_reason``

This test asserts the schema lands cleanly on a fresh DB via
``init_db()`` and is also idempotent (re-init has no effect).

Pure schema; no consumers are wired in this step.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from dportsv3.db.schema import init_db as init_state_db


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _indexes_for(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return {row["name"] for row in rows}


def test_job_events_table_exists_with_expected_columns(tmp_path):
    conn = _open(tmp_path / "state.db")
    init_state_db(conn)

    cols = _table_columns(conn, "job_events")
    assert cols == {
        "id",
        "ts",
        "job_id",
        "from_state",
        "to_state",
        "event_name",
        "actor",
        "detail_json",
    }


def test_job_events_index_on_job_id(tmp_path):
    conn = _open(tmp_path / "state.db")
    init_state_db(conn)

    assert "idx_job_events_job" in _indexes_for(conn, "job_events")


def test_jobs_has_new_lifecycle_columns(tmp_path):
    conn = _open(tmp_path / "state.db")
    init_state_db(conn)

    cols = _table_columns(conn, "jobs")
    assert "last_transition_at" in cols
    assert "retire_reason" in cols


def test_init_db_is_idempotent(tmp_path):
    db = tmp_path / "state.db"
    conn = _open(db)
    init_state_db(conn)
    # Second init on the same DB must not raise (the column-add
    # migrations are wrapped in try/except for the "already exists"
    # case; this test guards that path).
    init_state_db(conn)

    cols = _table_columns(conn, "jobs")
    assert "last_transition_at" in cols
    assert "retire_reason" in cols


def test_job_events_insert_smoke(tmp_path):
    """A direct insert into the new table should round-trip cleanly.

    This is the only behavioral check in this step — actual transition
    logic lands in Step 2 (lifecycle module). Here we just confirm
    the table accepts the shape we'll use.
    """
    conn = _open(tmp_path / "state.db")
    init_state_db(conn)

    conn.execute(
        """INSERT INTO job_events
           (ts, job_id, from_state, to_state, event_name, actor, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            "2026-05-20T10:00:00Z",
            "20260520-100000Z-test-foo_bar-1234.job",
            None,
            "queued",
            "hook_enqueued",
            "hook",
            '{"origin": "foo/bar"}',
        ),
    )
    conn.commit()

    row = conn.execute(
        "SELECT job_id, from_state, to_state, event_name, actor "
        "FROM job_events WHERE event_name = 'hook_enqueued'"
    ).fetchone()
    assert row is not None
    assert row["from_state"] is None
    assert row["to_state"] == "queued"
    assert row["actor"] == "hook"
