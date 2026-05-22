"""Lifecycle transitions for convert jobs (Step 20c)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dportsv3.agent.lifecycle import (
    IllegalTransition,
    JobEvent,
    JobState,
    apply,
)
from dportsv3.db.schema import init_db


def _open(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _walk_to_converting(conn: sqlite3.Connection, jid: str) -> None:
    apply(conn, jid, JobEvent.HOOK_ENQUEUED)
    apply(conn, jid, JobEvent.CLAIM)
    apply(conn, jid, JobEvent.CONVERT_START)


def test_convert_happy_path_lands_done(tmp_path: Path) -> None:
    """Hook → claim → convert_start → convert_ok → DONE."""
    conn = _open(tmp_path / "convert-ok.db")
    _walk_to_converting(conn, "convert-1")
    state = apply(conn, "convert-1", JobEvent.CONVERT_OK)
    assert state == JobState.DONE
    row = conn.execute(
        "SELECT retire_reason FROM jobs WHERE job_id = ?", ("convert-1",),
    ).fetchone()
    # CONVERT_OK isn't in _TERMINAL_REASONS (it's a success, not a
    # retire) — DONE jobs leave retire_reason unset/null.
    assert row["retire_reason"] in (None, "")


def test_convert_failure_lands_dead_with_reason(tmp_path: Path) -> None:
    """convert_gave_up → DEAD with retire_reason='convert_failed'."""
    conn = _open(tmp_path / "convert-fail.db")
    _walk_to_converting(conn, "convert-2")
    state = apply(conn, "convert-2", JobEvent.CONVERT_GAVE_UP)
    assert state == JobState.DEAD
    row = conn.execute(
        "SELECT retire_reason FROM jobs WHERE job_id = ?", ("convert-2",),
    ).fetchone()
    assert row["retire_reason"] == "convert_failed"


def test_convert_escalate_lands_escalated(tmp_path: Path) -> None:
    """Convert jobs can escalate to manual same as triage."""
    conn = _open(tmp_path / "convert-escalated.db")
    _walk_to_converting(conn, "convert-3")
    state = apply(conn, "convert-3", JobEvent.ESCALATE_MANUAL)
    assert state == JobState.ESCALATED


def test_convert_env_broken_lands_dead(tmp_path: Path) -> None:
    """env_broken can interrupt a converting job."""
    conn = _open(tmp_path / "convert-env-broken.db")
    _walk_to_converting(conn, "convert-4")
    state = apply(conn, "convert-4", JobEvent.ENV_BROKEN)
    assert state == JobState.DEAD


def test_convert_reap_orphan_lands_dead(tmp_path: Path) -> None:
    """Runner restart reaps mid-convert jobs."""
    conn = _open(tmp_path / "convert-reap.db")
    _walk_to_converting(conn, "convert-5")
    state = apply(conn, "convert-5", JobEvent.REAP_ORPHAN)
    assert state == JobState.DEAD


def test_convert_abandon_lands_dead(tmp_path: Path) -> None:
    """Operator can abandon a converting job."""
    conn = _open(tmp_path / "convert-abandon.db")
    _walk_to_converting(conn, "convert-6")
    state = apply(conn, "convert-6", JobEvent.ABANDON)
    assert state == JobState.DEAD


def test_convert_start_only_from_claimed(tmp_path: Path) -> None:
    """CONVERT_START is only valid from CLAIMED — firing from
    TRIAGING (or any other state) raises IllegalTransition."""
    conn = _open(tmp_path / "convert-illegal.db")
    apply(conn, "convert-7", JobEvent.HOOK_ENQUEUED)
    apply(conn, "convert-7", JobEvent.CLAIM)
    apply(conn, "convert-7", JobEvent.TRIAGE_START)
    with pytest.raises(IllegalTransition):
        apply(conn, "convert-7", JobEvent.CONVERT_START)


def test_converting_in_inflight_states(tmp_path: Path) -> None:
    """CONVERTING is in _INFLIGHT_STATES so the runner's startup
    orphan reap picks up abandoned converts."""
    from dportsv3.agent.lifecycle import _INFLIGHT_STATES
    assert JobState.CONVERTING in _INFLIGHT_STATES
