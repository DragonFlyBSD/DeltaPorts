"""Tests for the Phase 4 step 6 agentic HTML views.

These are page-shape assertions only — Jinja rendering succeeds, the
key fields are present in the HTML, target filters work end-to-end.
The data layer is covered by ``test_tracker_agentic_endpoints.py``.
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
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_state_db(conn)

    now = _now()
    conn.executemany(
        """INSERT INTO runs (run_id, profile, target, ts_start, last_seen_at)
           VALUES (?, ?, ?, ?, ?)""",
        [
            ("run-q2-001", "2026Q2", "@2026Q2", now, now),
            ("run-main-002", "main", "@main", now, now),
        ],
    )
    conn.executemany(
        """INSERT INTO bundles
           (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("b-q2-foo", "run-q2-001", "devel/foo", "", now, "fail", "@2026Q2", now),
            ("b-q2-bar", "run-q2-001", "devel/bar", "", now, "fail", "@2026Q2", now),
            ("b-main-foo", "run-main-002", "devel/foo", "", now, "fail", "@main", now),
        ],
    )
    conn.executemany(
        """INSERT INTO artifact_refs
           (bundle_id, relpath, backend, sha256, kind, size, created_at)
           VALUES (?, ?, 'blob', ?, ?, ?, ?)""",
        [
            ("b-q2-foo", "meta.txt", "abc123def456", "text/plain", 42, now),
            ("b-q2-foo", "logs/errors.txt", "ffee1122", "text/plain", 100, now),
        ],
    )
    conn.executemany(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("job-q2-foo", "queued", "triage", "devel/foo", "", "",
             now, "/tmp/job-q2-foo.job", now, "@2026Q2"),
            ("job-main-foo", "done", "triage", "devel/foo", "", "",
             now, "/tmp/job-main-foo.job", now, "@main"),
        ],
    )
    conn.execute(
        """INSERT INTO activity_log (ts, job_id, stage, message, duration_ms)
           VALUES (?, ?, ?, ?, ?)""",
        (now, "job-q2-foo", "triage_start", "began triage", 12),
    )
    conn.execute(
        """INSERT INTO runner_status (id, status, job_id, updated_at)
           VALUES (1, 'idle', NULL, ?)""",
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


def test_view_agentic_index(client: TestClient) -> None:
    resp = client.get("/agentic")
    assert resp.status_code == 200
    body = resp.text
    assert "Agentic" in body
    assert "b-q2-foo" in body
    assert "job-q2-foo" in body
    # Counts panel
    assert ">3<" in body or "3</div>" in body  # bundles count


def test_view_agentic_bundles_filter(client: TestClient) -> None:
    all_resp = client.get("/agentic/bundles")
    assert all_resp.status_code == 200
    assert "b-q2-foo" in all_resp.text
    assert "b-main-foo" in all_resp.text

    q2 = client.get("/agentic/bundles", params={"target": "@2026Q2"})
    assert q2.status_code == 200
    assert "b-q2-foo" in q2.text
    assert "b-main-foo" not in q2.text


def test_view_agentic_bundle_detail_lists_artifacts(client: TestClient) -> None:
    resp = client.get("/agentic/bundles/b-q2-foo")
    assert resp.status_code == 200
    body = resp.text
    assert "b-q2-foo" in body
    assert "meta.txt" in body
    assert "logs/errors.txt" in body
    # Link to artifact stream endpoint
    assert "/api/bundles/b-q2-foo/artifacts/meta.txt" in body


def test_view_agentic_bundle_detail_404(client: TestClient) -> None:
    assert client.get("/agentic/bundles/does-not-exist").status_code == 404


def test_view_agentic_jobs_state_filter(client: TestClient) -> None:
    pending = client.get(
        "/agentic/jobs", params={"state": "pending"}
    )
    assert pending.status_code == 200
    assert "job-q2-foo" in pending.text
    assert "job-main-foo" not in pending.text


def test_view_agentic_job_detail_shows_activity(client: TestClient) -> None:
    resp = client.get("/agentic/jobs/job-q2-foo")
    assert resp.status_code == 200
    body = resp.text
    assert "triage_start" in body
    assert "began triage" in body


def test_view_agentic_runner(client: TestClient) -> None:
    resp = client.get("/agentic/runner")
    assert resp.status_code == 200
    assert "idle" in resp.text


def test_view_agentic_activity(client: TestClient) -> None:
    resp = client.get("/agentic/activity")
    assert resp.status_code == 200
    body = resp.text
    assert "triage_start" in body
    assert "job-q2-foo" in body


def test_view_agentic_run_detail(client: TestClient) -> None:
    resp = client.get("/agentic/runs/run-q2-001")
    assert resp.status_code == 200
    body = resp.text
    assert "run-q2-001" in body
    assert "b-q2-foo" in body
    assert "b-q2-bar" in body


def test_view_nav_includes_agentic(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert ">Agentic<" in resp.text
