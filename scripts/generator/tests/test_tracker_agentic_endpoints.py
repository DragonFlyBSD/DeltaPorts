"""Tests for the Phase 4 step 5 agentic-read endpoints.

The tracker absorbs state-server's read API onto the same state.db.
These tests seed rows directly via SQL (matching what artifact-store
and state-server would write) and exercise the new ``/api/...`` routes.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dportsv3.db.schema import init_db as init_state_db
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def seeded_state_db(tmp_path: Path) -> Path:
    """Build a state.db with bundles/jobs/runs covering target filters."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_state_db(conn)

    now = _now()
    conn.executemany(
        """INSERT INTO runs (run_id, profile, target, ts_start, last_seen_at)
           VALUES (?, ?, ?, ?, ?)""",
        [
            ("run-2026Q2-001", "2026Q2", "@2026Q2", now, now),
            ("run-main-002", "main", "@main", now, now),
            ("run-legacy-003", "legacy", None, now, now),
        ],
    )
    conn.executemany(
        """INSERT INTO bundles
           (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("b-q2-a", "run-2026Q2-001", "devel/foo", "", now, "fail", "@2026Q2", now),
            ("b-q2-b", "run-2026Q2-001", "devel/bar", "", now, "fail", "@2026Q2", now),
            ("b-main-a", "run-main-002", "devel/foo", "", now, "fail", "@main", now),
            ("b-legacy", "run-legacy-003", "devel/baz", "", now, "fail", None, now),
        ],
    )
    conn.executemany(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            # Typed JobState values after Phase 1 lifecycle cutover.
            # "queued" maps into the "pending" UI bucket; "claimed",
            # "triaging", etc. all map into "inflight".
            ("job-q2-a", "queued", "triage", "devel/foo", "", "", now, "", now, "@2026Q2"),
            ("job-q2-b", "done",   "triage", "devel/bar", "", "", now, "", now, "@2026Q2"),
            ("job-main", "queued", "triage", "devel/foo", "", "", now, "", now, "@main"),
            ("job-legacy", "queued", "triage", "devel/baz", "", "", now, "", now, None),
        ],
    )
    conn.execute(
        """INSERT INTO events (ts, type, data_json) VALUES (?, ?, ?)""",
        (now, "bundle_upserted", json.dumps({"bundle_id": "b-q2-a", "target": "@2026Q2"})),
    )
    conn.execute(
        """INSERT INTO events (ts, type, data_json) VALUES (?, ?, ?)""",
        (now, "bundle_upserted", json.dumps({"bundle_id": "b-main-a", "target": "@main"})),
    )
    conn.execute(
        """INSERT INTO runner_status (id, status, updated_at)
           VALUES (1, 'idle', ?)""",
        (now,),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client(seeded_state_db: Path) -> TestClient:
    app = create_app(seeded_state_db)
    with TestClient(app) as test_client:
        yield test_client


def test_api_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_api_agentic_status_counts(client: TestClient) -> None:
    body = client.get("/api/agentic-status").json()
    assert body["bundles"] == 4
    assert body["runs"] == 3
    assert body["jobs"]["pending"] == 3
    assert body["jobs"]["done"] == 1


def test_api_runs_filters_by_target(client: TestClient) -> None:
    all_runs = client.get("/api/runs").json()
    assert len(all_runs) == 3
    q2_runs = client.get("/api/runs", params={"target": "@2026Q2"}).json()
    assert [r["run_id"] for r in q2_runs] == ["run-2026Q2-001"]


def test_api_run_detail_404(client: TestClient) -> None:
    resp = client.get("/api/runs/run-nope")
    assert resp.status_code == 404


def test_api_jobs_filters_by_state_and_target(client: TestClient) -> None:
    pending = client.get("/api/jobs", params={"state": "pending"}).json()
    assert {j["job_id"] for j in pending} == {"job-q2-a", "job-main", "job-legacy"}

    q2_pending = client.get(
        "/api/jobs", params={"state": "pending", "target": "@2026Q2"}
    ).json()
    assert [j["job_id"] for j in q2_pending] == ["job-q2-a"]


def test_api_jobs_legacy_null_target_only_in_unfiltered(client: TestClient) -> None:
    unfiltered = client.get("/api/jobs").json()
    assert any(j["job_id"] == "job-legacy" for j in unfiltered)

    filtered = client.get("/api/jobs", params={"target": "@main"}).json()
    assert all(j["job_id"] != "job-legacy" for j in filtered)


def test_api_bundles_filters(client: TestClient) -> None:
    q2 = client.get("/api/bundles", params={"target": "@2026Q2"}).json()
    assert {b["bundle_id"] for b in q2} == {"b-q2-a", "b-q2-b"}

    by_origin = client.get(
        "/api/bundles", params={"origin": "devel/foo"}
    ).json()
    assert {b["bundle_id"] for b in by_origin} == {"b-q2-a", "b-main-a"}


def test_api_bundle_detail_includes_artifacts_list(client: TestClient) -> None:
    body = client.get("/api/bundles/b-q2-a").json()
    assert body["bundle_id"] == "b-q2-a"
    assert body["target"] == "@2026Q2"
    assert "artifacts" in body
    assert isinstance(body["artifacts"], list)


def test_api_port_history_target_scoped(client: TestClient) -> None:
    all_foo = client.get("/api/ports/devel/foo").json()
    assert {b["bundle_id"] for b in all_foo} == {"b-q2-a", "b-main-a"}

    q2_foo = client.get(
        "/api/ports/devel/foo", params={"target": "@2026Q2"}
    ).json()
    assert {b["bundle_id"] for b in q2_foo} == {"b-q2-a"}


def test_api_runner_status_returns_singleton(client: TestClient) -> None:
    body = client.get("/api/runner-status").json()
    assert body["status"] == "idle"


def test_api_events_filters_by_target_payload(
    client: TestClient, seeded_state_db: Path
) -> None:
    # The SSE endpoint streams forever; verify target filtering by
    # exercising the query layer directly against the seeded DB.
    from dportsv3.tracker.agentic_queries import events_since
    from dportsv3.tracker.db import open_db

    conn = open_db(seeded_state_db)
    try:
        all_events = events_since(conn, last_id=0)
        q2_events = events_since(conn, last_id=0, target="@2026Q2")
    finally:
        conn.close()

    assert len(all_events) == 2
    assert len(q2_events) == 1
    assert json.loads(q2_events[0]["data_json"])["target"] == "@2026Q2"
