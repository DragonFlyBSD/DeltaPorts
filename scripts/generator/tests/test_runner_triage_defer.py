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
    convert-job lookup all see the same in-memory state.

    Also monkeypatches ``worker.assess_dops`` / ``classify_dops`` to bypass the
    chroot shell-out — tests don't have a real dev-env.
    ``runner._CLI_ENV_DEFAULT`` is set so the resolver's "env required"
    gate passes; classify routes back to the test's tmp repo via
    ``dops.classify`` directly.
    """
    from dportsv3.agent import worker
    from dportsv3.agent.dops import assess as _direct_assess

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    monkeypatch.setattr(runner_mod, "_state_db_conn", conn)

    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    def _fake_assess(env: str, origin: str):
        import os as _os
        from pathlib import Path as _Path
        repo = _Path(_os.environ.get("DP_HARNESS_REPO_ROOT") or ".")
        return _direct_assess(origin, repo)

    def _fake_classify(env: str, origin: str) -> str:
        return _fake_assess(env, origin).state

    monkeypatch.setattr(worker, "assess_dops", _fake_assess)
    monkeypatch.setattr(worker, "classify_dops", _fake_classify)

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


def test_defer_with_apply_lifecycle_false_skips_transition(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """Step 36 follow-up: when called from TriageStep AFTER the LLM
    has classified and triage_result.json has been written, the
    defer helper must still enqueue convert + log the activity row
    but NOT walk the triage lifecycle to DEAD — TriageStep emits
    TRIAGE_DEFER through its StepOutcome and the orchestrator
    wrapper walks lifecycle once. Without ``apply_lifecycle=False``
    the lifecycle would transition twice (helper here + outcome
    handler later) and produce an illegal-transition warning."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/lazy")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    job_path = queue_root / "pending" / "triage-late.job"
    job_path.write_text("type=triage\norigin=devel/lazy\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    job = {"origin": "devel/lazy", "target": "@main",
           "profile": "main"}
    result = _maybe_defer_to_convert(
        queue_root=queue_root, job=job, job_path=job_path,
        origin="devel/lazy",
        apply_lifecycle=False,
    )
    # Same return shape — caller can detect "deferred" with the
    # existing tuple check.
    assert result is not None
    success, status = result
    assert success
    assert "deferred_for_convert" in status

    # Convert was still enqueued (the audit + routing work happens
    # regardless of lifecycle handling).
    convert_jobs = list(queue_root.glob("pending/*-convert.job"))
    assert len(convert_jobs) == 1

    # But the triage row is still in its pre-defer state. TriageStep
    # emits TRIAGE_DEFER through StepOutcome, so the lifecycle walk
    # is the orchestrator's job — not this helper's.
    row = state_db.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id = ?",
        (job_path.name,),
    ).fetchone()
    assert row["state"] != "dead", (
        f"helper should not have walked lifecycle when "
        f"apply_lifecycle=False; state={row['state']!r}"
    )
    assert row["retire_reason"] is None


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


def test_missing_env_logs_dops_assessment_skip(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """No job dev_env and no resolvable dev-env should be visible in the UI,
    not only in runner.log."""
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", None)
    queue_root = _make_queue(tmp_path)
    job_path = queue_root / "pending" / "triage-no-env.job"
    job_path.write_text("type=triage\norigin=devel/no-env\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    result = _maybe_defer_to_convert(
        queue_root=queue_root,
        job={"origin": "devel/no-env", "target": "@main"},
        job_path=job_path,
        origin="devel/no-env",
    )

    assert result is None
    row = state_db.execute(
        "SELECT stage, extra_json FROM activity_log WHERE job_id = ? ORDER BY id DESC LIMIT 1",
        (job_path.name,),
    ).fetchone()
    assert row is not None
    assert row["stage"] == "triage_dops_assessment_skipped"
    assert "missing_dev_env" in row["extra_json"]
    assert list(queue_root.glob("pending/*-convert.job")) == []


def test_assessment_failure_logs_activity(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """A dev-env assessment exception should be operator-visible."""
    from dportsv3.agent import worker

    def _boom(env: str, origin: str):
        raise RuntimeError("synthetic classify failure")

    monkeypatch.setattr(worker, "assess_dops", _boom)
    queue_root = _make_queue(tmp_path)
    job_path = queue_root / "pending" / "triage-assess-fail.job"
    job_path.write_text("type=triage\norigin=devel/fail\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    result = _maybe_defer_to_convert(
        queue_root=queue_root,
        job={"origin": "devel/fail", "target": "@main"},
        job_path=job_path,
        origin="devel/fail",
    )

    assert result is None
    row = state_db.execute(
        "SELECT stage, extra_json FROM activity_log WHERE job_id = ? ORDER BY id DESC LIMIT 1",
        (job_path.name,),
    ).fetchone()
    assert row is not None
    assert row["stage"] == "triage_dops_assessment_failed"
    assert "synthetic classify failure" in row["extra_json"]
    assert list(queue_root.glob("pending/*-convert.job")) == []


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


def test_invariant_violation_does_not_defer(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """overlay.dops plus Makefile.DragonFly is a broken half-migration;
    the runner should surface it, not enqueue another convert loop."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/half")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/half\ntype port\nreason "x"\n'
    )
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    job_path = queue_root / "pending" / "triage-half.job"
    job_path.write_text("type=triage\norigin=devel/half\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    result = _maybe_defer_to_convert(
        queue_root=queue_root,
        job={"origin": "devel/half", "target": "@main"},
        job_path=job_path,
        origin="devel/half",
    )

    assert result is None
    assert list(queue_root.glob("pending/*-convert.job")) == []
    row = state_db.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id = ?",
        (job_path.name,),
    ).fetchone()
    assert row["state"] == "triaging"
    assert row["retire_reason"] in (None, "")


def test_resume_deferred_triage_after_convert_ok(
    tmp_path: Path, monkeypatch, state_db
) -> None:
    """Step 20d auto-resume: when a convert job finishes successfully,
    the previously-deferred triage gets re-enqueued from its
    archived .job file in done/, with the same bundle/run/origin
    metadata."""
    from dportsv3.agent.runner import _resume_deferred_triage
    from dportsv3.agent.lifecycle import (
        JobEvent, apply as lifecycle_apply,
    )

    queue_root = _make_queue(tmp_path)
    (queue_root / "done").mkdir()

    # Write a triage .job file under done/ (where the dispatcher
    # moved it after _maybe_defer_to_convert returned (True, ...)).
    triage_id = "20260523-000000Z-2026Q2-devel_foo-1234.job"
    triage_path = queue_root / "done" / triage_id
    triage_path.write_text(
        "type=triage\n"
        "bundle_id=bundle-abc\n"
        "run_id=run-xyz\n"
        "origin=devel/foo\n"
        "profile=main\n"
        "flavor=devel/foo\n"
        "iteration=1\n"
        "max_iterations=3\n"
        "user_context_rev=0\n"
    )

    # Drive the lifecycle of the triage to DEAD via TRIAGE_DEFER so
    # state.db carries the retire_reason the lookup keys on.
    lifecycle_apply(state_db, triage_id, JobEvent.HOOK_ENQUEUED,
                    detail={"origin": "devel/foo", "target": "@2026Q2"})
    lifecycle_apply(state_db, triage_id, JobEvent.CLAIM)
    lifecycle_apply(state_db, triage_id, JobEvent.TRIAGE_START)
    lifecycle_apply(state_db, triage_id, JobEvent.TRIAGE_DEFER,
                    detail={"deferred_for_convert": True,
                            "convert_job_id": "convert-1"})
    # The triage row needs origin/target/type set so the lookup
    # finds it (lifecycle.apply only manages state/retire_reason —
    # the runner's _register_new_job populates the rest).
    state_db.execute(
        "UPDATE jobs SET origin = ?, target = ?, type = ? WHERE job_id = ?",
        ("devel/foo", "@2026Q2", "triage", triage_id),
    )

    resumed = _resume_deferred_triage(
        queue_root, "convert-job-id", "devel/foo", "@2026Q2",
    )
    assert resumed is not None, "expected a resumed triage"
    new_path = queue_root / "pending" / resumed
    assert new_path.exists()
    content = new_path.read_text()
    assert "type=triage" in content
    assert "bundle_id=bundle-abc" in content
    assert "run_id=run-xyz" in content
    assert "origin=devel/foo" in content
    assert "iteration=1" in content


def test_resume_deferred_triage_returns_none_when_no_match(
    tmp_path: Path, state_db
) -> None:
    """No deferred triage for the (origin, target) → returns None,
    no .job file appears."""
    from dportsv3.agent.runner import _resume_deferred_triage

    queue_root = _make_queue(tmp_path)
    (queue_root / "done").mkdir()

    resumed = _resume_deferred_triage(
        queue_root, "convert-job-id", "devel/nothing", "@2026Q2",
    )
    assert resumed is None
    assert not list((queue_root / "pending").iterdir())


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


def test_dops_state_persisted_to_bundle_row(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """Step 11c layer-violation cleanup: the runner persists the
    dops assessment onto bundles.dops_state at triage time so the
    tracker doesn't have to compute it live."""
    from dportsv3.agent.runner import _maybe_defer_to_convert

    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/converted")
    (port / "overlay.dops").write_text(
        'target @main\nport devel/converted\ntype port\nreason "x"\n'
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    state_db.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at)
           VALUES ('b-1', '', 'devel/converted', '', '', 'failure',
                   '@main', '', '')""",
    )

    queue_root = _make_queue(tmp_path)
    job_path = queue_root / "pending" / "triage-state.job"
    job_path.write_text("type=triage\norigin=devel/converted\nbundle_id=b-1\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    _maybe_defer_to_convert(
        queue_root=queue_root,
        job={"origin": "devel/converted", "target": "@main",
             "bundle_id": "b-1"},
        job_path=job_path,
        origin="devel/converted",
    )

    row = state_db.execute(
        "SELECT dops_state FROM bundles WHERE bundle_id = 'b-1'",
    ).fetchone()
    assert row["dops_state"] == "converted"


def test_circuit_breaker_blocks_redefer_after_recent_convert_done(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """Recurrence guard: if a convert for (origin, target) already
    reached DONE but classify still says auto_safe_pending,
    `_maybe_defer_to_convert` must NOT re-enqueue another convert
    (which would loop the runner). It returns None and lets triage
    proceed instead."""
    from dportsv3.agent.runner import _maybe_defer_to_convert, enqueue_convert_job

    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/looper")
    # auto_safe_pending state: legacy Makefile.DragonFly present,
    # no overlay.dops. Classify will say "needs conversion."
    (port / "Makefile.DragonFly").write_text(
        'USES+= ssl\ndfly-patch:\n\t${REINPLACE_CMD} -e "s/a/b/" file\n'
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    # Stage a recent done convert for this origin/target.
    convert_path = enqueue_convert_job(
        queue_root, origin="devel/looper", target="@main",
        profile="main", requested_by="operator",
    )
    cid = convert_path.name
    lifecycle_apply(state_db, cid, JobEvent.CLAIM)
    lifecycle_apply(state_db, cid, JobEvent.CONVERT_START)
    lifecycle_apply(state_db, cid, JobEvent.CONVERT_OK)
    state_db.execute(
        "UPDATE jobs SET type = 'convert', origin = ?, target = ? WHERE job_id = ?",
        ("devel/looper", "@main", cid),
    )

    job_path = queue_root / "pending" / "triage-loop.job"
    job_path.write_text("type=triage\norigin=devel/looper\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    job = {"origin": "devel/looper", "target": "@main", "profile": "main"}
    result = _maybe_defer_to_convert(
        queue_root=queue_root, job=job, job_path=job_path,
        origin="devel/looper",
    )

    # Triage must proceed (None), no second convert enqueued.
    assert result is None, "circuit breaker should let triage run"
    open_converts = [
        p for p in queue_root.glob("pending/*-convert.job") if p.name != cid
    ]
    assert open_converts == [], "no new convert should be enqueued"


def test_no_defer_for_non_substrate_classification(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """A port whose substrate WOULD defer (needs_judgment Makefile.DragonFly)
    is left for normal triage routing when triage classified the failure as
    a non-substrate problem (plist-error etc.). Without this gate the
    bundle would burn convert tokens on the wrong layer and die at
    compose reapply before patch could run."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "lang/needs-skip")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    job_path = queue_root / "pending" / "triage-plist.job"
    job_path.write_text("type=triage\norigin=lang/needs-skip\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    job = {"origin": "lang/needs-skip", "target": "@main",
           "profile": "main"}
    result = _maybe_defer_to_convert(
        queue_root=queue_root, job=job, job_path=job_path,
        origin="lang/needs-skip",
        triage_classification="plist-error",
    )
    # Gate fires before assess_dops even runs → no defer, no convert
    # job, triage is free to route normally.
    assert result is None
    assert list(queue_root.glob("pending/*-convert.job")) == []

    # Activity row records the bypass so operators can audit.
    rows = state_db.execute(
        "SELECT stage, message FROM activity_log "
        "WHERE job_id = ? AND stage = 'triage_defer_skipped_non_substrate'",
        (job_path.name,),
    ).fetchall()
    assert len(rows) == 1
    assert "plist-error" in rows[0]["message"]


def test_defer_still_fires_for_novel_classification(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """Novel (not in the rubric) classification → conservative behavior
    preserved: still defer. Protects against a future classification
    name not yet in the negative list."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/unknown-cls")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    job_path = queue_root / "pending" / "triage-unk.job"
    job_path.write_text("type=triage\norigin=devel/unknown-cls\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    job = {"origin": "devel/unknown-cls", "target": "@main",
           "profile": "main"}
    result = _maybe_defer_to_convert(
        queue_root=queue_root, job=job, job_path=job_path,
        origin="devel/unknown-cls",
        triage_classification="brand-new-class-not-in-list",
    )
    assert result is not None
    assert "deferred_for_convert" in result[1]


def test_defer_still_fires_for_literal_unknown_classification(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """Triage's literal ``unknown`` classification (a real value from
    the rubric, not a missing field) still defers. The convert
    substrate probe sometimes surfaces what triage couldn't classify —
    pins the design choice that ``unknown`` is OUT of the negative
    set."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/literally-unknown")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    queue_root = _make_queue(tmp_path)

    job_path = queue_root / "pending" / "triage-unk-literal.job"
    job_path.write_text("type=triage\norigin=devel/literally-unknown\n")
    _bootstrap_triage_job(state_db, queue_root, job_path.name)

    job = {"origin": "devel/literally-unknown", "target": "@main",
           "profile": "main"}
    result = _maybe_defer_to_convert(
        queue_root=queue_root, job=job, job_path=job_path,
        origin="devel/literally-unknown",
        triage_classification="unknown",
    )
    assert result is not None
    assert "deferred_for_convert" in result[1]
