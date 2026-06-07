"""M4: a triage that terminates via TRIAGE_FAIL must leave the bundle
operator-actionable — a manual_handoff.md explaining the failure plus
resolution=triage_failed (so can_retry/can_take_over light up). Before
the fix the job died at DEAD/triage_failed but the bundle sat at
resolution=NULL with no handoff.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.agent import triage as triage_mod
from dportsv3.agent.lifecycle import JobEvent, apply as lifecycle_apply
from dportsv3.db.schema import init_db


@pytest.fixture
def state_db(tmp_path: Path, monkeypatch):
    conn = sqlite3.connect(str(tmp_path / "state.db"), isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    monkeypatch.setattr(runner_mod, "_state_db_conn", conn)
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")
    yield conn
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_bundle(conn, bid="b-1"):
    conn.execute(
        """INSERT INTO bundles
           (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at)
           VALUES (?, 'r-1', 'devel/foo', '', ?, 'failure', '@2026Q2', ?)""",
        (bid, _now(), _now()),
    )


def _bundle_dir(tmp_path: Path) -> Path:
    bd = tmp_path / "bundle"
    (bd / "analysis").mkdir(parents=True)
    (bd / "errors.txt").write_text("cc: fatal error\nmake: *** Error 1\n")
    return bd


def _bootstrap_to_triaging(conn, jid):
    for ev in (JobEvent.HOOK_ENQUEUED, JobEvent.CLAIM, JobEvent.TRIAGE_START):
        lifecycle_apply(conn, jid, ev, detail={"bundle_id": "b-1"})


def test_triage_failure_writes_handoff_and_sets_resolution(
    tmp_path: Path, monkeypatch, state_db,
):
    _seed_bundle(state_db)
    bundle_dir = _bundle_dir(tmp_path)
    queue_root = tmp_path / "queue"
    (queue_root / "pending").mkdir(parents=True)
    job_path = queue_root / "pending" / "triage-1.job"
    job_path.write_text("type=triage\norigin=devel/foo\n")
    _bootstrap_to_triaging(state_db, job_path.name)

    # Capture artifact-store writes (no real store in tests).
    saved: dict = {}
    monkeypatch.setattr(runner_mod, "artifact_store_put",
                        lambda bid, rel, data, kind: saved.__setitem__((bid, rel), data) or True)

    # A model must be configured so the step passes its readiness
    # precheck and reaches the LLM call (the precheck-fail path is a
    # separate orchestrator-halt branch, out of scope here).
    monkeypatch.setenv("DP_HARNESS_TRIAGE_MODEL", "test/model")

    # Force the triage LLM call to raise → TriageStep._err → failed
    # outcome → orchestrator fires TRIAGE_FAIL (bundle_id injected).
    def _boom(*a, **k):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(triage_mod, "run", _boom)

    job = {"origin": "devel/foo", "target": "@2026Q2",
           "bundle_id": "b-1", "run_id": "r-1"}
    success, status = runner_mod.process_triage_job(
        queue_root=queue_root, job_path=job_path, sibling_paths=[],
        job=job, bundle_dir=bundle_dir, playbooks_dir=None,
    )

    assert not success
    # Bundle is now actionable, not stranded at NULL.
    res = state_db.execute(
        "SELECT resolution FROM bundles WHERE bundle_id='b-1'",
    ).fetchone()["resolution"]
    assert res == "triage_failed"
    # A handoff was written explaining the failure.
    handoff = saved.get(("b-1", "analysis/manual_handoff.md"))
    assert handoff is not None
    body = handoff.decode("utf-8")
    assert "triage failed to run" in body
    assert "connection refused" in body
