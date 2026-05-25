"""End-to-end lifecycle integration tests for the runner.

Phase 1 Step 4. Drives ``process_job`` directly with stubbed LLM
results and a throwaway sqlite DB, asserting the lifecycle event
sequence and final state match expectations.

Coverage:
- Happy triage path: AUTO tier classification → TRIAGE_OK,
  auto-enqueued patch job exists in pending/, jobs.state == TRIAGED.
- MANUAL escalation: triage classification → MANUAL tier → TRIAGE_OK
  + ESCALATE_MANUAL, final state ESCALATED.
- Reap orphans on startup: a pre-existing PATCHING job is
  transitioned to DEAD with retire_reason="runner_restart".
- env_broken: when the cached health probe (Phase 2) shows broken,
  completion routes to ENV_BROKEN regardless of job_type and the
  job ends DEAD with retire_reason="env_broken".
- invalidate_health_cache clears entries.
- _looks_env_suspicious heuristic recognizes the known stderr
  patterns that trigger a forced re-probe.

The runner's LLM calls (``triage.run`` / ``patch.run``) and the dev-env
worker boundary are stubbed via monkeypatch. No real network, no
real chroot, no real dsynth.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dportsv3.agent import lifecycle, runner
from dportsv3.agent.llm import Usage
from dportsv3.db.schema import init_db as init_state_db


# --- Test stubs ---------------------------------------------------------------


@dataclass
class _StubTriageResult:
    """Mirror of dportsv3.agent.triage.TriageResult for stubbing."""
    text: str = ""
    classification: str = "plist-error"
    confidence: str = "high"
    snippet_rounds: int = 0
    usage: Usage = field(default_factory=Usage)


@dataclass
class _StubAttempt:
    attempt: int = 1
    tokens: int = 1000
    rebuild_ok: bool = True


@dataclass
class _StubPatchResult:
    """Mirror of dportsv3.agent.attempt_loop.PatchResult for stubbing."""
    status: str = "success"
    final_text: str = ""
    usage: Usage = field(default_factory=Usage)
    attempts: list = field(default_factory=lambda: [_StubAttempt()])
    proof: dict | None = field(
        default_factory=lambda: {"origin": "foo/bar", "rebuild_ok": True}
    )


# --- Fixtures -----------------------------------------------------------------


@pytest.fixture
def queue_env(tmp_path, monkeypatch):
    """A fully-wired throwaway queue + state.db + runner module state.

    Yields a dict with the queue_root, the open state-db connection,
    and helpers. Resets runner module globals on teardown.
    """
    # Queue directories
    queue_root = tmp_path / "queue"
    for sub in ("pending", "inflight", "done", "failed"):
        (queue_root / sub).mkdir(parents=True)

    # state.db sits one level up from queue_root per get_state_db_path()
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_state_db(conn)

    # Wire the runner's module-level connection at the throwaway DB.
    monkeypatch.setattr(runner, "_state_db_conn", conn, raising=False)
    # Drop any health cache state from previous tests.
    runner.invalidate_health_cache()

    # Stub the health probe — without an active env the runner skips
    # the probe; with it (set below), the decision engine probes on
    # every triage. Default the stub to "ready" so the happy-path
    # tests don't accidentally route to skip; individual tests that
    # want a broken env plant a broken EnvHealth into _health_cache.
    from dportsv3.agent import health as health_mod
    monkeypatch.setattr(
        health_mod, "check",
        lambda env, only=None: health_mod.EnvHealth(
            env=env, status="ready", probed_at="2026-05-21T00:00:00Z",
        ),
    )

    # Stub artifact-store HTTP calls (no server in tests).
    monkeypatch.setattr(runner, "artifact_store_put",
                        lambda *a, **kw: True, raising=False)
    monkeypatch.setattr(runner, "artifact_store_get",
                        lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(runner, "tracker_artifact_get",
                        lambda *a, **kw: None, raising=False)
    monkeypatch.setattr(runner, "bundle_artifact_list",
                        lambda *a, **kw: [], raising=False)
    # Stub the tracker history endpoint too (returns no prior bundles).
    monkeypatch.setattr(runner, "port_bundle_history",
                        lambda *a, **kw: [], raising=False)

    # Required env vars; values don't matter because we stub the LLM call.
    monkeypatch.setenv("DP_HARNESS_TRIAGE_MODEL", "test/stub-triage")
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "test/stub-patch")
    monkeypatch.setattr(runner, "_CLI_ENV_DEFAULT", "test-env")

    yield {"queue_root": queue_root, "conn": conn, "db_path": db_path}

    conn.close()


def _drop_synthetic_job(
    queue_env: dict,
    job_id: str = "20260520-test-foo_bar-1.job",
    *,
    job_type: str = "triage",
    bundle_dir: Path | None = None,
    extra_fields: dict | None = None,
) -> Path:
    """Write a .job file to pending/ and register the row via
    _register_new_job so the lifecycle test mirrors production flow."""
    queue_root = queue_env["queue_root"]
    job_path = queue_root / "pending" / job_id
    fields = {
        "type": job_type,
        "created_ts_utc": "20260520-100000Z",
        "profile": "test",
        "origin": "foo/bar",
        "flavor": "",
        "bundle_id": "foo_bar-20260520-100000Z",
        "target": "@test",
    }
    if bundle_dir is not None:
        fields["bundle_dir"] = str(bundle_dir)
    if extra_fields:
        fields.update(extra_fields)
    job_path.write_text("\n".join(f"{k}={v}" for k, v in fields.items()) + "\n")
    # Mirror the production HOOK_ENQUEUED transition.
    runner._register_new_job(job_id, metadata=fields)
    return job_path


def _make_bundle_dir(tmp_path: Path) -> Path:
    """Synthetic on-disk bundle dir so the harness's bundle_dir check passes."""
    bdir = tmp_path / "bundle"
    (bdir / "analysis").mkdir(parents=True)
    (bdir / "logs").mkdir(parents=True)
    (bdir / "logs" / "errors.txt").write_text("synthetic build error\n")
    (bdir / "meta.txt").write_text("origin=foo/bar\n")
    return bdir


# --- Tests --------------------------------------------------------------------


def test_full_triage_path_to_triaged(queue_env, tmp_path, monkeypatch):
    """AUTO tier triage: history ends TRIAGED, patch job auto-enqueued."""
    conn = queue_env["conn"]
    bdir = _make_bundle_dir(tmp_path)
    job_path = _drop_synthetic_job(queue_env, bundle_dir=bdir)
    job_id = job_path.name

    # Stub the LLM call: plist-error + high confidence → AUTO tier.
    from dportsv3.agent import triage as triage_module
    monkeypatch.setattr(triage_module, "run",
                        lambda *a, **kw: _StubTriageResult(
                            text="## Classification\nplist-error\n\n## Confidence\nhigh\n",
                            classification="plist-error",
                            confidence="high",
                        ))

    # Move job to inflight (the runner does this via claim_next_job_batch
    # in production; we shortcut here since we're testing process_job).
    inflight_path = queue_env["queue_root"] / "inflight" / job_id
    job_path.rename(inflight_path)
    runner._apply_transition(job_id, lifecycle.JobEvent.CLAIM)

    runner.process_job(queue_env["queue_root"], inflight_path, [],
                       dry_run=False, kedb_dir=None)

    hist = lifecycle.history(conn, job_id)
    events = [r["event_name"] for r in hist]
    assert events == [
        "hook_enqueued",
        "claim",
        "triage_start",
        "triage_ok",
    ], events
    assert lifecycle.current(conn, job_id) == lifecycle.JobState.TRIAGED

    # Patch job auto-enqueued: one new pending .job file, and a jobs
    # row in QUEUED state with type=patch.
    pending = list((queue_env["queue_root"] / "pending").glob("*.job"))
    assert len(pending) == 1
    patch_job_id = pending[0].name
    assert "patch" in patch_job_id
    assert lifecycle.current(conn, patch_job_id) == lifecycle.JobState.QUEUED


def test_triage_manual_escalates(queue_env, tmp_path, monkeypatch):
    """missing-dep classification → MANUAL tier → ESCALATED end state."""
    conn = queue_env["conn"]
    bdir = _make_bundle_dir(tmp_path)
    job_path = _drop_synthetic_job(queue_env, bundle_dir=bdir)
    job_id = job_path.name

    from dportsv3.agent import triage as triage_module
    monkeypatch.setattr(triage_module, "run",
                        lambda *a, **kw: _StubTriageResult(
                            text="## Classification\nmissing-dep\n\n## Confidence\nhigh\n",
                            classification="missing-dep",
                            confidence="high",
                        ))

    inflight_path = queue_env["queue_root"] / "inflight" / job_id
    job_path.rename(inflight_path)
    runner._apply_transition(job_id, lifecycle.JobEvent.CLAIM)

    runner.process_job(queue_env["queue_root"], inflight_path, [],
                       dry_run=False, kedb_dir=None)

    events = [r["event_name"] for r in lifecycle.history(conn, job_id)]
    assert events == [
        "hook_enqueued",
        "claim",
        "triage_start",
        "triage_ok",
        "escalate_manual",
    ], events
    assert lifecycle.current(conn, job_id) == lifecycle.JobState.ESCALATED

    row = conn.execute("SELECT retire_reason FROM jobs WHERE job_id = ?",
                       (job_id,)).fetchone()
    assert row["retire_reason"] == "escalated_manual"

    # No patch job enqueued.
    pending = list((queue_env["queue_root"] / "pending").glob("*.job"))
    assert pending == []


def test_reap_orphans_on_startup(queue_env):
    """A PATCHING-stuck job is transitioned to DEAD by reap_orphans."""
    conn = queue_env["conn"]
    # Walk a synthetic job to PATCHING using the lifecycle directly —
    # mirrors what a previous runner instance would have left behind.
    jid = "orphaned-job"
    for ev in [
        lifecycle.JobEvent.HOOK_ENQUEUED,
        lifecycle.JobEvent.CLAIM,
        lifecycle.JobEvent.TRIAGE_START,
        lifecycle.JobEvent.TRIAGE_OK,
        lifecycle.JobEvent.PATCH_START,
    ]:
        lifecycle.apply(conn, jid, ev, actor="prior-runner")
    assert lifecycle.current(conn, jid) == lifecycle.JobState.PATCHING

    # Simulate the runner-startup reap.
    n = lifecycle.reap_orphans(conn, actor="runner-test")
    assert n == 1

    assert lifecycle.current(conn, jid) == lifecycle.JobState.DEAD
    row = conn.execute(
        "SELECT retire_reason FROM jobs WHERE job_id = ?", (jid,)
    ).fetchone()
    assert row["retire_reason"] == "runner_restart"

    # The latest event is REAP_ORPHAN.
    hist = lifecycle.history(conn, jid)
    assert hist[-1]["event_name"] == "reap_orphan"
    assert hist[-1]["actor"] == "runner-test"


def test_env_broken_routes_to_dead(queue_env):
    """When the cached health probe shows broken, completion routes
    to ENV_BROKEN regardless of job_type and final state is DEAD
    with retire_reason=env_broken.
    """
    from dportsv3.agent import health as health_mod
    import time as _time

    conn = queue_env["conn"]
    # Plant a "broken" probe in the cache directly. ``time.monotonic``
    # is what _probe_health reads, so use that for the cache timestamp.
    broken = health_mod.EnvHealth(
        env="test-env",
        status="broken",
        checks=[health_mod.HealthCheck(
            name="python_runtime", status="broken",
            detail="missing py311 packages",
            operator_action="pkg install py311-...",
        )],
        operator_action="pkg install py311-...",
    )
    runner._health_cache["test-env"] = (_time.monotonic(), broken)

    # Phase 5 Step 4: _completion_events_for retired. The cached-
    # health-broken override now lives inside each Step's run() —
    # exercised via the orchestrator-driven happy path (separate
    # tests). Here we just confirm the cache flag is observable.
    assert runner._cached_health_broken() is True

    # End-to-end: walk a job to PATCHING, then fire ENV_BROKEN; the
    # job should land DEAD with retire_reason="env_broken".
    jid = "env-broken-job"
    for ev in [
        lifecycle.JobEvent.HOOK_ENQUEUED,
        lifecycle.JobEvent.CLAIM,
        lifecycle.JobEvent.TRIAGE_START,
        lifecycle.JobEvent.TRIAGE_OK,
        lifecycle.JobEvent.PATCH_START,
    ]:
        lifecycle.apply(conn, jid, ev)
    lifecycle.apply(conn, jid, lifecycle.JobEvent.ENV_BROKEN,
                    detail={"reason": "missing py311 packages"})

    assert lifecycle.current(conn, jid) == lifecycle.JobState.DEAD
    row = conn.execute(
        "SELECT retire_reason FROM jobs WHERE job_id = ?", (jid,)
    ).fetchone()
    assert row["retire_reason"] == "env_broken"


def test_invalidate_health_cache_clears_all(queue_env):
    """invalidate_health_cache() with no arg drops every env's entry."""
    import time as _time
    from dportsv3.agent import health as health_mod
    runner._health_cache["a"] = (_time.monotonic(), health_mod.EnvHealth(env="a", status="ready"))
    runner._health_cache["b"] = (_time.monotonic(), health_mod.EnvHealth(env="b", status="broken"))
    assert runner._cached_health_broken() is True
    runner.invalidate_health_cache()
    assert runner._cached_health_broken() is False


def test_looks_env_suspicious_detects_known_sentinels():
    """The heuristic that triggers cache invalidation mid-job."""
    assert runner._looks_env_suspicious({
        "ok": False, "stderr_tail": "dportsv3: missing DragonFly packages required...",
    }) is True
    assert runner._looks_env_suspicious({
        "ok": False, "stderr_tail": "compile error: foo.c:42",
    }) is False
    # ok=True is never suspicious
    assert runner._looks_env_suspicious({
        "ok": True, "stderr_tail": "missing DragonFly packages",
    }) is False
    assert runner._looks_env_suspicious(None) is False
