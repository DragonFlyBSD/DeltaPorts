"""Plan Step 11c-1 — verify-fix job lifecycle.

Mirrors the CONVERT_* shape: VERIFY_FIX_START lands at
VERIFYING_FIX, VERIFY_FIX_OK lands at DONE (regardless of the
underlying dsynth verdict — that lives on bundles.verification_status,
not on the job), VERIFY_FIX_GAVE_UP lands at DEAD with
retire_reason='verify_fix_failed'.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dportsv3.agent import lifecycle
from dportsv3.agent.lifecycle import (
    IllegalTransition,
    JobEvent,
    JobState,
    apply,
)
from dportsv3.db.schema import init_db


@pytest.fixture
def conn(tmp_path: Path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), isolation_level=None,
                        check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _enqueue(conn, jid: str = "vf-1") -> None:
    apply(conn, jid, JobEvent.HOOK_ENQUEUED)
    apply(conn, jid, JobEvent.CLAIM)


def test_verify_fix_happy_path_lands_done(conn) -> None:
    _enqueue(conn)
    assert apply(conn, "vf-1", JobEvent.VERIFY_FIX_START) == JobState.VERIFYING_FIX
    assert apply(conn, "vf-1", JobEvent.VERIFY_FIX_OK) == JobState.DONE


def test_verify_fix_gave_up_lands_dead_with_reason(conn) -> None:
    _enqueue(conn)
    apply(conn, "vf-1", JobEvent.VERIFY_FIX_START)
    assert apply(conn, "vf-1", JobEvent.VERIFY_FIX_GAVE_UP) == JobState.DEAD

    row = conn.execute(
        "SELECT retire_reason FROM jobs WHERE job_id = ?", ("vf-1",),
    ).fetchone()
    assert row["retire_reason"] == "verify_fix_failed"


def test_verify_fix_start_only_from_claimed(conn) -> None:
    apply(conn, "vf-1", JobEvent.HOOK_ENQUEUED)
    # QUEUED → VERIFY_FIX_START is not in the table.
    with pytest.raises(IllegalTransition):
        apply(conn, "vf-1", JobEvent.VERIFY_FIX_START)


def test_verify_fix_env_broken_lands_dead(conn) -> None:
    _enqueue(conn)
    apply(conn, "vf-1", JobEvent.VERIFY_FIX_START)
    assert apply(conn, "vf-1", JobEvent.ENV_BROKEN) == JobState.DEAD


def test_verify_fix_reap_orphan_lands_dead(conn) -> None:
    _enqueue(conn)
    apply(conn, "vf-1", JobEvent.VERIFY_FIX_START)
    assert apply(conn, "vf-1", JobEvent.REAP_ORPHAN) == JobState.DEAD


def test_verify_fix_abandon_lands_dead(conn) -> None:
    _enqueue(conn)
    apply(conn, "vf-1", JobEvent.VERIFY_FIX_START)
    assert apply(conn, "vf-1", JobEvent.ABANDON) == JobState.DEAD


def test_verifying_fix_in_inflight_states() -> None:
    assert JobState.VERIFYING_FIX in lifecycle._INFLIGHT_STATES


def test_verify_fix_ok_only_from_verifying_fix(conn) -> None:
    _enqueue(conn)
    # CLAIMED → VERIFY_FIX_OK is not in the table.
    with pytest.raises(IllegalTransition):
        apply(conn, "vf-1", JobEvent.VERIFY_FIX_OK)
