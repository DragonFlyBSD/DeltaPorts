"""Unit tests for PatchAttemptStep precheck logic.

Phase 5 Substep 3b. Covers the new precheck branches:

- Model resolution: PATCH_MODEL → TRIAGE_MODEL fallback (with WARN
  log); both unset → fail.
- dev_env resolution: from job field, from DP_HARNESS_ENV; absent
  in both → fail.
- Tier resolution from ``job['tier']`` happy path.
- Tier resolution via decide() fallback for hand-fired jobs
  (no tier field); confirms decide() is called with empty
  PortHistory + None env_health (legacy tier_for semantics).
- Policy load failure surfaces as precheck-fail.

End-to-end patch flow (run + record) is covered by the existing
test_runner_e2e_lifecycle suite that drives process_patch_job.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from dportsv3.agent.policy import Policy, Tier
from dportsv3.agent.step import StepCtx
from dportsv3.agent.steps import PatchAttemptStep, PatchServices


# --- helpers ------------------------------------------------------------------


def _policy() -> Policy:
    """Minimal policy with AUTO/ASSIST/MANUAL."""
    return Policy(
        tiers={
            "AUTO":   Tier(name="AUTO",   max_iterations=2, max_tokens=30000),
            "ASSIST": Tier(name="ASSIST", max_iterations=4, max_tokens=120000),
            "MANUAL": Tier(name="MANUAL", max_iterations=0, max_tokens=0),
        },
        classification_to_tier={
            "plist-error":   "AUTO",
            "compile-error": "ASSIST",
            "missing-dep":   "MANUAL",
        },
        confidence_floor={"AUTO": "high", "ASSIST": "medium"},
    )


@dataclass
class _LogRec:
    entries: list = None

    def __post_init__(self):
        if self.entries is None:
            self.entries = []

    def __call__(self, queue_root, level, message):
        self.entries.append((level, message))


def _ctx(tmp_path, job=None, *, helpers_overrides=None, bundle_text=None):
    job = job or {"origin": "devel/foo", "bundle_id": "b-1"}
    log_rec = _LogRec()
    from dportsv3.agent.runner import parse_triage_output

    services_kwargs = {
        "log": log_rec,
        "read_bundle_text": (lambda bd, bid, rp: bundle_text)
                              if bundle_text is not None
                              else (lambda bd, bid, rp: None),
        "parse_triage_output": parse_triage_output,
        # Stubbed I/O helpers; precheck never calls these.
        "write_error_note": lambda *a, **kw: None,
        "write_patch_audit": lambda *a, **kw: None,
        "write_tool_trace": lambda *a, **kw: None,
        "write_changes_diff": lambda *a, **kw: None,
        "looks_env_suspicious": lambda res: False,
        "invalidate_health_cache": lambda: None,
        "cached_health_broken": lambda env=None: False,
        "summarize_tool_call": lambda t, a, r: "",
        "activity_log": lambda *a, **kw: None,
        "load_port_history": lambda t, o, w: None,
    }
    if helpers_overrides:
        services_kwargs.update(helpers_overrides)
    ctx = StepCtx(job_id="job-x", job=job, queue_root=tmp_path)
    ctx.state["job_path"] = tmp_path / "job-x"
    ctx.state["payload"] = "(payload)"
    ctx.state["origin"] = job.get("origin", "?")
    ctx.state["services"] = PatchServices(**services_kwargs)
    return ctx, log_rec


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Each test starts with a clean DP_HARNESS_* env var set."""
    for var in ("DP_HARNESS_PATCH_MODEL", "DP_HARNESS_TRIAGE_MODEL",
                "DP_HARNESS_ENV", "DP_HARNESS_POLICY"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def policy_file(tmp_path, monkeypatch):
    import json
    p = tmp_path / "policy.json"
    p.write_text(json.dumps({
        "tiers": {
            "AUTO":   {"max_iterations": 2, "max_tokens": 30000},
            "ASSIST": {"max_iterations": 4, "max_tokens": 120000},
            "MANUAL": {},
        },
        "classification_to_tier": {
            "plist-error":   "AUTO",
            "compile-error": "ASSIST",
            "missing-dep":   "MANUAL",
        },
        "confidence_floor": {"AUTO": "high", "ASSIST": "medium"},
    }))
    return p


# --- model resolution -------------------------------------------------------


def test_precheck_fails_when_no_models_set(tmp_path, policy_file, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, _ = _ctx(tmp_path, job={"tier": "AUTO", "origin": "devel/foo"})
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "fail"
    assert "neither DP_HARNESS_PATCH_MODEL nor DP_HARNESS_TRIAGE_MODEL" in readiness.reason


def test_precheck_uses_patch_model(tmp_path, policy_file, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub-patch")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, log_rec = _ctx(tmp_path, job={"tier": "AUTO", "origin": "devel/foo"})
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    assert ctx.state["model"] == "stub-patch"
    # No WARN log when patch model is set.
    assert log_rec.entries == []


def test_precheck_falls_back_to_triage_model_with_warn(tmp_path, policy_file, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_TRIAGE_MODEL", "stub-triage")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, log_rec = _ctx(tmp_path, job={"tier": "AUTO", "origin": "devel/foo"})
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    assert ctx.state["model"] == "stub-triage"
    # Exactly one WARN log line about the fallback.
    warn_msgs = [m for level, m in log_rec.entries if level == "WARN"]
    assert len(warn_msgs) == 1
    assert "falling back to triage model" in warn_msgs[0]
    assert "stub-triage" in warn_msgs[0]


# --- dev_env resolution -----------------------------------------------------


def test_precheck_dev_env_from_job_field(tmp_path, policy_file, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    ctx, _ = _ctx(tmp_path, job={
        "tier": "AUTO", "origin": "devel/foo", "dev_env": "from-job-env",
    })
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    assert ctx.state["env"] == "from-job-env"


def test_precheck_dev_env_from_envvar(tmp_path, policy_file, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    monkeypatch.setenv("DP_HARNESS_ENV", "from-envvar")
    ctx, _ = _ctx(tmp_path, job={"tier": "AUTO", "origin": "devel/foo"})
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    assert ctx.state["env"] == "from-envvar"


def test_precheck_fails_without_dev_env(tmp_path, policy_file, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    ctx, _ = _ctx(tmp_path, job={"tier": "AUTO", "origin": "devel/foo"})
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "fail"
    assert "missing dev_env" in readiness.reason


# --- tier resolution --------------------------------------------------------


def test_precheck_tier_from_job_field(tmp_path, policy_file, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, _ = _ctx(tmp_path, job={
        "tier": "ASSIST", "origin": "devel/foo",
    })
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    assert ctx.state["tier"].name == "ASSIST"


def test_precheck_tier_fallback_to_decide_for_hand_fired_job(
    tmp_path, policy_file, monkeypatch,
):
    """No 'tier' field → parse triage.md → decide() resolves it.
    Empty history + None env_health → legacy tier_for semantics."""
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")

    triage_md = (
        "## Classification\nplist-error\n\n"
        "## Confidence\nhigh\n\n"
        "## Root Cause\nsynthetic\n"
    )
    ctx, _ = _ctx(
        tmp_path,
        job={"origin": "devel/foo", "bundle_id": "b-1"},  # no 'tier'
        bundle_text=triage_md,
    )
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    # plist-error + high → AUTO
    assert ctx.state["tier"].name == "AUTO"


def test_precheck_tier_fallback_with_missing_triage_md_lands_at_manual(
    tmp_path, policy_file, monkeypatch,
):
    """Hand-fired patch with no triage.md → empty classification
    → unknown → MANUAL via the policy default."""
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, _ = _ctx(
        tmp_path,
        job={"origin": "devel/foo", "bundle_id": "b-1"},
        bundle_text=None,  # missing
    )
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    assert ctx.state["tier"].name == "MANUAL"


def test_precheck_tier_fallback_bogus_classification_lands_at_manual(
    tmp_path, policy_file, monkeypatch,
):
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, _ = _ctx(
        tmp_path,
        job={"origin": "devel/foo"},
        bundle_text="## Classification\nnovel-error\n\n## Confidence\nhigh\n",
    )
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    assert ctx.state["tier"].name == "MANUAL"


def test_precheck_unknown_tier_string_falls_back_to_decide(
    tmp_path, policy_file, monkeypatch,
):
    """If job['tier'] is set but not a known tier name, fall back
    to parsing triage.md (don't crash, don't silently accept)."""
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, _ = _ctx(
        tmp_path,
        job={"tier": "TOTALLY_NEW_TIER", "origin": "devel/foo"},
        bundle_text="## Classification\nplist-error\n\n## Confidence\nhigh\n",
    )
    ctx.state["policy_path"] = str(policy_file)
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "ready"
    assert ctx.state["tier"].name == "AUTO"


# --- policy load -------------------------------------------------------------


def test_precheck_fails_when_policy_path_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, _ = _ctx(tmp_path, job={"tier": "AUTO", "origin": "devel/foo"})
    # Intentionally don't set ctx.state["policy_path"]
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "fail"
    assert "policy_path missing" in readiness.reason


def test_precheck_fails_when_policy_file_unreadable(tmp_path, monkeypatch):
    monkeypatch.setenv("DP_HARNESS_PATCH_MODEL", "stub")
    monkeypatch.setenv("DP_HARNESS_ENV", "test-env")
    ctx, _ = _ctx(tmp_path, job={"tier": "AUTO", "origin": "devel/foo"})
    ctx.state["policy_path"] = "/nonexistent/policy.json"
    readiness = PatchAttemptStep().precheck(ctx)
    assert readiness.status == "fail"
    assert "failed to load harness policy" in readiness.reason
