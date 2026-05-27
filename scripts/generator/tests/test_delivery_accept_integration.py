"""Step 11d-2: Accept-with-delivery flow + Delivery card UI.

Tests against the wired accept endpoint with a LocalPatchProvider
target. Network providers (11d-3 / 11d-4) test against monkeypatched
httpx; this file stays no-network.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from dportsv3.db.schema import init_db
from dportsv3.tracker.agentic_queries import latest_review_request_for_bundle
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_SAMPLE_DIFF = (
    "--- a/ports/devel/foo/dragonfly/patch-x.c\n"
    "+++ b/ports/devel/foo/dragonfly/patch-x.c\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


def _seed_bundle(conn, bundle_id, **kw):
    """Insert a verified, agent_fixed bundle ready for accept."""
    now = _now()
    conn.execute(
        """INSERT INTO bundles (
              bundle_id, run_id, origin, flavor, ts_utc, result,
              target, path, last_seen_at, resolution,
              verification_status, verification_at,
              error_signature
           ) VALUES (?, '', ?, '', ?, 'failure', ?, '', ?,
                     ?, ?, ?, ?)""",
        (bundle_id, kw.get("origin", "devel/foo"), now,
         kw.get("target", "@2026Q2"), now,
         kw.get("resolution", "agent_fixed"),
         kw.get("verification_status", "verified"),
         now, kw.get("error_signature", "sig-test-1")),
    )
    conn.commit()


def _seed_diff_artifact(artifact_root: Path, bundle_id: str, diff_text: str):
    """Write a changes.diff to the artifact blobstore + insert the
    artifact_refs row pointing at it."""
    import hashlib
    sha = hashlib.sha256(diff_text.encode()).hexdigest()
    obj_dir = artifact_root / "blobstore" / "objects" / "sha256" / sha[0:2] / sha[2:4]
    obj_dir.mkdir(parents=True, exist_ok=True)
    (obj_dir / sha).write_text(diff_text)
    return sha


def _insert_artifact_ref(conn, bundle_id, relpath, sha, kind="text"):
    conn.execute(
        """INSERT INTO artifact_refs
           (bundle_id, relpath, backend, sha256, kind, size, created_at)
           VALUES (?, ?, 'blob', ?, ?, ?, ?)""",
        (bundle_id, relpath, sha, kind, 0, _now()),
    )
    conn.commit()


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    """Set up a tracker server backed by a temp DB + artifact root +
    delivery.toml pointing at a local outbox. Returns a dict the
    individual tests can extend."""
    db_path = tmp_path / "state.db"
    artifact_root = tmp_path / "artifacts"
    outbox = tmp_path / "outbox"
    config_dir = tmp_path / "config"

    artifact_root.mkdir()
    outbox.mkdir()
    config_dir.mkdir()

    (config_dir / "delivery.toml").write_text(
        '[provider]\n'
        'type = "local-patch"\n'
        'branch_template = "agentic/{origin_safe}-{bundle_short}"\n'
    )

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.close()

    monkeypatch.setenv("DPORTSV3_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("DPORTSV3_DELIVERY_OUTBOX", str(outbox))
    monkeypatch.delenv("DPORTSV3_DELIVERY_CONFIG", raising=False)

    app = create_app(db_path)
    app.state.artifact_root = artifact_root

    return {
        "db_path": db_path,
        "artifact_root": artifact_root,
        "outbox": outbox,
        "config_dir": config_dir,
        "app": app,
    }


@pytest.fixture
def client(deployment):
    with TestClient(deployment["app"]) as c:
        yield c


def _open(deployment) -> sqlite3.Connection:
    conn = sqlite3.connect(str(deployment["db_path"]))
    conn.row_factory = sqlite3.Row
    return conn


# =====================================================================
# Accept-with-delivery happy path
# =====================================================================


def test_accept_with_delivery_writes_row_and_creates_patch(
    client, deployment,
):
    conn = _open(deployment)
    _seed_bundle(conn, "b-1")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-1", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-1", "analysis/changes.diff", sha)
    conn.close()

    resp = client.post("/api/bundles/b-1/accept", json={"operator": "alice"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["resolution"] == "accepted"
    assert "delivery" in body
    d = body["delivery"]
    assert d["status"] == "created"
    assert d["provider"] == "local-patch"
    assert d["branch"].startswith("agentic/devel-foo-")

    # bundle_review_requests row written.
    conn = _open(deployment)
    try:
        latest = latest_review_request_for_bundle(conn, "b-1")
    finally:
        conn.close()
    assert latest is not None
    assert latest["status"] == "created"
    assert latest["operator"] == "alice"
    assert latest["error_signature"] == "sig-test-1"

    # The patch file actually landed in the outbox.
    matches = list(deployment["outbox"].rglob("*.patch"))
    assert len(matches) == 1
    assert matches[0].read_text() == _SAMPLE_DIFF


def test_accept_emits_bundle_delivered_event(client, deployment):
    conn = _open(deployment)
    _seed_bundle(conn, "b-evt")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-evt", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-evt", "analysis/changes.diff", sha)
    conn.close()

    resp = client.post("/api/bundles/b-evt/accept", json={})
    assert resp.status_code == 200

    conn = _open(deployment)
    rows = conn.execute(
        "SELECT data_json FROM events WHERE type = 'bundle_delivered'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    payload = json.loads(rows[0][0])
    assert payload["bundle_id"] == "b-evt"
    assert payload["status"] == "created"
    assert payload["provider"] == "local-patch"


# =====================================================================
# Skipping paths — accept stays successful, no delivery row
# =====================================================================


def test_deliver_false_skips_delivery(client, deployment):
    conn = _open(deployment)
    _seed_bundle(conn, "b-skip")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-skip", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-skip", "analysis/changes.diff", sha)
    conn.close()

    resp = client.post(
        "/api/bundles/b-skip/accept", json={"deliver": False},
    )
    assert resp.status_code == 200
    d = resp.json()["delivery"]
    assert d["status"] == "skipped"
    assert d["skip_reason"] == "operator_optout"

    conn = _open(deployment)
    try:
        latest = latest_review_request_for_bundle(conn, "b-skip")
    finally:
        conn.close()
    assert latest is None


def test_no_delivery_config_skips_silently(client, deployment, monkeypatch):
    # Remove the delivery.toml — config probe should fail to find it.
    (deployment["config_dir"] / "delivery.toml").unlink()
    monkeypatch.delenv("DPORTSV3_DELIVERY_CONFIG", raising=False)

    conn = _open(deployment)
    _seed_bundle(conn, "b-no-cfg")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-no-cfg", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-no-cfg", "analysis/changes.diff", sha)
    conn.close()

    resp = client.post("/api/bundles/b-no-cfg/accept", json={})
    assert resp.status_code == 200
    d = resp.json()["delivery"]
    assert d["status"] == "skipped"
    assert d["skip_reason"] == "no_config"


def test_missing_changes_diff_skips(client, deployment):
    """If the bundle has no changes.diff artifact, delivery is
    skipped (not failed) — there's nothing to push."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-nodiff")
    conn.close()
    # Deliberately do NOT insert an artifact ref.

    resp = client.post("/api/bundles/b-nodiff/accept", json={})
    assert resp.status_code == 200
    d = resp.json()["delivery"]
    assert d["status"] == "skipped"
    assert d["skip_reason"] == "no_changes_diff"


def test_empty_changes_diff_skips(client, deployment):
    conn = _open(deployment)
    _seed_bundle(conn, "b-emptydiff")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-emptydiff", "")
    _insert_artifact_ref(conn, "b-emptydiff", "analysis/changes.diff", sha)
    conn.close()

    resp = client.post("/api/bundles/b-emptydiff/accept", json={})
    assert resp.status_code == 200
    d = resp.json()["delivery"]
    assert d["status"] == "skipped"
    assert d["skip_reason"] == "changes_diff_empty"


# =====================================================================
# Provider failure path — accept still succeeds, create_failed row
# =====================================================================


def test_github_provider_missing_clone_env_clear_error(
    client, deployment, monkeypatch,
):
    """Finding 5 (11d-3 review): orchestrator pre-validates
    $DPORTSV3_OPERATOR_CLONE for network providers. When unset,
    the create_failed row carries a config-specific error message
    naming the missing env var, not "clone_dir /nonexistent
    doesn't exist" from deep inside _git."""
    # Reconfigure delivery to point at github so the validation
    # path fires (local-patch ignores clone_dir).
    (deployment["config_dir"] / "delivery.toml").write_text(
        '[provider]\n'
        'type = "github"\n'
        'repo = "DragonFlyBSD/DeltaPorts"\n'
    )
    monkeypatch.setenv("DPORTSV3_DELIVERY_TOKEN", "ghp_test")
    monkeypatch.delenv("DPORTSV3_OPERATOR_CLONE", raising=False)

    conn = _open(deployment)
    _seed_bundle(conn, "b-no-clone")
    sha = _seed_diff_artifact(
        deployment["artifact_root"], "b-no-clone", _SAMPLE_DIFF,
    )
    _insert_artifact_ref(conn, "b-no-clone", "analysis/changes.diff", sha)
    conn.close()

    resp = client.post("/api/bundles/b-no-clone/accept", json={})
    assert resp.status_code == 200
    d = resp.json()["delivery"]
    assert d["status"] == "create_failed"
    assert "$DPORTSV3_OPERATOR_CLONE" in d["error"]
    assert "/nonexistent" not in d["error"]  # no fabricated path


def test_github_provider_missing_clone_dir_clear_error(
    client, deployment, monkeypatch, tmp_path,
):
    """Pre-validation also catches the case where the env var IS
    set but points at a nonexistent path."""
    (deployment["config_dir"] / "delivery.toml").write_text(
        '[provider]\n'
        'type = "github"\n'
        'repo = "DragonFlyBSD/DeltaPorts"\n'
    )
    monkeypatch.setenv("DPORTSV3_DELIVERY_TOKEN", "ghp_test")
    monkeypatch.setenv(
        "DPORTSV3_OPERATOR_CLONE", str(tmp_path / "does-not-exist"),
    )

    conn = _open(deployment)
    _seed_bundle(conn, "b-bad-clone")
    sha = _seed_diff_artifact(
        deployment["artifact_root"], "b-bad-clone", _SAMPLE_DIFF,
    )
    _insert_artifact_ref(conn, "b-bad-clone", "analysis/changes.diff", sha)
    conn.close()

    resp = client.post("/api/bundles/b-bad-clone/accept", json={})
    assert resp.status_code == 200
    d = resp.json()["delivery"]
    assert d["status"] == "create_failed"
    assert "doesn't exist" in d["error"]
    assert "does-not-exist" in d["error"]


def test_provider_failure_records_create_failed_row(
    client, deployment, monkeypatch,
):
    """When the provider raises, the accept still succeeds but a
    create_failed row is written so the operator sees what
    happened."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-fail")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-fail", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-fail", "analysis/changes.diff", sha)
    conn.close()

    # Sabotage LocalPatchProvider by removing the outbox AFTER setup
    # so its config check fires inside create_review_request.
    import shutil
    shutil.rmtree(deployment["outbox"])

    resp = client.post("/api/bundles/b-fail/accept", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"] == "accepted"
    d = body["delivery"]
    assert d["status"] == "create_failed"
    assert "outbox" in d["error"].lower() or "doesn't" in d["error"].lower()

    conn = _open(deployment)
    try:
        latest = latest_review_request_for_bundle(conn, "b-fail")
    finally:
        conn.close()
    assert latest is not None
    assert latest["status"] == "create_failed"
    assert latest["error"] is not None


# =====================================================================
# Idempotency — second accept lands as 'updated'
# =====================================================================


def test_second_accept_returns_updated_status(client, deployment):
    """LocalPatchProvider's same-content idempotency surfaces as
    status='updated' on a re-Accept of the same bundle."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-idem")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-idem", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-idem", "analysis/changes.diff", sha)
    conn.close()

    r1 = client.post("/api/bundles/b-idem/accept", json={})
    assert r1.status_code == 200
    assert r1.json()["delivery"]["status"] == "created"

    # The first accept moved the bundle to 'accepted' (terminal).
    # 11c terminal-state checks refuse a second accept with 409 —
    # so to test the orchestrator-level idempotency we reopen the
    # bundle first, then accept again.
    rep = client.post(
        "/api/bundles/b-idem/reopen",
        json={"reason": "test the delivery idempotency path"},
    )
    assert rep.status_code == 200
    # Reopen wiped verification_status; restore it so accept's gate
    # passes again.
    conn = _open(deployment, )
    conn.execute(
        "UPDATE bundles SET verification_status = 'verified' "
        "WHERE bundle_id = 'b-idem'"
    )
    conn.commit()
    conn.close()

    r2 = client.post("/api/bundles/b-idem/accept", json={})
    assert r2.status_code == 200, r2.text
    assert r2.json()["delivery"]["status"] == "updated"


def test_accept_persists_diff_sha256(client, deployment):
    """Finding 4: bundle_review_requests.diff_sha256 is populated
    on every accept (created + updated) so the GitHub short-circuit
    on a future re-Accept can compare against it."""
    import hashlib
    conn = _open(deployment)
    _seed_bundle(conn, "b-sha")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-sha", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-sha", "analysis/changes.diff", sha)
    conn.close()
    expected = hashlib.sha256(_SAMPLE_DIFF.encode()).hexdigest()

    r1 = client.post("/api/bundles/b-sha/accept", json={})
    assert r1.status_code == 200
    conn = _open(deployment)
    try:
        row = latest_review_request_for_bundle(conn, "b-sha")
    finally:
        conn.close()
    assert row["diff_sha256"] == expected


# =====================================================================
# UI Delivery card
# =====================================================================


def test_delivery_card_renders_on_created(client, deployment):
    conn = _open(deployment)
    _seed_bundle(conn, "b-ui-1")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-ui-1", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-ui-1", "analysis/changes.diff", sha)
    conn.close()

    client.post("/api/bundles/b-ui-1/accept", json={"operator": "alice"})
    body = client.get("/agentic/bundles/b-ui-1").text
    assert "Delivery" in body
    # Status pill rendered.
    assert "stat-pill built" in body  # 'created' uses the success pill style
    assert "agentic/devel-foo-" in body  # branch name shown
    assert "alice" in body  # operator


def test_delivery_card_absent_without_row(client, deployment):
    """Bundles that haven't been delivered yet don't render the
    Delivery card — keeps the page uncluttered."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-no-delivery")
    conn.close()

    body = client.get("/agentic/bundles/b-no-delivery").text
    # The word "Delivery" might appear elsewhere, but the card
    # has a known structural marker — check for the local-patch
    # provider code element which only renders inside the card.
    assert ">local-patch<" not in body


def test_delivery_card_shows_error_on_create_failed(
    client, deployment,
):
    """create_failed rows surface the error message inline so the
    operator can see what went wrong without grepping logs."""
    conn = _open(deployment)
    _seed_bundle(conn, "b-ui-fail")
    sha = _seed_diff_artifact(deployment["artifact_root"], "b-ui-fail", _SAMPLE_DIFF)
    _insert_artifact_ref(conn, "b-ui-fail", "analysis/changes.diff", sha)
    conn.close()

    import shutil
    shutil.rmtree(deployment["outbox"])
    client.post("/api/bundles/b-ui-fail/accept", json={})

    body = client.get("/agentic/bundles/b-ui-fail").text
    assert "create failed" in body
    assert "Error:" in body
