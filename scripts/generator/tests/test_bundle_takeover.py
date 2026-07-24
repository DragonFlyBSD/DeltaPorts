"""Step 28a: operator take-over of a failed bundle.

Covers:
- POST /api/bundles/{id}/take-over: happy path from each failure
  resolution, 409 from terminal / already-operator_owned states,
  404 on unknown bundle, skip-flag side effect.
- Hook-side skip-flag check (_maybe_skip_locked_origin): triage
  short-circuits when the origin is locked; activity row recorded.
- Skip-flag query helpers: is_origin_skipped / set_origin_skip /
  clear_origin_skip behave per the partial-unique-index contract.
- UI: take-over button surfaces only on failure-shaped
  resolutions, never on agent_fixed / accepted / NULL / already-
  operator_owned.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dportsv3.db.schema import init_db
from dportsv3.tracker.agentic_queries import (
    clear_origin_skip,
    is_origin_skipped,
    set_origin_skip,
)
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_bundle(conn, bundle_id: str, **kw) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at,
                                resolution)
           VALUES (?, '', ?, '', ?, 'failure', ?, '', ?, ?)""",
        (bundle_id, kw.get("origin", "devel/foo"), now,
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
                   origin="devel/budget")
    _insert_bundle(c, "b-gave-up", resolution="agent_gave_up",
                   origin="devel/gaveup")
    _insert_bundle(c, "b-escalated", resolution="escalated_manual",
                   origin="devel/esc")
    _insert_bundle(c, "b-convert-gave-up", resolution="convert_gave_up",
                   origin="devel/conv")
    _insert_bundle(c, "b-agent-fixed", resolution="agent_fixed",
                   origin="devel/fixed")
    _insert_bundle(c, "b-accepted", resolution="accepted",
                   origin="devel/acc")
    _insert_bundle(c, "b-rejected", resolution="rejected",
                   origin="devel/rej")
    _insert_bundle(c, "b-owned", resolution="operator_owned",
                   origin="devel/owned")
    _insert_bundle(c, "b-fresh", resolution=None,
                   origin="devel/fresh")
    _insert_bundle(c, "b-no-target", resolution="agent_gave_up",
                   origin="devel/notarget", target="")
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


# ---------------------------------------------------------------------
# Endpoint — happy paths across the four failure resolutions
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id,origin", [
    ("b-budget", "devel/budget"),
    ("b-gave-up", "devel/gaveup"),
    ("b-escalated", "devel/esc"),
    ("b-convert-gave-up", "devel/conv"),
])
def test_take_over_happy_path_from_failure_resolution(
    client, seeded_db, bundle_id, origin,
):
    resp = client.post(
        f"/api/bundles/{bundle_id}/take-over",
        json={"operator": "alice", "reason": "manual fix in progress"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["resolution"] == "operator_owned"
    assert body["taken_over_by"] == "alice"
    assert body["origin"] == origin

    row = _row(seeded_db, bundle_id)
    assert row["resolution"] == "operator_owned"
    assert row["taken_over_at"]
    assert row["taken_over_by"] == "alice"

    # Skip flag row opened for this (target, origin).
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", origin)
    finally:
        conn.close()
    assert lock is not None
    assert lock["bundle_id"] == bundle_id
    assert lock["set_by"] == "alice"
    assert lock["cleared_at"] is None


def test_take_over_defaults_operator_and_reason(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-budget/take-over",
        json={},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["taken_over_by"] == "operator"


# ---------------------------------------------------------------------
# Endpoint — refusals
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-accepted", "b-rejected",
])
def test_take_over_409_on_terminal_resolution(client, bundle_id):
    resp = client.post(f"/api/bundles/{bundle_id}/take-over", json={})
    assert resp.status_code == 409
    assert "terminal" in resp.json()["detail"]


def test_take_over_409_when_already_operator_owned(client):
    resp = client.post("/api/bundles/b-owned/take-over", json={})
    assert resp.status_code == 409
    assert "already operator_owned" in resp.json()["detail"]


def test_take_over_409_on_agent_fixed(client):
    """Success-side bundles route through 11c's Accept/Reject, not 28."""
    resp = client.post("/api/bundles/b-agent-fixed/take-over", json={})
    assert resp.status_code == 409


def test_take_over_404_on_unknown_bundle(client):
    resp = client.post("/api/bundles/does-not-exist/take-over", json={})
    assert resp.status_code == 404


def test_take_over_409_when_missing_target(client):
    resp = client.post("/api/bundles/b-no-target/take-over", json={})
    assert resp.status_code == 409
    assert "target/origin" in resp.json()["detail"]


def test_take_over_409_when_race_loses_to_concurrent_insert(
    client, seeded_db, monkeypatch,
):
    """If a concurrent operator action opens the lock between our
    is_origin_skipped pre-check and the set_origin_skip INSERT, the
    partial-unique index correctly rejects the duplicate. The
    endpoint must convert that sqlite3.IntegrityError to a 409 (not
    propagate it as a 500). Simulated by pre-seeding a lock + lying
    in the pre-check via monkeypatch."""
    # Pre-seed a lock that the endpoint's pre-check WILL miss
    # (because we'll monkey-patch is_origin_skipped to return None).
    conn = sqlite3.connect(str(seeded_db))
    set_origin_skip(
        conn, target="@2026Q2", origin="devel/budget",
        set_by="racer", reason="raced first", bundle_id="b-budget",
    )
    conn.commit()
    conn.close()

    import dportsv3.tracker.routes.bundle_actions as bundle_actions
    monkeypatch.setattr(bundle_actions, "is_origin_skipped", lambda *a, **kw: None)

    resp = client.post("/api/bundles/b-budget/take-over", json={})
    assert resp.status_code == 409
    assert "concurrent" in resp.json()["detail"]


def test_take_over_second_attempt_on_same_origin_via_sibling_bundle_409(
    client, seeded_db,
):
    """Once the (target, origin) is locked by one bundle, another bundle
    for the same pair cannot also take it over (409). Lock must be
    cleared first."""
    # Insert a sibling bundle that shares (target, origin).
    conn = sqlite3.connect(str(seeded_db))
    _insert_bundle(conn, "b-sibling", resolution="agent_gave_up",
                   origin="devel/budget")  # same origin as b-budget
    conn.close()

    r1 = client.post("/api/bundles/b-budget/take-over", json={})
    assert r1.status_code == 200
    r2 = client.post("/api/bundles/b-sibling/take-over", json={})
    assert r2.status_code == 409
    assert "already locked" in r2.json()["detail"]


# ---------------------------------------------------------------------
# Skip-flag query helpers
# ---------------------------------------------------------------------


def test_is_origin_skipped_returns_none_when_no_lock(seeded_db):
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        assert is_origin_skipped(conn, "@2026Q2", "devel/budget") is None
    finally:
        conn.close()


def test_set_origin_skip_and_query(seeded_db):
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        rid = set_origin_skip(
            conn, target="@2026Q2", origin="devel/budget",
            set_by="alice", reason="manual", bundle_id="b-budget",
        )
        assert rid > 0
        lock = is_origin_skipped(conn, "@2026Q2", "devel/budget")
        assert lock is not None
        assert lock["reason"] == "manual"
        assert lock["cleared_at"] is None
    finally:
        conn.close()


def test_set_origin_skip_rejects_duplicate_open_lock(seeded_db):
    conn = sqlite3.connect(str(seeded_db))
    try:
        set_origin_skip(
            conn, target="@2026Q2", origin="devel/budget",
            set_by="alice", reason="first", bundle_id="b-budget",
        )
        with pytest.raises(sqlite3.IntegrityError):
            set_origin_skip(
                conn, target="@2026Q2", origin="devel/budget",
                set_by="bob", reason="second", bundle_id="b-budget",
            )
    finally:
        conn.close()


def test_clear_origin_skip_allows_relock(seeded_db):
    conn = sqlite3.connect(str(seeded_db))
    try:
        set_origin_skip(
            conn, target="@2026Q2", origin="devel/budget",
            set_by="alice", reason="first", bundle_id="b-budget",
        )
        cleared = clear_origin_skip(
            conn, target="@2026Q2", origin="devel/budget",
            cleared_by="alice",
        )
        assert cleared is True
        # Now a new lock should succeed.
        rid = set_origin_skip(
            conn, target="@2026Q2", origin="devel/budget",
            set_by="bob", reason="second", bundle_id="b-budget",
        )
        assert rid > 0
    finally:
        conn.close()


def test_clear_origin_skip_returns_false_when_no_open_lock(seeded_db):
    conn = sqlite3.connect(str(seeded_db))
    try:
        cleared = clear_origin_skip(
            conn, target="@2026Q2", origin="devel/budget",
            cleared_by="alice",
        )
        assert cleared is False
    finally:
        conn.close()


# ---------------------------------------------------------------------
# UI — button surfacing
# ---------------------------------------------------------------------


def test_take_over_button_renders_on_failure_resolutions(client):
    body = client.get("/agentic/bundles/b-budget").text
    assert "Take over" in body
    assert 'id="op-take-over"' in body


def test_take_over_button_absent_on_agent_fixed(client):
    body = client.get("/agentic/bundles/b-agent-fixed").text
    assert 'id="op-take-over"' not in body


def test_take_over_button_absent_on_terminal(client):
    body = client.get("/agentic/bundles/b-accepted").text
    assert 'id="op-take-over"' not in body


def test_take_over_button_absent_on_already_operator_owned(client):
    body = client.get("/agentic/bundles/b-owned").text
    assert 'id="op-take-over"' not in body


def test_take_over_button_absent_on_fresh_bundle(client):
    """NULL resolution means the agent hasn't even started yet —
    UI hides the button (the endpoint itself permits it for CLI use)."""
    body = client.get("/agentic/bundles/b-fresh").text
    assert 'id="op-take-over"' not in body


# ---------------------------------------------------------------------
# Hook-side skip-flag check (runner integration)
# ---------------------------------------------------------------------


def test_maybe_skip_locked_origin_short_circuits_when_locked(tmp_path):
    """_maybe_skip_locked_origin returns a (True, status) tuple when
    the (target, origin) pair has an open skip-flag row, after firing
    an activity-log row."""
    from dportsv3.agent import runner as runner_mod
    import dportsv3.agent.runner as rm

    # Set up a state.db with an open lock for (@2026Q2, devel/budget).
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    set_origin_skip(
        conn, target="@2026Q2", origin="devel/budget",
        set_by="alice", reason="manual", bundle_id="b-budget",
    )
    # Insert a jobs row so _apply_transition has somewhere to update.
    conn.execute(
        """INSERT INTO jobs (job_id, state, type, origin, target,
                             last_seen_at)
           VALUES ('skip-test.job', 'triaging', 'triage', 'devel/budget',
                   '@2026Q2', ?)""",
        (_now(),),
    )

    activity_rows: list[dict] = []

    # Plumb the module-level _state_db_conn so the helper can find it.
    rm._state_db_conn = conn
    original_activity_log = rm.activity_log
    rm.activity_log = lambda queue_root, stage, message, **kw: activity_rows.append(
        {"stage": stage, "message": message, **kw}
    )
    try:
        outcome = runner_mod._maybe_skip_locked_origin(
            queue_root=tmp_path,
            job={"target": "@2026Q2"},
            job_id="skip-test.job",
            sibling_paths=None,
            origin="devel/budget",
        )
    finally:
        rm.activity_log = original_activity_log
        rm._state_db_conn = None
        conn.close()

    assert outcome is not None
    success, status = outcome
    assert success is True
    assert "origin_locked_by:b-budget" in status

    skip_rows = [r for r in activity_rows
                 if r["stage"] == "triage_skipped_origin_locked"]
    assert len(skip_rows) == 1
    extra = skip_rows[0]["extra"]
    assert extra["origin"] == "devel/budget"
    assert extra["target"] == "@2026Q2"
    assert extra["locking_bundle_id"] == "b-budget"


def test_maybe_skip_locked_origin_returns_none_when_unlocked(tmp_path):
    from dportsv3.agent import runner as runner_mod
    import dportsv3.agent.runner as rm

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False,
                           isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    rm._state_db_conn = conn
    try:
        outcome = runner_mod._maybe_skip_locked_origin(
            queue_root=tmp_path,
            job={"target": "@2026Q2"},
            job_id="any.job",
            sibling_paths=None,
            origin="devel/unlocked",
        )
        assert outcome is None
    finally:
        rm._state_db_conn = None
        conn.close()


def test_maybe_skip_locked_origin_noops_without_target(tmp_path):
    """No target → can't compute the (target, origin) key → return
    None (don't block triage on missing metadata)."""
    from dportsv3.agent import runner as runner_mod
    import dportsv3.agent.runner as rm
    import os

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False,
                           isolation_level=None)
    init_db(conn)
    rm._state_db_conn = conn
    # Clear any env-var fallback so the helper has no target source.
    original_env = os.environ.pop("DPORTSV3_TRACKER_TARGET", None)
    try:
        outcome = runner_mod._maybe_skip_locked_origin(
            queue_root=tmp_path,
            job={},  # no target
            job_id="any.job",
            sibling_paths=None,
            origin="devel/x",
        )
        assert outcome is None
    finally:
        if original_env is not None:
            os.environ["DPORTSV3_TRACKER_TARGET"] = original_env
        rm._state_db_conn = None
        conn.close()
