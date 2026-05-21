"""Step 10b — operator-triggered ABANDON event + tracker endpoint.

Coverage:

- Lifecycle: ABANDON from QUEUED + every in-flight state → DEAD with
  ``retire_reason='abandoned'``.
- Lifecycle: ABANDON from terminal states (DONE/DEAD/ESCALATED) is an
  IllegalTransition.
- Tracker: POST /api/jobs/{job_id}/abandon happy path, 404 for unknown,
  409 for already-terminal.
- UI: button appears on non-terminal job detail page, hidden on
  terminal.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dportsv3.agent import lifecycle
from dportsv3.agent.lifecycle import JobEvent, JobState
from dportsv3.db.schema import init_db
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- lifecycle unit tests ---------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


@pytest.mark.parametrize("preceding,expected_from", [
    ([], JobState.QUEUED),
    ([JobEvent.CLAIM], JobState.CLAIMED),
    ([JobEvent.CLAIM, JobEvent.TRIAGE_START], JobState.TRIAGING),
    ([JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK], JobState.TRIAGED),
    ([JobEvent.CLAIM, JobEvent.PATCH_START], JobState.PATCHING),
    ([JobEvent.CLAIM, JobEvent.PATCH_START, JobEvent.PATCH_OK], JobState.VERIFYING),
])
def test_abandon_from_active_states(conn, preceding, expected_from):
    job_id = f"j-{expected_from.value}"
    lifecycle.apply(conn, job_id, JobEvent.HOOK_ENQUEUED)
    for ev in preceding:
        lifecycle.apply(conn, job_id, ev)
    assert lifecycle.current(conn, job_id) == expected_from

    new_state = lifecycle.apply(conn, job_id, JobEvent.ABANDON,
                                actor="operator")
    assert new_state == JobState.DEAD
    row = conn.execute(
        "SELECT retire_reason FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    assert row["retire_reason"] == "abandoned"


@pytest.mark.parametrize("preceding", [
    [JobEvent.CLAIM, JobEvent.PATCH_START, JobEvent.PATCH_GAVE_UP],  # DEAD
    [JobEvent.CLAIM, JobEvent.PATCH_START, JobEvent.PATCH_OK, JobEvent.VERIFY_OK],  # DONE
    [JobEvent.CLAIM, JobEvent.TRIAGE_START, JobEvent.TRIAGE_OK,
     JobEvent.ESCALATE_MANUAL],  # ESCALATED
])
def test_abandon_from_terminal_rejected(conn, preceding):
    job_id = "j-terminal"
    lifecycle.apply(conn, job_id, JobEvent.HOOK_ENQUEUED)
    for ev in preceding:
        lifecycle.apply(conn, job_id, ev)
    with pytest.raises(lifecycle.IllegalTransition):
        lifecycle.apply(conn, job_id, JobEvent.ABANDON)


# --- tracker API tests ------------------------------------------------------


@pytest.fixture
def seeded_db_with_job(tmp_path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    # Drive HOOK_ENQUEUED through lifecycle.apply so job_events has
    # the initial row (subsequent ABANDON needs to read current state
    # from there). Then patch the jobs row with the bundle/origin
    # fields the API surfaces.
    lifecycle.apply(c, "queued-1", JobEvent.HOOK_ENQUEUED)
    now = _now()
    c.execute(
        "UPDATE jobs SET type='triage', origin='devel/foo', flavor='', "
        "created_ts_utc=?, last_seen_at=?, target='@2026Q2' "
        "WHERE job_id='queued-1'",
        (now, now),
    )
    # Dead job — no need for event history since the API just reads
    # current state from the cached jobs.state column.
    c.execute(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target, retire_reason)
           VALUES (?, ?, 'triage', 'devel/bar', '', '', ?, '', ?, '@2026Q2', 'patch_gave_up')""",
        ("dead-1", JobState.DEAD.value, now, now),
    )
    c.commit()
    c.close()
    return db_path


@pytest.fixture
def client(seeded_db_with_job):
    app = create_app(seeded_db_with_job)
    with TestClient(app) as c:
        yield c


def test_api_abandon_happy_path(client, seeded_db_with_job):
    resp = client.post("/api/jobs/queued-1/abandon")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["new_state"] == "dead"
    assert body["retire_reason"] == "abandoned"

    conn = sqlite3.connect(str(seeded_db_with_job))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id = 'queued-1'"
    ).fetchone()
    assert row["state"] == "dead"
    assert row["retire_reason"] == "abandoned"
    conn.close()


def test_api_abandon_unknown_returns_404(client):
    resp = client.post("/api/jobs/nope/abandon")
    assert resp.status_code == 404


def test_api_abandon_terminal_returns_409(client):
    resp = client.post("/api/jobs/dead-1/abandon")
    assert resp.status_code == 409
    assert "Cannot abandon" in resp.json()["detail"]


# --- UI ---------------------------------------------------------------------


def test_job_detail_shows_abandon_for_queued(client):
    body = client.get("/agentic/jobs/queued-1").text
    assert "Abandon job" in body
    assert "/api/jobs/queued-1/abandon" not in body  # endpoint built by JS
    assert 'data-job-id="queued-1"' in body


def test_job_detail_hides_abandon_for_terminal(client):
    body = client.get("/agentic/jobs/dead-1").text
    assert "Abandon job" not in body
