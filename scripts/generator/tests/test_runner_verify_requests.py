"""Step 11c layer-violation cleanup — verify_requests reconciler
(runner-side counterpart to the tracker's /verify endpoint).

The tracker INSERTs into verify_requests with status='pending' and
returns immediately. The runner's poll loop scans for pending rows,
calls enqueue_verify_job, and marks the request 'enqueued' (or
'failed' if enqueue raises). This restores the
tracker-doesn't-touch-the-queue invariant.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.db.schema import init_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_bundle(conn, bundle_id: str, origin: str = "devel/foo",
                 target: str = "@2026Q2") -> None:
    now = _now()
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at)
           VALUES (?, '', ?, '', ?, 'failure', ?, '', ?)""",
        (bundle_id, origin, now, target, now),
    )


def _seed_request(conn, bundle_id: str, env: str = "verify-env") -> int:
    cur = conn.execute(
        """INSERT INTO verify_requests
               (bundle_id, env, requested_by, requested_at, status)
           VALUES (?, ?, 'operator', ?, 'pending')""",
        (bundle_id, env, _now()),
    )
    return cur.lastrowid


@pytest.fixture
def state_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "state.db"
    c = sqlite3.connect(str(db_path), isolation_level=None,
                        check_same_thread=False)
    c.row_factory = sqlite3.Row
    init_db(c)
    monkeypatch.setattr(runner_mod, "_state_db_conn", c)
    yield c
    c.close()


def _queue_root(tmp_path: Path) -> Path:
    qr = tmp_path / "queue"
    (qr / "pending").mkdir(parents=True)
    return qr


def test_pending_request_gets_enqueued_and_marked(tmp_path, state_db):
    _seed_bundle(state_db, "b-1")
    req_id = _seed_request(state_db, "b-1", env="my-env")
    qr = _queue_root(tmp_path)

    runner_mod.process_verify_requests(qr)

    # .job file landed.
    jobs = list((qr / "pending").glob("*-verify.job"))
    assert len(jobs) == 1
    content = jobs[0].read_text()
    assert "bundle_id=b-1" in content
    assert "dev_env=my-env" in content
    assert "origin=devel/foo" in content
    assert "target=@2026Q2" in content

    # Request marked enqueued, job_id recorded.
    row = state_db.execute(
        "SELECT status, job_id FROM verify_requests WHERE id = ?",
        (req_id,),
    ).fetchone()
    assert row["status"] == "enqueued"
    assert row["job_id"] == jobs[0].name


def test_already_enqueued_request_is_skipped(tmp_path, state_db):
    _seed_bundle(state_db, "b-1")
    req_id = _seed_request(state_db, "b-1")
    state_db.execute(
        "UPDATE verify_requests SET status = 'enqueued', job_id = 'prior' WHERE id = ?",
        (req_id,),
    )
    qr = _queue_root(tmp_path)

    runner_mod.process_verify_requests(qr)

    assert list((qr / "pending").glob("*-verify.job")) == []


def test_unknown_bundle_marks_request_failed(tmp_path, state_db):
    req_id = _seed_request(state_db, "b-missing")
    qr = _queue_root(tmp_path)

    runner_mod.process_verify_requests(qr)

    row = state_db.execute(
        "SELECT status, error, job_id FROM verify_requests WHERE id = ?",
        (req_id,),
    ).fetchone()
    assert row["status"] == "failed"
    assert "not found" in (row["error"] or "")
    assert row["job_id"] is None


def test_multiple_pending_requests_all_processed(tmp_path, state_db):
    _seed_bundle(state_db, "b-1")
    _seed_bundle(state_db, "b-2", origin="devel/bar")
    _seed_request(state_db, "b-1")
    _seed_request(state_db, "b-2", env="env-2")
    qr = _queue_root(tmp_path)

    runner_mod.process_verify_requests(qr)

    assert len(list((qr / "pending").glob("*-verify.job"))) == 2
    rows = state_db.execute(
        "SELECT status FROM verify_requests ORDER BY id",
    ).fetchall()
    assert [r["status"] for r in rows] == ["enqueued", "enqueued"]
