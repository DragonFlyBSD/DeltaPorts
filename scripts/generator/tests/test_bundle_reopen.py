"""Step 28d: terminal-state reopen override.

Covers:
- POST /api/bundles/{id}/reopen happy paths from accepted, rejected,
  discarded.
- Body validation (missing/blank reason → 400).
- 409 from non-terminal states (NULL, agent_fixed, operator_owned,
  retry_requested, every failure resolution).
- 404 unknown bundle.
- Skip-lock semantics on reopen from discarded:
  - Own-lock → cleared
  - Sibling-lock → left intact (forensics preserved)
  - No lock → skip_action='none'
- reopened_* columns populated; prior terminal columns preserved.
- bundle_reopened event emitted with prior_resolution + skip_action.
- After reopen, bundle is takeover/discard/retry-able again.
- UI: Reopen button surfaces on terminal states; absent elsewhere.
"""

from __future__ import annotations

import json
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
                                resolution, accepted_at, rejected_at,
                                rejection_reason, discarded_at,
                                discard_reason, pre_terminal_resolution)
           VALUES (?, '', ?, '', ?, 'failure', ?, '', ?, ?, ?, ?, ?, ?, ?, ?)""",
        (bundle_id, kw.get("origin", "devel/foo"), now,
         kw.get("target", "@2026Q2"), now, kw.get("resolution"),
         kw.get("accepted_at"), kw.get("rejected_at"),
         kw.get("rejection_reason"),
         kw.get("discarded_at"), kw.get("discard_reason"),
         kw.get("pre_terminal_resolution")),
    )
    conn.commit()


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    now = _now()
    _insert_bundle(c, "b-accepted", resolution="accepted",
                   origin="devel/acc", accepted_at=now,
                   pre_terminal_resolution="agent_fixed")
    _insert_bundle(c, "b-rejected", resolution="rejected",
                   origin="devel/rej", rejected_at=now,
                   rejection_reason="wrong fix",
                   pre_terminal_resolution="agent_fixed")
    _insert_bundle(c, "b-discarded-own-lock",
                   resolution="discarded", origin="devel/disc1",
                   discarded_at=now, discard_reason="hopeless",
                   pre_terminal_resolution="agent_gave_up")
    _insert_bundle(c, "b-discarded-sibling-lock",
                   resolution="discarded", origin="devel/disc2",
                   discarded_at=now, discard_reason="duplicate",
                   pre_terminal_resolution="agent_gave_up")
    _insert_bundle(c, "b-discarded-no-lock",
                   resolution="discarded", origin="devel/disc3",
                   discarded_at=now, discard_reason="discard only this bundle",
                   pre_terminal_resolution="agent_budget_exhausted")
    _insert_bundle(c, "b-discarded-legacy",
                   resolution="discarded", origin="devel/disc4",
                   discarded_at=now, discard_reason="legacy row pre-snapshot")
    _insert_bundle(c, "b-agent-fixed", resolution="agent_fixed",
                   origin="devel/fixed")
    _insert_bundle(c, "b-budget", resolution="agent_budget_exhausted",
                   origin="devel/budget")
    _insert_bundle(c, "b-gave-up", resolution="agent_gave_up",
                   origin="devel/gaveup")
    _insert_bundle(c, "b-owned", resolution="operator_owned",
                   origin="devel/owned")
    _insert_bundle(c, "b-retry-requested", resolution="retry_requested",
                   origin="devel/retry")
    _insert_bundle(c, "b-fresh", resolution=None, origin="devel/fresh")

    # Pre-seed locks: b-discarded-own-lock owns its lock;
    # b-discarded-sibling-lock has its origin locked by a sibling.
    set_origin_skip(
        c, target="@2026Q2", origin="devel/disc1",
        set_by="alice", reason="discard: hopeless",
        bundle_id="b-discarded-own-lock",
    )
    set_origin_skip(
        c, target="@2026Q2", origin="devel/disc2",
        set_by="bob", reason="sibling staked first",
        bundle_id="some-sibling-bundle",
    )
    c.commit()
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


@pytest.mark.parametrize("bundle_id,prior,restored", [
    ("b-accepted", "accepted", "agent_fixed"),
    ("b-rejected", "rejected", "agent_fixed"),
    ("b-discarded-no-lock", "discarded", "agent_budget_exhausted"),
    ("b-discarded-legacy", "discarded", None),
])
def test_reopen_happy_path_restores_resolution(
    client, seeded_db, bundle_id, prior, restored,
):
    resp = client.post(
        f"/api/bundles/{bundle_id}/reopen",
        json={"reason": "operator changed their mind",
              "operator": "alice"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["resolution"] == restored
    assert body["reopened_from"] == prior
    assert body["reopened_by"] == "alice"

    row = _row(seeded_db, bundle_id)
    assert row["resolution"] == restored
    assert row["reopened_at"]
    assert row["reopened_by"] == "alice"
    assert row["reopened_from"] == prior
    # Snapshot is consumed on reopen so a re-accept-and-reopen
    # cycle doesn't restore stale state from the first round.
    assert row["pre_terminal_resolution"] is None


def test_reopen_preserves_prior_terminal_columns(client, seeded_db):
    """Forensics: reopen clears resolution but leaves prior
    terminal-state columns populated."""
    pre = _row(seeded_db, "b-rejected")
    assert pre["rejected_at"]
    assert pre["rejection_reason"] == "wrong fix"

    resp = client.post(
        "/api/bundles/b-rejected/reopen",
        json={"reason": "actually the fix was right"},
    )
    assert resp.status_code == 200

    post = _row(seeded_db, "b-rejected")
    # Original columns untouched.
    assert post["rejected_at"] == pre["rejected_at"]
    assert post["rejection_reason"] == "wrong fix"
    # Plus reopen audit fields.
    assert post["reopened_at"]
    assert post["reopened_from"] == "rejected"


# ---------------------------------------------------------------------
# Skip-lock semantics on reopen-from-discarded
# ---------------------------------------------------------------------


def test_reopen_clears_own_lock(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-discarded-own-lock/reopen",
        json={"reason": "actually salvageable"},
    )
    assert resp.status_code == 200
    assert resp.json()["skip_action"] == "cleared"

    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", "devel/disc1")
    finally:
        conn.close()
    assert lock is None


def test_reopen_leaves_sibling_lock_intact(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-discarded-sibling-lock/reopen",
        json={"reason": "duplicate fix is fine to retry"},
    )
    assert resp.status_code == 200
    skip_action = resp.json()["skip_action"]
    assert skip_action.startswith("left_intact_owned_by:some-sibling-bundle")

    # Lock survives — the sibling's stake is unchanged.
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", "devel/disc2")
    finally:
        conn.close()
    assert lock is not None
    assert lock["bundle_id"] == "some-sibling-bundle"


def test_reopen_no_lock_reports_none(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-discarded-no-lock/reopen",
        json={"reason": "give it another shot"},
    )
    assert resp.status_code == 200
    assert resp.json()["skip_action"] == "none"


def test_reopen_accepted_does_not_touch_skip_lock(client, seeded_db):
    """Reopening from accepted/rejected has no business with
    origin_skip_flags (only discarded interacts with the lock)."""
    # Pre-seed a lock for the same origin to make sure we don't
    # blindly clear it.
    conn = sqlite3.connect(str(seeded_db))
    set_origin_skip(
        conn, target="@2026Q2", origin="devel/acc",
        set_by="bob", reason="unrelated", bundle_id="b-other",
    )
    conn.commit()
    conn.close()

    resp = client.post(
        "/api/bundles/b-accepted/reopen",
        json={"reason": "actually the fix was broken"},
    )
    assert resp.status_code == 200
    assert resp.json()["skip_action"] == "none"

    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", "devel/acc")
    finally:
        conn.close()
    assert lock is not None  # untouched


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
def test_reopen_400_on_missing_or_blank_reason(client, body):
    resp = client.post("/api/bundles/b-accepted/reopen", json=body)
    assert resp.status_code == 400


# ---------------------------------------------------------------------
# Refusals (non-terminal states)
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-agent-fixed", "b-budget", "b-gave-up", "b-owned",
    "b-retry-requested", "b-fresh",
])
def test_reopen_409_on_non_terminal(client, bundle_id):
    resp = client.post(
        f"/api/bundles/{bundle_id}/reopen",
        json={"reason": "trying anyway"},
    )
    assert resp.status_code == 409
    assert "terminal" in resp.json()["detail"]


def test_reopen_404_unknown(client):
    resp = client.post(
        "/api/bundles/does-not-exist/reopen",
        json={"reason": "x"},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------
# Post-reopen the bundle becomes actionable again
# ---------------------------------------------------------------------


def test_reopened_bundle_can_be_taken_over(client):
    """After reopen, an originally-discarded bundle can be taken over."""
    client.post(
        "/api/bundles/b-discarded-no-lock/reopen",
        json={"reason": "salvageable"},
    )
    resp = client.post(
        "/api/bundles/b-discarded-no-lock/take-over",
        json={"operator": "alice"},
    )
    # Resolution after reopen is NULL — take-over allows from NULL.
    assert resp.status_code == 200
    assert resp.json()["resolution"] == "operator_owned"


def test_reopened_bundle_can_be_discarded_again(client):
    """After reopen + re-action, the cycle can land at discarded again."""
    client.post(
        "/api/bundles/b-discarded-no-lock/reopen",
        json={"reason": "first reopen"},
    )
    resp = client.post(
        "/api/bundles/b-discarded-no-lock/discard",
        json={"reason": "second discard", "skip_origin": False},
    )
    assert resp.status_code == 200
    assert resp.json()["resolution"] == "discarded"


# ---------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------


def test_reopen_emits_event_with_prior_and_skip_action(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-discarded-own-lock/reopen",
        json={"reason": "salvageable", "operator": "alice"},
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(str(seeded_db))
    rows = conn.execute(
        "SELECT data_json FROM events WHERE type = 'bundle_reopened'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["bundle_id"] == "b-discarded-own-lock"
    assert payload["reopened_from"] == "discarded"
    assert payload["skip_action"] == "cleared"
    assert payload["reopened_by"] == "alice"


# ---------------------------------------------------------------------
# UI button visibility
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-accepted", "b-rejected",
    "b-discarded-own-lock", "b-discarded-sibling-lock",
    "b-discarded-no-lock",
])
def test_reopen_button_renders_on_terminal_states(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-reopen"' in body


@pytest.mark.parametrize("bundle_id", [
    "b-agent-fixed", "b-budget", "b-gave-up", "b-owned",
    "b-retry-requested", "b-fresh",
])
def test_reopen_button_absent_on_non_terminal_states(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-reopen"' not in body
