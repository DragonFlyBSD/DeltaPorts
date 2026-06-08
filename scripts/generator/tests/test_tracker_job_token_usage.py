"""Step 9a/9c tests: token-usage aggregator, since_id cursor, columns.

What we pin here:

- ``token_usage_for_job`` sums llm_turn extra_json into a typed dict
  and identifies the largest-prompt turn.
- ``activity_for_job(since_id=N)`` returns only rows with id > N
  in oldest-first order (polling shape for live refresh).
- The ``/api/activity?job_id=X&since_id=N`` endpoint dispatches to
  ``activity_for_job`` rather than the global feed.
- The job detail page renders structured token columns for llm_turn
  rows (not crammed into prose) and the summary card.
- The live indicator only appears for non-terminal jobs.
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
from dportsv3.tracker.agentic_queries import (
    activity_for_job,
    token_usage_for_job,
)
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def seeded(tmp_path):
    """state.db with one active job + a sequence of activity rows."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_state_db(conn)

    now = _now()
    # Two jobs so we can verify the per-job filter.
    conn.executemany(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target)
           VALUES (?, ?, 'patch', 'devel/foo', '', '', ?, '', ?, '@2026Q2')""",
        [
            ("job-active", "patching", now, now),
            ("job-other", "patching", now, now),
        ],
    )
    # Job-active gets a mix of llm_turn + tool_call + attempt rows.
    activities = [
        ("job-active", "attempt_start",
         "attempt 1/4", None, None),
        ("job-active", "llm_turn",
         "A1.T1 in=1000 out=50 total=1050 cumulative=1050 → env_verify",
         None,
         {"attempt": 1, "turn": 1, "prompt_tokens": 1000,
          "completion_tokens": 50, "total_tokens": 1050,
          "cumulative_total_tokens": 1050,
          "tools_requested": ["env_verify"]}),
        ("job-active", "tool:env_verify",
         "status=ready ok", 10, None),
        ("job-active", "llm_turn",
         "A1.T2 in=5000 out=300 total=5300 cumulative=6350 → get_file,grep",
         None,
         {"attempt": 1, "turn": 2, "prompt_tokens": 5000,
          "completion_tokens": 300, "total_tokens": 5300,
          "cumulative_total_tokens": 6350,
          "tools_requested": ["get_file", "grep"]}),
        ("job-active", "tool:get_file",
         "/work/foo ok", 25, None),
        # A larger turn so we can verify largest_turn detection.
        ("job-active", "llm_turn",
         "A1.T3 in=80000 out=1000 total=81000 cumulative=87350 → dupe",
         None,
         {"attempt": 1, "turn": 3, "prompt_tokens": 80000,
          "completion_tokens": 1000, "total_tokens": 81000,
          "cumulative_total_tokens": 87350,
          "tools_requested": ["dupe"]}),
    ]
    # The other job shouldn't bleed in.
    activities.append(("job-other", "llm_turn",
                       "A1.T1", None,
                       {"prompt_tokens": 999, "completion_tokens": 1,
                        "total_tokens": 1000,
                        "cumulative_total_tokens": 1000}))
    for i, (job_id, stage, msg, dur, extra) in enumerate(activities):
        conn.execute(
            """INSERT INTO activity_log
               (ts, job_id, stage, message, duration_ms, extra_json)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_now(), job_id, stage, msg, dur,
             json.dumps(extra) if extra else None),
        )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client(seeded):
    app = create_app(seeded)
    with TestClient(app) as c:
        yield c


# --- token_usage_for_job aggregator -----------------------------------------


def test_token_usage_sums_only_llm_turn_rows(seeded):
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    out = token_usage_for_job(conn, "job-active")
    assert out["has_data"] is True
    # 1000 + 5000 + 80000 = 86000 (the tool/attempt rows don't count).
    assert out["prompt_tokens"] == 86_000
    assert out["completion_tokens"] == 1350
    assert out["total_tokens"] == 87_350
    assert out["llm_turns"] == 3


def test_token_usage_identifies_largest_turn(seeded):
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    out = token_usage_for_job(conn, "job-active")
    largest = out["largest_turn"]
    assert largest is not None
    assert largest["turn"] == 3
    assert largest["prompt_tokens"] == 80_000
    assert largest["tools_requested"] == ["dupe"]


def test_token_usage_no_data_when_no_llm_turns(seeded):
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    # Insert a job with only tool rows; aggregator should return has_data=False.
    conn.execute(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target)
           VALUES ('job-no-llm', 'done', 'patch', 'devel/x', '', '',
                   ?, '', ?, '@x')""",
        (_now(), _now()),
    )
    conn.execute(
        """INSERT INTO activity_log
           (ts, job_id, stage, message)
           VALUES (?, 'job-no-llm', 'tool:foo', 'no llm here')""",
        (_now(),),
    )
    conn.commit()
    out = token_usage_for_job(conn, "job-no-llm")
    assert out["has_data"] is False
    assert out["prompt_tokens"] == 0
    assert out["llm_turns"] == 0


def test_token_usage_does_not_leak_other_jobs(seeded):
    """job-other has its own llm_turn row. It must not appear in
    job-active's totals."""
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    out = token_usage_for_job(conn, "job-active")
    # If leakage happened we'd see 86,999 prompt instead of 86,000.
    assert out["prompt_tokens"] == 86_000


def test_token_usage_tolerates_invalid_extra_json(seeded):
    """A malformed extra_json row mustn't break the aggregator."""
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO activity_log
           (ts, job_id, stage, message, extra_json)
           VALUES (?, 'job-active', 'llm_turn', 'broken', '{not json')""",
        (_now(),),
    )
    conn.commit()
    out = token_usage_for_job(conn, "job-active")
    # Still has the three valid rows; the broken one is silently skipped.
    assert out["llm_turns"] == 4   # 3 valid + 1 broken (counted)
    assert out["prompt_tokens"] == 86_000


def test_token_usage_pre_h4_rows_degrade_billable_to_total(seeded):
    """The seeded rows predate H4 (no cached_tokens). cached must be 0
    and billable must fall back to total — never under-report cost on
    old jobs."""
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    out = token_usage_for_job(conn, "job-active")
    assert out["cached_tokens"] == 0
    assert out["billable_tokens"] == out["total_tokens"] == 87_350


def test_token_usage_aggregates_cached_and_billable(seeded):
    """H4 rows carry cached_tokens; the card sums cached and derives
    billable = (prompt - cached) + completion (the real cost)."""
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    conn.execute(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target)
           VALUES ('job-h4', 'patching', 'patch', 'databases/redis74', '', '',
                   ?, '', ?, '@2026Q2')""",
        (_now(), _now()),
    )
    # Two cache-dominated turns (the redis74 shape): huge prompt, almost
    # all cached, tiny real cost.
    for turn, (p, c, cached) in enumerate(
        [(92915, 107, 92672), (90000, 200, 89500)], start=1
    ):
        conn.execute(
            """INSERT INTO activity_log
               (ts, job_id, stage, message, extra_json)
               VALUES (?, 'job-h4', 'llm_turn', 'x', ?)""",
            (_now(), json.dumps({
                "attempt": 1, "turn": turn,
                "prompt_tokens": p, "completion_tokens": c,
                "total_tokens": p + c, "cached_tokens": cached,
            })),
        )
    conn.commit()
    out = token_usage_for_job(conn, "job-h4")
    assert out["prompt_tokens"] == 182_915
    assert out["completion_tokens"] == 307
    assert out["total_tokens"] == 183_222
    assert out["cached_tokens"] == 182_172
    # (92915-92672) + (90000-89500) + 307 = 243 + 500 + 307 = 1050.
    assert out["billable_tokens"] == 1_050


# --- activity_for_job since_id cursor ---------------------------------------


def test_activity_for_job_default_is_newest_first(seeded):
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    rows = activity_for_job(conn, "job-active")
    # Newest first, only this job's rows.
    assert len(rows) == 6
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids, reverse=True)


def test_activity_for_job_since_id_returns_oldest_first(seeded):
    """When since_id > 0, the polling shape returns only new rows
    in oldest-first order so the client can prepend each in order."""
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    # Pretend we already rendered the first 4 rows; ask for what's new.
    cursor = sorted(
        [r["id"] for r in activity_for_job(conn, "job-active")]
    )[3]   # id of the 4th row
    rows = activity_for_job(conn, "job-active", since_id=cursor)
    # Only rows with id > cursor.
    assert all(r["id"] > cursor for r in rows)
    # Oldest first.
    ids = [r["id"] for r in rows]
    assert ids == sorted(ids)


def test_activity_for_job_since_id_no_new_rows(seeded):
    conn = sqlite3.connect(str(seeded))
    conn.row_factory = sqlite3.Row
    # Cursor past the latest id → empty.
    rows = activity_for_job(conn, "job-active", since_id=999_999)
    assert rows == []


# --- API endpoint dispatch --------------------------------------------------


def test_api_activity_with_job_id_uses_activity_for_job(client):
    """Without job_id we get the global feed; with it, only this job."""
    body = client.get("/api/activity?job_id=job-active&limit=50").json()
    # All rows have job_id=job-active (no job-other bleed).
    assert all(r["job_id"] == "job-active" for r in body)


def test_api_activity_with_since_id(client):
    """Polling pattern: fetch all, then re-fetch with since_id of the
    highest id; second call returns []."""
    first = client.get("/api/activity?job_id=job-active&limit=50").json()
    assert first
    max_id = max(r["id"] for r in first)
    second = client.get(
        f"/api/activity?job_id=job-active&since_id={max_id}&limit=50"
    ).json()
    assert second == []


def test_api_activity_without_job_id_is_global(client):
    """Backward compat: no job_id → existing recent-activity behavior."""
    body = client.get("/api/activity?limit=20").json()
    # Rows from both jobs may appear.
    job_ids = {r["job_id"] for r in body}
    assert "job-active" in job_ids


# --- template rendering -----------------------------------------------------


def test_job_detail_renders_token_card(client):
    """The card displays sums + percentages."""
    body = client.get("/agentic/jobs/job-active").text
    assert "Token usage" in body
    # Prompt total formatted with thousands separator.
    assert "86,000" in body
    # Largest-turn line names the tool that preceded the explosion.
    assert "dupe" in body


def test_job_detail_renders_structured_columns(client):
    """llm_turn rows have structured token cells, not just prose."""
    body = client.get("/agentic/jobs/job-active").text
    # Headers.
    assert "Prompt" in body
    assert "Compl" in body
    assert "Cum" in body
    # Numbers landed as table cells.
    assert "80,000" in body          # largest turn's prompt
    assert "87,350" in body          # cumulative at the largest turn
    # The "→ tool" affordance replaced the crammed prose.
    assert "→ env_verify" in body or "→ dupe" in body


def test_job_detail_live_indicator_active_for_non_terminal_state(client):
    body = client.get("/agentic/jobs/job-active").text
    assert "live-indicator" in body
    assert 'class="live-indicator active"' in body
    # The pause toggle only renders for active jobs.
    assert "pause-toggle" in body


def test_job_detail_live_indicator_idle_for_terminal_state(seeded):
    """A job in done/dead/escalated must show idle and not include
    the polling JS that would spam the API."""
    # Mutate the job to terminal.
    conn = sqlite3.connect(str(seeded))
    conn.execute(
        "UPDATE jobs SET state='done', retire_reason=NULL "
        "WHERE job_id='job-active'"
    )
    conn.commit()
    conn.close()
    app = create_app(seeded)
    with TestClient(app) as client:
        body = client.get("/agentic/jobs/job-active").text
    assert "live-indicator" in body
    assert 'class="live-indicator active"' not in body
    # No pause toggle on terminal jobs.
    assert "pause-toggle" not in body
