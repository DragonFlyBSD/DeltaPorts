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
    monkeypatch.delenv("DP_HARNESS_ENV", raising=False)

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

    process_job(queue_root, job_file, [], dry_run=False, kedb_dir=None)

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


def test_process_convert_job_needs_judgment_without_env(
    tmp_path: Path, monkeypatch
) -> None:
    """needs_judgment + no DP_HARNESS_ENV → clear refusal so the
    operator knows the LLM path needs the env wired up."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/with-cond")
    (port / "Makefile.DragonFly").write_text(
        ".if ${OPSYS} == DragonFly\nUSES+=pkgconfig\n.endif\n"
    )
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.delenv("DP_HARNESS_ENV", raising=False)

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/with-cond", "target": "@main"},
    )
    assert not success
    assert "DP_HARNESS_ENV unset" in status


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
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
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
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    monkeypatch.setenv("DP_HARNESS_CONVERT_MODEL", "openai/test-model")

    fake_proof = {
        "origin": "devel/llm-ok",
        "mechanical_ops_written": 0,
        "framework_migrated_to_dops": ["mk block set for OPSYS"],
        "source_migrated_to_semantic": [],
        "source_patches_retained": [],
        "files_removed": ["Makefile.DragonFly"],
        "files_added": ["overlay.dops"],
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
    monkeypatch.setattr(worker, "dsynth_build",
                        lambda env, origin: {"rebuild_ok": True, "ok": True})

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
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
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
    """Auto-safe conversion + DP_HARNESS_ENV set → verification runs.
    materialize_dports (reapply / compose) returns ok → handler
    succeeds. We do NOT call dsynth_build; build outcome isn't a
    proxy for conversion correctness."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/verify-pass")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")

    from dportsv3.agent import worker
    monkeypatch.setattr(worker, "materialize_dports",
                        lambda env, origin: {"ok": True, "rc": 0})
    # Guard: dsynth_build must NOT be called from the verification
    # path. If it is, the test fails fast.
    monkeypatch.setattr(
        worker, "dsynth_build",
        lambda env, origin: pytest.fail(
            "dsynth_build called from convert verification — "
            "conversion is validated by reapply (compose) only"
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
    assert (port / "overlay.dops").exists()


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
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")

    from dportsv3.agent import worker
    monkeypatch.setattr(
        worker, "materialize_dports",
        lambda env, origin: {
            "ok": False, "rc": 2,
            "stderr_tail": "compose: dops parse error at line 3",
        },
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


def test_convert_tool_whitelist_blocks_build_tools() -> None:
    """Defense-in-depth: even if a future change ships extract /
    dsynth_build schemas to convert by mistake, the tool_loop's
    whitelist check refuses them at dispatch time."""
    from dportsv3.agent.tools import CONVERT_TOOL_NAMES, names
    forbidden = {"extract", "dsynth_build", "dupe", "genpatch",
                 "install_patches"}
    for f in forbidden:
        assert f in names(), f"tool {f!r} should exist for patch flow"
        assert f not in CONVERT_TOOL_NAMES, (
            f"convert flow must not expose {f!r}"
        )
    # Sanity: the tools we DO need are in the whitelist.
    for needed in ("env_verify", "list_dir", "get_file", "put_file",
                   "grep", "materialize_dports", "dops_reference"):
        assert needed in CONVERT_TOOL_NAMES


def test_process_convert_job_no_env_skips_verification(
    tmp_path: Path, monkeypatch
) -> None:
    """No DP_HARNESS_ENV → handler accepts the conversion on faith
    and notes that verification was skipped. Useful for offline
    runs / unit tests; smoke tests always have DP_HARNESS_ENV set."""
    repo = _make_repo(tmp_path)
    port = _make_port(repo, "devel/no-env")
    (port / "Makefile.DragonFly").write_text("USES+=pkgconfig\n")
    monkeypatch.setenv("DP_HARNESS_REPO_ROOT", str(repo))
    monkeypatch.delenv("DP_HARNESS_ENV", raising=False)

    success, status = process_convert_job(
        queue_root=tmp_path / "queue",
        job_path=tmp_path / "queue" / "x.job",
        sibling_paths=[],
        job={"origin": "devel/no-env", "target": "@main"},
    )
    assert success
    assert "verification skipped" in status


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
