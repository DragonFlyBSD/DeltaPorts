"""Step 11d-5: manual delivery status update endpoint + UI buttons.

Exercises the `POST /api/bundles/{id}/delivery/status` endpoint
and the Mark-merged / Mark-closed UI buttons on the Delivery card.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dportsv3.db.schema import init_db
from dportsv3.tracker import agentic_queries as q
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_bundle(conn, bundle_id, **kw):
    now = _now()
    conn.execute(
        """INSERT INTO bundles (
              bundle_id, run_id, origin, flavor, ts_utc, result,
              target, path, last_seen_at, resolution
           ) VALUES (?, '', ?, '', ?, 'failure', ?, '', ?, ?)""",
        (bundle_id, kw.get("origin", "devel/foo"), now,
         kw.get("target", "@2026Q2"), now, kw.get("resolution", "accepted")),
    )
    conn.commit()


def _seed_review_request(conn, bundle_id, status="created", **kw):
    return q.insert_review_request(
        conn,
        bundle_id=bundle_id,
        provider=kw.get("provider", "github"),
        status=status,
        provider_pr_id=kw.get("provider_pr_id", "42"),
        url=kw.get("url", "https://github.com/x/y/pull/42"),
        branch=kw.get("branch", "agentic/x"),
        title=kw.get("title", "fix"),
        operator=kw.get("operator", "alice"),
        error=kw.get("error"),
        error_signature=kw.get("error_signature", "sig-1"),
    )


@pytest.fixture
def deployment(tmp_path):
    db_path = tmp_path / "state.db"
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.close()

    app = create_app(db_path)
    app.state.artifact_root = artifact_root
    return {"db_path": db_path, "app": app}


@pytest.fixture
def client(deployment):
    with TestClient(deployment["app"]) as c:
        yield c


def _open(deployment):
    conn = sqlite3.connect(str(deployment["db_path"]))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------
# Happy paths — created → merged / closed
# ---------------------------------------------------------------------


@pytest.mark.parametrize("new_status", ["merged", "closed"])
def test_mark_status_happy_path(client, deployment, new_status):
    conn = _open(deployment)
    _seed_bundle(conn, "b-1")
    _seed_review_request(conn, "b-1", status="created")
    conn.commit()
    conn.close()

    resp = client.post(
        "/api/bundles/b-1/delivery/status",
        json={"status": new_status, "note": "merged upstream"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == new_status
    assert body["prior_status"] == "created"

    conn = _open(deployment)
    try:
        latest = q.latest_review_request_for_bundle(conn, "b-1")
    finally:
        conn.close()
    assert latest["status"] == new_status
    assert latest["last_synced_at"] is not None
    # Note lands in the `error` column with a "note:" prefix.
    assert latest["error"] is not None
    assert "merged upstream" in latest["error"]


def test_mark_status_without_note(client, deployment):
    """Note is optional. Row updates with error column NULL."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-no-note")
    _seed_review_request(conn, "b-no-note", status="created")
    conn.commit()
    conn.close()

    resp = client.post(
        "/api/bundles/b-no-note/delivery/status",
        json={"status": "merged"},
    )
    assert resp.status_code == 200

    conn = _open(deployment)
    try:
        latest = q.latest_review_request_for_bundle(conn, "b-no-note")
    finally:
        conn.close()
    assert latest["status"] == "merged"
    assert latest["error"] is None


def test_mark_status_from_updated_state(client, deployment):
    """`updated` (idempotency state) can transition to merged/closed
    just like `created`."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-upd")
    _seed_review_request(conn, "b-upd", status="updated")
    conn.commit()
    conn.close()

    resp = client.post(
        "/api/bundles/b-upd/delivery/status",
        json={"status": "merged"},
    )
    assert resp.status_code == 200
    assert resp.json()["prior_status"] == "updated"


# ---------------------------------------------------------------------
# Body validation
# ---------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    {},
    {"status": "created"},   # reserved for orchestrator
    {"status": "updated"},   # reserved
    {"status": "made-up"},
    {"status": ""},
])
def test_invalid_status_400(client, deployment, bad):
    conn = _open(deployment)
    _seed_bundle(conn, "b-bad")
    _seed_review_request(conn, "b-bad")
    conn.commit()
    conn.close()
    resp = client.post("/api/bundles/b-bad/delivery/status", json=bad)
    assert resp.status_code == 400
    assert "merged" in resp.json()["detail"]


def test_non_string_note_400(client, deployment):
    conn = _open(deployment)
    _seed_bundle(conn, "b-nstr")
    _seed_review_request(conn, "b-nstr")
    conn.commit()
    conn.close()
    resp = client.post(
        "/api/bundles/b-nstr/delivery/status",
        json={"status": "merged", "note": 42},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------


def test_404_when_bundle_has_no_delivery_row(client, deployment):
    conn = _open(deployment)
    _seed_bundle(conn, "b-nodelivery")
    conn.commit()
    conn.close()
    resp = client.post(
        "/api/bundles/b-nodelivery/delivery/status",
        json={"status": "merged"},
    )
    assert resp.status_code == 404
    assert "no delivery row" in resp.json()["detail"]


def test_404_unknown_bundle(client):
    """A bundle that doesn't exist at all has no delivery row by
    definition — same 404."""
    resp = client.post(
        "/api/bundles/does-not-exist/delivery/status",
        json={"status": "merged"},
    )
    assert resp.status_code == 404


@pytest.mark.parametrize("terminal", ["merged", "closed", "create_failed"])
def test_409_on_terminal_state(client, deployment, terminal):
    conn = _open(deployment)
    _seed_bundle(conn, f"b-t-{terminal}")
    _seed_review_request(conn, f"b-t-{terminal}", status=terminal)
    conn.commit()
    conn.close()
    resp = client.post(
        f"/api/bundles/b-t-{terminal}/delivery/status",
        json={"status": "merged"},
    )
    assert resp.status_code == 409
    assert "one-way" in resp.json()["detail"]


# ---------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------


def test_emits_delivery_status_changed_event(client, deployment):
    conn = _open(deployment)
    _seed_bundle(conn, "b-evt")
    _seed_review_request(conn, "b-evt", status="created")
    conn.commit()
    conn.close()

    resp = client.post(
        "/api/bundles/b-evt/delivery/status",
        json={"status": "merged", "note": "looks good"},
    )
    assert resp.status_code == 200

    conn = _open(deployment)
    rows = conn.execute(
        "SELECT data_json FROM events "
        "WHERE type = 'bundle_delivery_status_changed'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["bundle_id"] == "b-evt"
    assert payload["prior_status"] == "created"
    assert payload["new_status"] == "merged"
    assert payload["note"] == "looks good"


# ---------------------------------------------------------------------
# UI button visibility
# ---------------------------------------------------------------------


@pytest.mark.parametrize("status", ["created", "updated"])
def test_buttons_render_on_actionable_status(client, deployment, status):
    conn = _open(deployment)
    _seed_bundle(conn, f"b-ui-{status}")
    _seed_review_request(conn, f"b-ui-{status}", status=status)
    conn.commit()
    conn.close()

    body = client.get(f"/agentic/bundles/b-ui-{status}").text
    assert 'id="op-mark-merged"' in body
    assert 'id="op-mark-closed"' in body


@pytest.mark.parametrize("status", ["merged", "closed", "create_failed"])
def test_buttons_absent_on_terminal_status(client, deployment, status):
    conn = _open(deployment)
    _seed_bundle(conn, f"b-uit-{status}")
    _seed_review_request(conn, f"b-uit-{status}", status=status)
    conn.commit()
    conn.close()

    body = client.get(f"/agentic/bundles/b-uit-{status}").text
    assert 'id="op-mark-merged"' not in body
    assert 'id="op-mark-closed"' not in body


def test_buttons_absent_without_delivery_row(client, deployment):
    conn = _open(deployment)
    _seed_bundle(conn, "b-ui-none")
    conn.commit()
    conn.close()
    body = client.get("/agentic/bundles/b-ui-none").text
    assert 'id="op-mark-merged"' not in body
    assert 'id="op-mark-closed"' not in body


def test_note_renders_as_note_not_error(client, deployment):
    """A 'note:' prefixed value in the error column renders under
    the 'Note:' label rather than 'Error:' — distinguishes
    operator annotations from real provider failures."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-note-ui")
    _seed_review_request(
        conn, "b-note-ui", status="merged",
        error="note: landed upstream",
    )
    conn.commit()
    conn.close()
    body = client.get("/agentic/bundles/b-note-ui").text
    assert "Note:" in body
    assert "landed upstream" in body


def test_real_error_still_renders_as_error(client, deployment):
    """A bare error (no 'note:' prefix) stays under 'Error:' so
    create_failed rows are still clearly errors."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-real-err")
    _seed_review_request(
        conn, "b-real-err", status="create_failed",
        error="DeliveryAuthError: bad token",
    )
    conn.commit()
    conn.close()
    body = client.get("/agentic/bundles/b-real-err").text
    assert "Error:" in body
    assert "DeliveryAuthError" in body
