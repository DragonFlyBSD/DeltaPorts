"""Step 10a — reap_stale_queued safety + happy path.

The helper must reap a QUEUED row only when BOTH:

- ``last_transition_at`` (or ``last_seen_at``) is older than
  ``max_age_seconds`` (default 1h).
- The corresponding ``.job`` file is missing from
  ``queue_root/pending/``.

Either condition alone is too aggressive — a freshly restarted
runner must not reap brand-new queued work that simply hasn't been
claimed yet, and a queued row with the file still on disk is
legitimate work waiting in line.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from dportsv3.agent import lifecycle
from dportsv3.agent.lifecycle import JobEvent, JobState
from dportsv3.db.schema import init_db


def _iso(ts: datetime) -> str:
    return ts.isoformat()


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.fixture
def queue_root(tmp_path):
    qr = tmp_path / "queue"
    (qr / "pending").mkdir(parents=True)
    return qr


def _seed_queued(conn, job_id: str, age_seconds: int):
    """Insert a jobs row at state=queued with last_transition_at
    set to (now - age_seconds)."""
    ts = _iso(datetime.now(timezone.utc) - timedelta(seconds=age_seconds))
    # First drive the lifecycle transition so events table is consistent.
    # Then patch last_transition_at to the age we want.
    lifecycle.apply(conn, job_id, JobEvent.HOOK_ENQUEUED)
    conn.execute(
        "UPDATE jobs SET last_transition_at = ?, last_seen_at = ? "
        "WHERE job_id = ?",
        (ts, ts, job_id),
    )
    conn.commit()


# --- happy path -------------------------------------------------------------


def test_reaps_stale_queued_with_missing_file(conn, queue_root):
    _seed_queued(conn, "stale-1", age_seconds=7200)  # 2h old
    # No file in pending/.

    reaped = lifecycle.reap_stale_queued(
        conn, queue_root, max_age_seconds=3600,
    )
    assert reaped == ["stale-1"]
    assert lifecycle.current(conn, "stale-1") == JobState.DEAD
    # retire_reason = runner_restart (REAP_ORPHAN's mapped reason).
    row = conn.execute(
        "SELECT retire_reason FROM jobs WHERE job_id = 'stale-1'"
    ).fetchone()
    assert row["retire_reason"] == "runner_restart"


def test_reaps_multiple_stale(conn, queue_root):
    for i in range(3):
        _seed_queued(conn, f"stale-{i}", age_seconds=7200)
    reaped = lifecycle.reap_stale_queued(conn, queue_root, max_age_seconds=3600)
    assert set(reaped) == {"stale-0", "stale-1", "stale-2"}


# --- safety: do NOT reap legitimate work ------------------------------------


def test_does_not_reap_fresh_queued_even_if_file_missing(conn, queue_root):
    """A brand-new queued row (file not yet written) must not be reaped."""
    _seed_queued(conn, "fresh-1", age_seconds=30)  # 30s old

    reaped = lifecycle.reap_stale_queued(conn, queue_root, max_age_seconds=3600)
    assert reaped == []
    assert lifecycle.current(conn, "fresh-1") == JobState.QUEUED


def test_does_not_reap_old_queued_if_file_present(conn, queue_root):
    """A queued row that's old but whose .job file IS still on disk is
    legitimate work waiting to be claimed. Do not touch."""
    _seed_queued(conn, "old-but-present", age_seconds=7200)
    # File present.
    (queue_root / "pending" / "old-but-present").write_text("type=triage\n")

    reaped = lifecycle.reap_stale_queued(conn, queue_root, max_age_seconds=3600)
    assert reaped == []
    assert lifecycle.current(conn, "old-but-present") == JobState.QUEUED


def test_does_not_reap_terminal_states(conn, queue_root):
    """A 4-hour-old DONE/DEAD/ESCALATED row is not queued and must
    not be touched."""
    lifecycle.apply(conn, "done-1", JobEvent.HOOK_ENQUEUED)
    lifecycle.apply(conn, "done-1", JobEvent.CLAIM)
    lifecycle.apply(conn, "done-1", JobEvent.PATCH_START)
    lifecycle.apply(conn, "done-1", JobEvent.PATCH_OK)
    lifecycle.apply(conn, "done-1", JobEvent.VERIFY_OK)
    assert lifecycle.current(conn, "done-1") == JobState.DONE

    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=7200))
    conn.execute(
        "UPDATE jobs SET last_transition_at = ?, last_seen_at = ? WHERE job_id = 'done-1'",
        (old, old),
    )
    conn.commit()

    reaped = lifecycle.reap_stale_queued(conn, queue_root, max_age_seconds=3600)
    assert reaped == []
    assert lifecycle.current(conn, "done-1") == JobState.DONE


def test_does_not_reap_inflight_states(conn, queue_root):
    """Inflight states are reap_orphans' territory, not stale_queued's."""
    lifecycle.apply(conn, "claimed-1", JobEvent.HOOK_ENQUEUED)
    lifecycle.apply(conn, "claimed-1", JobEvent.CLAIM)
    old = _iso(datetime.now(timezone.utc) - timedelta(seconds=7200))
    conn.execute(
        "UPDATE jobs SET last_transition_at = ?, last_seen_at = ? "
        "WHERE job_id = 'claimed-1'",
        (old, old),
    )
    conn.commit()

    reaped = lifecycle.reap_stale_queued(conn, queue_root, max_age_seconds=3600)
    assert reaped == []
    assert lifecycle.current(conn, "claimed-1") == JobState.CLAIMED


# --- edge ------------------------------------------------------------------


def test_age_threshold_is_inclusive(conn, queue_root):
    """A row exactly at the threshold counts as stale."""
    _seed_queued(conn, "edge", age_seconds=3600)
    reaped = lifecycle.reap_stale_queued(conn, queue_root, max_age_seconds=3600)
    assert reaped == ["edge"]


def test_returns_empty_when_no_rows(conn, queue_root):
    assert lifecycle.reap_stale_queued(conn, queue_root) == []


def test_lifecycle_allows_queued_to_dead_via_reap_orphan(conn):
    """The new transition entry — verifiable in isolation from the helper."""
    lifecycle.apply(conn, "j-direct", JobEvent.HOOK_ENQUEUED)
    assert lifecycle.current(conn, "j-direct") == JobState.QUEUED
    new_state = lifecycle.apply(conn, "j-direct", JobEvent.REAP_ORPHAN)
    assert new_state == JobState.DEAD
