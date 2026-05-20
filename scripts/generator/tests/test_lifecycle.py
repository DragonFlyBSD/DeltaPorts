"""Unit tests for the job lifecycle state machine.

Phase 1 Step 2. Covers:
- every defined transition fires cleanly + writes exactly one event
- disallowed transitions raise and write no row
- ``current()`` reads the event log when the jobs.state cache is
  stale or holds a legacy value
- ``history()`` returns rows in id order
- ``reap_orphans()`` transitions only inflight-ish states
- concurrent ``apply()`` doesn't double-apply (sqlite WAL guard)
- full happy path produces the expected 7 events
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from dportsv3.agent.lifecycle import (
    IllegalTransition,
    JobEvent,
    JobState,
    TRANSITIONS,
    apply,
    current,
    history,
    reap_orphans,
)
from dportsv3.db.schema import init_db as init_state_db


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), isolation_level=None, timeout=5.0)
    conn.row_factory = sqlite3.Row
    init_state_db(conn)
    return conn


def _enqueue(conn: sqlite3.Connection, job_id: str = "job-1") -> JobState:
    return apply(conn, job_id, JobEvent.HOOK_ENQUEUED, actor="hook",
                 detail={"origin": "foo/bar"})


def test_hook_enqueued_creates_queued_job(tmp_path):
    conn = _open(tmp_path / "state.db")
    state = _enqueue(conn)
    assert state == JobState.QUEUED
    assert current(conn, "job-1") == JobState.QUEUED


def test_happy_path_produces_seven_events(tmp_path):
    conn = _open(tmp_path / "state.db")
    seq = [
        (JobEvent.HOOK_ENQUEUED, JobState.QUEUED),
        (JobEvent.CLAIM,         JobState.CLAIMED),
        (JobEvent.TRIAGE_START,  JobState.TRIAGING),
        (JobEvent.TRIAGE_OK,     JobState.TRIAGED),
        (JobEvent.PATCH_START,   JobState.PATCHING),
        (JobEvent.PATCH_OK,      JobState.VERIFYING),
        (JobEvent.VERIFY_OK,     JobState.DONE),
    ]
    for event, expected in seq:
        actual = apply(conn, "happy", event)
        assert actual == expected

    h = history(conn, "happy")
    assert len(h) == 7
    assert h[0]["from_state"] is None
    assert h[0]["to_state"] == "queued"
    assert h[-1]["to_state"] == "done"
    # in order, each from_state matches prior to_state
    for i in range(1, len(h)):
        assert h[i]["from_state"] == h[i - 1]["to_state"]


def test_every_transition_in_table_is_reachable(tmp_path):
    """Smoke-check every TRANSITIONS entry — drive each transition
    in isolation from a setup-walked initial state."""
    walk_paths = {
        # to-state: shortest event sequence that lands a fresh job there
        JobState.QUEUED:    [JobEvent.HOOK_ENQUEUED],
        JobState.CLAIMED:   [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM],
        JobState.TRIAGING:  [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START],
        JobState.TRIAGED:   [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK],
        JobState.PATCHING:  [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK, JobEvent.PATCH_START],
        JobState.VERIFYING: [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK, JobEvent.PATCH_START, JobEvent.PATCH_OK],
    }

    for i, ((from_state, event), to_state) in enumerate(TRANSITIONS.items()):
        if from_state is None:
            continue  # initial transitions covered by other tests
        conn = _open(tmp_path / f"state-{i}.db")
        # walk to the from_state
        jid = f"walked-{i}"
        for walk_event in walk_paths[from_state]:
            apply(conn, jid, walk_event)
        # fire the transition under test
        result = apply(conn, jid, event)
        assert result == to_state, f"{from_state} + {event} should -> {to_state}, got {result}"
        conn.close()


def test_illegal_transition_raises_and_writes_no_row(tmp_path):
    conn = _open(tmp_path / "state.db")
    _enqueue(conn)
    # QUEUED → PATCH_START is not in TRANSITIONS
    with pytest.raises(IllegalTransition):
        apply(conn, "job-1", JobEvent.PATCH_START)
    # only the initial enqueued event is in the log
    h = history(conn, "job-1")
    assert len(h) == 1
    assert h[0]["event_name"] == "hook_enqueued"


def test_current_falls_back_to_event_log_when_cache_legacy(tmp_path):
    """If jobs.state holds a legacy untyped value (the pre-cutover
    'pending'/'inflight'/etc.), current() should ignore it and read
    the event log."""
    conn = _open(tmp_path / "state.db")
    _enqueue(conn)
    # simulate a legacy upsert clobbering the cache
    conn.execute("UPDATE jobs SET state = 'pending' WHERE job_id = ?", ("job-1",))
    assert current(conn, "job-1") == JobState.QUEUED


def test_current_returns_none_for_unknown_job(tmp_path):
    conn = _open(tmp_path / "state.db")
    assert current(conn, "never-seen") is None


def test_history_in_chronological_order(tmp_path):
    conn = _open(tmp_path / "state.db")
    for event in [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START]:
        apply(conn, "job-1", event)
    h = history(conn, "job-1")
    assert [r["event_name"] for r in h] == ["hook_enqueued", "claim", "triage_start"]
    ids = [r["id"] for r in h]
    assert ids == sorted(ids)


def test_terminal_states_set_retire_reason(tmp_path):
    conn = _open(tmp_path / "state.db")
    apply(conn, "j", JobEvent.HOOK_ENQUEUED)
    apply(conn, "j", JobEvent.CLAIM)
    apply(conn, "j", JobEvent.TRIAGE_START)
    apply(conn, "j", JobEvent.TRIAGE_FAIL)
    row = conn.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id = 'j'"
    ).fetchone()
    assert row["state"] == "dead"
    assert row["retire_reason"] == "triage_failed"


def test_reap_orphans_transitions_inflight_only(tmp_path):
    conn = _open(tmp_path / "state.db")
    # Build one job in each terminal / queued / inflight state.
    apply(conn, "q",  JobEvent.HOOK_ENQUEUED)                            # QUEUED
    apply(conn, "cl", JobEvent.HOOK_ENQUEUED); apply(conn, "cl", JobEvent.CLAIM)  # CLAIMED
    apply(conn, "tr", JobEvent.HOOK_ENQUEUED); apply(conn, "tr", JobEvent.CLAIM); apply(conn, "tr", JobEvent.TRIAGE_START)  # TRIAGING
    apply(conn, "vr", JobEvent.HOOK_ENQUEUED); apply(conn, "vr", JobEvent.CLAIM); apply(conn, "vr", JobEvent.TRIAGE_START); apply(conn, "vr", JobEvent.TRIAGE_OK); apply(conn, "vr", JobEvent.PATCH_START); apply(conn, "vr", JobEvent.PATCH_OK)  # VERIFYING
    apply(conn, "dn", JobEvent.HOOK_ENQUEUED); apply(conn, "dn", JobEvent.CLAIM); apply(conn, "dn", JobEvent.TRIAGE_START); apply(conn, "dn", JobEvent.TRIAGE_OK); apply(conn, "dn", JobEvent.PATCH_START); apply(conn, "dn", JobEvent.PATCH_OK); apply(conn, "dn", JobEvent.VERIFY_OK)  # DONE

    n = reap_orphans(conn)
    assert n == 3   # cl, tr, vr — not q (queued is fine) and not dn (done is terminal)

    assert current(conn, "q") == JobState.QUEUED
    assert current(conn, "cl") == JobState.DEAD
    assert current(conn, "tr") == JobState.DEAD
    assert current(conn, "vr") == JobState.DEAD
    assert current(conn, "dn") == JobState.DONE

    # reaped jobs have retire_reason = "runner_restart"
    for jid in ("cl", "tr", "vr"):
        r = conn.execute(
            "SELECT retire_reason FROM jobs WHERE job_id = ?", (jid,)
        ).fetchone()
        assert r["retire_reason"] == "runner_restart"


def test_concurrent_apply_no_double_transition(tmp_path):
    """Two threads racing to apply CLAIM on the same QUEUED job: one
    wins, one raises IllegalTransition (current became CLAIMED before
    its check) or sqlite.OperationalError on busy timeout."""
    db = tmp_path / "state.db"
    conn0 = _open(db)
    _enqueue(conn0, "race")
    conn0.close()

    results: list[tuple[str, Exception | JobState]] = []

    def worker(label: str):
        c = sqlite3.connect(str(db), isolation_level=None, timeout=5.0)
        c.row_factory = sqlite3.Row
        try:
            res = apply(c, "race", JobEvent.CLAIM, actor=label)
            results.append((label, res))
        except Exception as exc:  # noqa: BLE001
            results.append((label, exc))
        finally:
            c.close()

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # one succeeded, one failed (either IllegalTransition or
    # OperationalError under contention)
    successes = [r for r in results if isinstance(r[1], JobState)]
    failures = [r for r in results if not isinstance(r[1], JobState)]
    assert len(successes) == 1
    assert len(failures) == 1

    final = sqlite3.connect(str(db))
    final.row_factory = sqlite3.Row
    rows = final.execute(
        "SELECT event_name FROM job_events WHERE job_id = 'race' AND event_name = 'claim'"
    ).fetchall()
    assert len(rows) == 1   # exactly one CLAIM event landed
    final.close()


def test_env_broken_from_any_active_state(tmp_path):
    """ENV_BROKEN should fire from any of CLAIMED, TRIAGING,
    TRIAGED, PATCHING, VERIFYING."""
    for start_state, walk in [
        (JobState.CLAIMED,    [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM]),
        (JobState.TRIAGING,   [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START]),
        (JobState.TRIAGED,    [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK]),
        (JobState.PATCHING,   [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK, JobEvent.PATCH_START]),
        (JobState.VERIFYING,  [JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK, JobEvent.PATCH_START, JobEvent.PATCH_OK]),
    ]:
        conn = _open(tmp_path / f"eb-{start_state.value}.db")
        jid = f"eb-{start_state.value}"
        for e in walk:
            apply(conn, jid, e)
        assert current(conn, jid) == start_state
        result = apply(conn, jid, JobEvent.ENV_BROKEN)
        assert result == JobState.DEAD
        r = conn.execute(
            "SELECT retire_reason FROM jobs WHERE job_id = ?", (jid,)
        ).fetchone()
        assert r["retire_reason"] == "env_broken"
        conn.close()
