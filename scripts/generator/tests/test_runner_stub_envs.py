"""Tests for runner.stub_unprobed_envs — bridges the env-list UI
gap by upserting placeholder env_health_status rows for envs that
exist on disk but haven't been health-probed yet."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from dportsv3.agent import env_resolver, runner
from dportsv3.db.schema import init_db


@pytest.fixture
def in_memory_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    monkeypatch.setattr(runner, "_state_db_conn", conn)
    yield conn
    conn.close()


def _row_count(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM env_health_status"
    ).fetchone()["n"]


def test_stub_inserts_row_for_each_disk_env(in_memory_db, monkeypatch):
    monkeypatch.setattr(
        env_resolver, "list_available_envs",
        lambda: ("alpha", "beta", "gamma"),
    )
    inserted = runner.stub_unprobed_envs()
    assert inserted == 3
    assert _row_count(in_memory_db) == 3
    rows = in_memory_db.execute(
        "SELECT env, status FROM env_health_status ORDER BY env"
    ).fetchall()
    assert [(r["env"], r["status"]) for r in rows] == [
        ("alpha", "unprobed"), ("beta", "unprobed"), ("gamma", "unprobed"),
    ]


def test_stub_does_not_overwrite_real_probe(in_memory_db, monkeypatch):
    """A real probe row (status=ready/degraded/broken) must survive
    a subsequent stub run — INSERT OR IGNORE is load-bearing."""
    now = datetime.now(timezone.utc).isoformat()
    in_memory_db.execute(
        """INSERT INTO env_health_status
           (env, status, probed_at, operator_action, detail_json, updated_at)
           VALUES (?, 'ready', ?, NULL, '{"checks":[]}', ?)""",
        ("alpha", now, now),
    )
    in_memory_db.commit()

    monkeypatch.setattr(
        env_resolver, "list_available_envs",
        lambda: ("alpha", "beta"),
    )
    inserted = runner.stub_unprobed_envs()
    assert inserted == 1  # only beta is new
    rows = {r["env"]: r["status"] for r in in_memory_db.execute(
        "SELECT env, status FROM env_health_status"
    )}
    assert rows == {"alpha": "ready", "beta": "unprobed"}


def test_stub_with_no_disk_envs_returns_zero(in_memory_db, monkeypatch):
    monkeypatch.setattr(env_resolver, "list_available_envs", lambda: ())
    assert runner.stub_unprobed_envs() == 0
    assert _row_count(in_memory_db) == 0


def test_stub_idempotent(in_memory_db, monkeypatch):
    monkeypatch.setattr(
        env_resolver, "list_available_envs", lambda: ("only",),
    )
    assert runner.stub_unprobed_envs() == 1
    # Second call: row already there, no new inserts.
    assert runner.stub_unprobed_envs() == 0
    assert _row_count(in_memory_db) == 1


def test_stub_no_db_conn_is_safe(monkeypatch):
    """When state.db isn't open (e.g. partial init) stub should
    no-op rather than crash."""
    monkeypatch.setattr(runner, "_state_db_conn", None)
    monkeypatch.setattr(
        env_resolver, "list_available_envs",
        lambda: ("alpha",),
    )
    # Must not raise.
    assert runner.stub_unprobed_envs() == 0
