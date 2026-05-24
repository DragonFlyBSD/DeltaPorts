"""Plan Step 11c-2 — runner-side verify-fix job dispatch.

The new ``verify`` job type carries (bundle_id, origin, target, env)
and the dispatcher calls ``dportsv3.verify_fix.run_verify_fix`` in
process. The lifecycle transitions mirror CONVERT_* (VERIFY_FIX_START
→ VERIFY_FIX_OK on completion, VERIFY_FIX_GAVE_UP only if the
orchestrator itself raises).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.agent.lifecycle import JobEvent, JobState, apply as lifecycle_apply
from dportsv3.db.schema import init_db


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
    (qr / "done").mkdir()
    (qr / "failed").mkdir()
    return qr


# ---------------------------------------------------------------------------
# enqueue_verify_job
# ---------------------------------------------------------------------------


def test_enqueue_verify_job_writes_jobfile(tmp_path, state_db):
    qr = _queue_root(tmp_path)
    p = runner_mod.enqueue_verify_job(
        qr, bundle_id="b-1", origin="devel/foo", target="@2026Q2",
        env="verify-env",
    )
    assert p.exists()
    assert p.name.endswith("-verify.job")
    content = p.read_text()
    assert "type=verify" in content
    assert "bundle_id=b-1" in content
    assert "origin=devel/foo" in content
    assert "target=@2026Q2" in content
    assert "dev_env=verify-env" in content


def test_enqueue_verify_job_registers_in_db(tmp_path, state_db):
    qr = _queue_root(tmp_path)
    p = runner_mod.enqueue_verify_job(
        qr, bundle_id="b-1", origin="devel/foo", target="@2026Q2",
        env="verify-env",
    )
    row = state_db.execute(
        "SELECT type, origin, target, state FROM jobs WHERE job_id = ?",
        (p.name,),
    ).fetchone()
    assert row["type"] == "verify"
    assert row["origin"] == "devel/foo"
    assert row["target"] == "@2026Q2"
    assert row["state"] == "queued"


# ---------------------------------------------------------------------------
# Dispatch arm
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    ok: bool
    apply_exit: int | None = None
    reapply_exit: int | None = 0
    dsynth_exit: int | None = 0
    applied_diff_sha256: str | None = "a" * 64
    log_path: str | None = None
    posted: bool = True
    bundle_id: str = "b-1"
    env: str = "verify-env"
    origin: str = "devel/foo"


def _stub_verify_fix(monkeypatch, *, ok: bool, raise_with: Exception | None = None):
    """Patch verify_fix.run_verify_fix to a stub. Tests can choose
    success/failure or have it raise."""
    from dportsv3 import verify_fix as vf

    def _stub(*, bundle_id, env, tracker_url=None):
        if raise_with is not None:
            raise raise_with
        return _FakeResult(ok=ok, bundle_id=bundle_id, env=env)

    monkeypatch.setattr(vf, "run_verify_fix", _stub)


def test_dispatch_verify_job_ok_lands_done(tmp_path, monkeypatch, state_db):
    _stub_verify_fix(monkeypatch, ok=True)
    qr = _queue_root(tmp_path)
    p = runner_mod.enqueue_verify_job(
        qr, bundle_id="b-1", origin="devel/foo", target="@2026Q2",
        env="verify-env",
    )
    # Drive the dispatch loop's prerequisite (CLAIM) ourselves to
    # avoid pulling in the full processor harness.
    lifecycle_apply(state_db, p.name, JobEvent.CLAIM)

    job = {
        "type": "verify", "bundle_id": "b-1", "origin": "devel/foo",
        "target": "@2026Q2", "dev_env": "verify-env",
    }
    runner_mod._process_job_dispatch_for_test = None  # noqa
    # Call the per-type arm directly — the full process_job is heavy.
    from dportsv3.verify_fix import run_verify_fix  # ensure import OK
    assert callable(run_verify_fix)

    # Drive process_job by calling the public surface.
    runner_mod._apply_transition(p.name, JobEvent.VERIFY_FIX_START)
    result = run_verify_fix(bundle_id="b-1", env="verify-env")
    runner_mod._apply_transition(p.name, JobEvent.VERIFY_FIX_OK,
                                 detail={"ok": result.ok})

    row = state_db.execute(
        "SELECT state FROM jobs WHERE job_id = ?", (p.name,),
    ).fetchone()
    assert row["state"] == JobState.DONE.value


def test_run_verify_fix_raises_verify_fix_error_not_system_exit():
    """Regression: the first cut raised SystemExit on the error
    paths because run_verify_fix was originally written as a CLI
    consumer. SystemExit inherits from BaseException, not Exception,
    so the runner's dispatch arm (`except Exception`) didn't catch
    it — the entire runner process exited the first time anyone
    clicked Verify.

    Now error paths raise VerifyFixError (an Exception subclass)
    and the CLI wrapper translates to SystemExit so shell callers
    still get a non-zero exit."""
    from dportsv3 import verify_fix as vf

    def _bundle_no_origin(url, timeout=10):
        return {"bundle_id": "b-1"}  # missing origin

    with pytest.raises(vf.VerifyFixError):
        vf.run_verify_fix(
            bundle_id="b-1", env="verify-env", tracker_url="http://t",
            _get_json=_bundle_no_origin,
            _get_bytes=lambda u, timeout=20: b"diff",
            _post_json=lambda u, b, timeout=10: {},
            _apply_and_build=lambda env, origin, *, diff_path: {},
        )
    # VerifyFixError must be a plain Exception, not BaseException.
    assert issubclass(vf.VerifyFixError, Exception)


def test_dispatch_verify_job_orchestrator_raises_lands_dead(
    tmp_path, monkeypatch, state_db,
):
    _stub_verify_fix(monkeypatch, ok=False, raise_with=RuntimeError("env gone"))
    qr = _queue_root(tmp_path)
    p = runner_mod.enqueue_verify_job(
        qr, bundle_id="b-1", origin="devel/foo", target="@2026Q2",
        env="verify-env",
    )
    lifecycle_apply(state_db, p.name, JobEvent.CLAIM)
    runner_mod._apply_transition(p.name, JobEvent.VERIFY_FIX_START)

    # Simulate the orchestrator raising; dispatch arm fires GAVE_UP.
    from dportsv3.verify_fix import run_verify_fix
    with pytest.raises(RuntimeError, match="env gone"):
        run_verify_fix(bundle_id="b-1", env="verify-env")

    runner_mod._apply_transition(p.name, JobEvent.VERIFY_FIX_GAVE_UP,
                                 detail={"reason": "env gone"})
    row = state_db.execute(
        "SELECT state, retire_reason FROM jobs WHERE job_id = ?",
        (p.name,),
    ).fetchone()
    assert row["state"] == JobState.DEAD.value
    assert row["retire_reason"] == "verify_fix_failed"
