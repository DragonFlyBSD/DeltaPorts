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
    trace_path = tmp_path / "tool_trace.jsonl"
    trace_path.write_text(
        json.dumps({"type": "attempt_start", "attempt": 1, "tokens_used_so_far": 0, "budget": 1000}) + "\n"
        + json.dumps({"type": "tool_call", "attempt": 1, "turn": 1, "tool": "dsynth_build", "args": {"origin": "devel/foo"}, "result": {"ok": False}, "duration_ms": 42}) + "\n"
        + json.dumps({"type": "attempt_end", "attempt": 1, "rebuild_ok": False, "tokens": 500}) + "\n"
    )
    meta_path = tmp_path / "meta.txt"
    meta_path.write_text("origin=devel/foo\n", encoding="utf-8")
    errors_path = tmp_path / "errors.txt"
    errors_path.write_text("build failed\n", encoding="utf-8")
    triage_path = tmp_path / "triage.md"
    triage_path.write_text(
        "## Classification\npatch-error\n\n- one\n\n```\n<unsafe>\n```\n",
        encoding="utf-8",
    )
    json_path = tmp_path / "patch_audit.json"
    json_path.write_text('{"status":"budget-exhausted","attempts":[1]}', encoding="utf-8")
    gzip_path = tmp_path / "full.log.gz"
    gzip_path.write_bytes(b"\x1f\x8bcompressed")
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
            ("b-q2-foo-retry", "run-q2-001", "devel/foo", "", now, "failure", "@2026Q2", now),
            ("b-q2-bar", "run-q2-001", "devel/bar", "", now, "fail", "@2026Q2", now),
            ("b-main-foo", "run-main-002", "devel/foo", "", now, "fail", "@main", now),
        ],
    )
    conn.executemany(
        """INSERT INTO artifact_refs
           (bundle_id, relpath, backend, fs_path, kind, size, created_at)
           VALUES (?, ?, 'fs', ?, ?, ?, ?)""",
        [
            ("b-q2-foo", "meta.txt", str(meta_path), "text", meta_path.stat().st_size, now),
            ("b-q2-foo", "logs/errors.txt", str(errors_path), "text", errors_path.stat().st_size, now),
            ("b-q2-foo", "analysis/triage.md", str(triage_path), "text", triage_path.stat().st_size, now),
            ("b-q2-foo", "analysis/patch_audit.json", str(json_path), "json", json_path.stat().st_size, now),
            ("b-q2-foo", "logs/full.log.gz", str(gzip_path), "gzip", gzip_path.stat().st_size, now),
            ("b-q2-foo", "analysis/tool_trace.jsonl", str(trace_path), "text", trace_path.stat().st_size, now),
            ("b-q2-foo", "logs/missing.txt", str(tmp_path / "missing.txt"), "text", 0, now),
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
            ("job-q2-dead", "dead", "patch", "devel/dead", "", "",
              now, "/tmp/job-q2-dead.job", now, "@2026Q2"),
            ("job-q2-manual", "escalated", "triage", "devel/manual", "", "",
              now, "/tmp/job-q2-manual.job", now, "@2026Q2"),
        ],
    )
    conn.execute(
        "UPDATE jobs SET retire_reason = 'patch_gave_up' WHERE job_id = 'job-q2-dead'"
    )
    conn.execute(
        "UPDATE jobs SET retire_reason = 'escalated_manual' WHERE job_id = 'job-q2-manual'"
    )
    conn.execute(
        """INSERT INTO activity_log (ts, job_id, stage, message, duration_ms)
           VALUES (?, ?, ?, ?, ?)""",
        (now, "job-q2-foo", "triage_start", "began triage", 12),
    )
    conn.execute(
        """INSERT INTO activity_log
           (ts, job_id, stage, message, duration_ms, extra_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            now, "job-q2-foo", "decision", "tier=AUTO for plist-error/high",
            0,
            json.dumps({
                "action": "auto_patch",
                "tier": "AUTO",
                "classification": "plist-error",
                "confidence": "high",
                "recent_failures": 0,
                "max_attempts": 3,
            }),
        ),
    )
    conn.executemany(
        """INSERT INTO job_events
           (ts, job_id, from_state, to_state, event_name, actor, detail_json)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        [
            (now, "job-q2-foo", None, "queued", "hook_enqueued", "hook", '{"origin":"devel/foo"}'),
            (now, "job-q2-foo", "queued", "claimed", "claim", "runner", None),
            (now, "job-q2-foo", "claimed", "triaging", "triage_start", "runner", None),
        ],
    )
    conn.execute(
        """INSERT INTO runner_status (id, status, job_id, updated_at)
           VALUES (1, 'idle', NULL, ?)""",
        (now,),
    )
    conn.execute(
        """INSERT INTO env_health_status
           (env, status, probed_at, operator_action, detail_json, updated_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            "test-env", "broken", now, "fix python runtime",
            json.dumps({
                "env": "test-env",
                "status": "broken",
                "checks": [{"name": "python_runtime", "status": "broken", "detail": "missing py311"}],
            }),
            now,
        ),
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
    assert "Jobs done / dead / escalated" in body
    assert "patch_gave_up" in body
    assert "Environment health" in body
    assert "test-env" in body
    assert "fix python runtime" in body
    # Counts panel
    assert ">4<" in body or "4</div>" in body  # bundles count


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
    assert "Tool trace" in body
    assert "dsynth_build" in body
    assert "rebuild_ok=False" in body
    # Link to artifact stream endpoint
    assert "/api/bundles/b-q2-foo/artifacts/meta.txt" in body
    assert "/agentic/bundles/b-q2-foo?artifact=meta.txt" in body
    assert "/agentic/bundles/b-q2-foo/artifacts/analysis/triage.md" in body
    assert "raw" in body
    # Default preview prefers analysis/triage.md over logs/errors.txt.
    assert "patch-error" in body
    assert "<h3>Classification</h3>" in body


def test_view_agentic_bundle_detail_selects_artifact_inline(client: TestClient) -> None:
    resp = client.get("/agentic/bundles/b-q2-foo", params={"artifact": "meta.txt"})
    assert resp.status_code == 200
    assert "origin=devel/foo" in resp.text
    assert "open full page" in resp.text


def test_view_agentic_bundle_detail_missing_selected_artifact_404(client: TestClient) -> None:
    assert client.get("/agentic/bundles/b-q2-foo", params={"artifact": "nope.txt"}).status_code == 404


def test_view_agentic_artifact_text_inline(client: TestClient) -> None:
    resp = client.get("/agentic/bundles/b-q2-foo/artifacts/meta.txt")
    assert resp.status_code == 200
    assert "origin=devel/foo" in resp.text
    assert "Open raw artifact" in resp.text


def test_view_agentic_artifact_markdown_rendered(client: TestClient) -> None:
    resp = client.get("/agentic/bundles/b-q2-foo/artifacts/analysis/triage.md")
    assert resp.status_code == 200
    assert "Markdown" in resp.text
    assert "<h3>Classification</h3>" in resp.text
    assert "<li>one</li>" in resp.text
    assert "&lt;unsafe&gt;" in resp.text
    assert "<unsafe>" not in resp.text


def test_view_agentic_artifact_json_pretty_printed(client: TestClient) -> None:
    resp = client.get("/agentic/bundles/b-q2-foo/artifacts/analysis/patch_audit.json")
    assert resp.status_code == 200
    assert "JSON" in resp.text
    assert "budget-exhausted" in resp.text
    assert "&#34;status&#34;" in resp.text


def test_view_agentic_artifact_gzip_download_notice(client: TestClient) -> None:
    resp = client.get("/agentic/bundles/b-q2-foo/artifacts/logs/full.log.gz")
    assert resp.status_code == 200
    assert "application/gzip" in resp.text
    assert "raw download" in resp.text


def test_view_agentic_artifact_missing_file_404(client: TestClient) -> None:
    assert client.get("/agentic/bundles/b-q2-foo/artifacts/logs/missing.txt").status_code == 404


def test_view_agentic_bundle_detail_404(client: TestClient) -> None:
    assert client.get("/agentic/bundles/does-not-exist").status_code == 404


def test_view_agentic_jobs_state_filter(client: TestClient) -> None:
    pending = client.get(
        "/agentic/jobs", params={"state": "pending"}
    )
    assert pending.status_code == 200
    assert "job-q2-foo" in pending.text
    assert "job-main-foo" not in pending.text

    dead = client.get("/agentic/jobs", params={"state": "dead"})
    assert dead.status_code == 200
    assert "job-q2-dead" in dead.text
    assert "job-q2-manual" not in dead.text

    escalated = client.get("/agentic/jobs", params={"state": "escalated"})
    assert escalated.status_code == 200
    assert "job-q2-manual" in escalated.text
    assert "job-q2-dead" not in escalated.text


def test_view_agentic_job_detail_shows_activity(client: TestClient) -> None:
    resp = client.get("/agentic/jobs/job-q2-foo")
    assert resp.status_code == 200
    body = resp.text
    assert "triage_start" in body
    assert "began triage" in body
    assert "action=auto_patch" in body
    assert "class=plist-error" in body
    assert "Retire reason" in body
    assert "1/3 failures in last 2h" in body
    assert "Lifecycle transitions" in body
    assert "hook_enqueued" in body
    assert "claimed" in body


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
    assert "action=auto_patch" in body


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
