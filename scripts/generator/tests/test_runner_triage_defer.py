"""Tests for the lazy convert-job hook in process_triage_job
(Step 20d).

The hook (``_maybe_defer_to_convert``) is exercised directly
here — the surrounding orchestrator + step machinery is heavy and
unrelated to what we're verifying.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.agent.runner import (
    _find_active_convert_job,
    _maybe_defer_to_convert,
)
from dportsv3.agent.lifecycle import JobEvent, apply as lifecycle_apply
from dportsv3.db.schema import init_db


def _make_repo(tmp_path: Path) -> Path:
    (tmp_path / "ports").mkdir()
    return tmp_path


def _make_port(repo: Path, origin: str) -> Path:
    port = repo / "ports" / origin
    port.mkdir(parents=True)
    return port


@pytest.fixture
def state_db(tmp_path: Path, monkeypatch):
    """Wire _state_db_conn so lifecycle.apply + activity_log + the
    convert-job lookup all see the same in-memory state."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    monkeypatch.setattr(runner_mod, "_state_db_conn", conn)
    yield conn
    conn.close()


def _make_queue(tmp_path: Path) -> Path:
    qr = tmp_path / "queue"
    (qr / "pending").mkdir(parents=True)
    return qr


def _bootstrap_triage_job(conn, queue_root, jid):
    """Walk a job to TRIAGING so the deferred-path transitions are
    valid (TRIAGE_OK + ESCALATE_MANUAL only fire from TRIAGING +
    TRIAGED respectively)."""
    lifecycle_apply(conn, jid, JobEvent.HOOK_ENQUEUED)
    lifecycle_apply(conn, jid, JobEvent.CLAIM)
    lifecycle_apply(conn, jid, JobEvent.TRIAGE_START)


def test_defer_for_needs_judgment_port(tmp_path: Path, monkeypatch, state_db) -> None:
    """Port whose ``Makefile.DragonFly`` has a conditional → needs
    judgment → hook enqueues a convert job and short-circuits the
    triage. Returned status carries the convert_job_id."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/cond")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    job_path = queue_root / "pending" / "triage-1.job"
    job_path.write_text("type=triage\norigin=devel/cond\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    job = {"origin": "devel/cond", "target": "@main",
           "profile": "main"}
    result = _maybe_defer_to_convert(
        queue_root=queue_root, job=job, job_path=job_path, origin="devel/cond",
    )
    assert result is not None
    success, status = result
    assert success
    assert "deferred_for_convert" in status

    # Convert job file is on disk.
    convert_jobs = list(queue_root.glob("pending/*-convert.job"))
    assert len(convert_jobs) == 1
    content = convert_jobs[0].read_text()
    assert "origin=devel/cond" in content
    assert "requested_by=triage" in content

    # The triage job is parked at DEAD with the dedicated
    # 'deferred_for_convert' retire reason, so the manual queue
    # filters it out instead of surfacing it as actionable.
    row = state_db.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id = ?",
        (job_path.name,),
    ).fetchone()
    assert row["state"] == "dead"
    assert row["retire_reason"] == "deferred_for_convert"


def test_defer_attaches_to_existing_convert_job(
    tmp_path: Path, monkeypatch, state_db
) -> None:
    """If a convert job for the same (origin, target) is already in
    flight, the hook attaches to it instead of enqueuing a second."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/dup")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    # Pre-existing convert job for the same port.
    existing_convert = runner_mod.enqueue_convert_job(
        queue_root, origin="devel/dup", target="@main",
        profile="main", requested_by="operator",
    )

    # First triage job for the same failure.
    job_path = queue_root / "pending" / "triage-dup.job"
    job_path.write_text("type=triage\norigin=devel/dup\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    job = {"origin": "devel/dup", "target": "@main", "profile": "main"}
    result = _maybe_defer_to_convert(
        queue_root=queue_root, job=job, job_path=job_path, origin="devel/dup",
    )
    assert result is not None
    success, status = result
    assert success
    assert existing_convert.name in status
    # Only one convert job in the pending dir — no duplicate.
    convert_jobs = list(queue_root.glob("pending/*-convert.job"))
    assert len(convert_jobs) == 1


def test_no_defer_for_converted_port(tmp_path: Path, monkeypatch, state_db) -> None:
    """Port has overlay.dops and no legacy artifacts → hook returns
    None so triage proceeds normally."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/done")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/done\ntype port\nreason "ok"\n'
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    result = _maybe_defer_to_convert(
        queue_root=_make_queue(tmp_path),
        job={"origin": "devel/done", "target": "@main"},
        job_path=Path("/tmp/x.job"),
        origin="devel/done",
    )
    assert result is None


def test_no_defer_for_not_in_scope_port(
    tmp_path: Path, monkeypatch, state_db
) -> None:
    """Port has no overlay artifacts at all → not in scope; the
    triage flow handles it as usual."""
    repo = _make_repo(tmp_path)
    _make_port(repo, "devel/plain")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    result = _maybe_defer_to_convert(
        queue_root=_make_queue(tmp_path),
        job={"origin": "devel/plain", "target": "@main"},
        job_path=Path("/tmp/x.job"),
        origin="devel/plain",
    )
    assert result is None


def test_defer_for_auto_safe_port(tmp_path: Path, monkeypatch, state_db) -> None:
    """Even ``auto_safe_pending`` ports get deferred — the
    deterministic converter still needs to run; triage on a
    half-migrated port is wasted tokens."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/auto-safe")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    job_path = queue_root / "pending" / "triage-auto.job"
    job_path.write_text("type=triage\norigin=devel/auto-safe\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    result = _maybe_defer_to_convert(
        queue_root=queue_root,
        job={"origin": "devel/auto-safe", "target": "@main"},
        job_path=job_path,
        origin="devel/auto-safe",
    )
    assert result is not None
    success, _ = result
    assert success


def test_find_active_convert_job_filters_by_origin_target(
    tmp_path: Path, state_db
) -> None:
    """The lookup honors origin + target; a convert job for a
    *different* origin shouldn't be returned."""
    queue_root = _make_queue(tmp_path)
    runner_mod.enqueue_convert_job(
        queue_root, origin="devel/foo", target="@main",
        profile="main", requested_by="operator",
    )
    runner_mod.enqueue_convert_job(
        queue_root, origin="devel/bar", target="@main",
        profile="main", requested_by="operator",
    )
    found_foo = _find_active_convert_job("devel/foo", "@main")
    found_bar = _find_active_convert_job("devel/bar", "@main")
    found_baz = _find_active_convert_job("devel/baz", "@main")
    assert found_foo is not None and "devel_foo" in found_foo
    assert found_bar is not None and "devel_bar" in found_bar
    assert found_baz is None
