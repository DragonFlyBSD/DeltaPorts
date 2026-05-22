"""Tests for the convert-job handler + enqueue (Step 20c).

Covers:
- ``enqueue_convert_job`` writes a well-formed .job file under
  ``pending/`` and registers the job in state.db.
- ``process_convert_job`` runs the deterministic translator
  against an auto-safe port and returns success.
- ``process_convert_job`` returns a ``needs_llm`` failure for a
  port whose ``Makefile.DragonFly`` has conditional blocks.
- ``process_convert_job`` short-circuits to success for an
  already-converted port.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.agent.runner import enqueue_convert_job, process_convert_job


def _make_repo(tmp_path: Path) -> Path:
    """Make a tmp repo root with ports/ but no actual ports yet."""
    (tmp_path / "ports").mkdir()
    return tmp_path


def _make_port(repo: Path, origin: str) -> Path:
    port_dir = repo / "ports" / origin
    port_dir.mkdir(parents=True)
    return port_dir


@pytest.fixture(autouse=True)
def _isolate_state_db(tmp_path: Path, monkeypatch):
    """Point runner._state_db_conn at a tmp state.db so the registration
    side-effects of enqueue_convert_job don't write to the host's
    shared state.db."""
    from dportsv3.db.schema import init_db
    import sqlite3
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    init_db(conn)
    monkeypatch.setattr(runner_mod, "_state_db_conn", conn)
    yield
    conn.close()


def test_enqueue_convert_job_writes_jobfile(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    (queue_root / "pending").mkdir(parents=True)
    job_path = enqueue_convert_job(
        queue_root,
        origin="devel/foo",
        target="@main",
        profile="main",
        requested_by="triage",
    )
    assert job_path.exists()
    assert job_path.name.endswith("-convert.job")
    content = job_path.read_text()
    assert "type=convert" in content
    assert "origin=devel/foo" in content
    assert "target=@main" in content
    assert "requested_by=triage" in content


def test_process_convert_job_auto_safe_port(
    tmp_path: Path, monkeypatch
) -> None:
    """A port with a plain Makefile.DragonFly (auto-safe bucket)
    gets converted by the deterministic translator. overlay.dops
    appears, handler returns (True, ...)."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/auto-safe")
    (port / "Makefile.DragonFly").write_text(
        "USES+=pkgconfig\nCONFIGURE_ARGS+=--with-foo\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    job = {"origin": "devel/auto-safe", "target": "@main"}
    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job=job,
    )
    assert success, f"expected success, got status={status!r}"
    assert (port / "overlay.dops").exists()
    dops = (port / "overlay.dops").read_text()
    assert "target @main" in dops
    assert "port devel/auto-safe" in dops


def test_process_convert_job_needs_llm_for_conditional(
    tmp_path: Path, monkeypatch
) -> None:
    """A Makefile.DragonFly with a .if conditional → ``needs_judgment``
    → the handler returns a ``needs_llm:`` failure so the dispatcher
    fires CONVERT_GAVE_UP with that detail. Once 20b lands, this
    case will run the LLM tool loop instead."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/with-cond")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    job = {"origin": "devel/with-cond", "target": "@main"}
    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job=job,
    )
    assert not success
    assert "needs_llm" in status


def test_process_convert_job_already_converted(
    tmp_path: Path, monkeypatch
) -> None:
    """Port already has overlay.dops and no legacy artifacts. The
    handler should short-circuit to success without touching the
    file."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/done")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/done\ntype port\nreason "test"\n'
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    before = (port / "overlay.dops").read_text()
    job = {"origin": "devel/done", "target": "@main"}
    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job=job,
    )
    assert success
    assert "already converted" in status
    assert (port / "overlay.dops").read_text() == before


def test_process_convert_job_not_in_scope(
    tmp_path: Path, monkeypatch
) -> None:
    """Port with no overlay artifacts at all → handler refuses
    with a clear status. This shouldn't happen via the triage hook
    once 20d lands (classify gates the enqueue), but guards
    against operator-enqueued nonsense."""
    repo = _make_repo(tmp_path)
    _make_port(repo, "devel/nothing")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/nothing", "target": "@main"},
    )
    assert not success
    assert "not in dops scope" in status
