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
    # 2.5d: include llm_turn events so the session viewer can join
    # per-turn cumulative token counts onto the assistant cards.
    trace_path.write_text(
        json.dumps({"type": "attempt_start", "attempt": 1, "tokens_used_so_far": 0, "budget": 1000}) + "\n"
        + json.dumps({"type": "llm_turn", "attempt": 1, "turn": 1,
                       "prompt_tokens": 800, "completion_tokens": 50,
                       "total_tokens": 850, "cumulative_total_tokens": 850,
                       "tools_requested": ["materialize_dports"]}) + "\n"
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
    # Phase 2: session dump fixture. Hand-build a tiny but
    # structurally-faithful JSONL transcript:
    #   - system prompt
    #   - initial user prompt with two ## sections, one of which is
    #     intentionally >10KB so the "bloat" highlighting fires
    #   - one assistant turn with reasoning_content + a tool_call
    #     containing literal HTML to verify XSS escape
    #   - one tool result with materialize_dports-shaped content so
    #     the headline summarizer fires
    import gzip as _gzip  # noqa: PLC0415
    session_path = tmp_path / "20260601-foo-patch.job.attempt1.jsonl.gz"
    user_prompt = (
        "## Automation Context\n- one\n\n"
        "## Build Errors\n" + ("error line\n" * 1500) + "\n"
        "## Port Files\nsmall\n"
    )
    rec_system = {"role": "system", "content": "you are a patch agent\n"}
    rec_user = {"role": "user", "content": user_prompt}
    rec_assistant = {
        "role": "assistant",
        "content": "",
        "reasoning_content": "thinking <script>alert(1)</script>",
        "tool_calls": [{
            "id": "call_abc",
            "function": {
                "name": "materialize_dports",
                "arguments": '{"origin": "devel/foo"}',
            },
        }],
    }
    rec_tool = {
        "role": "tool",
        "tool_call_id": "call_abc",
        "content": json.dumps({
            "ok": True,
            "stdout_tail": (
                "Compose succeeded\n"
                "[ok] apply_semantic_ops: changed=1 skipped=0\n"
                "summary: ports=1 ops=1 applied=1 fallback=0 errors=0\n"
            ),
        }),
    }
    with _gzip.open(session_path, "wt", encoding="utf-8") as fh:
        for rec in (rec_system, rec_user, rec_assistant, rec_tool):
            fh.write(json.dumps(rec) + "\n")
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
            ("b-q2-foo", "analysis/sessions/" + session_path.name,
             str(session_path), "gzip",
             session_path.stat().st_size, now),
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
    # Step 9 lifetime-token-cost fixture: two llm_turn rows on
    # job-q2-foo so the bundle detail page for b-q2-foo can show
    # accumulated cost (job-q2-foo is queued/devel/foo @2026Q2).
    conn.executemany(
        """INSERT INTO activity_log
           (ts, job_id, stage, message, duration_ms, extra_json)
           VALUES (?, ?, 'llm_turn', ?, ?, ?)""",
        [
            (now, "job-q2-foo", "turn 1", 0, json.dumps({
                "attempt": 1, "turn": 1, "prompt_tokens": 1200,
                "completion_tokens": 400, "total_tokens": 1600,
            })),
            (now, "job-q2-foo", "turn 2", 0, json.dumps({
                "attempt": 1, "turn": 2, "prompt_tokens": 2300,
                "completion_tokens": 700, "total_tokens": 3000,
            })),
        ],
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
    # Step 9 — pending-manual count surfaces with a link out to the
    # queue (fixture has one open user_context_requests row).
    assert "Pending manual" in body
    assert "/agentic/manual" in body


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


def test_view_agentic_index_shows_convert_card(client: TestClient) -> None:
    """Step 20f: dashboard surfaces convert-job progress alongside the
    existing pending-manual card. Fixture has no convert jobs so the
    card reads '0 / 0 / 0' but must render."""
    resp = client.get("/agentic")
    assert resp.status_code == 200
    body = resp.text
    assert "Convert open / done / dead" in body


def test_view_agentic_bundle_detail_shows_dops_state(
    client: TestClient, seeded_state_db: Path,
) -> None:
    """Step 20f + Step 11c layer-violation cleanup: bundle detail
    page reads dops_state from the bundle row (the runner persists
    it at triage time via worker.assess_dops, so the tracker no
    longer reaches into the host filesystem at render time)."""
    import sqlite3
    conn = sqlite3.connect(str(seeded_state_db))
    conn.execute(
        "UPDATE bundles SET dops_state = 'converted' WHERE bundle_id = 'b-q2-foo'",
    )
    conn.commit()
    conn.close()

    resp = client.get("/agentic/bundles/b-q2-foo")
    assert resp.status_code == 200
    body = resp.text
    assert ">dops<" in body
    assert "converted" in body


def test_view_agentic_bundle_detail_hides_dops_when_null(
    client: TestClient,
) -> None:
    """Legacy rows without persisted dops_state don't show the
    pill — the template short-circuits on NULL."""
    resp = client.get("/agentic/bundles/b-q2-foo")
    assert resp.status_code == 200
    # Without an UPDATE the dops_state stays NULL; pill suppressed.
    assert ">dops<" not in resp.text


def test_view_agentic_bundle_detail_shows_lifetime_token_cost(client: TestClient) -> None:
    """Step 9 — bundle page surfaces accumulated token cost across
    every job for this (origin, target). Fixture has two llm_turn
    rows on job-q2-foo (devel/foo @2026Q2): totals 3500 prompt,
    1100 completion, 4600 total."""
    resp = client.get("/agentic/bundles/b-q2-foo")
    assert resp.status_code == 200
    body = resp.text
    assert "Lifetime token cost for this port" in body
    assert "3,500" in body
    assert "1,100" in body
    assert "4,600" in body


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


def test_markdown_renders_github_table():
    """GitHub-style ``| col | col |`` + ``|---|---|`` rows produce
    a ``<table>`` with ``<thead>`` + ``<tbody>``. Cell contents are
    HTML-escaped and inline-rendered (bold + code work in cells)."""
    from dportsv3.tracker.server import _render_markdown
    md = (
        "| Status | Tokens |\n"
        "|--------|-------:|\n"
        "| **ok** | `1234` |\n"
    )
    out = _render_markdown(md)
    assert "<table class=\"artifact-table\">" in out
    assert "<thead>" in out and "<tbody>" in out
    assert "<th>Status</th>" in out
    assert 'style="text-align:right;"' in out  # second column right-aligned
    assert "<strong>ok</strong>" in out
    assert "<code>1234</code>" in out


def test_markdown_table_xss_escape():
    """Cell contents containing HTML-special chars stay escaped."""
    from dportsv3.tracker.server import _render_markdown
    md = "| A | B |\n|---|---|\n| <script>x</script> | safe |\n"
    out = _render_markdown(md)
    assert "<script>x</script>" not in out
    assert "&lt;script&gt;" in out


def test_markdown_lonely_pipe_does_not_start_table():
    """A line beginning with `|` but with no separator on the next
    line stays in paragraph context — protects prose with literal
    pipes from being misclassified as a malformed table."""
    from dportsv3.tracker.server import _render_markdown
    out = _render_markdown(
        "Paragraph one.\n"
        "| not a table because no separator |\n"
        "Still paragraph.\n"
    )
    assert "<table" not in out


def test_render_diff_basic():
    """Unified diff is parsed into colored line rows with hunk
    headers and a top-of-file stat."""
    from dportsv3.tracker.server import _render_diff
    diff = (
        "--- a/foo\n"
        "+++ b/foo\n"
        "@@ -1,2 +1,2 @@\n"
        " kept\n"
        "-removed\n"
        "+added\n"
    )
    out = _render_diff(diff)
    assert '<div class="diff-view">' in out
    assert '<div class="diff-stat">1 file, ' in out
    assert "diff-stat-add\">+1</span>" in out
    assert "diff-stat-del\">-1</span>" in out
    assert "diff-add" in out
    assert "diff-del" in out
    assert "diff-hunk-header" in out


def test_render_diff_escapes_html():
    """Diff content is HTML-escaped so file contents like ``<script>``
    can't break out of the renderer."""
    from dportsv3.tracker.server import _render_diff
    out = _render_diff(
        "--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-<script>alert(1)</script>\n+ok\n"
    )
    assert "<script>alert(1)" not in out
    assert "&lt;script&gt;" in out


def test_render_diff_multi_file():
    """Two distinct files in one diff produce stat ``2 files`` and
    each ``---``/``+++`` pair opens a fresh ``diff-file`` block."""
    from dportsv3.tracker.server import _render_diff
    d = (
        "--- a/foo\n+++ b/foo\n@@ -1,1 +1,1 @@\n-a\n+b\n"
        "--- a/bar\n+++ b/bar\n@@ -1,1 +1,1 @@\n-c\n+d\n"
    )
    out = _render_diff(d)
    assert "2 files" in out
    assert out.count('<div class="diff-file">') == 2
    # Both add lines visible.
    assert out.count('class="diff-line diff-add"') == 2


def test_render_diff_empty_input():
    """Empty string in → renderer outputs a 0-file stat and no
    file/hunk blocks. Operator-friendly degraded path."""
    from dportsv3.tracker.server import _render_diff
    out = _render_diff("")
    assert "0 files" in out
    assert "diff-file" not in out  # neither file blocks nor headers
    assert "diff-hunk" not in out


def test_render_diff_hunk_only_no_headers():
    """Some artifacts carry just hunk content (no ``--- a/foo`` /
    ``+++ b/foo`` preamble). The renderer must still emit the hunk
    body — auto-opening a virtual file rather than dropping content."""
    from dportsv3.tracker.server import _render_diff
    out = _render_diff(
        "@@ -1,2 +1,2 @@\n kept\n-removed\n+added\n"
    )
    assert "1 file" in out
    assert "diff-hunk" in out
    assert "diff-add" in out and "diff-del" in out


def test_render_diff_no_newline_marker():
    """``\\ No newline at end of file`` lines from git's unified-diff
    output are valid meta rows — they shouldn't bump line counters
    or trip the line-number gutter."""
    from dportsv3.tracker.server import _render_diff
    out = _render_diff(
        "--- a/x\n+++ b/x\n@@ -1,1 +1,1 @@\n-old\n+new\n"
        "\\ No newline at end of file\n"
    )
    # Stat counts only the +/- lines, not the meta row.
    assert "+1" in out and "-1" in out
    # The marker is rendered as a diff-meta row.
    assert "No newline at end of file" in out
    assert 'class="diff-line diff-meta"' in out


def test_is_diff_path_recognizes_patch_convention():
    """FreeBSD ports' ``patch-*`` filename convention triggers the diff
    renderer regardless of trailing extension."""
    from dportsv3.tracker.server import _is_diff_path
    assert _is_diff_path("analysis/changes.diff")
    assert _is_diff_path("foo.rej")
    assert _is_diff_path("foo.patch")
    assert _is_diff_path("port/files/patch-Makefile.pre.in")
    assert _is_diff_path("port/dragonfly/patch-src_main.c")
    assert not _is_diff_path("analysis/triage.md")
    assert not _is_diff_path("port/Makefile.DragonFly")


def test_artifact_media_type_makefile_variants():
    """``Makefile.DragonFly`` / ``Makefile.am`` / ``pkg-plist.amd64``
    render inline as text/plain. Pre-fix these were octet-stream
    because their ``suffix`` is the variant tag, not ``.txt``."""
    from dportsv3.tracker.server import _artifact_media_type
    media, inline = _artifact_media_type("port/Makefile.DragonFly", None)
    assert media == "text/plain; charset=utf-8" and inline
    media, inline = _artifact_media_type("port/Makefile.am", None)
    assert media == "text/plain; charset=utf-8" and inline
    media, inline = _artifact_media_type("port/pkg-plist.amd64", None)
    assert media == "text/plain; charset=utf-8" and inline
    media, inline = _artifact_media_type("port/files/patch-foo.c", None)
    assert media == "text/plain; charset=utf-8" and inline
    # Unknown extension without fs_path → still octet-stream
    media, inline = _artifact_media_type("foo.weird", None)
    assert media == "application/octet-stream" and not inline


def test_artifact_media_type_content_sniff(tmp_path):
    """When name+extension don't match, fall back to a content sniff
    on the on-disk file."""
    from dportsv3.tracker.server import _artifact_media_type
    text = tmp_path / "looks-text"
    text.write_text("hello world\n")
    media, inline = _artifact_media_type("looks-text", None, fs_path=text)
    assert media == "text/plain; charset=utf-8" and inline

    binary = tmp_path / "looks-bin"
    binary.write_bytes(b"\x00\x01\x02\x03" * 50)
    media, inline = _artifact_media_type("looks-bin", None, fs_path=binary)
    assert media == "application/octet-stream" and not inline


def test_session_view_renders_structure(client: TestClient) -> None:
    """Session viewer route returns 200 and the rendered HTML contains
    the per-role pills, byte badges, and tool headline summary."""
    resp = client.get(
        "/agentic/bundles/b-q2-foo/sessions/20260601-foo-patch.job.attempt1.jsonl.gz"
    )
    assert resp.status_code == 200
    body = resp.text
    # Role pills present.
    assert "pill-system" in body
    assert "pill-user" in body
    assert "pill-assistant" in body
    assert "pill-tool" in body
    # Assistant turn 1 anchor.
    assert "turn 1" in body
    # Tool name carried back from tool_call_id.
    assert "materialize_dports" in body
    # Headline summarizer fired and included the compose summary line.
    assert "applied=1" in body


def test_session_view_user_prompt_section_breakdown(
    client: TestClient,
) -> None:
    """The per-section user-prompt breakdown table fires and flags the
    >10KB section as bloat."""
    resp = client.get(
        "/agentic/bundles/b-q2-foo/sessions/20260601-foo-patch.job.attempt1.jsonl.gz"
    )
    assert resp.status_code == 200
    body = resp.text
    assert "user-prompt-sections" in body
    # Section names rendered.
    assert "Build Errors" in body
    assert "Port Files" in body
    # The Build Errors section is >10KB so the bloat class fires.
    assert 'class="bloat"' in body


def test_session_view_escapes_reasoning_html(client: TestClient) -> None:
    """reasoning_content containing literal HTML (e.g. ``<script>``)
    must be escaped — no XSS via a malicious model output."""
    resp = client.get(
        "/agentic/bundles/b-q2-foo/sessions/20260601-foo-patch.job.attempt1.jsonl.gz"
    )
    assert resp.status_code == 200
    body = resp.text
    # Raw <script> tag must NOT appear inline.
    assert "<script>alert(1)" not in body
    # Escaped form must appear.
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body


def test_artifact_route_redirects_session_to_viewer(
    client: TestClient,
) -> None:
    """Hitting the generic /artifacts/...jsonl.gz path for a session
    redirects to the structured viewer route — keeps existing links
    working without surprising operators with an octet-stream download."""
    resp = client.get(
        "/agentic/bundles/b-q2-foo/artifacts/analysis/sessions/20260601-foo-patch.job.attempt1.jsonl.gz",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/sessions/20260601-foo-patch.job.attempt1.jsonl.gz" in (
        resp.headers.get("location", "")
    )


def test_session_view_404_on_missing(client: TestClient) -> None:
    resp = client.get(
        "/agentic/bundles/b-q2-foo/sessions/does-not-exist.jsonl.gz"
    )
    assert resp.status_code == 404


def test_session_view_attaches_cumulative_tokens(client: TestClient) -> None:
    """When ``analysis/tool_trace.jsonl`` carries ``llm_turn`` events
    matching the session's attempt, the viewer joins per-turn
    cumulative_total_tokens onto each assistant card so the TOC can
    surface budget-bleed turns. The fixture's llm_turn for turn 1
    carries cumulative=850; the assistant card and TOC entry should
    both display it."""
    resp = client.get(
        "/agentic/bundles/b-q2-foo/sessions/20260601-foo-patch.job.attempt1.jsonl.gz"
    )
    assert resp.status_code == 200
    body = resp.text
    # Greek sigma + thousands-separated value renders in BOTH the turn
    # card header and the TOC entry.
    assert "Σ 850 tok" in body
    assert "Σ850" in body  # compact form in TOC
    # Per-turn prompt-tokens badge.
    assert "p: 800" in body


def test_build_cumulative_token_map_filters_by_attempt() -> None:
    """The map joiner filters tool_trace events to a specific attempt
    number — a second-attempt llm_turn shouldn't bleed into the first
    attempt's session view (and vice versa)."""
    from dportsv3.tracker.server import _build_cumulative_token_map
    trace = [
        {"type": "llm_turn", "attempt": 1, "turn": 1,
         "prompt_tokens": 100, "completion_tokens": 10,
         "total_tokens": 110, "cumulative_total_tokens": 110},
        {"type": "llm_turn", "attempt": 2, "turn": 1,
         "prompt_tokens": 200, "completion_tokens": 20,
         "total_tokens": 220, "cumulative_total_tokens": 220},
        {"type": "tool_call", "attempt": 1, "turn": 1, "tool": "x"},
    ]
    m1 = _build_cumulative_token_map(trace, attempt=1)
    assert m1 == {1: {
        "prompt_tokens": 100, "completion_tokens": 10,
        "total_tokens": 110, "cumulative_total_tokens": 110,
    }}
    m2 = _build_cumulative_token_map(trace, attempt=2)
    assert m2[1]["cumulative_total_tokens"] == 220
    # No attempt → empty map (can't unambiguously filter).
    assert _build_cumulative_token_map(trace, attempt=None) == {}


def test_session_view_tool_call_anchors(client: TestClient) -> None:
    """Assistant ``tool_calls`` should render as ``<a href="#tool-<id>">``
    links and the matching tool-result card carries an ``<span
    id="tool-<id>">`` anchor marker. Operators can jump from a multi-
    call assistant turn to a specific result card."""
    resp = client.get(
        "/agentic/bundles/b-q2-foo/sessions/20260601-foo-patch.job.attempt1.jsonl.gz"
    )
    assert resp.status_code == 200
    body = resp.text
    # The fixture uses tool_call_id "call_abc". Both the assistant
    # link and the tool result's anchor span must reference it.
    assert 'href="#tool-call_abc"' in body
    assert 'id="tool-call_abc"' in body


def test_parse_session_records_blob_backend_path(tmp_path) -> None:
    """Regression: when the artifact is stored via the blob backend
    (content-addressed under blobstore/objects/sha256/aa/bb/<sha>),
    the resolved on-disk path has NO ``.gz`` extension even though
    the file IS gzip-compressed. _parse_session_records must accept
    an explicit ``gzipped`` hint from the caller (who has the relpath)
    rather than relying on path.suffix."""
    import gzip as _gzip
    from dportsv3.tracker.server import _parse_session_records

    # Write a real gzip file under a no-extension path (simulates blob storage).
    blob_path = tmp_path / "ab" / "cd" / "abcdef0123456789"
    blob_path.parent.mkdir(parents=True)
    rec = {"role": "system", "content": "hello"}
    with _gzip.open(blob_path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")

    # Pre-fix path.suffix is "" so the default-sniff path would try
    # UTF-8-decoding gzip bytes. gzipped=True must override.
    records = _parse_session_records(blob_path, gzipped=True)
    assert len(records) == 1 and records[0]["role"] == "system"


def test_render_diff_meta_lines_use_single_column_layout() -> None:
    """``diff --git`` / ``index ...`` / ``\\ No newline at end of file``
    rows must not inherit the line-number gutter columns — they should
    sit flush at the left margin. The .diff-line.diff-meta override
    lives in the shared progress.css (2.5a moved the rules out of the
    per-template <style> blocks); this test pins the relevant rules
    so a future cleanup of progress.css doesn't silently regress the
    visual alignment."""
    from pathlib import Path
    css = (
        Path(__file__).parent.parent
        / "dportsv3" / "tracker" / "static" / "progress.css"
    ).read_text()
    assert ".diff-line.diff-meta" in css
    assert ".diff-line.diff-meta .ln-old" in css
    assert "display: none" in css or "display:none" in css


def test_is_session_relpath_matches_pattern() -> None:
    from dportsv3.tracker.server import _is_session_relpath
    assert _is_session_relpath("analysis/sessions/foo.jsonl.gz")
    assert _is_session_relpath("analysis/sessions/foo.jsonl")
    assert not _is_session_relpath("analysis/changes.diff")
    assert not _is_session_relpath("logs/foo.jsonl.gz")
    # No deeper subdirectories — we match only one path component.
    assert not _is_session_relpath("analysis/sessions/sub/foo.jsonl.gz")


def test_split_user_prompt_sections_handles_preamble() -> None:
    """Content before the first ## heading is captured under '(preamble)'.
    Headings that aren't at column 0 are not split (so a nested ## inside
    a code block doesn't accidentally start a new section)."""
    from dportsv3.tracker.server import _split_user_prompt_sections
    md = (
        "preamble line\nanother\n"
        "## Section A\nbody a\n"
        "## Section B\nbody b\n"
    )
    secs = _split_user_prompt_sections(md)
    assert secs[0]["name"] == "(preamble)"
    assert secs[1]["name"] == "Section A"
    assert secs[2]["name"] == "Section B"
    assert secs[1]["bytes"] > 0
    assert secs[2]["bytes"] > 0


def test_summarize_tool_result_materialize_headline() -> None:
    """materialize_dports tool result's `summary:` line is hoisted to
    the headline so the operator sees applied=N at a glance. Dispatch
    is keyed on ``tool_name`` after the 2.5e refactor — the same raw
    payload routed under a different tool_name would NOT pick up the
    materialize summarizer."""
    from dportsv3.tracker.server import _summarize_tool_result
    raw = json.dumps({
        "ok": True,
        "stdout_tail": (
            "Compose succeeded\n"
            "[ok] apply_semantic_ops: changed=2 skipped=0\n"
            "summary: ports=1 ops=2 applied=2 fallback=0 errors=0\n"
            "top_warning_codes: I_COMPOSE_MODE_DOPS_SUPPRESSES_COMPAT=1\n"
        ),
    })
    s = _summarize_tool_result(raw, tool_name="materialize_dports")
    assert s["ok"] is True
    assert "applied=2" in s["headline"]
    # The warning line is captured separately for highlighting.
    assert "I_COMPOSE_MODE_DOPS_SUPPRESSES_COMPAT" in s["warnings_line"]


def test_summarize_tool_result_unknown_tool_degrades_gracefully() -> None:
    """Without a tool_name match the summary keeps ok+error but skips
    the headline. The raw content is still accessible in the card's
    collapsible — no information loss, just no at-a-glance headline."""
    from dportsv3.tracker.server import _summarize_tool_result
    raw = json.dumps({
        "ok": False, "error": "boom",
        "stdout_tail": "summary: ports=1 ops=1 applied=1 errors=0\n",
    })
    s = _summarize_tool_result(raw, tool_name="some_unknown_tool")
    assert s["ok"] is False
    assert s.get("error") == "boom"
    # No headline computed for unknown tools.
    assert s["headline"] == ""


def test_summarize_tool_result_apply_intent() -> None:
    """apply_intent shows intent_type + paths_changed count + diff
    size + mode. Diff size only fires when substrate_diff is non-empty."""
    from dportsv3.tracker.server import _summarize_tool_result
    raw = json.dumps({
        "ok": True, "intent_type": "drop_patch",
        "paths_changed": ["a", "b"],
        "substrate_diff": "x" * 1082,
        "mode": "dops",
    })
    s = _summarize_tool_result(raw, tool_name="apply_intent")
    assert s["headline"] == "drop_patch paths_changed=2 diff=1082B mode=dops"


def test_summarize_tool_result_intent_reference() -> None:
    """intent_reference shows intent_type + count of matched playbooks."""
    from dportsv3.tracker.server import _summarize_tool_result
    raw = json.dumps({
        "ok": True, "intent_type": "replace_in_dops_block",
        "schema": {"...": "..."},
        "playbooks": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
    })
    s = _summarize_tool_result(raw, tool_name="intent_reference")
    assert s["headline"] == "replace_in_dops_block playbooks=3"


def test_summarize_tool_result_extract_does_not_overwrite_materialize() -> None:
    """Regression for the 2.5e order-sensitivity footgun. Pre-refactor
    the heuristic chain would, for a result carrying both stdout_tail
    summary AND a wrksrc field, overwrite the materialize headline
    with ``wrksrc=...``. With per-tool dispatch this can't happen
    because we route on the tool name, not on key shape — verify by
    feeding a payload that has BOTH fields under tool_name=extract:
    extract's summarizer takes wrksrc; the materialize summary line
    in stdout_tail is correctly ignored."""
    from dportsv3.tracker.server import _summarize_tool_result
    raw = json.dumps({
        "ok": True,
        "wrksrc": "/work/obj/devel/foo/foo-1.0",
        "stdout_tail": "summary: ports=1 ops=1 applied=1 errors=0\n",
    })
    # Under extract: only wrksrc is shown — no leak from stdout_tail.
    se = _summarize_tool_result(raw, tool_name="extract")
    assert "wrksrc=/work/obj/devel/foo/foo-1.0" == se["headline"]
    # Under materialize: stdout_tail summary is shown — no leak from wrksrc.
    sm = _summarize_tool_result(raw, tool_name="materialize_dports")
    assert "applied=1" in sm["headline"]
    assert "wrksrc=" not in sm["headline"]


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
    # Step 10b: the banner exposes an inline Abandon affordance so
    # the operator can clear the blocker without sqlite spelunking.
    assert "abandon-blocker-btn" in body
    assert "Abandon job " in body


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


# --------------------------------------------------------------------
# Active env UI control
# --------------------------------------------------------------------


def test_view_agentic_index_renders_active_env_banner_unset(client: TestClient) -> None:
    resp = client.get("/agentic")
    assert resp.status_code == 200
    body = resp.text
    # Banner shows the "none" message when unset.
    assert "Active env" in body
    assert "none" in body
    # Per-row "set" button is present for the seeded env.
    assert 'data-env="test-env"' in body


def test_view_agentic_index_renders_active_env_when_set(client: TestClient) -> None:
    # PUT to set the active env, then re-render.
    client.put("/api/config/active-env", json={"name": "test-env"})
    resp = client.get("/agentic")
    body = resp.text
    assert "<strong>test-env</strong>" in body
    # Active row gets the active pill (instead of a set button).
    assert "env-row-active" in body
    assert ">active</span>" in body
