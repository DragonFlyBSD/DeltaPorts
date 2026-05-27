"""Step 28-extra: skip-flag check in process_patch_job /
process_convert_job.

The 28a triage-side check closed the obvious case, but jobs already
enqueued before the take-over (patch jobs queued from prior triage,
convert jobs from the queue) would still run unless the same check
fires at their dispatch tops. These tests exercise the helper
directly with job_type='patch' / 'convert' to confirm the activity
stage names + lifecycle transitions are correct.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dportsv3.db.schema import init_db
from dportsv3.tracker.agentic_queries import set_origin_skip


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def state_db(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    set_origin_skip(
        conn, target="@2026Q2", origin="devel/locked",
        set_by="alice", reason="manual fix",
        bundle_id="b-staker",
    )
    return conn


def _wire_runner(rm, conn, activity_sink):
    rm._state_db_conn = conn
    rm.activity_log = lambda queue_root, stage, message, **kw: (
        activity_sink.append({"stage": stage, "message": message, **kw})
    )


def _seed_job(conn, job_id, state, job_type):
    conn.execute(
        """INSERT INTO jobs (job_id, state, type, origin, target,
                             last_seen_at)
           VALUES (?, ?, ?, 'devel/locked', '@2026Q2', ?)""",
        (job_id, state, job_type, _now()),
    )


# ---------------------------------------------------------------------
# Helper directly: job_type label maps into stage name
# ---------------------------------------------------------------------


@pytest.mark.parametrize("job_type,expected_stage,from_state", [
    ("triage", "triage_skipped_origin_locked", "triaging"),
    ("patch", "patch_skipped_origin_locked", "patching"),
    ("convert", "convert_skipped_origin_locked", "converting"),
])
def test_skip_check_emits_per_job_type_stage_and_transitions(
    tmp_path, state_db, monkeypatch, job_type, expected_stage, from_state,
):
    from dportsv3.agent import runner as runner_mod
    import dportsv3.agent.runner as rm

    _seed_job(state_db, f"{job_type}-test.job", from_state, job_type)

    activity_rows: list[dict] = []
    original_activity = rm.activity_log
    _wire_runner(rm, state_db, activity_rows)
    try:
        outcome = runner_mod._maybe_skip_locked_origin(
            queue_root=tmp_path,
            job={"target": "@2026Q2"},
            job_id=f"{job_type}-test.job",
            sibling_paths=None,
            origin="devel/locked",
            job_type=job_type,
        )
    finally:
        rm.activity_log = original_activity
        rm._state_db_conn = None

    assert outcome is not None
    success, status = outcome
    assert success is True
    assert "origin_locked_by:b-staker" in status

    # Activity row tagged with the per-job-type stage name.
    matching = [r for r in activity_rows if r["stage"] == expected_stage]
    assert len(matching) == 1, activity_rows
    extra = matching[0]["extra"]
    assert extra["job_type"] == job_type
    assert extra["origin"] == "devel/locked"
    assert extra["locking_bundle_id"] == "b-staker"

    # Lifecycle: job retired DEAD with retire_reason='origin_locked'.
    row = state_db.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id = ?",
        (f"{job_type}-test.job",),
    ).fetchone()
    assert row["state"] == "dead"
    assert row["retire_reason"] == "origin_locked"


def test_skip_check_default_job_type_is_triage(tmp_path, state_db):
    """Backwards compat: default job_type stays 'triage' so the
    original 28a call site (which doesn't pass job_type explicitly)
    keeps emitting the same stage name."""
    from dportsv3.agent import runner as runner_mod
    import dportsv3.agent.runner as rm

    _seed_job(state_db, "default-test.job", "triaging", "triage")

    activity_rows: list[dict] = []
    original_activity = rm.activity_log
    _wire_runner(rm, state_db, activity_rows)
    try:
        runner_mod._maybe_skip_locked_origin(
            queue_root=tmp_path,
            job={"target": "@2026Q2"},
            job_id="default-test.job",
            sibling_paths=None,
            origin="devel/locked",
            # no job_type — defaults to "triage"
        )
    finally:
        rm.activity_log = original_activity
        rm._state_db_conn = None

    stages = [r["stage"] for r in activity_rows]
    assert "triage_skipped_origin_locked" in stages


def test_skip_check_unlocked_origin_proceeds(tmp_path, state_db):
    """No lock on the (target, origin) → returns None for every
    job_type."""
    from dportsv3.agent import runner as runner_mod
    import dportsv3.agent.runner as rm

    activity_rows: list[dict] = []
    original_activity = rm.activity_log
    _wire_runner(rm, state_db, activity_rows)
    try:
        for jt in ("triage", "patch", "convert"):
            outcome = runner_mod._maybe_skip_locked_origin(
                queue_root=tmp_path,
                job={"target": "@2026Q2"},
                job_id=f"{jt}-unlocked.job",
                sibling_paths=None,
                origin="devel/not-locked",
                job_type=jt,
            )
            assert outcome is None, jt
    finally:
        rm.activity_log = original_activity
        rm._state_db_conn = None
