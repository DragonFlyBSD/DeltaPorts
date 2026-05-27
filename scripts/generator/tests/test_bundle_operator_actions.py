"""Plan Step 11c-3 — POST /verify, /accept, /reject endpoints.

Verify is the gate: Accept rejects with 409 if
verification_status is not 'verified'. Reject works from any
non-terminal state. Verify enqueues a verify-type job.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
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
                                resolution, verification_status)
           VALUES (?, '', ?, '', ?, 'failure', ?, '', ?, ?, ?)""",
        (bundle_id, kw.get("origin", "devel/foo"), now,
         kw.get("target", "@2026Q2"), now,
         kw.get("resolution"), kw.get("verification_status")),
    )
    conn.commit()


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    # Bundle states we need to test across.
    _insert_bundle(c, "b-agent-fixed",
                   resolution="agent_fixed", verification_status=None)
    _insert_bundle(c, "b-verified",
                   resolution="agent_fixed",
                   verification_status="verified")
    _insert_bundle(c, "b-verify-failed",
                   resolution="agent_fixed",
                   verification_status="verification_failed")
    _insert_bundle(c, "b-accepted",
                   resolution="accepted", verification_status="verified")
    _insert_bundle(c, "b-rejected",
                   resolution="rejected", verification_status=None)
    # Seed at least one dev-env so the bundle detail page's Verify
    # picker renders a non-empty <select> (the new UI disables the
    # Verify button outright when no envs are registered).
    c.execute(
        """INSERT INTO env_health_status
           (env, status, updated_at) VALUES (?, ?, ?)""",
        ("verify-env", "healthy", "2026-05-27T00:00:00+00:00"),
    )
    c.commit()
    c.close()
    return db_path


@pytest.fixture
def client(seeded_db, monkeypatch):
    app = create_app(seeded_db)
    with TestClient(app) as c:
        yield c


def _row(db_path: Path, bid: str) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM bundles WHERE bundle_id = ?", (bid,)).fetchone()
    conn.close()
    return r


# ---------------------------------------------------------------------------
# /verify
# ---------------------------------------------------------------------------


def test_verify_records_pending_request_for_agent_fixed(client, seeded_db):
    """Layer-violation cleanup: the tracker no longer touches the
    queue filesystem. It records intent in verify_requests; the
    runner's poll loop reconciles."""
    resp = client.post(
        "/api/bundles/b-agent-fixed/verify",
        json={"env": "verify-env"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["bundle_id"] == "b-agent-fixed"
    assert body["status"] == "pending"
    assert isinstance(body["request_id"], int)
    assert body["env"] == "verify-env"
    # The row landed in verify_requests with status='pending'.
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM verify_requests WHERE id = ?", (body["request_id"],),
    ).fetchone()
    conn.close()
    assert row["bundle_id"] == "b-agent-fixed"
    assert row["env"] == "verify-env"
    assert row["status"] == "pending"
    assert row["job_id"] is None


def test_verify_emits_verify_requested_event(client, seeded_db):
    client.post(
        "/api/bundles/b-agent-fixed/verify",
        json={"env": "verify-env"},
    )
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT data_json FROM events WHERE type = 'verify_requested' "
        "ORDER BY id DESC LIMIT 1",
    ).fetchone()
    conn.close()
    import json
    data = json.loads(row["data_json"])
    assert data["bundle_id"] == "b-agent-fixed"
    assert data["env"] == "verify-env"


def test_verify_works_from_verified_re_run(client):
    resp = client.post(
        "/api/bundles/b-verified/verify",
        json={"env": "verify-env"},
    )
    assert resp.status_code == 200


def test_verify_rejects_terminal_states(client):
    for bid in ("b-accepted", "b-rejected"):
        resp = client.post(
            f"/api/bundles/{bid}/verify",
            json={"env": "verify-env"},
        )
        assert resp.status_code == 409, bid


def test_verify_404_unknown_bundle(client):
    resp = client.post(
        "/api/bundles/nope/verify",
        json={"env": "verify-env"},
    )
    assert resp.status_code == 404


def test_verify_400_missing_env(client):
    resp = client.post("/api/bundles/b-agent-fixed/verify", json={})
    assert resp.status_code == 400


def test_verify_does_not_touch_queue_filesystem(client, seeded_db, tmp_path):
    """Regression: tracker /verify used to write a .job file into a
    queue path resolved from $DPORTSV3_QUEUE_ROOT or a convention,
    coupling the tracker to runner colocation. It no longer does."""
    queue_candidate = tmp_path / "queue" / "pending"
    queue_candidate.mkdir(parents=True)
    client.post(
        "/api/bundles/b-agent-fixed/verify",
        json={"env": "verify-env"},
    )
    # No .job file should have been written anywhere by the tracker.
    assert list(queue_candidate.glob("*.job")) == []


# ---------------------------------------------------------------------------
# /accept — gated on verified
# ---------------------------------------------------------------------------


def test_accept_happy_path_from_verified(client, seeded_db):
    resp = client.post("/api/bundles/b-verified/accept", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "accepted"
    row = _row(seeded_db, "b-verified")
    assert row["resolution"] == "accepted"
    assert row["accepted_at"]


def test_accept_409_when_not_verified(client):
    """The whole point: accept is structurally impossible on an
    unverified bundle."""
    resp = client.post("/api/bundles/b-agent-fixed/accept", json={})
    assert resp.status_code == 409
    assert "verified" in resp.json()["detail"].lower()


def test_accept_409_when_verification_failed(client):
    resp = client.post("/api/bundles/b-verify-failed/accept", json={})
    assert resp.status_code == 409


def test_accept_409_when_already_terminal(client):
    for bid in ("b-accepted", "b-rejected"):
        resp = client.post(f"/api/bundles/{bid}/accept", json={})
        assert resp.status_code == 409, bid


def test_accept_404_unknown(client):
    resp = client.post("/api/bundles/nope/accept", json={})
    assert resp.status_code == 404


def test_accept_emits_event(client, seeded_db):
    client.post("/api/bundles/b-verified/accept", json={})
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT data_json FROM events WHERE type = 'bundle_accepted' "
        "ORDER BY id DESC LIMIT 1",
    ).fetchone()
    conn.close()
    import json
    data = json.loads(row["data_json"])
    assert data["bundle_id"] == "b-verified"


# ---------------------------------------------------------------------------
# /reject — works from any non-terminal state
# ---------------------------------------------------------------------------


def test_reject_from_agent_fixed_without_verify(client, seeded_db):
    """Operator can reject without verifying first (obviously-wrong fix)."""
    resp = client.post(
        "/api/bundles/b-agent-fixed/reject",
        json={"reason": "wrong approach"},
    )
    assert resp.status_code == 200
    row = _row(seeded_db, "b-agent-fixed")
    assert row["resolution"] == "rejected"
    assert row["rejection_reason"] == "wrong approach"


def test_reject_from_verified(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-verified/reject",
        json={"reason": "regression in tests/foo.c"},
    )
    assert resp.status_code == 200
    row = _row(seeded_db, "b-verified")
    assert row["resolution"] == "rejected"


def test_reject_from_verification_failed(client):
    resp = client.post(
        "/api/bundles/b-verify-failed/reject",
        json={"reason": "verify failed, abandoning"},
    )
    assert resp.status_code == 200


def test_reject_400_missing_reason(client):
    resp = client.post(
        "/api/bundles/b-agent-fixed/reject", json={},
    )
    assert resp.status_code == 400


def test_reject_409_when_already_terminal(client):
    for bid in ("b-accepted", "b-rejected"):
        resp = client.post(
            f"/api/bundles/{bid}/reject", json={"reason": "x"},
        )
        assert resp.status_code == 409, bid


def test_reject_404_unknown(client):
    resp = client.post(
        "/api/bundles/nope/reject", json={"reason": "x"},
    )
    assert resp.status_code == 404


def test_reject_emits_event(client, seeded_db):
    client.post(
        "/api/bundles/b-agent-fixed/reject",
        json={"reason": "wrong"},
    )
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT data_json FROM events WHERE type = 'bundle_rejected' "
        "ORDER BY id DESC LIMIT 1",
    ).fetchone()
    conn.close()
    import json
    data = json.loads(row["data_json"])
    assert data["bundle_id"] == "b-agent-fixed"
    assert data["reason"] == "wrong"


# ---------------------------------------------------------------------------
# Slice 4 — UI button matrix
# ---------------------------------------------------------------------------


def test_ui_agent_fixed_shows_verify_and_reject_but_not_accept(client):
    body = client.get("/agentic/bundles/b-agent-fixed").text
    assert "Operator actions" in body
    assert 'id="op-verify"' in body
    assert 'id="op-accept"' in body
    assert 'id="op-reject"' in body
    # Accept must be disabled because verification_status is NULL.
    # Look for the disabled attribute on the accept button.
    import re
    accept_match = re.search(
        r'<button[^>]*id="op-accept"[^>]*>', body, re.DOTALL,
    )
    assert accept_match
    assert "disabled" in accept_match.group(0)
    # Verify and Reject should NOT be disabled.
    verify_match = re.search(
        r'<button[^>]*id="op-verify"[^>]*>', body, re.DOTALL,
    )
    assert verify_match
    assert "disabled" not in verify_match.group(0)


def test_ui_verified_enables_accept(client):
    body = client.get("/agentic/bundles/b-verified").text
    assert "Operator actions" in body
    import re
    accept_match = re.search(
        r'<button[^>]*id="op-accept"[^>]*>', body, re.DOTALL,
    )
    assert accept_match
    assert "disabled" not in accept_match.group(0)


def test_ui_verification_failed_blocks_accept(client):
    body = client.get("/agentic/bundles/b-verify-failed").text
    assert "Operator actions" in body
    import re
    accept_match = re.search(
        r'<button[^>]*id="op-accept"[^>]*>', body, re.DOTALL,
    )
    assert accept_match
    assert "disabled" in accept_match.group(0)
    # Verify should still be enabled (operator can re-run it).
    verify_match = re.search(
        r'<button[^>]*id="op-verify"[^>]*>', body, re.DOTALL,
    )
    assert "disabled" not in verify_match.group(0)


def test_ui_terminal_states_show_only_reopen(client):
    """Terminal resolutions (accepted/rejected) hide the 11c trio
    but Step 28d's Reopen button surfaces so the operator can undo."""
    for bid in ("b-accepted", "b-rejected"):
        body = client.get(f"/agentic/bundles/{bid}").text
        assert 'id="op-verify"' not in body, bid
        assert 'id="op-accept"' not in body, bid
        assert 'id="op-reject"' not in body, bid
        assert 'id="op-reopen"' in body, bid


def test_ui_buttons_use_data_attributes_not_onclick(client):
    """Regression: the first cut put bundle_id inside onclick="..."
    via {{ ... | tojson }}, which produced double-quoted JSON inside
    a double-quoted HTML attribute and silently broke the handler.
    The bundle_id must flow via a data-* attribute instead."""
    body = client.get("/agentic/bundles/b-agent-fixed").text
    # No onclick attributes on op-* buttons.
    import re
    for action in ("verify", "accept", "reject"):
        pattern = rf'<button[^>]*id="op-{action}"[^>]*>'
        match = re.search(pattern, body, re.DOTALL)
        assert match, f"button op-{action} missing"
        assert "onclick" not in match.group(0), (
            f"button op-{action} regressed to onclick='...'"
        )
        assert 'data-bundle="b-agent-fixed"' in match.group(0), (
            f"button op-{action} missing data-bundle"
        )
    # The script block sets up listeners via addEventListener.
    assert "addEventListener('click'" in body


def test_ui_bundle_without_resolution_hides_buttons(client, seeded_db, tmp_path):
    # Insert a bundle that hasn't been triaged yet (resolution=NULL).
    conn = sqlite3.connect(str(seeded_db))
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at)
           VALUES ('b-fresh', '', 'devel/foo', '', '', 'failure', '@2026Q2', '', '')""",
    )
    conn.commit()
    conn.close()
    body = client.get("/agentic/bundles/b-fresh").text
    assert "Operator actions" not in body
