"""WS8 — the runner's muted-issue short-circuit.

Muting an issue must stop auto-work on its occurrences, not just hide
them: `_maybe_skip_muted_issue` retires a job DEAD (retire_reason
'issue_muted') before triage/patch spends any agent budget. Mirrors the
origin-lock skip, so the same properties are pinned: proceed when not
muted, short-circuit when muted, best-effort on missing data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dportsv3.agent import runner
from dportsv3.agent.lifecycle import JobEvent, apply as lifecycle_apply
from dportsv3.db.schema import init_db as init_state_db
from dportsv3.tracker.agentic_queries import issue_for_bundle


@pytest.fixture
def runner_db(tmp_path, monkeypatch):
    conn = sqlite3.connect(str(tmp_path / "state.db"), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_state_db(conn)
    monkeypatch.setattr(runner, "_state_db_conn", conn, raising=False)
    # Keep the guard focused on transition logic — silence the side logs.
    monkeypatch.setattr(runner, "activity_log", lambda *a, **k: None)
    monkeypatch.setattr(runner, "log", lambda *a, **k: None)
    yield conn
    conn.close()


def _seed_bundle_and_issue(conn, bundle_id, issue_key, *, muted, origin="ftp/curl",
                           target="@2026Q3"):
    now = "2026-07-25T00:00:00Z"
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
              result, target, issue_key, last_seen_at)
           VALUES (?, 'r', ?, '', ?, 'failure', ?, ?, ?)""",
        (bundle_id, origin, now, target, issue_key, now),
    )
    conn.execute(
        """INSERT INTO issues (issue_key, target, origin, fingerprint, state,
              times_seen, first_seen_at, last_seen_at, muted_by, updated_at)
           VALUES (?, ?, ?, 'fp', ?, 1, ?, ?, ?, ?)""",
        (issue_key, target, origin, "muted" if muted else "unresolved",
         now, now, "op" if muted else None, now),
    )
    conn.commit()


def _seed_job_at_triaging(conn, job_id, bundle_id, origin="ftp/curl"):
    lifecycle_apply(conn, job_id, JobEvent.HOOK_ENQUEUED, actor="hook",
                    detail={"bundle_id": bundle_id, "origin": origin,
                            "type": "triage", "target": "@2026Q3"})
    lifecycle_apply(conn, job_id, JobEvent.CLAIM, actor="runner")
    lifecycle_apply(conn, job_id, JobEvent.TRIAGE_START, actor="runner")


def _job(bundle_id, origin="ftp/curl"):
    return {"bundle_id": bundle_id, "origin": origin, "target": "@2026Q3",
            "type": "triage"}


def test_issue_for_bundle_joins(runner_db):
    _seed_bundle_and_issue(runner_db, "b1", "iss1", muted=False)
    assert issue_for_bundle(runner_db, "b1")["issue_key"] == "iss1"
    # a bundle with no issue_key → None
    runner_db.execute(
        "INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at) "
        "VALUES ('b0','r','a/a','','2026-07-25T00:00:00Z','failure','@2026Q3','x')"
    )
    runner_db.commit()
    assert issue_for_bundle(runner_db, "b0") is None


def test_unmuted_issue_proceeds(runner_db, tmp_path):
    _seed_bundle_and_issue(runner_db, "b1", "iss1", muted=False)
    _seed_job_at_triaging(runner_db, "j1", "b1")
    assert runner._maybe_skip_muted_issue(
        queue_root=tmp_path, job=_job("b1"), job_id="j1",
        sibling_paths=[], origin="ftp/curl",
    ) is None
    # job untouched (still triaging)
    assert runner_db.execute(
        "SELECT state FROM jobs WHERE job_id='j1'"
    ).fetchone()["state"] == "triaging"


def test_muted_issue_short_circuits_job_dead(runner_db, tmp_path):
    _seed_bundle_and_issue(runner_db, "b1", "iss1", muted=True)
    _seed_job_at_triaging(runner_db, "j1", "b1")
    result = runner._maybe_skip_muted_issue(
        queue_root=tmp_path, job=_job("b1"), job_id="j1",
        sibling_paths=[], origin="ftp/curl",
    )
    assert result == (True, "issue_muted:iss1")
    row = runner_db.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id='j1'"
    ).fetchone()
    assert row["state"] == "dead"
    assert row["retire_reason"] == "issue_muted"
    # the skip is recorded as a lifecycle event
    assert runner_db.execute(
        "SELECT COUNT(*) FROM job_events "
        "WHERE job_id='j1' AND event_name='skip_issue_muted'"
    ).fetchone()[0] == 1


def test_muted_skip_fans_out_to_siblings(runner_db, tmp_path):
    _seed_bundle_and_issue(runner_db, "b1", "iss1", muted=True)
    _seed_job_at_triaging(runner_db, "lead", "b1")
    _seed_job_at_triaging(runner_db, "sib", "b1")
    runner._maybe_skip_muted_issue(
        queue_root=tmp_path, job=_job("b1"), job_id="lead",
        sibling_paths=[Path("sib")], origin="ftp/curl",
    )
    states = {
        r["job_id"]: r["state"]
        for r in runner_db.execute("SELECT job_id, state FROM jobs")
    }
    assert states["lead"] == "dead" and states["sib"] == "dead"


def test_no_bundle_id_proceeds(runner_db, tmp_path):
    assert runner._maybe_skip_muted_issue(
        queue_root=tmp_path, job={"origin": "x"}, job_id="j", sibling_paths=[],
        origin="x",
    ) is None


def test_bundle_without_issue_proceeds(runner_db, tmp_path):
    runner_db.execute(
        "INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at) "
        "VALUES ('b0','r','a/a','','2026-07-25T00:00:00Z','failure','@2026Q3','x')"
    )
    runner_db.commit()
    _seed_job_at_triaging(runner_db, "j0", "b0", origin="a/a")
    assert runner._maybe_skip_muted_issue(
        queue_root=tmp_path, job=_job("b0", origin="a/a"), job_id="j0",
        sibling_paths=[], origin="a/a",
    ) is None
