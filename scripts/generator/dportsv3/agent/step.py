"""Step protocol + Orchestrator for the agentic loop.

Phase 5 Step 1 of the agentic framework. Layer 2 of
``agentic-framework-design.md``.

A ``Step`` is one bounded unit of work the runner performs on a
job: triage, patch attempt, rebuild verify. Each step exposes
three hooks the orchestrator calls in order:

- ``precheck(ctx)``: gate. Returns ready/skip/fail. ready → run;
  skip → next step; fail → halt the orchestrator.
- ``run(ctx)``: the work. Returns a ``StepOutcome`` carrying
  status, the next lifecycle event to fire, and optional detail
  fields.
- ``record(ctx, outcome)``: persists artifacts, writes activity-
  log entries, cleans up temp resources.

The ``Orchestrator`` drives one step list against one ``StepCtx``,
fires lifecycle events between steps, and surfaces an
``OrchestratorResult`` summarizing what happened.

Phase 5 lands the protocol + driver only; concrete steps
(``TriageStep``, ``PatchAttemptStep``) land in Steps 2 and 3.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, runtime_checkable

from .lifecycle import JobEvent


# --- Result types ------------------------------------------------------------


@dataclass
class StepReadiness:
    """Result of ``Step.precheck(ctx)``.

    - ``ready``: run the step.
    - ``skip``:  orchestrator moves to the next step in the list.
    - ``fail``:  orchestrator halts. ``reason`` is surfaced in
                 the OrchestratorResult and (optionally) wired to
                 a lifecycle failure event by the caller.
    """
    status: Literal["ready", "skip", "fail"]
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepOutcome:
    """Result of ``Step.run(ctx)``.

    - ``status``: coarse outcome for orchestrator routing.
    - ``next_event``: lifecycle event the orchestrator fires after
      ``record`` (None means "no primary transition").
    - ``extra_events``: additional events to fire after
      ``next_event`` (e.g. TriageStep emits TRIAGE_OK as
      ``next_event`` and adds ESCALATE_MANUAL when the decision
      routes to MANUAL).
    - ``detail``: free-form structured data the orchestrator
      includes in the lifecycle events' ``detail_json``.
    """
    status: Literal["success", "needs-help", "failed", "skipped"]
    next_event: JobEvent | None = None
    extra_events: list[JobEvent] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepResult:
    """One step's full record — what precheck said + the run
    outcome (or None if precheck didn't pass)."""
    step_name: str
    readiness: StepReadiness
    outcome: StepOutcome | None = None


def outcome_events(outcome: StepOutcome | None) -> list[JobEvent]:
    """Lifecycle events encoded by a step outcome, in fire order."""
    if outcome is None:
        return []
    events: list[JobEvent] = []
    if outcome.next_event is not None:
        events.append(outcome.next_event)
    events.extend(outcome.extra_events)
    return events


@dataclass
class OrchestratorResult:
    """End-of-run summary for a job's step list."""
    job_id: str
    step_results: list[StepResult] = field(default_factory=list)
    halted: bool = False
    halt_reason: str = ""

    def step_by_name(self, name: str) -> StepResult | None:
        for r in self.step_results:
            if r.step_name == name:
                return r
        return None


# --- StepCtx -----------------------------------------------------------------


@dataclass
class StepCtx:
    """Render-time context every step receives.

    Identity + the runner-side helpers a step needs to do its work
    without coupling to module globals. Steps must not mutate
    fields other than ``state`` (the shared scratchpad).
    """
    # Identity
    job_id: str
    job: dict
    queue_root: Path | None = None

    # Lifecycle / observability — caller binds these from the runner.
    apply_transition: Callable[..., bool] | None = None
    activity_log: Callable[..., None] | None = None

    # Optional resources steps may use; nullable so unit tests can
    # construct a ctx incrementally.
    db_conn: sqlite3.Connection | None = None
    env_name: str | None = None
    bundle_dir: Path | None = None
    bundle_id: str | None = None
    kedb_dir: Path | None = None

    # Step-shared scratchpad. precheck → run → record pass data
    # through here (e.g. precheck resolves a tier; run consumes it).
    state: dict[str, Any] = field(default_factory=dict)


# --- Step Protocol -----------------------------------------------------------


@runtime_checkable
class Step(Protocol):
    """A bounded unit of work the orchestrator drives.

    Implementations are usually dataclasses (no mutable state per
    instance; ``ctx.state`` carries per-run data).
    """
    name: str

    def precheck(self, ctx: StepCtx) -> StepReadiness: ...
    def run(self, ctx: StepCtx) -> StepOutcome: ...
    def record(self, ctx: StepCtx, outcome: StepOutcome) -> None: ...


# --- Orchestrator ------------------------------------------------------------


class Orchestrator:
    """Drive a sequence of steps against one StepCtx.

    For each step:
    1. precheck → if fail, halt and return; if skip, continue.
    2. run → record → fire next_event via ctx.apply_transition.
    3. Append a StepResult.

    Uncaught exceptions from run/record are caught here, surfaced
    in the OrchestratorResult as ``halted=True``, and bubble no
    further. (Steps that want to be tolerant should catch their
    own exceptions inside run().)
    """

    def run(self, ctx: StepCtx, steps: list[Step]) -> OrchestratorResult:
        result = OrchestratorResult(job_id=ctx.job_id)
        for step in steps:
            try:
                readiness = step.precheck(ctx)
            except Exception as exc:  # noqa: BLE001 — orchestrator must not crash
                result.halted = True
                result.halt_reason = (
                    f"precheck of {step.name} raised: {type(exc).__name__}: {exc}"
                )
                result.step_results.append(StepResult(
                    step_name=step.name,
                    readiness=StepReadiness(status="fail", reason=result.halt_reason),
                ))
                return result

            if readiness.status == "fail":
                result.halted = True
                result.halt_reason = readiness.reason
                result.step_results.append(StepResult(
                    step_name=step.name, readiness=readiness,
                ))
                return result

            if readiness.status == "skip":
                result.step_results.append(StepResult(
                    step_name=step.name, readiness=readiness,
                ))
                continue

            # readiness == "ready"
            try:
                outcome = step.run(ctx)
            except Exception as exc:  # noqa: BLE001
                result.halted = True
                result.halt_reason = (
                    f"run of {step.name} raised: {type(exc).__name__}: {exc}"
                )
                result.step_results.append(StepResult(
                    step_name=step.name, readiness=readiness,
                    outcome=StepOutcome(status="failed", detail={"exc": result.halt_reason}),
                ))
                return result

            try:
                step.record(ctx, outcome)
            except Exception as exc:  # noqa: BLE001
                # record failures don't halt — they're observability
                # writes that shouldn't block the lifecycle event.
                if ctx.activity_log is not None:
                    try:
                        ctx.activity_log(
                            ctx.queue_root, "step_record_error",
                            f"{step.name}.record raised: {type(exc).__name__}: {exc}",
                            job_id=ctx.job_id,
                        )
                    except Exception:
                        pass

            for evt in outcome_events(outcome):
                if ctx.apply_transition is None:
                    break
                try:
                    ctx.apply_transition(
                        ctx.job_id, evt, detail=outcome.detail or None,
                    )
                except Exception as exc:  # noqa: BLE001
                    if ctx.activity_log is not None:
                        try:
                            ctx.activity_log(
                                ctx.queue_root, "step_transition_error",
                                f"{step.name} → {evt} failed: "
                                f"{type(exc).__name__}: {exc}",
                                job_id=ctx.job_id,
                            )
                        except Exception:
                            pass

            result.step_results.append(StepResult(
                step_name=step.name, readiness=readiness, outcome=outcome,
            ))
        return result
