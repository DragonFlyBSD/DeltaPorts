"""Unit tests for the Step protocol + Orchestrator.

Phase 5 Step 1. Concrete steps (TriageStep, PatchAttemptStep) land
in Steps 2 and 3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from dportsv3.agent.lifecycle import JobEvent
from dportsv3.agent.step import (
    Orchestrator,
    OrchestratorResult,
    Step,
    StepCtx,
    StepOutcome,
    StepReadiness,
    StepResult,
)


# --- test doubles -------------------------------------------------------------


@dataclass
class _RecordingStep:
    """Step that records every hook invocation onto ctx.state."""
    name: str
    precheck_status: str = "ready"
    precheck_reason: str = ""
    outcome_status: str = "success"
    next_event: JobEvent | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def precheck(self, ctx: StepCtx) -> StepReadiness:
        ctx.state.setdefault("calls", []).append((self.name, "precheck"))
        return StepReadiness(status=self.precheck_status,  # type: ignore[arg-type]
                             reason=self.precheck_reason)

    def run(self, ctx: StepCtx) -> StepOutcome:
        ctx.state.setdefault("calls", []).append((self.name, "run"))
        return StepOutcome(
            status=self.outcome_status,  # type: ignore[arg-type]
            next_event=self.next_event,
            detail=dict(self.detail),
        )

    def record(self, ctx: StepCtx, outcome: StepOutcome) -> None:
        ctx.state.setdefault("calls", []).append((self.name, "record"))


@dataclass
class _RaisingStep:
    name: str = "raiser"
    where: str = "run"  # "precheck" | "run" | "record"

    def precheck(self, ctx: StepCtx) -> StepReadiness:
        if self.where == "precheck":
            raise ValueError("precheck-boom")
        return StepReadiness(status="ready")

    def run(self, ctx: StepCtx) -> StepOutcome:
        if self.where == "run":
            raise ValueError("run-boom")
        return StepOutcome(status="success")

    def record(self, ctx: StepCtx, outcome: StepOutcome) -> None:
        if self.where == "record":
            raise ValueError("record-boom")


# --- fixtures ----------------------------------------------------------------


@pytest.fixture
def ctx_with_recorder():
    """A StepCtx that captures apply_transition + activity_log calls
    onto a shared list so tests can assert side effects."""
    events: list[tuple[str, dict]] = []
    logs: list[tuple[str, str]] = []

    def fake_apply(job_id, event, detail=None):
        events.append((event.value if hasattr(event, "value") else str(event),
                       dict(detail or {})))
        return True

    def fake_log(queue_root, stage, message, job_id=None, duration_ms=None, extra=None):
        logs.append((stage, message))

    ctx = StepCtx(
        job_id="job-x",
        job={"origin": "devel/foo"},
        apply_transition=fake_apply,
        activity_log=fake_log,
    )
    return ctx, events, logs


# --- ordering + happy path ---------------------------------------------------


def test_steps_run_in_order(ctx_with_recorder):
    ctx, events, _ = ctx_with_recorder
    steps = [
        _RecordingStep(name="a", next_event=JobEvent.TRIAGE_OK),
        _RecordingStep(name="b", next_event=JobEvent.PATCH_OK),
    ]
    orch = Orchestrator()
    result = orch.run(ctx, steps)

    assert not result.halted
    assert [r.step_name for r in result.step_results] == ["a", "b"]
    # Each step's hooks fired in precheck → run → record order.
    assert ctx.state["calls"] == [
        ("a", "precheck"), ("a", "run"), ("a", "record"),
        ("b", "precheck"), ("b", "run"), ("b", "record"),
    ]
    # next_events fired
    assert events == [("triage_ok", {}), ("patch_ok", {})]


def test_outcome_detail_flows_to_apply_transition(ctx_with_recorder):
    ctx, events, _ = ctx_with_recorder
    step = _RecordingStep(name="a", next_event=JobEvent.TRIAGE_OK,
                          detail={"classification": "plist-error"})
    Orchestrator().run(ctx, [step])
    assert events == [("triage_ok", {"classification": "plist-error"})]


def test_no_next_event_skips_transition(ctx_with_recorder):
    ctx, events, _ = ctx_with_recorder
    step = _RecordingStep(name="a", next_event=None)
    Orchestrator().run(ctx, [step])
    assert events == []


# --- precheck routing --------------------------------------------------------


def test_precheck_skip_advances_to_next(ctx_with_recorder):
    ctx, events, _ = ctx_with_recorder
    steps = [
        _RecordingStep(name="a", precheck_status="skip"),
        _RecordingStep(name="b", next_event=JobEvent.PATCH_OK),
    ]
    result = Orchestrator().run(ctx, steps)

    assert not result.halted
    # 'a' had only precheck called (skip).
    calls = ctx.state["calls"]
    assert ("a", "precheck") in calls
    assert ("a", "run") not in calls
    assert ("b", "run") in calls
    assert events == [("patch_ok", {})]


def test_precheck_fail_halts_and_records_reason(ctx_with_recorder):
    ctx, events, _ = ctx_with_recorder
    steps = [
        _RecordingStep(name="a", precheck_status="fail",
                       precheck_reason="env broken"),
        _RecordingStep(name="b", next_event=JobEvent.PATCH_OK),
    ]
    result = Orchestrator().run(ctx, steps)

    assert result.halted
    assert result.halt_reason == "env broken"
    # 'b' never ran
    assert all(c[0] != "b" for c in ctx.state.get("calls", []))
    assert events == []


# --- exception handling ------------------------------------------------------


def test_precheck_exception_halts_orchestrator(ctx_with_recorder):
    ctx, events, _ = ctx_with_recorder
    result = Orchestrator().run(ctx, [_RaisingStep(where="precheck")])
    assert result.halted
    assert "precheck-boom" in result.halt_reason
    assert "ValueError" in result.halt_reason
    assert events == []


def test_run_exception_halts_orchestrator(ctx_with_recorder):
    ctx, events, _ = ctx_with_recorder
    result = Orchestrator().run(ctx, [_RaisingStep(where="run")])
    assert result.halted
    assert "run-boom" in result.halt_reason
    # Step's outcome was recorded as failed.
    assert result.step_results[0].outcome.status == "failed"
    # No transition fired.
    assert events == []


def test_record_exception_does_not_halt(ctx_with_recorder):
    """A record() exception is observability noise, not a flow
    error. The orchestrator must continue."""
    ctx, events, logs = ctx_with_recorder
    steps = [
        _RaisingStep(where="record"),
        _RecordingStep(name="b", next_event=JobEvent.PATCH_OK),
    ]
    result = Orchestrator().run(ctx, steps)

    assert not result.halted
    # The next step still ran
    assert any(c == ("b", "run") for c in ctx.state.get("calls", []))
    # And the record-error got logged
    assert any("step_record_error" == stage for stage, _ in logs)


# --- transition firing -------------------------------------------------------


def test_apply_transition_failure_logs_but_does_not_halt(ctx_with_recorder):
    ctx, events, logs = ctx_with_recorder

    def boom(job_id, event, detail=None):
        raise RuntimeError("db locked")

    ctx.apply_transition = boom
    steps = [
        _RecordingStep(name="a", next_event=JobEvent.TRIAGE_OK),
        _RecordingStep(name="b", next_event=JobEvent.PATCH_OK),
    ]
    result = Orchestrator().run(ctx, steps)

    assert not result.halted
    # Both steps still ran
    assert ("b", "run") in ctx.state.get("calls", [])
    # The transition errors were logged
    assert sum(1 for stage, _ in logs if stage == "step_transition_error") == 2


def test_no_activity_log_callable_does_not_crash(ctx_with_recorder):
    """ctx.activity_log being None should not break things when
    record() raises or transition fails."""
    ctx, events, _ = ctx_with_recorder
    ctx.activity_log = None  # operator opts out of activity logging
    ctx.apply_transition = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("x"))

    steps = [_RecordingStep(name="a", next_event=JobEvent.TRIAGE_OK)]
    result = Orchestrator().run(ctx, steps)

    # No crash; no transition events captured anywhere either.
    assert not result.halted


# --- result accessors --------------------------------------------------------


def test_result_step_by_name(ctx_with_recorder):
    ctx, _, _ = ctx_with_recorder
    steps = [
        _RecordingStep(name="a"),
        _RecordingStep(name="b"),
    ]
    result = Orchestrator().run(ctx, steps)
    assert result.step_by_name("a").step_name == "a"
    assert result.step_by_name("b").step_name == "b"
    assert result.step_by_name("nope") is None


def test_empty_step_list_is_a_no_op(ctx_with_recorder):
    ctx, events, _ = ctx_with_recorder
    result = Orchestrator().run(ctx, [])
    assert not result.halted
    assert result.step_results == []
    assert events == []


# --- Protocol conformance ----------------------------------------------------


def test_recording_step_satisfies_protocol():
    assert isinstance(_RecordingStep(name="x"), Step)


def test_step_state_is_per_run():
    """Each call to Orchestrator.run uses the ctx the caller hands
    in — state doesn't leak across orchestrator instances."""
    s = _RecordingStep(name="a")

    ctx1 = StepCtx(job_id="j1", job={})
    Orchestrator().run(ctx1, [s])

    ctx2 = StepCtx(job_id="j2", job={})
    Orchestrator().run(ctx2, [s])

    assert ctx1.state["calls"] == [("a", "precheck"), ("a", "run"), ("a", "record")]
    assert ctx2.state["calls"] == [("a", "precheck"), ("a", "run"), ("a", "record")]
