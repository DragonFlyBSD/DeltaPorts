"""Attempt-grouping for the job-detail timeline (Phase 6 redesign)."""

from __future__ import annotations

from dportsv3.tracker.render import group_activity_by_attempt


def _row(id, stage, extra=None):
    return {"id": id, "stage": stage, "extra": extra, "message": stage, "ts": "t"}


def test_groups_attempts_with_outcomes_tokens_tools():
    activity = [
        _row(1, "triage_start"),
        _row(2, "decision", {"action": "patch"}),
        _row(3, "attempt_start", {"attempt": 1}),
        _row(4, "llm_turn", {"total_tokens": 100}),
        _row(5, "tool:get_file", {"ok": True}),
        _row(6, "attempt_end", {"rebuild_ok": False}),
        _row(7, "attempt_start", {"attempt": 2}),
        _row(8, "llm_turn", {"total_tokens": 200}),
        _row(9, "attempt_end", {"rebuild_ok": True}),
    ]
    groups = group_activity_by_attempt(activity)
    assert [g["label"] for g in groups] == ["Triage / setup", "Attempt 1", "Attempt 2"]
    assert groups[0]["kind"] == "setup"

    a1, a2 = groups[1], groups[2]
    assert (a1["outcome"], a1["outcome_cls"]) == ("rebuild failed", "failed")
    assert a1["tokens"] == 100 and a1["n_tools"] == 1
    assert (a2["outcome"], a2["outcome_cls"]) == ("rebuild passed", "built")
    assert a2["tokens"] == 200

    # The last (most recent) group opens by default; earlier ones fold.
    assert groups[-1]["open"] is True
    assert groups[0]["open"] is False and groups[1]["open"] is False


def test_groups_are_chronological_regardless_of_input_order():
    activity = [
        _row(3, "attempt_start", {"attempt": 1}),
        _row(1, "triage_start"),
        _row(2, "decision"),
    ]
    groups = group_activity_by_attempt(activity)
    assert [g["kind"] for g in groups] == ["setup", "attempt"]
    assert [r["id"] for r in groups[0]["rows"]] == [1, 2]


def test_attempt_without_end_has_no_outcome():
    groups = group_activity_by_attempt([
        _row(1, "attempt_start", {"attempt": 1}),
        _row(2, "llm_turn", {"total_tokens": 50}),
    ])
    assert groups[0]["outcome"] is None
    assert groups[0]["tokens"] == 50


def test_empty_activity_returns_no_groups():
    assert group_activity_by_attempt([]) == []


def test_terminal_job_detail_renders_grouped_timeline(tmp_path):
    """A done job's detail shows the attempt-grouped timeline, not the flat
    live table."""
    import json
    import sqlite3
    from datetime import datetime, timezone

    import pytest
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from dportsv3.db.schema import init_db
    from dportsv3.tracker.server import create_app

    db = tmp_path / "state.db"
    c = sqlite3.connect(str(db)); c.row_factory = sqlite3.Row; init_db(c)
    now = datetime.now(timezone.utc).isoformat()
    c.execute(
        "INSERT INTO jobs (job_id,state,type,origin,flavor,bundle_dir,"
        "created_ts_utc,path,last_seen_at) VALUES "
        "('j-done','done','patch','devel/foo','','',?,'',?)", (now, now))
    rows = [
        ("attempt_start", {"attempt": 1}),
        ("llm_turn", {"total_tokens": 100, "tools_requested": ["grep"]}),
        ("attempt_end", {"rebuild_ok": False}),
        ("attempt_start", {"attempt": 2}),
        ("attempt_end", {"rebuild_ok": True}),
    ]
    for stage, extra in rows:
        c.execute("INSERT INTO activity_log (ts,job_id,stage,message,extra_json) "
                  "VALUES (?,?,?,?,?)", (now, "j-done", stage, stage, json.dumps(extra)))
    c.commit(); c.close()

    with TestClient(create_app(db)) as client:
        body = client.get("/agentic/jobs/j-done").text
    assert "attempt-group" in body            # grouped review view
    assert 'id="activity-table"' not in body  # flat/live table suppressed
    assert "Attempt 1" in body and "Attempt 2" in body
    assert "rebuild failed" in body and "rebuild passed" in body
