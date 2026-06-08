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
# tests/conftest.py autouse-fixture stubs list_available_envs to ()
# and resets runner._CLI_ENV_DEFAULT for every test.


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
    shared state.db.

    Also monkeypatches ``worker.classify_dops`` + ``worker.env_paths``
    so tests don't need a real dev-env — both route to the test's
    tmp repo. The production code routes through the chroot via
    ``_exec``; here we bypass that for unit-test speed.
    """
    from dportsv3.db.schema import init_db
    from dportsv3.agent import worker
    from dportsv3.agent.dops import classify as _direct_classify
    import sqlite3
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None)
    init_db(conn)
    monkeypatch.setattr(runner_mod, "_state_db_conn", conn)

    def _fake_classify(env: str, origin: str) -> str:
        import os as _os
        from pathlib import Path as _Path
        repo = _Path(_os.environ.get("DP_HARNESS_REPO_ROOT") or ".")
        return _direct_classify(origin, repo)

    def _fake_env_paths(env: str):
        import os as _os
        from pathlib import Path as _Path
        repo = _Path(_os.environ.get("DP_HARNESS_REPO_ROOT") or ".")
        # Synthesize a writable that contains work/DeltaPorts as the
        # test repo. convert_record consumes this.
        fake_writable = tmp_path / "_fake_writable"
        target = fake_writable / "work" / "DeltaPorts"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            # Symlink so writes through the path land in the real
            # tmp repo where the test created ports/.
            target.symlink_to(repo)
        return worker.EnvPaths(env_dir=fake_writable, writable=fake_writable)

    monkeypatch.setattr(worker, "classify_dops", _fake_classify)
    monkeypatch.setattr(worker, "env_paths", _fake_env_paths)
    yield
    conn.close()


def test_process_job_accepts_convert_without_bundle(
    tmp_path: Path, monkeypatch
) -> None:
    """Regression: process_job's bundle-required gate must NOT fail
    convert jobs. Convert is port-level, so bundle_id and bundle_dir
    are both empty. The gate from earlier dispatcher versions
    silently moved convert jobs to failed/ and the runner went idle,
    which is exactly what happened in the smoke test."""
    from dportsv3.agent.runner import process_job

    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/no-bundle")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    queue_root = tmp_path / "queue"
    (queue_root / "pending").mkdir(parents=True)
    (queue_root / "inflight").mkdir()
    (queue_root / "done").mkdir()
    (queue_root / "failed").mkdir()

    # Pretend the runner already claimed it (file is in inflight/).
    job_file = queue_root / "inflight" / "20260522-000000Z-test-convert.job"
    job_file.write_text(
        "type=convert\norigin=devel/no-bundle\ntarget=@main\n"
    )

    from dportsv3.agent.lifecycle import JobEvent, apply as la
    la(runner_mod._state_db_conn, job_file.name, JobEvent.HOOK_ENQUEUED)
    la(runner_mod._state_db_conn, job_file.name, JobEvent.CLAIM)

    process_job(queue_root, job_file, [], dry_run=False, playbooks_dir=None)

    # The convert job either landed at DONE (deterministic conversion
    # succeeded + no env to verify in, accepted on faith) or at DEAD
    # via CONVERT_GAVE_UP. Either is acceptable — the regression
    # check is that the job is NOT stuck in CLAIMED.
    row = runner_mod._state_db_conn.execute(
        "SELECT state FROM jobs WHERE job_id = ?", (job_file.name,),
    ).fetchone()
    assert row[0] in ("done", "dead"), (
        f"convert job stuck in state={row[0]!r}; expected done or dead"
    )


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


def test_enqueue_convert_job_propagates_bundle_dir(tmp_path: Path) -> None:
    """Triage-enqueued converts must carry the originating bundle_dir
    so the convert's audit (commit message, activity rows) can link
    back. Regression for the empty 'bundle ?' in commit messages
    seen on devel/gperf 2026-05-25."""
    queue_root = tmp_path / "queue"
    (queue_root / "pending").mkdir(parents=True)
    job_path = enqueue_convert_job(
        queue_root,
        origin="devel/foo",
        target="@main",
        requested_by="triage",
        bundle_dir="/logs/bundles/devel_foo-20260526-100000Z",
    )
    content = job_path.read_text()
    assert "bundle_dir=/logs/bundles/devel_foo-20260526-100000Z" in content


def test_enqueue_convert_job_omits_bundle_dir_when_absent(tmp_path: Path) -> None:
    """Operator-fired converts have no originating bundle; bundle_dir
    is dropped from the jobfile rather than written as 'bundle_dir='."""
    queue_root = tmp_path / "queue"
    (queue_root / "pending").mkdir(parents=True)
    job_path = enqueue_convert_job(
        queue_root, origin="devel/foo", target="@main",
        requested_by="operator",
    )
    assert "bundle_dir" not in job_path.read_text()


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
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")
    # Mock the chroot-side verification — the real one shells
    # `dev-env exec` which needs an actual env.
    from dportsv3.agent import worker
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: {"ok": True, "rc": 0})
    monkeypatch.setattr(worker, "materialize_dports_with_report",
                        lambda env, origin: {"ok": True, "rc": 0, "report": None})
    monkeypatch.setattr(worker, "commit_port_changes",
                        lambda env, origin, message: {"ok": True,
                                                       "committed": True,
                                                       "origin": origin,
                                                       "paths_changed": []})

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
    # Deterministic-converted overlays declare `target @any`
    # (auto_safe_pending only fires for unscoped Makefile.DragonFly).
    assert "target @any" in dops
    assert "port devel/auto-safe" in dops


def test_process_convert_job_defers_only_op_to_empty_overlay(
    tmp_path: Path, monkeypatch
) -> None:
    """A deterministic convert whose only op can't apply (ambiguous
    mk.var.set) defers it: the op is dropped, leaving a header-only
    overlay that composes. Convert SUCCEEDS — the effective-ops-empty
    guard is bypassed because the dropped op is recorded as intent —
    so the retriage->patch flow can re-author it. Without the bypass
    this returned (False, 'effective_ops_empty') and patch never ran."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/defer-empty")
    (port / "Makefile.DragonFly").write_text("FOO=bar\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    from dportsv3.agent import worker
    calls = {"n": 0}

    def fake_report(env, origin):  # noqa: ARG001
        calls["n"] += 1
        if calls["n"] == 1:
            # First compose: the only op (mk set FOO) is ambiguous.
            return {
                "ok": False, "rc": 2, "stdout_tail": "", "stderr_tail": "",
                "report": {"ok": False, "ports": [{
                    "origin": origin,
                    "dops_failed_op_results": [{
                        "id": "op-0001-mk-var-set",
                        "kind": "mk.var.set",
                        "diagnostics": [{
                            "severity": "error",
                            "code": "E_APPLY_AMBIGUOUS_MATCH",
                            "source_path": f"/x/ports/{origin}/Makefile",
                            "message": "multiple assignments found for FOO",
                        }],
                    }],
                }]},
            }
        # After the drop the overlay is header-only and composes.
        return {"ok": True, "rc": 0, "report": None}

    monkeypatch.setattr(worker, "materialize_dports_with_report", fake_report)
    monkeypatch.setattr(worker, "commit_port_changes",
                        lambda env, origin, message: {"ok": True,
                                                       "committed": True,
                                                       "origin": origin,
                                                       "paths_changed": []})

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/defer-empty", "target": "@2026Q2"},
    )
    assert success, f"expected success, got status={status!r}"
    dops = (port / "overlay.dops").read_text()
    assert "port devel/defer-empty" in dops   # header kept
    assert "mk set FOO" not in dops           # the only op was deferred


def test_process_convert_job_needs_judgment_without_env(
    tmp_path: Path, monkeypatch
) -> None:
    """needs_judgment + no resolvable dev-env → clear refusal so the
    operator knows the LLM path needs the env wired up."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/with-cond")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/with-cond", "target": "@main"},
    )
    assert not success
    # The runner's earlier "env required" gate fires before
    # _run_llm_conversion can complain about its own model env.
    assert "no dev-env resolved" in status


def test_process_convert_job_needs_judgment_without_model(
    tmp_path: Path, monkeypatch
) -> None:
    """needs_judgment + env but no model → clear refusal."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/with-cond")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")
    for var in ("DP_HARNESS_CONVERT_MODEL", "DP_HARNESS_PATCH_MODEL",
                "DP_HARNESS_TRIAGE_MODEL"):
        monkeypatch.delenv(var, raising=False)

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/with-cond", "target": "@main"},
    )
    assert not success
    assert "no model configured" in status


def test_process_convert_job_needs_judgment_llm_success(
    tmp_path: Path, monkeypatch
) -> None:
    """needs_judgment + env + model + mocked convert.run returning
    a valid proof + mocked dsynth_build green → handler succeeds.
    Verifies the LLM wire-up is in place."""
    from dportsv3.agent import convert as convert_mod
    from dportsv3.agent import worker
    from dportsv3.agent.attempt_loop import PatchResult, Usage

    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/llm-ok")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")
    monkeypatch.setenv("DP_HARNESS_CONVERT_MODEL", "openai/test-model")

    fake_proof = {
        "origin": "devel/llm-ok",
        "mechanical_ops_written": 0,
        "framework_migrated_to_dops": ["mk block set for OPSYS"],
        "source_migrated_to_semantic": [],
        "source_patches_retained": [],
        "files_removed": ["Makefile.DragonFly"],
        "files_added": ["overlay.dops"],
        "validate_dops_ok": True,
        "verification_pending": True,
    }
    monkeypatch.setattr(
        convert_mod, "run",
        lambda *args, **kwargs: convert_mod.ConvertResult(
            success=True, proof=fake_proof,
            raw_result=PatchResult(status="success", final_text="ok"),
            status="conversion_proof_parsed",
        ),
    )
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: {"ok": True, "rc": 0})
    monkeypatch.setattr(worker, "materialize_dports_with_report",
                        lambda env, origin: {"ok": True, "rc": 0, "report": None})
    monkeypatch.setattr(worker, "dsynth_build",
                        lambda env, origin: {"rebuild_ok": True, "ok": True})
    monkeypatch.setattr(worker, "commit_port_changes",
                        lambda env, origin, message: {"ok": True,
                                                       "committed": True,
                                                       "origin": origin,
                                                       "paths_changed": []})

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/llm-ok", "target": "@main"},
    )
    assert success, status
    assert "verified" in status


def test_process_convert_job_needs_judgment_llm_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """convert.run returns success=False → handler reports
    llm_convert_failed with the proof-parser's status."""
    from dportsv3.agent import convert as convert_mod
    from dportsv3.agent.attempt_loop import PatchResult

    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/llm-fail")
    (port / "Makefile.DragonFly").write_text(".if 1\n.endif\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")
    monkeypatch.setenv("DP_HARNESS_CONVERT_MODEL", "openai/test-model")

    monkeypatch.setattr(
        convert_mod, "run",
        lambda *args, **kwargs: convert_mod.ConvertResult(
            success=False, proof=None,
            raw_result=PatchResult(status="budget-exhausted", final_text=""),
            status="no_conversion_proof_block",
        ),
    )

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/llm-fail", "target": "@main"},
    )
    assert not success
    assert "llm_convert_failed" in status
    assert "no_conversion_proof_block" in status


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
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

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


def test_process_convert_job_verifies_with_reapply_pass(
    tmp_path: Path, monkeypatch
) -> None:
    """Auto-safe conversion + dev-env resolvable → verification runs.
    materialize_dports (reapply / compose) returns ok → handler
    succeeds. We do NOT call dsynth_build; build outcome isn't a
    proxy for conversion correctness."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/verify-pass")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    from dportsv3.agent import worker
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: {"ok": True, "rc": 0})
    monkeypatch.setattr(worker, "materialize_dports_with_report",
                        lambda env, origin: {"ok": True, "rc": 0, "report": None})
    # Guard: dsynth_build must NOT be called from the verification
    # path. If it is, the test fails fast.
    monkeypatch.setattr(
        worker, "dsynth_build",
        lambda env, origin: pytest.fail(
            "dsynth_build called from convert verification — "
            "conversion is validated by reapply (compose) only"
        ),
    )
    # The post-convert env commit (stopgap for the convert→patch
    # handoff). Stub here; real coverage is in
    # test_worker_commit_port_changes.py.
    commit_calls = []
    def fake_commit(env, origin, message):
        commit_calls.append((env, origin, message))
        return {"ok": True, "committed": True, "origin": origin,
                "paths_changed": [f"ports/{origin}"]}
    monkeypatch.setattr(worker, "commit_port_changes", fake_commit)

    # Capture activity_log rows so we can assert the
    # commit_port_changes_ok row gets emitted (observability gap
    # the analyzer flagged on devel/gperf 2026-05-25).
    activity_rows = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: activity_rows.append(
            {"stage": stage, "message": message, **kw}
        ),
    )

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/verify-pass", "target": "@main"},
    )
    assert success, status
    assert "reapply" in status or "verified" in status
    assert "committed to env" in status
    # commit_port_changes fires exactly once with the resolved env.
    assert len(commit_calls) == 1
    assert commit_calls[0][1] == "devel/verify-pass"
    assert (port / "overlay.dops").exists()
    # commit_port_changes_ok activity row recorded — operator /
    # analyzer can see the handoff cleared without reading git log.
    ok_rows = [r for r in activity_rows
               if r["stage"] == "commit_port_changes_ok"]
    assert len(ok_rows) == 1
    assert ok_rows[0]["extra"]["origin"] == "devel/verify-pass"
    assert ok_rows[0]["extra"]["committed"] is True


def test_process_convert_job_emits_failure_row_when_commit_fails(
    tmp_path: Path, monkeypatch,
) -> None:
    """When commit_port_changes returns ok=False, _verify_conversion
    must emit a commit_port_changes_failed activity row AND return
    success=False so the operator sees the broken handoff."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/commit-fail")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    from dportsv3.agent import worker
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: {"ok": True, "rc": 0})
    monkeypatch.setattr(worker, "materialize_dports_with_report",
                        lambda env, origin: {"ok": True, "rc": 0, "report": None})
    monkeypatch.setattr(
        worker, "commit_port_changes",
        lambda env, origin, message: {
            "ok": False,
            "error": "commit_port_changes failed for ports/devel/commit-fail",
            "stderr_tail": "fatal: not a git repository",
        },
    )
    activity_rows = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: activity_rows.append(
            {"stage": stage, "message": message, **kw}
        ),
    )

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/commit-fail", "target": "@main"},
    )
    assert not success
    assert "env commit failed" in status
    fail_rows = [r for r in activity_rows
                 if r["stage"] == "commit_port_changes_failed"]
    assert len(fail_rows) == 1
    assert "not a git repository" in fail_rows[0]["extra"]["stderr_tail"]


def test_process_convert_job_commit_message_includes_bundle_dir(
    tmp_path: Path, monkeypatch,
) -> None:
    """The convert job's bundle_dir field must reach the commit
    message — was rendered as 'bundle ?' before bundle_dir
    propagation."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/with-bundle")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    from dportsv3.agent import worker
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: {"ok": True, "rc": 0})
    monkeypatch.setattr(worker, "materialize_dports_with_report",
                        lambda env, origin: {"ok": True, "rc": 0, "report": None})
    captured_message = []
    def fake_commit(env, origin, message):
        captured_message.append(message)
        return {"ok": True, "committed": True, "origin": origin,
                "paths_changed": [f"ports/{origin}"]}
    monkeypatch.setattr(worker, "commit_port_changes", fake_commit)

    process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/with-bundle", "target": "@main",
             "bundle_dir": "/logs/bundles/devel_with-bundle-20260526Z"},
    )
    assert len(captured_message) == 1
    # The bundle ref made it into the commit message — no more "?".
    assert "devel_with-bundle-20260526Z" in captured_message[0]
    assert "?" not in captured_message[0]


def test_process_convert_job_reapply_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """Conversion writes overlay.dops but reapply (compose) rejects
    it — handler surfaces the rc + stderr so the operator can read
    the compose diagnostics."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/verify-fail")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    from dportsv3.agent import worker
    fail_result = {
        "ok": False, "rc": 2,
        "stderr_tail": "compose: dops parse error at line 3",
    }
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: dict(fail_result))
    monkeypatch.setattr(
        worker, "materialize_dports_with_report",
        lambda env, origin: {**fail_result, "report": None},
    )

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/verify-fail", "target": "@main"},
    )
    assert not success
    assert "reapply failed" in status
    assert "dops parse error" in status


def test_summarize_compose_failure_prefers_structured_report():
    # When --json compose output is available, the failure summary
    # must come from the parsed report's first failing stage's first
    # error — not from last-line scraping of stdout_tail, which on
    # JSON output picks the closing `}` of the document and produces
    # the misleading `reapply failed: rc=2 '}'` status.
    report = {
        "stages": [
            {"name": "preflight_validate", "success": True, "errors": []},
            {
                "name": "apply_semantic_ops",
                "success": False,
                "errors": [
                    "E_COMPOSE_APPLY_FAILED: devel/libunistring: 1 op(s) "
                    "failed [op-0001-mk-target-set(mk.target.set)="
                    "E_APPLY_PARSE_FAILED]"
                ],
            },
        ],
        "summary": {"errors": 1},
    }
    # diag is the raw JSON text — last non-empty line is `}`.
    diag = '{\n  "stages": [],\n  "summary": {}\n}'
    out = runner_mod._summarize_compose_failure(report, diag)
    assert "E_APPLY_PARSE_FAILED" in out
    assert out != "}"


def test_summarize_compose_failure_falls_back_to_text_when_no_report():
    # Older bundles (or any case where --json output didn't parse)
    # carry report=None. The text-scrape fallback must still produce
    # the meaningful error line and skip the compose footer.
    diag = (
        "compose: dops parse error at line 3\n"
        "done\n"
        "modes: dops=1 compat=0\n"
    )
    out = runner_mod._summarize_compose_failure(None, diag)
    assert "dops parse error at line 3" in out


def test_verify_failure_rolls_back_env_and_logs_activity(
    tmp_path: Path, monkeypatch,
) -> None:
    """On any _verify_conversion failure path, the env's ports/<origin>/
    subtree must be rolled back to git HEAD (worker.reset_port) AND a
    convert_verify_failed activity row must be emitted with the reason
    code. Without rollback the LLM convert agent's put_file overlay.dops
    persists as an untracked file (audio/cdparanoia 2026-05-26 had
    operators staring at a "live" dops file while the job reported
    failure with no rollback trail)."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/verify-rollback")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "materialize_dports",
        lambda env, origin: {
            "ok": False, "rc": 2,
            "stderr_tail": "compose: E_COMPOSE_APPLY_FAILED",
        },
    )
    reset_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        worker, "reset_port",
        lambda env, origin: (
            reset_calls.append((env, origin))
            or {"ok": True, "origin": origin,
                "paths_changed": [f"ports/{origin}"]}
        ),
    )
    activity_rows: list[dict] = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: activity_rows.append(
            {"stage": stage, "message": message, **kw}
        ),
    )

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/verify-rollback", "target": "@main"},
    )
    assert not success
    # Rollback fired exactly once with the right env/origin.
    assert reset_calls == [("test-env", "devel/verify-rollback")]
    # convert_verify_failed activity row carries the reason code.
    verify_rows = [r for r in activity_rows
                   if r["stage"] == "convert_verify_failed"]
    assert len(verify_rows) == 1, activity_rows
    extra = verify_rows[0]["extra"]
    assert extra["reason_code"] == "reapply_failed"
    assert extra["reset_ok"] is True
    assert extra["origin"] == "devel/verify-rollback"


def test_llm_convert_failure_rolls_back_env_and_logs_activity(
    tmp_path: Path, monkeypatch,
) -> None:
    """When `convert_mod.run` returns success=False (budget exhausted,
    no proof block, etc.), the orphaned overlay.dops the agent may have
    written via put_file persists in the env's writable layer unless
    the handler rolls back. Fix #4 covered _verify_conversion failures
    only — this branch was uncovered. cdparanoia 2026-05-26 burned 4
    patch attempts on the residue from such a budget-out."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/needs-judge")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+= pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")
    monkeypatch.setenv("DP_HARNESS_CONVERT_MODEL", "x")
    monkeypatch.setenv("DP_HARNESS_CONVERT_API_KEY", "x")

    from dportsv3.agent import worker, convert as convert_mod
    from types import SimpleNamespace
    monkeypatch.setattr(
        convert_mod, "run",
        lambda *a, **kw: SimpleNamespace(
            success=False, proof=None,
            raw_result=SimpleNamespace(
                status="budget_exhausted",
                usage=SimpleNamespace(
                    prompt_tokens=0, completion_tokens=0, total_tokens=0,
                ),
            ),
            status="no_conversion_proof_block",
        ),
    )
    reset_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        worker, "reset_port",
        lambda env, origin: (
            reset_calls.append((env, origin))
            or {"ok": True, "origin": origin,
                "paths_changed": [f"ports/{origin}"]}
        ),
    )
    activity_rows: list[dict] = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: activity_rows.append(
            {"stage": stage, "message": message, **kw}
        ),
    )

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/needs-judge", "target": "@main"},
    )
    assert not success
    assert "llm_convert_failed" in status
    assert reset_calls == [("test-env", "devel/needs-judge")]
    verify_rows = [r for r in activity_rows
                   if r["stage"] == "convert_verify_failed"]
    assert len(verify_rows) == 1, activity_rows
    assert verify_rows[0]["extra"]["reason_code"] == "llm_convert_failed"


def test_apply_files_removed_deletes_listed_paths(
    tmp_path: Path, monkeypatch,
) -> None:
    """The CONVERT_SYSTEM prompt promises the handler will finalize
    `files_removed` from the proof. Verify the handler actually
    deletes the listed port-subtree files and logs an activity row."""
    from dportsv3.agent import worker
    from dportsv3.agent.runner import _apply_files_removed

    port_dir = tmp_path / "writable" / "work" / "DeltaPorts" / "ports" / "devel" / "p"
    port_dir.mkdir(parents=True)
    (port_dir / "Makefile.DragonFly").write_text("legacy\n")
    (port_dir / "overlay.dops").write_text("# fresh\n")
    (port_dir / "diffs").mkdir()
    (port_dir / "diffs" / "old.diff").write_text("--- a\n+++ b\n")

    monkeypatch.setattr(
        worker, "env_paths",
        lambda env: worker.EnvPaths(
            env_dir=tmp_path / "writable",
            writable=tmp_path / "writable",
        ),
    )
    activity_rows: list[dict] = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: activity_rows.append(
            {"stage": stage, "message": message, **kw}
        ),
    )

    _apply_files_removed(
        queue_root=tmp_path / "queue", env="test-env", origin="devel/p",
        proof={"files_removed": [
            "Makefile.DragonFly",                # legitimate
            "ports/devel/p/diffs/old.diff",      # fully-qualified, stripped
            "../escape.txt",                     # refused
            "overlay.dops",                      # refused (don't delete the fresh overlay)
            "nonexistent",                       # idempotent, no error
        ]},
    )

    assert not (port_dir / "Makefile.DragonFly").exists()
    assert not (port_dir / "diffs" / "old.diff").exists()
    assert (port_dir / "overlay.dops").exists()  # protected
    rows = [r for r in activity_rows if r["stage"] == "convert_files_removed"]
    assert len(rows) == 1
    extra = rows[0]["extra"]
    assert "Makefile.DragonFly" in extra["removed"]
    assert "diffs/old.diff" in extra["removed"]
    assert "nonexistent" in extra["removed"]  # idempotent
    skipped_paths = {row["path"] for row in extra["skipped"]}
    assert "../escape.txt" in skipped_paths
    assert "overlay.dops" in skipped_paths


def test_apply_files_removed_noop_when_field_missing(
    tmp_path: Path, monkeypatch,
) -> None:
    """No `files_removed` key (or empty) → silent no-op. No activity
    row emitted (avoids noise for successful auto-safe converts where
    the deterministic translator handled the cleanup)."""
    from dportsv3.agent.runner import _apply_files_removed
    activity_rows: list[dict] = []
    monkeypatch.setattr(
        runner_mod, "activity_log",
        lambda queue_root, stage, message, **kw: activity_rows.append(
            {"stage": stage}
        ),
    )
    _apply_files_removed(
        queue_root=tmp_path, env="e", origin="devel/x", proof={},
    )
    _apply_files_removed(
        queue_root=tmp_path, env="e", origin="devel/x",
        proof={"files_removed": []},
    )
    assert activity_rows == []


def test_convert_tool_whitelist_blocks_build_tools() -> None:
    """Defense-in-depth: even if a future change ships make_extract /
    dsynth_build schemas to convert by mistake, the tool_loop's
    whitelist check refuses them at dispatch time."""
    from dportsv3.agent.tools import CONVERT_TOOL_NAMES, names
    forbidden = {"make_extract", "make_patch", "dsynth_build", "dupe",
                 "genpatch", "install_patches"}
    for f in forbidden:
        assert f in names(), f"tool {f!r} should exist for patch flow"
        assert f not in CONVERT_TOOL_NAMES, (
            f"convert flow must not expose {f!r}"
        )
    # Sanity: the tools we DO need are in the whitelist.
    for needed in ("env_verify", "list_dir", "get_file", "put_file",
                   "grep", "dops_reference", "validate_dops"):
        assert needed in CONVERT_TOOL_NAMES
    # materialize_dports is intentionally NOT exposed to the
    # convert agent — only the handler invokes it for verification
    # after the agent emits the proof. If the agent has it, it
    # tends to call it and then wander into /work/artifacts/compose/.
    assert "materialize_dports" not in CONVERT_TOOL_NAMES


def test_process_convert_job_requires_dev_env(
    tmp_path: Path, monkeypatch
) -> None:
    """Convert needs a dev-env — the substrate for both
    classification and conversion is the chroot's writable
    overlay. With no env set, the handler refuses with a clear
    error rather than silently reading the host clone."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/no-env")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/no-env", "target": "@main"},
    )
    assert not success
    assert "no dev-env resolved" in status


def test_process_convert_job_bootstraps_when_dir_absent(
    tmp_path: Path, monkeypatch
) -> None:
    """Option A: a pure-upstream port with no DeltaPorts overlay dir
    still gets bootstrapped — convert creates the dir + header overlay
    and verifies via reapply. (We're here because the port failed to
    build, so it exists upstream.)"""
    repo = _make_repo(tmp_path)  # devel/ghost dir intentionally absent
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    from dportsv3.agent import worker
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: {"ok": True, "rc": 0})
    monkeypatch.setattr(worker, "materialize_dports_with_report",
                        lambda env, origin: {"ok": True, "rc": 0, "report": None})
    monkeypatch.setattr(
        worker, "commit_port_changes",
        lambda env, origin, message: {
            "ok": True, "committed": True, "origin": origin,
            "paths_changed": [f"ports/{origin}"]},
    )

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/ghost", "target": "@main"},
    )
    assert success, status
    overlay = repo / "ports" / "devel" / "ghost" / "overlay.dops"
    assert overlay.exists()  # dir + header created from nothing
    assert "port devel/ghost" in overlay.read_text()


def test_process_convert_job_bootstraps_empty_scope(
    tmp_path: Path, monkeypatch
) -> None:
    """Step 44: a port dir that exists but has no DragonFly delta gets a
    deterministic header-only overlay.dops, then verifies via reapply
    (no build). Convert opens the substrate; patch fills the body."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/bare")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setattr(runner_mod, "_CLI_ENV_DEFAULT", "test-env")

    from dportsv3.agent import worker
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: {"ok": True, "rc": 0})
    monkeypatch.setattr(worker, "materialize_dports_with_report",
                        lambda env, origin: {"ok": True, "rc": 0, "report": None})
    monkeypatch.setattr(
        worker, "dsynth_build",
        lambda env, origin: pytest.fail(
            "dsynth_build called from bootstrap convert — verification is "
            "reapply (compose) only"
        ),
    )
    monkeypatch.setattr(
        worker, "commit_port_changes",
        lambda env, origin, message: {
            "ok": True, "committed": True, "origin": origin,
            "paths_changed": [f"ports/{origin}"]},
    )

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/bare", "target": "@main"},
    )
    assert success, status
    overlay = port / "overlay.dops"
    assert overlay.exists()
    text = overlay.read_text()
    assert "target @any" in text
    assert "port devel/bare" in text
    assert "type port" in text
    assert "reason " in text
