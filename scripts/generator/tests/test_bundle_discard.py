"""Step 28b: operator discard of a failed (or operator-owned) bundle.

Covers:
- POST /api/bundles/{id}/discard happy paths across failure
  resolutions + operator_owned.
- 400 on missing/blank reason; 409 on terminal states; 404 on
  unknown bundle.
- skip_origin=true opens the per-(target, origin) lock; =false
  leaves no lock; sibling-locked pair reports already_locked.
- UI button visibility per resolution state.
- bundle_discarded event emitted.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dportsv3.db.schema import init_db
from dportsv3.tracker.agentic_queries import (
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
    _insert_bundle(c, "b-owned", resolution="operator_owned",
                   origin="devel/owned")
    _insert_bundle(c, "b-agent-fixed", resolution="agent_fixed",
                   origin="devel/fixed")
    _insert_bundle(c, "b-accepted", resolution="accepted",
                   origin="devel/acc")
    _insert_bundle(c, "b-rejected", resolution="rejected",
                   origin="devel/rej")
    _insert_bundle(c, "b-discarded", resolution="discarded",
                   origin="devel/already-discarded")
    _insert_bundle(c, "b-fresh", resolution=None,
                   origin="devel/fresh")
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
# Happy paths
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id,origin", [
    ("b-budget", "devel/budget"),
    ("b-gave-up", "devel/gaveup"),
    ("b-escalated", "devel/esc"),
    ("b-convert-gave-up", "devel/conv"),
    ("b-owned", "devel/owned"),
])
def test_discard_happy_path_opens_lock_by_default(
    client, seeded_db, bundle_id, origin,
):
    resp = client.post(
        f"/api/bundles/{bundle_id}/discard",
        json={"reason": "vendored binary; upstream gone"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["resolution"] == "discarded"
    assert body["discard_reason"] == "vendored binary; upstream gone"
    assert body["skip_origin"] is True
    assert body["skip_action"] == "opened"

    row = _row(seeded_db, bundle_id)
    assert row["resolution"] == "discarded"
    assert row["discarded_at"]
    assert row["discard_reason"] == "vendored binary; upstream gone"

    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", origin)
    finally:
        conn.close()
    assert lock is not None
    assert lock["bundle_id"] == bundle_id


def test_discard_skip_origin_false_leaves_no_lock(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-budget/discard",
        json={"reason": "give the loop another try later",
              "skip_origin": False},
    )
    assert resp.status_code == 200
    assert resp.json()["skip_action"] == "none"

    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", "devel/budget")
    finally:
        conn.close()
    assert lock is None


def test_discard_race_lost_still_succeeds_with_distinct_skip_action(
    client, seeded_db, monkeypatch,
):
    """Race semantics: discard expresses intent to walk away from the
    bundle regardless of who locks the origin. If a concurrent action
    opens the lock between the pre-check and the INSERT, the discard
    still lands (200) but skip_action reports race_lost_to_concurrent_lock
    so the operator sees what happened — distinct from take-over,
    where the same race produces 409 because take-over IS the lock."""
    conn = sqlite3.connect(str(seeded_db))
    set_origin_skip(
        conn, target="@2026Q2", origin="devel/budget",
        set_by="racer", reason="raced first", bundle_id="b-budget",
    )
    conn.commit()
    conn.close()

    import dportsv3.tracker.server as server_mod
    monkeypatch.setattr(server_mod, "is_origin_skipped", lambda *a, **kw: None)

    resp = client.post(
        "/api/bundles/b-budget/discard",
        json={"reason": "walking away anyway"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "discarded"
    assert body["skip_action"] == "race_lost_to_concurrent_lock"


def test_discard_with_existing_sibling_lock_reports_already_locked(
    client, seeded_db,
):
    """A sibling bundle staked the (target, origin) first. Discard
    succeeds (the bundle is being walked away from regardless), but
    the lock keeps its original provenance — no duplicate insert."""
    conn = sqlite3.connect(str(seeded_db))
    _insert_bundle(conn, "b-sibling", resolution="agent_gave_up",
                   origin="devel/budget")  # same origin as b-budget
    conn.close()

    # Sibling takes over first → opens the lock.
    r0 = client.post("/api/bundles/b-sibling/take-over", json={})
    assert r0.status_code == 200

    # Now discard b-budget. Lock is already open under b-sibling.
    r1 = client.post(
        "/api/bundles/b-budget/discard",
        json={"reason": "duplicate of the sibling"},
    )
    assert r1.status_code == 200
    assert "already_locked_by:b-sibling" in r1.json()["skip_action"]

    # Lock provenance is unchanged.
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", "devel/budget")
    finally:
        conn.close()
    assert lock is not None
    assert lock["bundle_id"] == "b-sibling"


# ---------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------


@pytest.mark.parametrize("body", [
    {},
    {"reason": ""},
    {"reason": "   "},
    {"reason": None},
    {"reason": 42},
])
def test_discard_400_on_missing_or_blank_reason(client, body):
    resp = client.post("/api/bundles/b-budget/discard", json=body)
    assert resp.status_code == 400
    assert "reason" in resp.json()["detail"]


# ---------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-accepted", "b-rejected", "b-discarded",
])
def test_discard_409_on_terminal_resolution(client, bundle_id):
    resp = client.post(
        f"/api/bundles/{bundle_id}/discard",
        json={"reason": "nope"},
    )
    assert resp.status_code == 409
    assert "terminal" in resp.json()["detail"]


def test_discard_409_on_agent_fixed(client):
    """Success-side bundles route through 11c's Reject, not 28b's
    discard."""
    resp = client.post(
        "/api/bundles/b-agent-fixed/discard",
        json={"reason": "wrong fix"},
    )
    assert resp.status_code == 409


def test_discard_404_unknown(client):
    resp = client.post(
        "/api/bundles/does-not-exist/discard",
        json={"reason": "x"},
    )
    assert resp.status_code == 404


def test_discard_after_take_over_terminalizes_operator_owned(client, seeded_db):
    """Operator stakes a bundle, then decides to walk away → discard
    works from operator_owned."""
    r0 = client.post("/api/bundles/b-budget/take-over", json={})
    assert r0.status_code == 200
    r1 = client.post(
        "/api/bundles/b-budget/discard",
        json={"reason": "manual attempt didn't pan out"},
    )
    assert r1.status_code == 200
    row = _row(seeded_db, "b-budget")
    assert row["resolution"] == "discarded"


# ---------------------------------------------------------------------
# 11c terminal-check extension: discarded blocks accept/reject
# ---------------------------------------------------------------------


def test_accept_409_on_discarded(client, seeded_db):
    """28b extension to 11c: a discarded bundle can't be accepted."""
    conn = sqlite3.connect(str(seeded_db))
    conn.execute(
        "UPDATE bundles SET verification_status='verified' "
        "WHERE bundle_id='b-discarded'"
    )
    conn.commit()
    conn.close()
    resp = client.post("/api/bundles/b-discarded/accept", json={})
    assert resp.status_code == 409
    assert "discarded" in resp.json()["detail"]


def test_reject_409_on_discarded(client):
    resp = client.post(
        "/api/bundles/b-discarded/reject",
        json={"reason": "trying anyway"},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------
# UI button visibility
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-budget", "b-gave-up", "b-escalated", "b-convert-gave-up",
    "b-owned",
])
def test_discard_button_renders_on_failure_or_operator_owned(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-discard"' in body


@pytest.mark.parametrize("bundle_id", [
    "b-agent-fixed", "b-accepted", "b-rejected", "b-discarded", "b-fresh",
])
def test_discard_button_absent_on_other_resolutions(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-discard"' not in body


def test_terminal_discarded_hides_action_buttons_except_reopen(client):
    """A discarded bundle is terminal — only the Step 28d Reopen
    button is allowed (it's the undo path)."""
    body = client.get("/agentic/bundles/b-discarded").text
    assert 'id="op-verify"' not in body
    assert 'id="op-accept"' not in body
    assert 'id="op-reject"' not in body
    assert 'id="op-take-over"' not in body
    assert 'id="op-discard"' not in body
    assert 'id="op-retry"' not in body
    # Reopen is the only action surfaced on terminal states.
    assert 'id="op-reopen"' in body
