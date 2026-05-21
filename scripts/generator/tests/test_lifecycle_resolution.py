"""Lifecycle ``apply()`` propagates the agent's verdict to ``bundles.resolution``.

The hook records ``bundles.result='failure'`` at ingest and never updates it.
Without resolution propagation, after the patch agent fixes a build the UI
still shows the bundle as red/failed. ``apply()`` now writes
``bundles.resolution`` for PATCH_OK / PATCH_GAVE_UP / PATCH_BUDGET_OUT /
ESCALATE_MANUAL when ``detail['bundle_id']`` is present.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from dportsv3.agent import lifecycle
from dportsv3.agent.lifecycle import JobEvent, JobState
from dportsv3.db.schema import init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _seed_bundle(conn, bundle_id="b-1"):
    conn.execute(
        """INSERT INTO bundles
           (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at)
           VALUES (?, 'r-1', 'devel/foo', '', ?, 'failure', '@2026Q2', ?)""",
        (bundle_id, _now(), _now()),
    )
    conn.commit()


def _drive(conn, job_id, *events, bundle_id=None):
    """Drive a job through HOOK_ENQUEUED + a sequence of events.
    bundle_id (if given) is passed in the detail of every transition."""
    detail = {"bundle_id": bundle_id} if bundle_id else None
    lifecycle.apply(conn, job_id, JobEvent.HOOK_ENQUEUED, detail=detail)
    for ev in events:
        lifecycle.apply(conn, job_id, ev, detail=detail)


def _resolution(conn, bundle_id):
    row = conn.execute(
        "SELECT resolution FROM bundles WHERE bundle_id = ?", (bundle_id,)
    ).fetchone()
    return row["resolution"] if row else None


# --- happy paths -----------------------------------------------------------


def test_patch_ok_sets_agent_fixed(conn):
    _seed_bundle(conn)
    _drive(
        conn, "job-1",
        JobEvent.CLAIM, JobEvent.PATCH_START, JobEvent.PATCH_OK,
        bundle_id="b-1",
    )
    assert _resolution(conn, "b-1") == "agent_fixed"


def test_patch_gave_up_sets_agent_gave_up(conn):
    _seed_bundle(conn)
    _drive(
        conn, "job-2",
        JobEvent.CLAIM, JobEvent.PATCH_START, JobEvent.PATCH_GAVE_UP,
        bundle_id="b-1",
    )
    assert _resolution(conn, "b-1") == "agent_gave_up"


def test_patch_budget_out_sets_agent_budget_exhausted(conn):
    _seed_bundle(conn)
    _drive(
        conn, "job-3",
        JobEvent.CLAIM, JobEvent.PATCH_START, JobEvent.PATCH_BUDGET_OUT,
        bundle_id="b-1",
    )
    assert _resolution(conn, "b-1") == "agent_budget_exhausted"


def test_escalate_manual_sets_escalated_manual(conn):
    _seed_bundle(conn)
    _drive(
        conn, "job-4",
        JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK,
        JobEvent.ESCALATE_MANUAL,
        bundle_id="b-1",
    )
    assert _resolution(conn, "b-1") == "escalated_manual"


# --- non-effecting paths ---------------------------------------------------


def test_non_terminal_event_does_not_write_resolution(conn):
    _seed_bundle(conn)
    lifecycle.apply(conn, "job-x", JobEvent.HOOK_ENQUEUED,
                    detail={"bundle_id": "b-1"})
    lifecycle.apply(conn, "job-x", JobEvent.CLAIM,
                    detail={"bundle_id": "b-1"})
    assert _resolution(conn, "b-1") is None


def test_terminal_event_without_bundle_id_is_skipped(conn):
    """detail dict missing bundle_id → no write. Older callers /
    non-bundle-owning transitions must not touch unrelated rows."""
    _seed_bundle(conn)
    _drive(
        conn, "job-y",
        JobEvent.CLAIM, JobEvent.PATCH_START, JobEvent.PATCH_OK,
        # bundle_id omitted
    )
    assert _resolution(conn, "b-1") is None


def test_unknown_bundle_id_silently_skips(conn):
    """detail['bundle_id'] doesn't exist in bundles → UPDATE affects
    zero rows; transition still succeeds."""
    _seed_bundle(conn)
    _drive(
        conn, "job-z",
        JobEvent.CLAIM, JobEvent.PATCH_START, JobEvent.PATCH_OK,
        bundle_id="b-nonexistent",
    )
    # b-1 untouched; transition for job-z still committed.
    assert _resolution(conn, "b-1") is None
    assert lifecycle.current(conn, "job-z") == JobState.VERIFYING


# --- idempotency ------------------------------------------------------------


def test_resolution_overwrites_on_subsequent_terminal_event(conn):
    """If a job somehow re-enters and lands on a different terminal,
    the latest resolution wins. (Not expected in normal flow but the
    behaviour should be predictable.)"""
    _seed_bundle(conn)
    # ESCALATE_MANUAL after TRIAGE_OK.
    lifecycle.apply(conn, "j1", JobEvent.HOOK_ENQUEUED,
                    detail={"bundle_id": "b-1"})
    lifecycle.apply(conn, "j1", JobEvent.CLAIM,
                    detail={"bundle_id": "b-1"})
    lifecycle.apply(conn, "j1", JobEvent.TRIAGE_START,
                    detail={"bundle_id": "b-1"})
    lifecycle.apply(conn, "j1", JobEvent.TRIAGE_OK,
                    detail={"bundle_id": "b-1"})
    lifecycle.apply(conn, "j1", JobEvent.ESCALATE_MANUAL,
                    detail={"bundle_id": "b-1"})
    assert _resolution(conn, "b-1") == "escalated_manual"

    # New patch job for same bundle later succeeds.
    lifecycle.apply(conn, "j2", JobEvent.HOOK_ENQUEUED,
                    detail={"bundle_id": "b-1"})
    lifecycle.apply(conn, "j2", JobEvent.CLAIM,
                    detail={"bundle_id": "b-1"})
    lifecycle.apply(conn, "j2", JobEvent.PATCH_START,
                    detail={"bundle_id": "b-1"})
    lifecycle.apply(conn, "j2", JobEvent.PATCH_OK,
                    detail={"bundle_id": "b-1"})
    assert _resolution(conn, "b-1") == "agent_fixed"
