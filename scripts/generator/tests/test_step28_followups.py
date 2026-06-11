"""Step 28 follow-up findings from the final review pass.

Finding 1: convert dispatcher's CONVERT_OK fired after a skip-locked
process_convert_job exit raised IllegalTransition and triggered a
pointless _resume_deferred_triage call. Fix: dispatcher detects the
origin_locked_by: status sentinel and skips both.

Finding 2: operator_owned bundles couldn't reach Accept after a
successful Verify (UI gated Accept on actionable=agent_fixed). And
even if they did via curl, accept would leave the origin_skip_flags
row open forever (accept is terminal; reopen-from-accepted doesn't
clear locks). Fix: widen the UI gate to verify_eligible, and have
the accept endpoint release the skip flag iff prior_resolution was
operator_owned and this bundle owns the lock.
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


# =====================================================================
# Finding 2 — Accept on operator_owned-verified
# =====================================================================


def _insert_bundle(conn, bundle_id, **kw):
    now = _now()
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at,
                                resolution, verification_status,
                                taken_over_at, taken_over_by)
           VALUES (?, '', ?, '', ?, 'failure', ?, '', ?, ?, ?, ?, ?)""",
        (bundle_id, kw.get("origin", "devel/foo"), now,
         kw.get("target", "@2026Q2"), now,
         kw.get("resolution"), kw.get("verification_status"),
         kw.get("taken_over_at"), kw.get("taken_over_by")),
    )
    conn.commit()


@pytest.fixture
def seeded_db(tmp_path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    now = _now()
    _insert_bundle(c, "b-owned-verified-own-lock",
                   resolution="operator_owned",
                   verification_status="verified",
                   origin="devel/own1",
                   taken_over_at=now, taken_over_by="alice")
    _insert_bundle(c, "b-owned-verified-sibling-lock",
                   resolution="operator_owned",
                   verification_status="verified",
                   origin="devel/own2",
                   taken_over_at=now, taken_over_by="alice")
    _insert_bundle(c, "b-owned-verified-no-lock",
                   resolution="operator_owned",
                   verification_status="verified",
                   origin="devel/own3",
                   taken_over_at=now, taken_over_by="alice")
    _insert_bundle(c, "b-owned-unverified",
                   resolution="operator_owned",
                   verification_status=None,
                   origin="devel/own4",
                   taken_over_at=now, taken_over_by="alice")
    _insert_bundle(c, "b-agent-verified",
                   resolution="agent_fixed",
                   verification_status="verified",
                   origin="devel/agent")

    set_origin_skip(
        c, target="@2026Q2", origin="devel/own1",
        set_by="alice", reason="take-over",
        bundle_id="b-owned-verified-own-lock",
    )
    set_origin_skip(
        c, target="@2026Q2", origin="devel/own2",
        set_by="bob", reason="sibling staked",
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


def _row(db_path, bid):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM bundles WHERE bundle_id = ?",
                     (bid,)).fetchone()
    conn.close()
    return r


# --- Endpoint behavior ---


def test_accept_operator_owned_verified_with_own_lock_clears_lock(
    client, seeded_db,
):
    resp = client.post(
        "/api/bundles/b-owned-verified-own-lock/accept", json={},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolution"] == "accepted"
    assert body["prior_resolution"] == "operator_owned"
    assert body["skip_action"] == "cleared"

    row = _row(seeded_db, "b-owned-verified-own-lock")
    assert row["resolution"] == "accepted"
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


def test_accept_operator_owned_verified_sibling_lock_left_intact(
    client, seeded_db,
):
    resp = client.post(
        "/api/bundles/b-owned-verified-sibling-lock/accept", json={},
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


def test_accept_operator_owned_verified_no_lock_reports_none(client):
    resp = client.post(
        "/api/bundles/b-owned-verified-no-lock/accept", json={},
    )
    assert resp.status_code == 200
    assert resp.json()["skip_action"] == "none"


def test_accept_operator_owned_unverified_409(client):
    """The verification gate still holds — operator_owned without
    a successful verify can't be accepted."""
    resp = client.post("/api/bundles/b-owned-unverified/accept", json={})
    assert resp.status_code == 409
    assert "verification_status" in resp.json()["detail"]


def test_accept_agent_fixed_verified_unchanged_behavior(client, seeded_db):
    """Regression: agent_fixed-verified accept still works (no
    skip-flag interaction since agent_fixed never opened a lock)."""
    resp = client.post("/api/bundles/b-agent-verified/accept", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "accepted"
    assert body["prior_resolution"] == "agent_fixed"
    assert body["skip_action"] == "none"


def test_accept_emits_event_with_prior_and_skip_action(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-owned-verified-own-lock/accept", json={},
    )
    assert resp.status_code == 200

    conn = sqlite3.connect(str(seeded_db))
    rows = conn.execute(
        "SELECT data_json FROM events WHERE type = 'bundle_accepted'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["prior_resolution"] == "operator_owned"
    assert payload["skip_action"] == "cleared"


# --- UI surfacing ---


def test_accept_button_renders_on_operator_owned_verified(client):
    body = client.get("/agentic/bundles/b-owned-verified-no-lock").text
    assert 'id="op-accept"' in body
    # Should NOT be disabled — verification has passed.
    import re
    match = re.search(r'<button[^>]*id="op-accept"[^>]*>', body,
                       re.DOTALL)
    assert match
    assert "disabled" not in match.group(0)


def test_accept_button_absent_on_operator_owned_unverified(client):
    body = client.get("/agentic/bundles/b-owned-unverified").text
    # show_accept_button = actionable(False) or can_accept(False) = False
    # → button doesn't render at all (not disabled — absent).
    assert 'id="op-accept"' not in body


def test_accept_button_still_renders_disabled_on_agent_fixed_unverified(
    client, seeded_db,
):
    """Regression: 11c's existing convention of rendering Accept
    disabled-before-verify on agent_fixed must still hold."""
    _insert_bundle(
        sqlite3.connect(str(seeded_db)),
        "b-agent-unverified", resolution="agent_fixed",
        verification_status=None, origin="devel/agent-u",
    )
    body = client.get("/agentic/bundles/b-agent-unverified").text
    assert 'id="op-accept"' in body
    # Should render with disabled attribute since not verified.
    import re
    match = re.search(r'<button[^>]*id="op-accept"[^>]*>', body,
                       re.DOTALL)
    assert match
    assert "disabled" in match.group(0)


def test_reject_button_still_absent_on_operator_owned(client):
    """Reject deliberately stays agent_fixed-only — its semantics
    (re-triage with rejection reason) only make sense there."""
    body = client.get("/agentic/bundles/b-owned-verified-no-lock").text
    assert 'id="op-reject"' not in body
