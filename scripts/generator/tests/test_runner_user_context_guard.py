"""Step 5 — duplicate-origin guard for process_user_context_updates.

When operator-submitted context arrives, the runner should enqueue a
new triage job *unless* a same-(run_id, origin) job is already in
flight. Without the guard, the next sweep would happily double-enqueue
on top of a job that's already triaging/patching, producing duplicate
work and noisy lifecycle events.

Active states (mirrored from dportsv3.agent.lifecycle.JobState):
queued, claimed, triaging, triaged, patching, verifying.
Terminal states (done, dead, escalated) do NOT block.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dportsv3.agent import runner
from dportsv3.db.schema import init_db as init_state_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed(conn: sqlite3.Connection, *, job_state: str | None) -> None:
    """Seed a run + user_context_request + user_context + optional same-
    origin job in the given state."""
    now = _now()
    conn.execute(
        """INSERT INTO runs (run_id, profile, target, ts_start, last_seen_at)
           VALUES (?, ?, ?, ?, ?)""",
        ("run-1", "main", "@main", now, now),
    )
    conn.execute(
        """INSERT INTO bundles
           (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        ("b-1", "run-1", "devel/foo", "", now, "fail", "@main", now),
    )
    conn.execute(
        """INSERT INTO user_context_requests
           (run_id, origin, bundle_id, confidence, classification,
            iteration, max_iterations, requested_at, status,
            last_context_rev_handled)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0)""",
        ("run-1", "devel/foo", "b-1", "low", "missing-dep", 1, 3, now),
    )
    conn.execute(
        """INSERT INTO user_context
           (run_id, origin, context_text, updated_at, context_rev)
           VALUES (?, ?, ?, ?, ?)""",
        ("run-1", "devel/foo", "try option B", now, 1),
    )
    if job_state is not None:
        conn.execute(
            """INSERT INTO jobs
               (job_id, state, type, origin, flavor, bundle_dir,
                created_ts_utc, path, last_seen_at, target)
               VALUES (?, ?, 'triage', ?, '', '', ?, '', ?, ?)""",
            (f"blocker-{job_state}", job_state, "devel/foo", now, now, "@main"),
        )
    conn.commit()


@pytest.fixture
def runner_db(tmp_path, monkeypatch):
    """Throwaway state.db wired into the runner module. Stubs
    enqueue_triage_job so we can assert it was/wasn't called."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_state_db(conn)
    monkeypatch.setattr(runner, "_state_db_conn", conn, raising=False)

    queue_root = tmp_path / "queue"
    for sub in ("pending", "inflight", "done", "failed"):
        (queue_root / sub).mkdir(parents=True)

    # Capture enqueue calls without writing a real job file.
    enqueued: list[dict] = []

    def fake_enqueue(qr, bundle_id, run_id, origin, profile, flavor,
                     iteration, max_iterations, previous_bundle, context_rev):
        enqueued.append({
            "bundle_id": bundle_id, "run_id": run_id, "origin": origin,
            "previous_bundle": previous_bundle, "context_rev": context_rev,
        })
        return queue_root / "pending" / f"fake-{len(enqueued)}.job"

    monkeypatch.setattr(runner, "enqueue_triage_job", fake_enqueue)
    monkeypatch.setattr(runner, "get_run_profile", lambda rid: "main")
    monkeypatch.setattr(runner, "get_bundle_flavor", lambda bid: "")
    monkeypatch.setattr(runner, "find_latest_bundle_id",
                        lambda rid, o: "b-1")

    yield conn, queue_root, enqueued
    conn.close()


# --- duplicate-origin guard --------------------------------------------------


@pytest.mark.parametrize("blocking_state", [
    "queued", "claimed", "triaging", "triaged", "patching", "verifying",
])
def test_active_same_origin_job_blocks_retriage(runner_db, blocking_state):
    conn, queue_root, enqueued = runner_db
    _seed(conn, job_state=blocking_state)

    runner.process_user_context_updates(queue_root)

    # No new triage enqueued; request stays pending for next sweep.
    assert enqueued == []
    row = conn.execute(
        "SELECT status, last_context_rev_handled FROM user_context_requests"
    ).fetchone()
    assert row["status"] == "pending"
    assert row["last_context_rev_handled"] == 0


@pytest.mark.parametrize("terminal_state", ["done", "dead", "escalated"])
def test_terminal_same_origin_job_does_not_block(runner_db, terminal_state):
    """A done/dead/escalated job is not "in flight" — the retry should
    fire normally."""
    conn, queue_root, enqueued = runner_db
    _seed(conn, job_state=terminal_state)

    runner.process_user_context_updates(queue_root)

    assert len(enqueued) == 1
    assert enqueued[0]["origin"] == "devel/foo"
    assert enqueued[0]["context_rev"] == 1
    row = conn.execute(
        "SELECT status, last_context_rev_handled FROM user_context_requests"
    ).fetchone()
    assert row["status"] == "retriage_enqueued"
    assert row["last_context_rev_handled"] == 1


def test_no_blocker_enqueues_retriage(runner_db):
    conn, queue_root, enqueued = runner_db
    _seed(conn, job_state=None)

    runner.process_user_context_updates(queue_root)

    assert len(enqueued) == 1
    assert enqueued[0]["previous_bundle"] == "b-1"


def test_blocker_for_different_origin_does_not_block(runner_db):
    """Active job for a different origin must not block this retry."""
    conn, queue_root, enqueued = runner_db
    _seed(conn, job_state=None)
    # Active job for *another* origin.
    conn.execute(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target)
           VALUES ('other', 'patching', 'patch', 'devel/other',
                   '', '', ?, '', ?, '@main')""",
        (_now(), _now()),
    )
    conn.commit()

    runner.process_user_context_updates(queue_root)

    assert len(enqueued) == 1


def test_block_skipped_when_state_db_unavailable(tmp_path, monkeypatch):
    """If _state_db_conn is None the guard returns None and the
    outer function's existing early-return handles it. Just sanity
    check we don't crash."""
    monkeypatch.setattr(runner, "_state_db_conn", None, raising=False)
    runner.process_user_context_updates(tmp_path)  # should not raise


def test_guard_returns_blocker_id(runner_db):
    """The helper returns the blocker's job_id so the activity_log
    message can include it for operator debugging."""
    conn, _, _ = runner_db
    _seed(conn, job_state="patching")
    blocker = runner._has_active_same_origin_job("run-1", "devel/foo")
    assert blocker == "blocker-patching"


def test_guard_returns_none_when_no_blocker(runner_db):
    conn, _, _ = runner_db
    _seed(conn, job_state=None)
    assert runner._has_active_same_origin_job("run-1", "devel/foo") is None
