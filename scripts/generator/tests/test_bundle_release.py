"""Step 28e: operator release of an operator_owned bundle.

The "Hand back to the loop" action — operator stops staking the
(target, origin) without terminalizing the bundle. Covers:

- Happy path from operator_owned.
- 409 from every non-operator_owned resolution.
- 404 unknown; 400 missing/blank reason.
- Lock semantics: own-lock cleared, sibling-lock left intact,
  no-lock skip_action=none.
- taken_over_* forensics preserved.
- bundle_released event payload.
- Post-release the bundle is actionable again (take-over works).
- UI: Verify on operator_owned (Step 28e extension); Release only
  on operator_owned.
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
                                resolution, taken_over_at, taken_over_by)
           VALUES (?, '', ?, '', ?, 'failure', ?, '', ?, ?, ?, ?)""",
        (bundle_id, kw.get("origin", "devel/foo"), now,
         kw.get("target", "@2026Q2"), now, kw.get("resolution"),
         kw.get("taken_over_at"), kw.get("taken_over_by")),
    )
    conn.commit()


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    now = _now()
    _insert_bundle(c, "b-owned-own-lock",
                   resolution="operator_owned",
                   origin="devel/own1",
                   taken_over_at=now, taken_over_by="alice")
    _insert_bundle(c, "b-owned-sibling-lock",
                   resolution="operator_owned",
                   origin="devel/own2",
                   taken_over_at=now, taken_over_by="alice")
    _insert_bundle(c, "b-owned-no-lock",
                   resolution="operator_owned",
                   origin="devel/own3",
                   taken_over_at=now, taken_over_by="alice")
    _insert_bundle(c, "b-agent-fixed", resolution="agent_fixed",
                   origin="devel/fixed")
    _insert_bundle(c, "b-budget", resolution="agent_budget_exhausted",
                   origin="devel/budget")
    _insert_bundle(c, "b-accepted", resolution="accepted",
                   origin="devel/acc")
    _insert_bundle(c, "b-discarded", resolution="discarded",
                   origin="devel/disc")
    _insert_bundle(c, "b-retry-requested", resolution="retry_requested",
                   origin="devel/retry")
    _insert_bundle(c, "b-fresh", resolution=None, origin="devel/fresh")

    # Pre-seed locks: own-lock case owns its lock; sibling-lock
    # case has the origin locked under a different bundle.
    set_origin_skip(
        c, target="@2026Q2", origin="devel/own1",
        set_by="alice", reason="take-over",
        bundle_id="b-owned-own-lock",
    )
    set_origin_skip(
        c, target="@2026Q2", origin="devel/own2",
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
# Happy paths + lock semantics
# ---------------------------------------------------------------------


def test_release_own_lock_clears_resolution_and_lock(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-owned-own-lock/release",
        json={"reason": "actually the loop should retry"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["resolution"] is None
    assert body["skip_action"] == "cleared"

    row = _row(seeded_db, "b-owned-own-lock")
    assert row["resolution"] is None
    # Forensics preserved.
    assert row["taken_over_at"]
    assert row["taken_over_by"] == "alice"

    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", "devel/own1")
    finally:
        conn.close()
    assert lock is None


def test_release_sibling_lock_leaves_lock_intact(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-owned-sibling-lock/release",
        json={"reason": "sibling has it covered"},
    )
    assert resp.status_code == 200
    skip_action = resp.json()["skip_action"]
    assert skip_action.startswith("left_intact_owned_by:some-sibling-bundle")

    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    try:
        lock = is_origin_skipped(conn, "@2026Q2", "devel/own2")
    finally:
        conn.close()
    assert lock is not None
    assert lock["bundle_id"] == "some-sibling-bundle"


def test_release_no_lock_reports_none(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-owned-no-lock/release",
        json={"reason": "no lock to worry about"},
    )
    assert resp.status_code == 200
    assert resp.json()["skip_action"] == "none"


# ---------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-agent-fixed", "b-budget", "b-accepted", "b-discarded",
    "b-retry-requested", "b-fresh",
])
def test_release_409_on_non_operator_owned(client, bundle_id):
    resp = client.post(
        f"/api/bundles/{bundle_id}/release",
        json={"reason": "x"},
    )
    assert resp.status_code == 409
    assert "operator_owned" in resp.json()["detail"]


def test_release_404_unknown(client):
    resp = client.post(
        "/api/bundles/does-not-exist/release",
        json={"reason": "x"},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("body", [
    {},
    {"reason": ""},
    {"reason": "   "},
    {"reason": None},
    {"reason": 42},
])
def test_release_400_on_missing_or_blank_reason(client, body):
    resp = client.post("/api/bundles/b-owned-no-lock/release", json=body)
    assert resp.status_code == 400


# ---------------------------------------------------------------------
# Post-release the bundle is actionable again
# ---------------------------------------------------------------------


def test_released_bundle_can_be_taken_over_again(client):
    """After release, an originally operator_owned bundle can be
    re-staked."""
    r1 = client.post(
        "/api/bundles/b-owned-no-lock/release",
        json={"reason": "first attempt didn't pan out"},
    )
    assert r1.status_code == 200

    r2 = client.post(
        "/api/bundles/b-owned-no-lock/take-over",
        json={"operator": "bob"},
    )
    assert r2.status_code == 200
    assert r2.json()["resolution"] == "operator_owned"
    assert r2.json()["taken_over_by"] == "bob"


# ---------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------


def test_release_emits_event_with_skip_action(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-owned-own-lock/release",
        json={"reason": "loop can retry now", "operator": "alice"},
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(str(seeded_db))
    rows = conn.execute(
        "SELECT data_json FROM events WHERE type = 'bundle_released'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["bundle_id"] == "b-owned-own-lock"
    assert payload["origin"] == "devel/own1"
    assert payload["skip_action"] == "cleared"
    assert payload["released_by"] == "alice"


# ---------------------------------------------------------------------
# UI button surfacing
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-owned-own-lock", "b-owned-sibling-lock", "b-owned-no-lock",
])
def test_release_button_renders_on_operator_owned(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-release"' in body


@pytest.mark.parametrize("bundle_id", [
    "b-agent-fixed", "b-budget", "b-accepted", "b-discarded",
    "b-retry-requested", "b-fresh",
])
def test_release_button_absent_on_non_operator_owned(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-release"' not in body


# ---------------------------------------------------------------------
# Verify-on-operator_owned (28e UI extension)
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bundle_id", [
    "b-owned-own-lock", "b-owned-sibling-lock", "b-owned-no-lock",
])
def test_verify_button_surfaces_on_operator_owned(client, bundle_id):
    """Step 28e extension: an operator who manually fixed something
    should be able to verify their fix via the existing 11c endpoint.
    The button now surfaces on operator_owned as well as agent_fixed."""
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-verify"' in body


def test_verify_button_still_surfaces_on_agent_fixed(client):
    """Regression: 11c's existing Verify-on-agent_fixed must keep
    working after the 28e refactor moved Verify outside the trio."""
    body = client.get("/agentic/bundles/b-agent-fixed").text
    assert 'id="op-verify"' in body


@pytest.mark.parametrize("bundle_id", [
    "b-budget", "b-accepted", "b-discarded",
    "b-retry-requested", "b-fresh",
])
def test_verify_button_absent_on_non_agent_lanes(client, bundle_id):
    body = client.get(f"/agentic/bundles/{bundle_id}").text
    assert 'id="op-verify"' not in body
