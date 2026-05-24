"""Plan Step 11b Slice 2 — POST /api/bundles/{bundle_id}/verification
+ verification_status / verification_at / verification_applied_diff_sha256
columns on bundles.

The endpoint is the tracker-side counterpart to the
`dportsv3 dev-env apply-and-build` substrate primitive (Slice 1).
Slice 3 will glue the two together.
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


def _insert_bundle(conn, bundle_id: str, **extra) -> None:
    now = _now()
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at)
           VALUES (?, '', ?, '', ?, 'failure', ?, '', ?)""",
        (bundle_id, extra.get("origin", "devel/foo"), now,
         extra.get("target", "@2026Q2"), now),
    )
    conn.commit()


@pytest.fixture
def seeded_db(tmp_path: Path):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    _insert_bundle(c, "b-verified")
    _insert_bundle(c, "b-failed")
    c.close()
    return db_path


@pytest.fixture
def client(seeded_db):
    app = create_app(seeded_db)
    with TestClient(app) as c:
        yield c


def _bundle_row(db_path: Path, bundle_id: str) -> sqlite3.Row:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM bundles WHERE bundle_id = ?", (bundle_id,),
    ).fetchone()
    conn.close()
    return row


def test_post_verification_happy_path_verified(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-verified/verification",
        json={
            "ok": True,
            "applied_diff_sha256": "a" * 64,
            "dsynth_exit": 0,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["bundle_id"] == "b-verified"
    assert body["verification_status"] == "verified"
    assert body["applied_diff_sha256"] == "a" * 64
    assert body["verification_at"]

    row = _bundle_row(seeded_db, "b-verified")
    assert row["verification_status"] == "verified"
    assert row["verification_applied_diff_sha256"] == "a" * 64
    assert row["verification_at"]


def test_post_verification_happy_path_failed(client, seeded_db):
    resp = client.post(
        "/api/bundles/b-failed/verification",
        json={
            "ok": False,
            "applied_diff_sha256": "b" * 64,
            "dsynth_exit": 1,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["verification_status"] == "verification_failed"
    row = _bundle_row(seeded_db, "b-failed")
    assert row["verification_status"] == "verification_failed"


def test_post_verification_unknown_bundle_returns_404(client):
    resp = client.post(
        "/api/bundles/nope/verification",
        json={"ok": True, "applied_diff_sha256": None},
    )
    assert resp.status_code == 404


def test_post_verification_missing_ok_returns_400(client):
    resp = client.post(
        "/api/bundles/b-verified/verification",
        json={"applied_diff_sha256": "x"},
    )
    assert resp.status_code == 400
    assert "ok" in resp.json()["detail"]


def test_post_verification_non_bool_ok_returns_400(client):
    resp = client.post(
        "/api/bundles/b-verified/verification",
        json={"ok": "true", "applied_diff_sha256": None},
    )
    assert resp.status_code == 400


def test_post_verification_missing_diff_sha_returns_400(client):
    resp = client.post(
        "/api/bundles/b-verified/verification",
        json={"ok": True},
    )
    assert resp.status_code == 400
    assert "applied_diff_sha256" in resp.json()["detail"]


def test_post_verification_accepts_null_diff_sha(client, seeded_db):
    """A diff-less verification (just rebuilt the existing state) is
    legitimate; the column stores NULL."""
    resp = client.post(
        "/api/bundles/b-verified/verification",
        json={"ok": True, "applied_diff_sha256": None},
    )
    assert resp.status_code == 200
    row = _bundle_row(seeded_db, "b-verified")
    assert row["verification_applied_diff_sha256"] is None
    assert row["verification_status"] == "verified"


def test_post_verification_overwrites_prior_attempt(client, seeded_db):
    """Re-POST with a different diff hash overwrites — the column is
    the *last* attempt, not a history."""
    client.post(
        "/api/bundles/b-verified/verification",
        json={"ok": False, "applied_diff_sha256": "old-sha"},
    )
    client.post(
        "/api/bundles/b-verified/verification",
        json={"ok": True, "applied_diff_sha256": "new-sha"},
    )
    row = _bundle_row(seeded_db, "b-verified")
    assert row["verification_status"] == "verified"
    assert row["verification_applied_diff_sha256"] == "new-sha"


def test_post_verification_emits_event(client, seeded_db):
    client.post(
        "/api/bundles/b-verified/verification",
        json={
            "ok": True,
            "applied_diff_sha256": "a" * 64,
            "dsynth_exit": 0,
        },
    )
    conn = sqlite3.connect(str(seeded_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT type, data_json FROM events "
        "WHERE type = 'bundle_verified' ORDER BY id DESC LIMIT 1",
    ).fetchone()
    conn.close()
    assert row is not None
    import json
    data = json.loads(row["data_json"])
    assert data["bundle_id"] == "b-verified"
    assert data["verification_status"] == "verified"


# ---------------------------------------------------------------------------
# Slice 4 — UI surface (verification pill on bundle list + detail)
# ---------------------------------------------------------------------------


def test_bundle_detail_renders_verified_pill(client, seeded_db):
    client.post(
        "/api/bundles/b-verified/verification",
        json={"ok": True, "applied_diff_sha256": "a" * 64},
    )
    body = client.get("/agentic/bundles/b-verified").text
    assert ">Verification<" in body
    assert "verified" in body


def test_bundle_detail_renders_verification_failed_pill(client, seeded_db):
    client.post(
        "/api/bundles/b-failed/verification",
        json={"ok": False, "applied_diff_sha256": "b" * 64},
    )
    body = client.get("/agentic/bundles/b-failed").text
    assert ">Verification<" in body
    assert "verification failed" in body


def test_bundle_detail_omits_verification_row_when_unset(client):
    """Pre-Step-11b bundles should render unchanged."""
    body = client.get("/agentic/bundles/b-verified").text
    assert ">Verification<" not in body


def test_bundle_list_renders_verified_column(client, seeded_db):
    client.post(
        "/api/bundles/b-verified/verification",
        json={"ok": True, "applied_diff_sha256": "a" * 64},
    )
    body = client.get("/agentic/bundles").text
    assert ">Verified<" in body  # column header
    assert "verified" in body
