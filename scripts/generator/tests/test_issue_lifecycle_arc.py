"""WS10 — the issue lifecycle as one end-to-end arc.

Each seam of the Sentry model is proven in isolation elsewhere
(``test_issue_writer`` for ingest/rollup/regress, ``test_issue_actions``
for the operator endpoints, ``test_runner_mute_skip`` for the runner
short-circuit). This is the single narrative test that drives the *real*
``ArtifactStore``, the *real* issue-action endpoints, and the *real*
runner mute check through the whole lifecycle in sequence, pinning the
handoffs between them:

    ingest → unresolved → rollup → resolve → regress-on-new-occurrence
    → mute → runner skips auto-triage → unmute → back to regressed
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import dportsv3.agent.runner as runner
from dportsv3.artifact_store import ArtifactStore
from dportsv3.fingerprint import compute_fingerprint, issue_key
from dportsv3.tracker.agentic_queries import get_issue
from dportsv3.tracker.server import create_app

ERR = "cc: error: undefined reference to `foo'\n"
TARGET, ORIGIN = "@2026Q3", "ftp/curl"


def _key() -> str:
    return issue_key(TARGET, ORIGIN, compute_fingerprint(ERR))


def _ingest(store: ArtifactStore, bundle_id: str, ts: str) -> None:
    store.upsert_run_bundle({
        "run_id": "r", "profile": "ci", "ts_utc": ts, "bundle_id": bundle_id,
        "origin": ORIGIN, "flavor": "", "result": "failure",
        "target": TARGET, "errors_text": ERR,
    })


@pytest.fixture
def arc(tmp_path):
    store = ArtifactStore(tmp_path)
    with TestClient(create_app(str(store.db_path))) as client:
        yield store, client


def test_issue_lifecycle_arc(arc):
    store, client = arc
    conn = store.conn
    key = _key()
    now = datetime.now(timezone.utc)

    # 1. First occurrence creates the issue, unresolved, times_seen=1.
    _ingest(store, "b1", (now - timedelta(hours=2)).isoformat())
    iss = get_issue(conn, key)
    assert (iss["state"], iss["times_seen"]) == ("unresolved", 1)

    # 2. A second occurrence of the same fingerprint rolls up — no new issue.
    _ingest(store, "b2", (now - timedelta(hours=1)).isoformat())
    assert get_issue(conn, key)["times_seen"] == 2

    # 3. Operator resolves the issue via the endpoint.
    assert client.post(f"/api/issues/{key}/resolve", json={}).status_code == 200
    iss = get_issue(conn, key)
    assert iss["state"] == "resolved" and iss["resolved_at"]

    # 4. A fresh occurrence arriving AFTER the resolve regresses the issue
    #    (the fix came back). times_seen keeps climbing.
    occ3_ts = (
        datetime.fromisoformat(iss["resolved_at"]) + timedelta(minutes=1)
    ).isoformat()
    _ingest(store, "b3", occ3_ts)
    iss = get_issue(conn, key)
    assert iss["state"] == "regressed" and iss["regressed_at"]
    assert iss["times_seen"] == 3

    # 5. Operator mutes the (regressed) issue.
    assert client.post(f"/api/issues/{key}/mute", json={}).status_code == 200
    assert get_issue(conn, key)["state"] == "muted"

    # 6. The runner short-circuits auto-triage on the muted issue's
    #    occurrence: the job retires DEAD with retire_reason='issue_muted'.
    conn.execute(
        "INSERT INTO jobs (job_id, state, type, origin, target, bundle_id, "
        "last_seen_at) VALUES ('j-b3','triaging','triage',?,?, 'b3', ?)",
        (ORIGIN, TARGET, occ3_ts),
    )
    conn.commit()
    runner._state_db_conn = conn
    try:
        outcome = runner._maybe_skip_muted_issue(
            queue_root=store.logs_root,
            job={"bundle_id": "b3", "target": TARGET},
            job_id="j-b3", sibling_paths=None, origin=ORIGIN, job_type="triage",
        )
    finally:
        runner._state_db_conn = None
    assert outcome == (True, f"issue_muted:{key}")
    jrow = conn.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id='j-b3'"
    ).fetchone()
    assert (jrow["state"], jrow["retire_reason"]) == ("dead", "issue_muted")

    # 7. Unmute recomputes the open state: a post-resolve occurrence exists,
    #    so the issue lands back on regressed (not plain unresolved).
    assert client.post(f"/api/issues/{key}/unmute", json={}).status_code == 200
    assert get_issue(conn, key)["state"] == "regressed"


def test_issue_action_gate_rejects_illegal_and_unknown(arc):
    """The endpoint gate refuses an action that doesn't apply to the
    current state (409) and an unknown issue (404) — the two failure
    modes the arc relies on to stay well-formed."""
    store, client = arc
    key = _key()
    _ingest(store, "b1", datetime.now(timezone.utc).isoformat())
    # Fresh issue is unresolved: unmute (needs muted) and reopen (needs
    # resolved) are both illegal → 409.
    assert client.post(f"/api/issues/{key}/unmute", json={}).status_code == 409
    assert client.post(f"/api/issues/{key}/reopen", json={}).status_code == 409
    # Unknown issue → 404 regardless of action.
    assert client.post("/api/issues/does-not-exist/mute", json={}).status_code == 404
