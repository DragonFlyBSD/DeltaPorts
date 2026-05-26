"""End-to-end orchestrator parity test.

Phase 5 Step 5. Walks full job flows through ``process_job`` with
stubbed LLM + worker boundaries, asserting that the orchestrator-
driven path produces the same lifecycle event sequences the
hand-coded ``_completion_events_for`` produced pre-Phase-5.

Covers:
- Patch happy path: success → PATCH_OK + VERIFY_OK → DONE.
- Patch budget exhausted: → PATCH_BUDGET_OUT → DEAD.
- Patch gave-up: → PATCH_GAVE_UP → DEAD.
- Patch sibling fan-out: lead + sibling get identical event sequences.
- Patch precheck halt: no models set → catchall PATCH_GAVE_UP fires
  for lead + siblings.
- Triage cached-health-broken override: forces ENV_BROKEN even
  when triage itself succeeded.

The existing test_runner_e2e_lifecycle.py covers the simpler
triage paths (auto_patch enqueue, manual escalate). This file
extends coverage to the patch flow + sibling fan-out + the
precheck-halt path, all through the orchestrator.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from dportsv3.agent import lifecycle, runner
from dportsv3.agent.llm import Usage
from dportsv3.db.schema import init_db as init_state_db


# --- stubs -------------------------------------------------------------------


@dataclass
class _StubAttempt:
    attempt: int = 1
    tokens: int = 1000
    rebuild_ok: bool = True


@dataclass
class _StubPatchResult:
    status: str = "success"
    final_text: str = ""
    usage: Usage = field(default_factory=Usage)
    attempts: list = field(default_factory=lambda: [_StubAttempt()])
    proof: dict | None = field(
        default_factory=lambda: {"origin": "foo/bar", "rebuild_ok": True}
    )


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def queue_env(tmp_path, monkeypatch):
    """Same shape as test_runner_e2e_lifecycle but with patch-flow
    stubs wired in."""
    queue_root = tmp_path / "queue"
    for sub in ("pending", "inflight", "done", "failed"):
        (queue_root / sub).mkdir(parents=True)

    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_state_db(conn)

    monkeypatch.setattr(runner, "_state_db_conn", conn, raising=False)
    runner.invalidate_health_cache()

    # Stub artifact-store & tracker network calls.
    monkeypatch.setattr(runner, "artifact_store_put", lambda *a, **kw: True)
    monkeypatch.setattr(runner, "artifact_store_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "tracker_artifact_get", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "bundle_artifact_list", lambda *a, **kw: [])
    monkeypatch.setattr(runner, "port_bundle_history", lambda *a, **kw: [])

    # Stub the worker's env path resolution (the patch flow's
    # _write_changes_diff calls it via git -C).
    from dportsv3.agent import worker

    @dataclass
    class _Paths:
        env_dir: Path = field(default_factory=lambda: tmp_path / "env")
        writable: Path = field(default_factory=lambda: tmp_path / "env" / "writable")

        @property
        def deltaports(self) -> Path:
            return self.writable / "work" / "DeltaPorts"

    monkeypatch.setattr(worker, "env_paths", lambda env: _Paths())

    # Stub a healthy env so the gate doesn't pause + decide() proceeds.
    from dportsv3.agent import health as health_mod
    monkeypatch.setattr(
        health_mod, "check",
        lambda env, only=None: health_mod.EnvHealth(
            env=env, status="ready", probed_at="2026-05-21T00:00:00Z",
        ),
    )

    monkeypatch.setenv("DP_HARNESS_TRIAGE_MODEL", "stub-triage")
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub-patch")
    monkeypatch.setattr(runner, "_CLI_ENV_DEFAULT", "test-env")

    yield {"queue_root": queue_root, "conn": conn, "tmp_path": tmp_path}
    conn.close()


def _make_bundle_dir(tmp_path: Path) -> Path:
    bdir = tmp_path / "bundle"
    (bdir / "analysis").mkdir(parents=True, exist_ok=True)
    (bdir / "logs").mkdir(parents=True, exist_ok=True)
    (bdir / "logs" / "errors.txt").write_text("synthetic error\n")
    (bdir / "meta.txt").write_text("origin=foo/bar\n")
    (bdir / "analysis" / "triage.md").write_text(
        "## Classification\nplist-error\n\n"
        "## Confidence\nhigh\n\n"
        "## Suggested Fix\nadd a thing\n"
    )
    return bdir


def _drop_patch_job(queue_env: dict, *, job_id: str, bundle_dir: Path,
                    tier: str = "AUTO", extra: dict | None = None) -> Path:
    queue_root = queue_env["queue_root"]
    job_path = queue_root / "pending" / job_id
    fields = {
        "type": "patch",
        "created_ts_utc": "20260521-100000Z",
        "profile": "test",
        "origin": "foo/bar",
        "flavor": "",
        "bundle_id": f"bundle-{job_id}",
        "tier": tier,
        "dev_env": "test-env",
        "bundle_dir": str(bundle_dir),
        "target": "@test",
    }
    if extra:
        fields.update(extra)
    job_path.write_text("\n".join(f"{k}={v}" for k, v in fields.items()) + "\n")
    runner._register_new_job(job_id, metadata=fields)
    return job_path


def _claim(queue_env: dict, job_path: Path) -> Path:
    """Move .job file to inflight + fire CLAIM event."""
    inflight = queue_env["queue_root"] / "inflight" / job_path.name
    job_path.rename(inflight)
    runner._apply_transition(job_path.name, lifecycle.JobEvent.CLAIM)
    return inflight


# --- patch happy path -------------------------------------------------------


def test_patch_success_full_chain(queue_env, tmp_path, monkeypatch):
    """Stubbed patch returns status=success → PATCH_OK + VERIFY_OK
    fire → final state DONE."""
    from dportsv3.agent import patch as patch_module

    bdir = _make_bundle_dir(tmp_path)
    monkeypatch.setattr(patch_module, "run",
                        lambda *a, **kw: _StubPatchResult(status="success"))

    job_path = _drop_patch_job(queue_env, job_id="job-patch-1.job", bundle_dir=bdir)
    inflight = _claim(queue_env, job_path)
    runner.process_job(queue_env["queue_root"], inflight, [],
                       dry_run=False, playbooks_dir=None)

    events = [r["event_name"]
              for r in lifecycle.history(queue_env["conn"], "job-patch-1.job")]
    assert events == [
        "hook_enqueued", "claim", "patch_start", "patch_ok", "verify_ok",
    ], events
    assert (lifecycle.current(queue_env["conn"], "job-patch-1.job")
            == lifecycle.JobState.DONE)


def test_patch_budget_exhausted(queue_env, tmp_path, monkeypatch):
    from dportsv3.agent import patch as patch_module

    bdir = _make_bundle_dir(tmp_path)
    monkeypatch.setattr(
        patch_module, "run",
        lambda *a, **kw: _StubPatchResult(status="budget-exhausted"),
    )

    job_path = _drop_patch_job(queue_env, job_id="job-budget.job", bundle_dir=bdir)
    inflight = _claim(queue_env, job_path)
    runner.process_job(queue_env["queue_root"], inflight, [],
                       dry_run=False, playbooks_dir=None)

    events = [r["event_name"]
              for r in lifecycle.history(queue_env["conn"], "job-budget.job")]
    assert events == [
        "hook_enqueued", "claim", "patch_start", "patch_budget_out",
    ], events
    row = queue_env["conn"].execute(
        "SELECT retire_reason FROM jobs WHERE job_id = ?", ("job-budget.job",)
    ).fetchone()
    assert row["retire_reason"] == "patch_budget_exhausted"


def test_patch_gave_up(queue_env, tmp_path, monkeypatch):
    from dportsv3.agent import patch as patch_module

    bdir = _make_bundle_dir(tmp_path)
    monkeypatch.setattr(
        patch_module, "run",
        lambda *a, **kw: _StubPatchResult(status="needs-help"),
    )

    job_path = _drop_patch_job(queue_env, job_id="job-help.job", bundle_dir=bdir)
    inflight = _claim(queue_env, job_path)
    runner.process_job(queue_env["queue_root"], inflight, [],
                       dry_run=False, playbooks_dir=None)

    events = [r["event_name"]
              for r in lifecycle.history(queue_env["conn"], "job-help.job")]
    assert events == [
        "hook_enqueued", "claim", "patch_start", "patch_gave_up",
    ]


# --- sibling fan-out ---------------------------------------------------------


def test_patch_sibling_fan_out(queue_env, tmp_path, monkeypatch):
    """A patch job with one sibling. Both lead and sibling should
    receive the same lifecycle events (patch_start, patch_ok,
    verify_ok)."""
    from dportsv3.agent import patch as patch_module

    bdir = _make_bundle_dir(tmp_path)
    monkeypatch.setattr(patch_module, "run",
                        lambda *a, **kw: _StubPatchResult(status="success"))

    lead_path = _drop_patch_job(queue_env, job_id="lead.job", bundle_dir=bdir)
    sib_path = _drop_patch_job(queue_env, job_id="sib.job", bundle_dir=bdir)
    lead_inflight = _claim(queue_env, lead_path)
    sib_inflight = _claim(queue_env, sib_path)

    runner.process_job(queue_env["queue_root"], lead_inflight, [sib_inflight],
                       dry_run=False, playbooks_dir=None)

    lead_events = [r["event_name"]
                   for r in lifecycle.history(queue_env["conn"], "lead.job")]
    sib_events = [r["event_name"]
                  for r in lifecycle.history(queue_env["conn"], "sib.job")]

    expected = ["hook_enqueued", "claim", "patch_start", "patch_ok", "verify_ok"]
    assert lead_events == expected, lead_events
    assert sib_events == expected, sib_events

    assert (lifecycle.current(queue_env["conn"], "lead.job")
            == lifecycle.JobState.DONE)
    assert (lifecycle.current(queue_env["conn"], "sib.job")
            == lifecycle.JobState.DONE)


def test_patch_sibling_fan_out_on_failure(queue_env, tmp_path, monkeypatch):
    """Sibling should mirror the lead's failure event too."""
    from dportsv3.agent import patch as patch_module

    bdir = _make_bundle_dir(tmp_path)
    monkeypatch.setattr(
        patch_module, "run",
        lambda *a, **kw: _StubPatchResult(status="budget-exhausted"),
    )

    lead_path = _drop_patch_job(queue_env, job_id="lead-fail.job", bundle_dir=bdir)
    sib_path = _drop_patch_job(queue_env, job_id="sib-fail.job", bundle_dir=bdir)
    lead_inflight = _claim(queue_env, lead_path)
    sib_inflight = _claim(queue_env, sib_path)

    runner.process_job(queue_env["queue_root"], lead_inflight, [sib_inflight],
                       dry_run=False, playbooks_dir=None)

    expected = ["hook_enqueued", "claim", "patch_start", "patch_budget_out"]
    for jid in ("lead-fail.job", "sib-fail.job"):
        events = [r["event_name"]
                  for r in lifecycle.history(queue_env["conn"], jid)]
        assert events == expected, (jid, events)


# --- precheck halt ----------------------------------------------------------


def test_patch_precheck_halt_synthesizes_failure_event(
    queue_env, tmp_path, monkeypatch,
):
    """When precheck fails (e.g. no model env vars set), the
    orchestrator halts without firing events; the wrapper
    synthesizes PATCH_GAVE_UP for lead + siblings."""
    bdir = _make_bundle_dir(tmp_path)
    # Force precheck failure: no model env vars.
    monkeypatch.delenv("DP_HARNESS_PATCH_MODEL", raising=False)
    monkeypatch.delenv("DP_HARNESS_TRIAGE_MODEL", raising=False)

    lead_path = _drop_patch_job(queue_env, job_id="halt-lead.job", bundle_dir=bdir)
    sib_path = _drop_patch_job(queue_env, job_id="halt-sib.job", bundle_dir=bdir)
    lead_inflight = _claim(queue_env, lead_path)
    sib_inflight = _claim(queue_env, sib_path)

    runner.process_job(queue_env["queue_root"], lead_inflight, [sib_inflight],
                       dry_run=False, playbooks_dir=None)

    expected = ["hook_enqueued", "claim", "patch_start", "patch_gave_up"]
    for jid in ("halt-lead.job", "halt-sib.job"):
        events = [r["event_name"]
                  for r in lifecycle.history(queue_env["conn"], jid)]
        assert events == expected, (jid, events)
        assert (lifecycle.current(queue_env["conn"], jid)
                == lifecycle.JobState.DEAD)


# --- triage env_broken override ---------------------------------------------


def test_triage_env_broken_override_via_step(queue_env, tmp_path, monkeypatch):
    """If the cached health probe is broken, TriageStep should
    return ENV_BROKEN as its next_event regardless of decide()'s
    action. Final state: DEAD with retire_reason=env_broken."""
    import time as _time
    from dportsv3.agent import health as health_mod
    from dportsv3.agent import triage as triage_module

    # Plant a broken EnvHealth in the runner's cache.
    runner._health_cache["test-env"] = (
        _time.monotonic(),
        health_mod.EnvHealth(env="test-env", status="broken"),
    )

    bdir = _make_bundle_dir(tmp_path)

    from dataclasses import dataclass as _dc, field as _f
    from dportsv3.agent.llm import Usage as _Usage

    @_dc
    class _Stub:
        text: str = "## Classification\nplist-error\n\n## Confidence\nhigh\n"
        classification: str = "plist-error"
        confidence: str = "high"
        snippet_rounds: int = 0
        usage: _Usage = _f(default_factory=_Usage)

    monkeypatch.setattr(triage_module, "run", lambda *a, **kw: _Stub())

    queue_root = queue_env["queue_root"]
    job_path = queue_root / "pending" / "triage-env-broken.job"
    fields = {
        "type": "triage",
        "created_ts_utc": "20260521-100000Z",
        "profile": "test",
        "origin": "foo/bar",
        "bundle_id": "b-env-broken",
        "bundle_dir": str(bdir),
        "target": "@test",
    }
    job_path.write_text("\n".join(f"{k}={v}" for k, v in fields.items()) + "\n")
    runner._register_new_job("triage-env-broken.job", metadata=fields)

    inflight = _claim(queue_env, job_path)
    runner.process_job(queue_env["queue_root"], inflight, [],
                       dry_run=False, playbooks_dir=None)

    events = [r["event_name"]
              for r in lifecycle.history(queue_env["conn"], "triage-env-broken.job")]
    # decide() returns skip (env_health.status=broken from cache);
    # TriageStep emits ENV_BROKEN as next_event.
    assert "env_broken" in events
    assert (lifecycle.current(queue_env["conn"], "triage-env-broken.job")
            == lifecycle.JobState.DEAD)
    row = queue_env["conn"].execute(
        "SELECT retire_reason FROM jobs WHERE job_id = ?",
        ("triage-env-broken.job",),
    ).fetchone()
    assert row["retire_reason"] == "env_broken"
