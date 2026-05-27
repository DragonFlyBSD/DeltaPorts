"""Step 28c: operator retry-with-context on a failed or
operator-owned bundle.

Covers:
- POST /api/bundles/{id}/retry happy path across failure
  resolutions + operator_owned. Plants user_context + user_context_requests
  rows so the runner's existing process_user_context_updates poll
  picks it up; sets bundle.resolution='retry_requested' (transient).
- Body validation: 400 on missing/blank/non-string/oversized context.
- 409 on terminal (accepted/rejected/discarded) and agent_fixed.
- 404 unknown bundle.
- bundle_retry_requested event emitted with rev + char count.
- UI: Retry button surfaces on failure + operator_owned; absent on
  terminal / agent_fixed / fresh.
- Runner sweep clears retry_requested → NULL when actually enqueuing
  the retriage (so a stuck retry_requested is observable).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dportsv3.db.schema import init_db
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_bundle(conn, bundle_id: str, **kw) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at,
                                resolution)
           VALUES (?, ?, ?, '', ?, 'failure', ?, '', ?, ?)""",
        (bundle_id, kw.get("run_id", "run-1"),
         kw.get("origin", "devel/foo"), now,
         kw.get("target", "@2026Q2"), now, kw.get("resolution")),
    )
    conn.commit()


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    _insert_bundle(c, "b-budget", resolution="agent_budget_exhausted",
                   origin="devel/budget", run_id="run-budget")
    _insert_bundle(c, "b-gave-up", resolution="agent_gave_up",
                   origin="devel/gaveup", run_id="run-gaveup")
    _insert_bundle(c, "b-escalated", resolution="escalated_manual",
                   origin="devel/esc", run_id="run-esc")
    _insert_bundle(c, "b-convert-gave-up", resolution="convert_gave_up",
                   origin="devel/conv", run_id="run-conv")
    _insert_bundle(c, "b-owned", resolution="operator_owned",
                   origin="devel/owned", run_id="run-owned")
    _insert_bundle(c, "b-agent-fixed", resolution="agent_fixed",
                   origin="devel/fixed", run_id="run-fixed")
    _insert_bundle(c, "b-accepted", resolution="accepted",
                   origin="devel/acc", run_id="run-acc")
    _insert_bundle(c, "b-rejected", resolution="rejected",
                   origin="devel/rej", run_id="run-rej")
    _insert_bundle(c, "b-discarded", resolution="discarded",
                   origin="devel/disc", run_id="run-disc")
    _insert_bundle(c, "b-fresh", resolution=None,
                   origin="devel/fresh", run_id="run-fresh")
    _insert_bundle(c, "b-no-runid", resolution="agent_gave_up",
                   origin="devel/noid", run_id="")
    c.close()
    return db_path


@pytest.fixture
def client(seeded_db):
    app = create_app(seeded_db)
    with TestClient(app) as c:
        yield c


def _row(db_path: Path, bid: str) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM bundles WHERE bundle_id = ?",
                     (bid,)).fetchone()
    conn.close()
    return r


def _user_context(db_path: Path, run_id: str, origin: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT * FROM user_context WHERE run_id = ? AND origin = ?",
        (run_id, origin),
    ).fetchone()
    conn.close()
    return r


def _ucr(db_path: Path, run_id: str, origin: str, bundle_id: str) -> sqlite3.Row | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        """SELECT * FROM user_context_requests
           WHERE run_id = ? AND origin = ? AND bundle_id = ?""",
        (run_id, origin, bundle_id),
    ).fetchone()
    conn.close()
    return r


def _history(db_path: Path, run_id: str, origin: str) -> list[sqlite3.Row]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT context_rev, submitted_by FROM user_context_history
           WHERE run_id = ? AND origin = ?
           ORDER BY context_rev ASC""",
        (run_id, origin),
    ).fetchall()
    conn.close()
    return list(rows)


# ---------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id,run_id,origin", [
    ("b-budget", "run-budget", "devel/budget"),
    ("b-gave-up", "run-gaveup", "devel/gaveup"),
    ("b-escalated", "run-esc", "devel/esc"),
    ("b-convert-gave-up", "run-conv", "devel/conv"),
    ("b-owned", "run-owned", "devel/owned"),
])
def test_retry_plants_context_and_request(
    client, seeded_db, bundle_id, run_id, origin,
):
    resp = client.post(
        f"/api/bundles/{bundle_id}/retry",
        json={"context": "try with -fpermissive next time",
              "operator": "alice"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["resolution"] == "retry_requested"
    assert body["context_rev"] == 1
    assert body["requested_by"] == "alice"
    assert body["run_id"] == run_id
    assert body["origin"] == origin

    # Bundle moved to retry_requested.
    row = _row(seeded_db, bundle_id)
    assert row["resolution"] == "retry_requested"

    # user_context populated.
    uc = _user_context(seeded_db, run_id, origin)
    assert uc is not None
    assert uc["context_text"] == "try with -fpermissive next time"
    assert uc["context_rev"] == 1

    # user_context_requests pending row.
    ucr = _ucr(seeded_db, run_id, origin, bundle_id)
    assert ucr is not None
    assert ucr["status"] == "pending"
    assert ucr["iteration"] == 1
    assert ucr["max_iterations"] == 3


def test_retry_bumps_context_rev_on_second_call(client, seeded_db):
    r1 = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": "first"},
    )
    assert r1.status_code == 200
    assert r1.json()["context_rev"] == 1
    r2 = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": "second, with more detail"},
    )
    assert r2.status_code == 200
    assert r2.json()["context_rev"] == 2

    uc = _user_context(seeded_db, "run-budget", "devel/budget")
    assert uc["context_text"] == "second, with more detail"
    assert uc["context_rev"] == 2


def test_retry_history_records_operator_when_supplied(client, seeded_db):
    """Step 29b symmetry: with operator field set, the new
    user_context_history row carries submitted_by = that name."""
    resp = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": "round one", "operator": "alice"},
    )
    assert resp.status_code == 200, resp.text
    rows = _history(seeded_db, "run-budget", "devel/budget")
    assert len(rows) == 1
    assert rows[0]["submitted_by"] == "alice"


def test_retry_history_submitted_by_is_null_when_operator_absent(
    client, seeded_db,
):
    """Step 29b symmetry fix: missing/empty ``operator`` body field
    lands ``submitted_by = NULL`` in user_context_history, matching
    /api/manual-requests/.../context's NULL-on-empty behavior.

    Previously ``/retry`` defaulted the field to the literal
    "operator" string for both the response (``requested_by``) AND
    the history row, which made the same anonymous submission look
    different depending on which endpoint produced it. The fix
    splits the two roles: ``requested_by`` keeps the literal
    fallback for compatibility, ``submitted_by`` records NULL.
    """
    # Case 1: operator field omitted entirely.
    resp = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": "anonymous round"},
    )
    assert resp.status_code == 200, resp.text
    # Response stamps "operator" so existing event consumers still
    # see a non-empty requested_by.
    assert resp.json()["requested_by"] == "operator"

    # Case 2: operator field present but empty/whitespace.
    resp2 = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": "second round", "operator": "   "},
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["requested_by"] == "operator"

    # Both history rows record NULL (not the literal "operator").
    rows = _history(seeded_db, "run-budget", "devel/budget")
    assert len(rows) == 2
    assert rows[0]["submitted_by"] is None
    assert rows[1]["submitted_by"] is None


def test_retry_emits_bundle_retry_requested_event(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": "x"},
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(str(seeded_db))
    rows = conn.execute(
        """SELECT type, data_json FROM events
           WHERE type = 'bundle_retry_requested'"""
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][1])
    assert payload["bundle_id"] == "b-budget"
    assert payload["origin"] == "devel/budget"
    assert payload["run_id"] == "run-budget"
    assert payload["context_chars"] == 1
    assert payload["context_rev"] == 1


# ---------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {},
    {"context": ""},
    {"context": "   "},
    {"context": None},
])
def test_retry_400_on_missing_or_blank_context(client, body):
    resp = client.post("/api/bundles/b-budget/retry", json=body)
    assert resp.status_code == 400


def test_retry_400_on_non_string_context(client):
    resp = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": 42},
    )
    assert resp.status_code == 400
    assert "string" in resp.json()["detail"]


def test_retry_400_on_oversized_context(client):
    resp = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": "x" * 8001},
    )
    assert resp.status_code == 400
    assert "max 8000" in resp.json()["detail"]


def test_retry_accepts_exactly_8000_chars(client):
    resp = client.post(
        "/api/bundles/b-budget/retry",
        json={"context": "x" * 8000},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-accepted", "b-rejected", "b-discarded",
])
def test_retry_409_on_terminal_resolution(client, bundle_id):
    resp = client.post(
        f"/api/bundles/{bundle_id}/retry",
        json={"context": "trying anyway"},
    )
    assert resp.status_code == 409
    assert "terminal" in resp.json()["detail"]


def test_retry_409_on_agent_fixed(client):
    """agent_fixed routes through 11c Reject (which already re-triages
    with the rejection reason as user_context). /retry is for
    failure-shaped bundles only."""
    resp = client.post(
        "/api/bundles/b-agent-fixed/retry",
        json={"context": "wrong fix"},
    )
    assert resp.status_code == 409
    assert "11c Reject" in resp.json()["detail"]


def test_retry_404_unknown(client):
    resp = client.post(
        "/api/bundles/does-not-exist/retry",
        json={"context": "x"},
    )
    assert resp.status_code == 404


def test_retry_409_when_missing_run_id(client):
    resp = client.post(
        "/api/bundles/b-no-runid/retry",
        json={"context": "x"},
    )
    assert resp.status_code == 409
    assert "run_id/origin" in resp.json()["detail"]


# ---------------------------------------------------------------------
# UI surfacing
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-budget", "b-gave-up", "b-escalated", "b-convert-gave-up",
    "b-owned",
])
def test_retry_button_renders_on_failure_or_operator_owned(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-retry"' in body
    assert "Retry with context" in body


@pytest.mark.parametrize("bundle_id", [
    "b-agent-fixed", "b-accepted", "b-rejected", "b-discarded", "b-fresh",
])
def test_retry_button_absent_on_other_resolutions(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-retry"' not in body


# ---------------------------------------------------------------------
# Runner-side: retry_requested clears on enqueue
# ---------------------------------------------------------------------


def test_process_user_context_updates_clears_retry_requested(tmp_path):
    """When the runner sweep enqueues a retriage for a bundle whose
    resolution is 'retry_requested', the resolution must clear
    (→ NULL) so a stuck retry_requested is observable.
    """
    from dportsv3.agent import runner as runner_mod
    import dportsv3.agent.runner as rm

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_db(conn)

    # Seed: a bundle in retry_requested + matching user_context +
    # user_context_requests rows so the sweep has work to do.
    now = _now()
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at,
                                resolution)
           VALUES ('b-retry', 'run-retry', 'devel/x', '', ?, 'failure',
                   '@2026Q2', '', ?, 'retry_requested')""",
        (now, now),
    )
    conn.execute(
        """INSERT INTO runs (run_id, profile, target, last_seen_at)
           VALUES ('run-retry', 'default', '@2026Q2', ?)""",
        (now,),
    )
    conn.execute(
        """INSERT INTO user_context
           (run_id, origin, context_text, updated_at, context_rev)
           VALUES ('run-retry', 'devel/x', 'op hint', ?, 1)""",
        (now,),
    )
    conn.execute(
        """INSERT INTO user_context_requests
           (run_id, origin, bundle_id, iteration, max_iterations,
            requested_at, status, last_context_rev_handled)
           VALUES ('run-retry', 'devel/x', 'b-retry', 1, 3, ?,
                   'pending', 0)""",
        (now,),
    )

    rm._state_db_conn = conn
    # Stub the helpers that talk to disk / the queue. We only care
    # that the bundle's resolution clears.
    queue_root = tmp_path / "queue"
    (queue_root / "pending").mkdir(parents=True)

    class _StubJobPath:
        name = "stub.job"

    original_enqueue = rm.enqueue_triage_job
    rm.enqueue_triage_job = lambda *a, **kw: _StubJobPath()

    original_activity = rm.activity_log
    rm.activity_log = lambda *a, **kw: None

    try:
        runner_mod.process_user_context_updates(queue_root)
    finally:
        rm.enqueue_triage_job = original_enqueue
        rm.activity_log = original_activity
        rm._state_db_conn = None

    # Resolution cleared.
    res = conn.execute(
        "SELECT resolution FROM bundles WHERE bundle_id = 'b-retry'"
    ).fetchone()
    assert res["resolution"] is None
    conn.close()
