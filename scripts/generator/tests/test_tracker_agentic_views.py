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
    handoff_path = tmp_path / "manual_handoff.md"
    handoff_path.write_text(
        "## What we tried\n\n- **Origin:** `devel/manual`\n"
        "- **Reason:** retry cap reached\n",
        encoding="utf-8",
    )
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
            ("b-q2-manual", "run-q2-001", "devel/manual", "", now, "fail", "@2026Q2", now),
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
            ("b-q2-manual", "analysis/manual_handoff.md", str(handoff_path), "text", handoff_path.stat().st_size, now),
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
    # Step 9 — a manual request for devel/foo with a sibling queued
    # triage job that should surface as the active-job blocker.
    conn.execute(
        """INSERT INTO user_context_requests
           (run_id, origin, bundle_id, classification, confidence,
            iteration, max_iterations, requested_at, status)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("run-q2-001", "devel/foo", "b-q2-foo", "patch-error", "medium",
         1, 3, now, "pending"),
    )
    conn.execute(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        ("job-q2-foo-blocker", "queued", "triage", "devel/foo", "", "",
         now, "/tmp/job-q2-foo-blocker.job", now, "@2026Q2"),
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
    assert ">5<" in body or "5</div>" in body  # bundles count


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


def test_view_agentic_bundle_detail_renders_artifact_rail(client: TestClient) -> None:
    """Step 9 — operator-canonical files surface as a quick-links rail
    above the full artifact table. Only artifacts that actually exist
    on the bundle become pills.

    The fixture's b-q2-foo has meta.txt, logs/errors.txt,
    analysis/triage.md, analysis/patch_audit.json, logs/full.log.gz,
    analysis/tool_trace.jsonl. It does NOT have proposed_fix.md or
    manual_handoff.md, so those pills must be absent."""
    resp = client.get("/agentic/bundles/b-q2-foo")
    assert resp.status_code == 200
    body = resp.text
    assert "Quick links" in body
    assert 'class="artifact-rail"' in body
    # Present-artifact pills surface.
    for label in ("Triage", "Patch audit", "Tool trace",
                  "Errors log", "Full log (.gz)", "meta.txt"):
        assert label in body, f"missing rail pill for {label!r}"
    # Absent artifacts must NOT have pills (b-q2-foo doesn't have these).
    assert "Proposed fix" not in body
    assert "Manual handoff" not in body
    # Each pill links into the bundle detail with ?artifact=…
    assert "?artifact=analysis/triage.md" in body
    assert "?artifact=logs/errors.txt" in body


def test_view_agentic_bundle_detail_shows_prior_attempts(client: TestClient) -> None:
    """Step 9 — prior-attempts table lists other bundles for the same
    (origin, target) and excludes the bundle being viewed.

    Fixture has two ``devel/foo @2026Q2`` bundles (b-q2-foo and
    b-q2-foo-retry) plus an unrelated ``devel/foo @main`` bundle. From
    b-q2-foo's page, the table must include b-q2-foo-retry, exclude
    b-q2-foo itself, and exclude the @main variant."""
    resp = client.get("/agentic/bundles/b-q2-foo")
    assert resp.status_code == 200
    body = resp.text
    assert "Prior attempts for this origin" in body
    # The retry bundle for the same target is listed…
    assert "b-q2-foo-retry" in body
    # …the @main variant (different target) is not.
    assert "b-main-foo" not in body
    # The current bundle appears in the page title etc., but not as a
    # row inside the prior-attempts table — assert the table block
    # itself doesn't contain a row link to the current bundle.
    prior_section = body.split("Prior attempts for this origin", 1)[1]
    prior_section = prior_section.split("</table>", 1)[0]
    assert "b-q2-foo-retry" in prior_section
    assert "/agentic/bundles/b-q2-foo<" not in prior_section
    assert ">b-q2-foo</a>" not in prior_section


def test_view_agentic_job_detail_shows_prior_attempts(client: TestClient) -> None:
    """Step 9 — same prior-attempts table on the job detail page,
    keyed off the job's (origin, target). job-q2-foo is devel/foo
    @2026Q2 → both b-q2-foo and b-q2-foo-retry are valid prior
    attempts."""
    resp = client.get("/agentic/jobs/job-q2-foo")
    assert resp.status_code == 200
    body = resp.text
    assert "Prior attempts for this origin" in body
    assert "b-q2-foo-retry" in body
    assert "b-q2-foo" in body
    assert "b-main-foo" not in body


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


def test_markdown_inline_bold_and_code_rendered():
    """Step 6 follow-up: ``**bold**`` and ``inline `code` `` show as
    <strong> / <code> rather than literal asterisks and backticks.
    Surfaced by the manual_handoff.md viewer where every bullet uses
    both styles."""
    from dportsv3.tracker.server import _render_markdown
    md = (
        "- **Origin:** `devel/readline`\n"
        "- **Reason:** retry cap reached\n"
        "\n"
        "Plain paragraph with `inline` and **strong** bits.\n"
    )
    out = _render_markdown(md)
    assert "<strong>Origin:</strong>" in out
    assert "<code>devel/readline</code>" in out
    assert "<strong>Reason:</strong>" in out
    assert "<code>inline</code>" in out
    assert "<strong>strong</strong>" in out
    # Literal asterisks must not leak.
    assert "**Origin" not in out


def test_markdown_inline_does_not_break_html_escape():
    """Bold/code containing HTML-special chars stay escaped — no XSS
    via, e.g., ``**<script>**`` markdown."""
    from dportsv3.tracker.server import _render_markdown
    out = _render_markdown("- **<script>** and `<img src=x>`\n")
    assert "<script>" not in out  # raw tag would be a vuln
    assert "<strong>&lt;script&gt;</strong>" in out
    assert "<code>&lt;img src=x&gt;</code>" in out


def test_markdown_inline_code_blocks_backticks_from_being_bolded():
    """`**not bold**` inside backticks stays literal — the order of
    application protects it."""
    from dportsv3.tracker.server import _render_markdown
    out = _render_markdown("Try `**literal**` here.\n")
    assert "<code>**literal**</code>" in out
    assert "<strong>literal</strong>" not in out


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


def test_view_agentic_job_detail_renders_handoff_when_escalated(client: TestClient) -> None:
    """Step 9 — escalated jobs inline the manual handoff so the
    operator doesn't need to bounce out to /agentic/manual. The
    fixture's job-q2-manual is escalated and its sibling bundle
    b-q2-manual has analysis/manual_handoff.md."""
    resp = client.get("/agentic/jobs/job-q2-manual")
    assert resp.status_code == 200
    body = resp.text
    assert "Manual handoff" in body
    assert "retry cap reached" in body
    # Link out to the manual-queue page for follow-up.
    assert "Open in manual queue" in body
    assert "/agentic/manual/run-q2-001/devel/manual" in body


def test_view_agentic_job_detail_no_handoff_when_not_escalated(client: TestClient) -> None:
    """The handoff panel is gated on state=escalated; a queued job
    should never show it even if a sibling bundle has the artifact."""
    resp = client.get("/agentic/jobs/job-q2-foo")
    assert resp.status_code == 200
    assert "Manual handoff" not in resp.text


def test_view_agentic_manual_detail_shows_blocking_job(client: TestClient) -> None:
    """Step 9 — when an open triage/patch job exists for the same
    (origin, target), surface a banner explaining that submitting
    fresh context will queue behind it. Fixture has job-q2-foo-blocker
    in 'queued' state for devel/foo @2026Q2; the manual detail for
    that pair must reference it."""
    resp = client.get("/agentic/manual/run-q2-001/devel/foo")
    assert resp.status_code == 200
    body = resp.text
    assert "job is already in flight" in body
    # Either of the two queued devel/foo @2026Q2 jobs in the fixture
    # is a legitimate "blocker"; the runner-facing point is that
    # *some* job is in flight, so just assert one is linked.
    assert ("job-q2-foo-blocker" in body) or ("job-q2-foo" in body)
    assert "queued" in body


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
