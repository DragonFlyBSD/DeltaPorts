"""Concrete Step implementations.

Phase 5 Step 2 of the agentic framework. Lifts the bodies of the
runner's per-job handlers into ``Step`` classes the orchestrator
can drive.

For Phase 5, ``TriageStep`` is the only step that touches lifecycle
event firing directly (via ``ctx.apply_transition`` for the
secondary ESCALATE_MANUAL event). The primary TRIAGE_OK / ENV_BROKEN
transitions remain wired through the runner's
``_completion_events_for`` mapping, called from ``process_job``
based on the returned ``(success, status)`` tuple. Step 4 of the
phase finishes the migration by routing all completion events
through ``StepOutcome.next_event`` / ``extra_events`` instead.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .step import Step, StepCtx, StepOutcome, StepReadiness


# -----------------------------------------------------------------------------
# PatchEventDispatcher — observability sink for harness_patch.run's on_event.
# -----------------------------------------------------------------------------


@dataclass
class PatchEventDispatcher:
    """Named callable that routes ``harness_patch.run`` events.

    Replaces the ad-hoc closure that lived inside
    ``_process_patch_job_harness``. Three responsibilities:

    1. Append every event to ``trace_events`` (the runner persists
       this to ``analysis/tool_trace.jsonl`` after the run).
    2. Emit one ``activity_log`` row per event so the UI shows
       live progress.
    3. On tool results that look env-suspicious, force-invalidate
       the health cache so the next gate cycle re-probes. We
       never set "broken" from a tool error directly — the probe
       is authoritative.

    Construction:
        dispatcher = PatchEventDispatcher(
            queue_root=..., job_id=..., origin=...,
            activity_log=activity_log,
            looks_env_suspicious=_looks_env_suspicious,
            invalidate_health_cache=invalidate_health_cache,
            summarize_tool_call=_summarize_tool_call,
        )
        # then pass dispatcher as on_event:
        harness_patch.run(payload, ..., on_event=dispatcher)

    Callables are injected (not module imports) so the class is
    decoupled from runner internals and trivial to unit-test.
    """
    queue_root: Path | None
    job_id: str
    origin: str
    activity_log: Callable[..., None]
    looks_env_suspicious: Callable[[dict], bool]
    invalidate_health_cache: Callable[..., None]
    summarize_tool_call: Callable[[str, dict, dict], str]
    trace_events: list[dict] = field(default_factory=list)

    def __call__(self, ev: dict) -> None:
        self.trace_events.append(ev)
        et = ev.get("type")
        if et == "tool_call":
            res = ev.get("result") or {}
            if isinstance(res, dict) and self.looks_env_suspicious(res):
                try:
                    self.invalidate_health_cache()
                except Exception:
                    pass
                self.activity_log(
                    self.queue_root, "health_recheck_forced",
                    "tool result looks env-suspicious; forcing re-probe",
                    job_id=self.job_id,
                    extra={"tool": ev.get("tool")},
                )

        if et == "attempt_start":
            self.activity_log(
                self.queue_root, "attempt_start",
                f"attempt {ev.get('attempt')}/{ev.get('iterations')} for "
                f"{self.origin} (tokens used "
                f"{ev.get('tokens_used_so_far')}/{ev.get('budget')})",
                job_id=self.job_id,
                extra={k: v for k, v in ev.items() if k != "type"},
            )
        elif et == "tool_call":
            args = ev.get("args") or {}
            res = ev.get("result") or {}
            summary = self.summarize_tool_call(ev.get("tool", ""), args, res)
            self.activity_log(
                self.queue_root, f"tool:{ev.get('tool')}",
                summary,
                job_id=self.job_id,
                duration_ms=ev.get("duration_ms"),
                extra={
                    "attempt": ev.get("attempt"),
                    "turn": ev.get("turn"),
                    "ok": bool(res.get("ok")) if isinstance(res, dict) else None,
                },
            )
        elif et == "attempt_end":
            self.activity_log(
                self.queue_root, "attempt_end",
                f"attempt {ev.get('attempt')} for {self.origin}: "
                f"rebuild_ok={ev.get('rebuild_ok')} tokens={ev.get('tokens')}",
                job_id=self.job_id,
                extra={k: v for k, v in ev.items() if k != "type"},
            )


@dataclass
class TriageStep:
    """One triage attempt against a failure bundle.

    ``ctx.state`` carries the data the runner pre-populates:

      - ``job_path``     : Path of the .job file (for write_error_note)
      - ``payload``      : str (output of build_triage_payload)
      - ``origin``       : str (job.origin, defaulting to "unknown")
      - ``policy_path``  : str (resolved DP_HARNESS_POLICY)
      - ``runner_helpers``: dict of callables the step needs:
          materialize_bundle    (bundle_id, dest_dir) -> int
          artifact_store_put    (bundle_id, relpath, data, kind) -> bool
          write_error_note      (job_path, msg) -> None
          write_triage_audit    (bundle_dir, bundle_id, result, model) -> None
          enqueue_patch_job     (queue_root, job, tier_name, dev_env) -> Path
          upsert_user_context_request (queue_root, **fields) -> None
          update_runner_status  (status, **fields) -> None
          probe_health_cached   (env, ttl) -> EnvHealth | None
          load_port_history     (target, origin, window_hours) -> PortHistory
          log                   (queue_root, level, message) -> None

    The step returns a ``StepOutcome`` whose ``detail['status_str']``
    is one of {"done", "manual_tier", "skipped_env_broken",
    "<error message>"} — the same tuple-status the legacy
    ``process_triage_job`` returned. The runner's
    ``_completion_events_for`` maps those into lifecycle events
    until Step 4 of this phase migrates completion-event firing
    fully into the step's ``StepOutcome``.
    """
    name: str = "triage"

    def precheck(self, ctx: StepCtx) -> StepReadiness:
        model = os.environ.get("DP_HARNESS_TRIAGE_MODEL")
        if not model:
            return StepReadiness(
                status="fail",
                reason="DP_HARNESS_TRIAGE_MODEL not set; cannot run triage",
            )
        ctx.state["model"] = model
        return StepReadiness(status="ready")

    def run(self, ctx: StepCtx) -> StepOutcome:
        # Imports kept inside run() so this module stays cheap to
        # import (sections of the agent package optionally need
        # litellm / providers).
        from dportsv3.agent import policy as harness_policy  # noqa: PLC0415
        from dportsv3.agent import triage as harness_triage  # noqa: PLC0415
        from dportsv3.agent.decision import decide            # noqa: PLC0415

        helpers = ctx.state["runner_helpers"]
        queue_root = ctx.queue_root
        job = ctx.job
        job_path: Path = ctx.state["job_path"]
        origin: str = ctx.state["origin"]
        payload: str = ctx.state["payload"]
        model: str = ctx.state["model"]
        policy_path: str = ctx.state["policy_path"]
        kedb_dir = ctx.kedb_dir
        bundle_dir = ctx.bundle_dir
        bundle_id = ctx.bundle_id or job.get("bundle_id")

        # Tunables (env-resolved per call — same as legacy).
        api_base = os.environ.get("DP_HARNESS_TRIAGE_API_BASE") or None
        api_key = os.environ.get("DP_HARNESS_TRIAGE_API_KEY") or None
        custom_llm_provider = os.environ.get("DP_HARNESS_TRIAGE_PROVIDER") or None
        timeout = int(os.environ.get("DP_HARNESS_TIMEOUT", "120"))
        max_snippet_rounds = int(os.environ.get("DP_HARNESS_MAX_SNIPPET_ROUNDS", "5"))

        # Materialize the bundle if it arrived via artifact-store
        # (no on-disk dir). Stash tempdir in state so record() can
        # clean up + upload triage.md.
        materialized_tmp: Path | None = None
        if bundle_dir is None:
            if not bundle_id:
                return _err("harness triage requires bundle_dir or bundle_id",
                            helpers, job_path)
            try:
                materialized_tmp = Path(tempfile.mkdtemp(prefix=f"bundle-{bundle_id}-"))
                n = helpers["materialize_bundle"](bundle_id, materialized_tmp)
            except Exception as exc:
                return _err(f"failed to materialize bundle {bundle_id}: {exc}",
                            helpers, job_path)
            if n == 0:
                shutil.rmtree(materialized_tmp, ignore_errors=True)
                return _err(f"bundle {bundle_id} has no artifacts in the store",
                            helpers, job_path)
            bundle_dir = materialized_tmp
            helpers["log"](queue_root, "INFO",
                           f"materialized {n} artifact(s) for bundle {bundle_id} "
                           f"into {materialized_tmp}")
            ctx.state["materialized_tmp"] = materialized_tmp
            ctx.state["bundle_dir"] = bundle_dir

        # ----- LLM call -----
        helpers["activity_log"](
            queue_root, "api_call_start",
            f"Calling harness triage for {origin}",
            job_id=ctx.job_id,
            extra={"agent": "dports-triage", "model": model},
        )
        start = time.time()
        try:
            result = harness_triage.run(
                payload,
                bundle_dir=bundle_dir,
                model=model,
                api_base=api_base,
                api_key=api_key,
                custom_llm_provider=custom_llm_provider,
                timeout=timeout,
                max_snippet_rounds=max_snippet_rounds,
            )
        except Exception as exc:
            helpers["activity_log"](
                queue_root, "api_error",
                f"Harness triage failed for {origin}: {str(exc)[:200]}",
                job_id=ctx.job_id,
            )
            return _err(str(exc), helpers, job_path)
        duration_ms = int((time.time() - start) * 1000)
        helpers["activity_log"](
            queue_root, "api_call_complete",
            f"Harness triage response received for {origin} "
            f"(rounds={result.snippet_rounds}, tokens={result.usage.total_tokens})",
            job_id=ctx.job_id, duration_ms=duration_ms,
        )

        helpers["write_triage_audit"](bundle_dir, bundle_id, result, model)
        helpers["activity_log"](
            queue_root, "write_output",
            f"Wrote harness triage outputs for {origin}",
            job_id=ctx.job_id,
        )

        # ----- decide() -----
        try:
            pol = harness_policy.load_policy(policy_path)
        except Exception as exc:
            helpers["activity_log"](
                queue_root, "policy_error",
                f"Failed to load harness policy at {policy_path}: {exc}",
                job_id=ctx.job_id,
            )
            return _err(f"policy load failed: {exc}", helpers, job_path)

        max_attempts = int(os.environ.get("DP_HARNESS_MAX_PATCH_ATTEMPTS", "3"))
        window_hours = int(os.environ.get("DP_HARNESS_ATTEMPT_WINDOW_HOURS", "2"))
        target_value = job.get("target", "") or ""
        history = helpers["load_port_history"](target_value, origin, window_hours)

        env_health = None
        runner_env_name = os.environ.get("DP_HARNESS_ENV") or ""
        if runner_env_name:
            health_ttl = int(os.environ.get("DP_HARNESS_HEALTH_CACHE_SECONDS", "60"))
            try:
                env_health = helpers["probe_health_cached"](runner_env_name, health_ttl)
            except Exception:
                env_health = None

        dec = decide(
            classification=result.classification,
            confidence=result.confidence,
            history=history,
            env_health=env_health,
            policy=pol,
            max_attempts=max_attempts,
            window_hours=window_hours,
        )
        tier = dec.tier
        ctx.state["decision"] = dec
        ctx.state["tier"] = tier

        helpers["activity_log"](
            queue_root, "decision",
            dec.reason,
            job_id=ctx.job_id,
            extra={**dec.extra, "action": dec.action, "tier": tier.name},
        )

        # ----- route -----
        if dec.action == "skip":
            return StepOutcome(
                status="success",
                detail={"status_str": "skipped_env_broken", "action": dec.action},
            )

        if dec.action == "escalate_manual":
            run_id = job.get("run_id", "")
            iteration = int(job.get("iteration", "1"))
            max_iterations = int(job.get("max_iterations", "3"))
            helpers["upsert_user_context_request"](
                queue_root,
                run_id=run_id,
                origin=origin,
                bundle_id=bundle_id or (bundle_dir.name if bundle_dir else ""),
                classification=result.classification,
                confidence=result.confidence,
                iteration=iteration,
                max_iterations=max_iterations,
            )
            helpers["activity_log"](
                queue_root, "triage_manual",
                f"Triage tier MANUAL for {origin} "
                f"(classification={result.classification}, confidence={result.confidence}); "
                f"no auto-enqueue",
                job_id=ctx.job_id,
                extra={
                    "classification": result.classification,
                    "confidence": result.confidence,
                    "tier": tier.name,
                    "run_id": run_id,
                },
            )
            helpers["update_runner_status"](
                "waiting", job_id=ctx.job_id, stage="waiting_user_context",
                extra={"origin": origin, "type": "triage", "tier": tier.name},
            )
            return StepOutcome(
                status="success",
                detail={"status_str": "manual_tier", "action": dec.action},
            )

        # auto_patch
        helpers["enqueue_patch_job"](
            queue_root, job,
            tier_name=tier.name,
            dev_env=os.environ.get("DP_HARNESS_ENV") or None,
        )
        helpers["activity_log"](
            queue_root, "enqueue_patch",
            f"Auto-enqueued patch job for {origin} "
            f"(tier={tier.name}, classification={result.classification})",
            job_id=ctx.job_id,
            extra={
                "classification": result.classification,
                "confidence": result.confidence,
                "tier": tier.name,
                "max_iterations": tier.max_iterations,
                "max_tokens": tier.max_tokens,
            },
        )
        return StepOutcome(
            status="success",
            detail={"status_str": "done", "action": dec.action},
        )

    def record(self, ctx: StepCtx, outcome: StepOutcome) -> None:
        # Tempdir cleanup + upload triage.md back if we materialized
        # the bundle. Idempotent on early-return paths because
        # materialized_tmp is only stashed when materialization
        # succeeded.
        materialized_tmp: Path | None = ctx.state.get("materialized_tmp")
        if materialized_tmp is None:
            return
        bundle_id = ctx.bundle_id or ctx.job.get("bundle_id")
        helpers = ctx.state.get("runner_helpers") or {}
        if bundle_id and "artifact_store_put" in helpers:
            tmd = materialized_tmp / "analysis" / "triage.md"
            if tmd.exists():
                try:
                    helpers["artifact_store_put"](
                        bundle_id, "analysis/triage.md",
                        tmd.read_bytes(), "text",
                    )
                except Exception:
                    pass
        shutil.rmtree(materialized_tmp, ignore_errors=True)


def _err(msg: str, helpers: dict[str, Any], job_path: Path) -> StepOutcome:
    """Build a failure StepOutcome + write the .job.error note.

    Mirrors what _process_triage_job_harness used to do when its
    inner call returned (False, msg).
    """
    try:
        helpers["write_error_note"](job_path, msg)
    except Exception:
        pass
    return StepOutcome(
        status="failed",
        detail={"status_str": msg, "error": True},
    )


# -----------------------------------------------------------------------------
# PatchAttemptStep — one patch run via the dportsv3.agent.patch harness.
# -----------------------------------------------------------------------------


@dataclass
class PatchAttemptStep:
    """One patch attempt against a triage-classified failure.

    ``ctx.state`` carries:

      - ``job_path``       : Path of the .job file
      - ``payload``        : str (build_patch_payload output)
      - ``origin``         : str (job.origin)
      - ``policy_path``    : str (resolved DP_HARNESS_POLICY)
      - ``runner_helpers`` : dict of injected callables (see below)

    Required ``runner_helpers``:

      - read_bundle_text(bundle_dir, bundle_id, relpath) -> str | None
      - write_error_note(job_path, msg) -> None
      - write_patch_audit(bundle_dir, bundle_id, result, model) -> None
      - write_tool_trace(bundle_dir, bundle_id, trace_events) -> None
      - write_changes_diff(bundle_dir, bundle_id, env, origin) -> None
      - looks_env_suspicious(result: dict) -> bool
      - invalidate_health_cache() -> None
      - summarize_tool_call(tool, args, result) -> str
      - activity_log(queue_root, stage, msg, ...) -> None
      - log(queue_root, level, msg) -> None
      - load_port_history(target, origin, window_hours) -> PortHistory

    Precheck resolves the tier (preferring ``job["tier"]`` set by
    the triage step). When the field is missing — hand-fired patch
    jobs — it parses ``analysis/triage.md`` and calls
    ``decide(empty_history, env_health=None)``. Empty history +
    None env_health give the *legacy* tier_for behavior (no cap,
    no env short-circuit on hand-fired). This absorbs the Phase-3
    leftover ``tier_for`` call without changing hand-fired
    behavior; the Phase-3 retry cap applies only to auto-enqueued
    patch jobs (which already carry tier).
    """
    name: str = "patch"

    def precheck(self, ctx: StepCtx) -> StepReadiness:
        # Model resolution — DP_HARNESS_PATCH_MODEL takes precedence;
        # fall back to DP_HARNESS_TRIAGE_MODEL with a warning so the
        # operator knows patch quality may be lower.
        helpers = ctx.state.get("runner_helpers") or {}
        model = os.environ.get("DP_HARNESS_PATCH_MODEL")
        if not model:
            model = os.environ.get("DP_HARNESS_TRIAGE_MODEL")
            if not model:
                return StepReadiness(
                    status="fail",
                    reason="neither DP_HARNESS_PATCH_MODEL nor DP_HARNESS_TRIAGE_MODEL set",
                )
            if "log" in helpers:
                helpers["log"](
                    ctx.queue_root, "WARN",
                    f"DP_HARNESS_PATCH_MODEL unset; falling back to triage model "
                    f"({model}) for patch — set DP_HARNESS_PATCH_MODEL "
                    f"to silence this and likely improve patch quality",
                )
        ctx.state["model"] = model

        # dev_env: prefer job field, fall back to env var. Required.
        env = ctx.job.get("dev_env") or os.environ.get("DP_HARNESS_ENV") or ""
        if not env:
            return StepReadiness(
                status="fail",
                reason="patch job missing dev_env (set job 'dev_env' field or DP_HARNESS_ENV)",
            )
        ctx.state["env"] = env

        # Policy + tier resolution.
        from dportsv3.agent import policy as harness_policy  # noqa: PLC0415
        policy_path = ctx.state.get("policy_path")
        if not policy_path:
            return StepReadiness(status="fail",
                                 reason="policy_path missing from ctx.state")
        try:
            pol = harness_policy.load_policy(policy_path)
        except Exception as exc:
            return StepReadiness(
                status="fail",
                reason=f"failed to load harness policy at {policy_path}: {exc}",
            )
        ctx.state["policy"] = pol

        tier_name = (ctx.job.get("tier") or "").strip()
        if tier_name and tier_name in pol.tiers:
            ctx.state["tier"] = pol.tiers[tier_name]
            return StepReadiness(status="ready")

        # Hand-fired patch path: derive tier from triage.md via
        # decide(). Empty history + None env_health preserve legacy
        # tier_for semantics (no cap, no env short-circuit) for
        # operator-triggered patches.
        from dportsv3.agent.decision import PortHistory, decide  # noqa: PLC0415

        read_bundle_text = helpers.get("read_bundle_text")
        if read_bundle_text is None:
            return StepReadiness(
                status="fail",
                reason="runner_helpers missing read_bundle_text for tier fallback",
            )
        triage_text = read_bundle_text(
            ctx.bundle_dir, ctx.bundle_id or ctx.job.get("bundle_id"),
            "analysis/triage.md",
        ) or ""
        # parse_triage_output is a runner-side helper — keep the
        # parsing here inline so steps.py doesn't depend on runner.py
        # imports. Cheap copy of the two regex lookups.
        triage = _parse_triage_minimal(triage_text)
        history = PortHistory.empty(
            target=ctx.job.get("target", "") or "",
            origin=ctx.state.get("origin") or ctx.job.get("origin", ""),
        )
        dec = decide(
            classification=triage.get("classification", ""),
            confidence=triage.get("confidence", ""),
            history=history,
            env_health=None,
            policy=pol,
        )
        ctx.state["tier"] = dec.tier
        return StepReadiness(status="ready")

    def run(self, ctx: StepCtx) -> StepOutcome:
        from dportsv3.agent import patch as harness_patch  # noqa: PLC0415

        helpers = ctx.state["runner_helpers"]
        queue_root = ctx.queue_root
        job = ctx.job
        job_path: Path = ctx.state["job_path"]
        origin: str = ctx.state["origin"]
        payload: str = ctx.state["payload"]
        model: str = ctx.state["model"]
        env: str = ctx.state["env"]
        tier = ctx.state["tier"]
        bundle_id = ctx.bundle_id or job.get("bundle_id")

        # Companion vars with triage-fallback chain.
        api_base = (os.environ.get("DP_HARNESS_PATCH_API_BASE")
                    or os.environ.get("DP_HARNESS_TRIAGE_API_BASE")
                    or None)
        api_key = (os.environ.get("DP_HARNESS_PATCH_API_KEY")
                   or os.environ.get("DP_HARNESS_TRIAGE_API_KEY")
                   or None)
        custom_llm_provider = (os.environ.get("DP_HARNESS_PATCH_PROVIDER")
                               or os.environ.get("DP_HARNESS_TRIAGE_PROVIDER")
                               or None)
        timeout = int(os.environ.get("DP_HARNESS_PATCH_TIMEOUT", "600"))

        helpers["activity_log"](
            queue_root, "api_call_start",
            f"Calling harness patch for {origin} (tier={tier.name}, env={env})",
            job_id=ctx.job_id,
            extra={"agent": "dports-patch", "model": model, "tier": tier.name},
        )

        dispatcher = PatchEventDispatcher(
            queue_root=queue_root,
            job_id=ctx.job_id,
            origin=origin,
            activity_log=helpers["activity_log"],
            looks_env_suspicious=helpers["looks_env_suspicious"],
            invalidate_health_cache=helpers["invalidate_health_cache"],
            summarize_tool_call=helpers["summarize_tool_call"],
        )

        start = time.time()
        try:
            result = harness_patch.run(
                payload,
                tier=tier,
                env=env,
                model=model,
                api_base=api_base,
                api_key=api_key,
                custom_llm_provider=custom_llm_provider,
                timeout=timeout,
                on_event=dispatcher,
            )
        except Exception as exc:
            helpers["activity_log"](
                queue_root, "api_error",
                f"Harness patch failed for {origin}: {str(exc)[:200]}",
                job_id=ctx.job_id,
            )
            return _err(str(exc), helpers, job_path)
        duration_ms = int((time.time() - start) * 1000)

        helpers["activity_log"](
            queue_root, "api_call_complete",
            f"Harness patch finished for {origin} (status={result.status}, "
            f"attempts={len(result.attempts)}, tokens={result.usage.total_tokens})",
            job_id=ctx.job_id, duration_ms=duration_ms,
        )

        # Stash for record() — and for any downstream step.
        ctx.state["patch_result"] = result
        ctx.state["trace_events"] = list(dispatcher.trace_events)

        # Persist outputs.
        helpers["write_patch_audit"](ctx.bundle_dir, bundle_id, result, model)
        helpers["write_tool_trace"](ctx.bundle_dir, bundle_id, dispatcher.trace_events)
        helpers["write_changes_diff"](ctx.bundle_dir, bundle_id, env, origin)
        helpers["activity_log"](
            queue_root, "write_output",
            f"Wrote harness patch outputs for {origin}",
            job_id=ctx.job_id,
        )

        if result.status == "success":
            return StepOutcome(
                status="success",
                detail={"status_str": "done", "patch_status": result.status},
            )
        # needs-help / budget-exhausted — job recorded, not retried.
        return StepOutcome(
            status="success",
            detail={"status_str": result.status, "patch_status": result.status},
        )

    def record(self, ctx: StepCtx, outcome: StepOutcome) -> None:
        # Patch doesn't materialize the bundle into a tempdir
        # (unlike triage), so there's no cleanup to do here. Outputs
        # are persisted inside run() because they depend on the
        # tool_trace + dispatcher state captured during the LLM call.
        return None


def _parse_triage_minimal(text: str) -> dict[str, str]:
    """Tiny mirror of runner.parse_triage_output — extracts
    classification + confidence from a triage.md.

    Inlined here so steps.py doesn't import from runner.py. The
    regexes track the runner's authoritative parser; if the agent's
    output format changes, update both. (Phase 5 doesn't change the
    output format.)
    """
    import re  # noqa: PLC0415
    out = {"classification": "", "confidence": ""}
    if not text:
        return out
    m = re.search(r"^##\s*Classification\s*\n+([^\n#]+)", text, re.MULTILINE)
    if m:
        out["classification"] = m.group(1).strip().lower()
    m = re.search(r"^##\s*Confidence\s*\n+([^\n#]+)", text, re.MULTILINE)
    if m:
        out["confidence"] = m.group(1).strip().lower()
    return out
