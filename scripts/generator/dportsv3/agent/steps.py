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

from .lifecycle import JobEvent
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
            ok = bool(res.get("ok")) if isinstance(res, dict) else None
            extra: dict = {
                "attempt": ev.get("attempt"),
                "turn": ev.get("turn"),
                "ok": ok,
            }
            # On failure, pin stderr_tail + stdout_tail + rc into
            # the activity row's extra_json so /api/activity surfaces
            # the diagnostic without anyone having to spelunk
            # runner.log or tool_trace artifacts. The summary line
            # is one cap'd line; this is the full picture.
            if ok is False and isinstance(res, dict):
                err = (res.get("stderr_tail") or "").strip()
                out = (res.get("stdout_tail") or "").strip()
                if err:
                    extra["stderr_tail"] = err[:2000]
                if out:
                    extra["stdout_tail"] = out[:2000]
                if "rc" in res:
                    extra["rc"] = res["rc"]
                if "error" in res:
                    extra["error"] = str(res["error"])[:500]
            self.activity_log(
                self.queue_root, f"tool:{ev.get('tool')}",
                summary,
                job_id=self.job_id,
                duration_ms=ev.get("duration_ms"),
                extra=extra,
            )
        elif et == "attempt_end":
            self.activity_log(
                self.queue_root, "attempt_end",
                f"attempt {ev.get('attempt')} for {self.origin}: "
                f"rebuild_ok={ev.get('rebuild_ok')} tokens={ev.get('tokens')}",
                job_id=self.job_id,
                extra={k: v for k, v in ev.items() if k != "type"},
            )
        elif et == "llm_turn":
            # Per-LLM-round telemetry. The "prompt" share usually
            # dominates because conversation history compounds with
            # every tool result; this row makes that visible.
            tools_str = ",".join(ev.get("tools_requested") or []) or "(text-only)"
            self.activity_log(
                self.queue_root, "llm_turn",
                f"A{ev.get('attempt')}.T{ev.get('turn')} "
                f"in={ev.get('prompt_tokens')} "
                f"out={ev.get('completion_tokens')} "
                f"total={ev.get('total_tokens')} "
                f"cumulative={ev.get('cumulative_total_tokens')} "
                f"→ {tools_str}",
                job_id=self.job_id,
                extra={k: v for k, v in ev.items() if k != "type"},
            )


@dataclass
class TriageServices:
    materialize_bundle: Callable[..., Any]
    artifact_store_put: Callable[..., Any]
    write_error_note: Callable[..., Any]
    write_triage_audit: Callable[..., Any]
    enqueue_patch_job: Callable[..., Any]
    upsert_user_context_request: Callable[..., Any]
    update_runner_status: Callable[..., Any]
    probe_health_cached: Callable[..., Any]
    cached_health_broken: Callable[..., bool]
    load_port_history: Callable[..., Any]
    log: Callable[..., Any]
    activity_log: Callable[..., Any]
    write_manual_handoff: Callable[..., Any] | None = None
    # Step 36 follow-up: substrate-defer hook called AFTER the LLM
    # has classified and ``write_triage_audit`` has persisted the
    # typed TriageResult. Returns ``(success, status_str)`` if the
    # bundle should defer to convert, or None to proceed with the
    # normal post-triage decision tree. The runner-side
    # implementation skips its own lifecycle walk; TriageStep emits
    # TRIAGE_DEFER via the returned StepOutcome.
    maybe_defer_to_convert: Callable[..., Any] | None = None


@dataclass
class TriageStep:
    """One triage attempt against a failure bundle.

    ``ctx.state`` carries the data the runner pre-populates:

      - ``job_path``     : Path of the .job file (for write_error_note)
      - ``payload``      : str (output of build_triage_payload)
      - ``origin``       : str (job.origin, defaulting to "unknown")
      - ``policy_path``  : str (resolved DP_HARNESS_POLICY)
      - ``services``: TriageServices with callables the step needs:
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

        services: TriageServices = ctx.state["services"]
        queue_root = ctx.queue_root
        job = ctx.job
        job_path: Path = ctx.state["job_path"]
        origin: str = ctx.state["origin"]
        payload: str = ctx.state["payload"]
        model: str = ctx.state["model"]
        policy_path: str = ctx.state["policy_path"]
        playbooks_dir = ctx.playbooks_dir
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
                            services, job_path, JobEvent.TRIAGE_FAIL)
            try:
                materialized_tmp = Path(tempfile.mkdtemp(prefix=f"bundle-{bundle_id}-"))
                n = services.materialize_bundle(bundle_id, materialized_tmp)
            except Exception as exc:
                return _err(f"failed to materialize bundle {bundle_id}: {exc}",
                            services, job_path, JobEvent.TRIAGE_FAIL)
            if n == 0:
                shutil.rmtree(materialized_tmp, ignore_errors=True)
                return _err(f"bundle {bundle_id} has no artifacts in the store",
                            services, job_path, JobEvent.TRIAGE_FAIL)
            bundle_dir = materialized_tmp
            services.log(queue_root, "INFO",
                         f"materialized {n} artifact(s) for bundle {bundle_id} "
                         f"into {materialized_tmp}")
            ctx.state["materialized_tmp"] = materialized_tmp
            ctx.state["bundle_dir"] = bundle_dir

        # ----- LLM call -----
        services.activity_log(
            queue_root, "api_call_start",
            f"Calling harness triage for {origin}",
            job_id=ctx.job_id,
            extra={"agent": "dports-triage", "model": model},
        )
        # Per-turn token telemetry — also wired for triage so the
        # operator can see how many tokens each snippet round consumed.
        def _triage_event(ev: dict) -> None:
            if ev.get("type") != "llm_turn":
                return
            services.activity_log(
                queue_root, "llm_turn",
                f"triage T{ev.get('turn')} "
                f"(snippet_round={ev.get('snippet_round')}) "
                f"in={ev.get('prompt_tokens')} "
                f"out={ev.get('completion_tokens')} "
                f"total={ev.get('total_tokens')} "
                f"cumulative={ev.get('cumulative_total_tokens')}",
                job_id=ctx.job_id,
                extra={k: v for k, v in ev.items() if k != "type"},
            )

        start = time.time()
        from dportsv3.agent import session_dump as _sd  # noqa: PLC0415
        from dportsv3.agent.runner import artifact_store_put  # noqa: PLC0415
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
                on_event=_triage_event,
                session_dump=_sd.make_dumper(
                    bundle_id=ctx.bundle_id or job.get("bundle_id"),
                    job_id=ctx.job_id,
                    put_artifact=artifact_store_put,
                ),
            )
        except Exception as exc:
            services.activity_log(
                queue_root, "api_error",
                f"Harness triage failed for {origin}: {str(exc)[:200]}",
                job_id=ctx.job_id,
            )
            return _err(str(exc), services, job_path, JobEvent.TRIAGE_FAIL)
        duration_ms = int((time.time() - start) * 1000)
        services.activity_log(
            queue_root, "api_call_complete",
            f"Harness triage response received for {origin} "
            f"(rounds={result.snippet_rounds}, tokens={result.usage.total_tokens})",
            job_id=ctx.job_id, duration_ms=duration_ms,
        )

        services.write_triage_audit(bundle_dir, bundle_id, result, model)
        services.activity_log(
            queue_root, "write_output",
            f"Wrote harness triage outputs for {origin}",
            job_id=ctx.job_id,
        )

        # ----- Step 36 follow-up: substrate-defer (post-classify) -----
        # Pre-Step-36 the substrate check ran at the top of
        # process_triage_job — convert was dispatched *before* triage
        # classified, leaving the convert agent blind to the actual
        # build failure (python311 / plist-drift class). Now the
        # check runs HERE, after triage_result.json is on the bundle,
        # so convert can ``load_phase_result`` and see classification
        # + root_cause + evidence excerpt in its payload. The
        # service callback runs with apply_lifecycle=False; we emit
        # TRIAGE_DEFER through StepOutcome so the orchestrator
        # wrapper walks lifecycle once.
        if services.maybe_defer_to_convert is not None:
            try:
                deferred = services.maybe_defer_to_convert(
                    queue_root=queue_root, job=job, job_path=job_path,
                    origin=origin,
                )
            except Exception as exc:
                # Defer-check is best-effort: a failure here just
                # means we fall through to the normal triage decision
                # tree (no defer). The runner-side helper already
                # logs its own warnings via ``log``/``activity_log``;
                # don't re-raise.
                services.activity_log(
                    queue_root, "triage_defer_check_failed",
                    f"substrate defer check failed for {origin}: "
                    f"{exc!s}"[:240],
                    job_id=ctx.job_id,
                )
                deferred = None
            if deferred is not None:
                status_str = (
                    deferred[1] if isinstance(deferred, tuple)
                    and len(deferred) >= 2 else "deferred_for_convert"
                )
                return StepOutcome(
                    status="success",
                    next_event=JobEvent.TRIAGE_DEFER,
                    detail={"status_str": status_str},
                )

        # ----- decide() -----
        try:
            pol = harness_policy.load_policy(policy_path)
        except Exception as exc:
            services.activity_log(
                queue_root, "policy_error",
                f"Failed to load harness policy at {policy_path}: {exc}",
                job_id=ctx.job_id,
            )
            return _err(f"policy load failed: {exc}", services, job_path,
                        JobEvent.TRIAGE_FAIL)

        max_attempts = int(os.environ.get("DP_HARNESS_MAX_PATCH_ATTEMPTS", "3"))
        window_hours = int(os.environ.get("DP_HARNESS_ATTEMPT_WINDOW_HOURS", "2"))
        bundle_backstop = int(os.environ.get("DP_HARNESS_BUNDLE_BACKSTOP", "10"))
        signature_stickiness = int(
            os.environ.get("DP_HARNESS_SIGNATURE_STICKINESS", "3")
        )
        target_value = job.get("target", "") or ""
        history = services.load_port_history(target_value, origin, window_hours)

        env_health = None
        from dportsv3.agent import runner as _runner  # noqa: PLC0415
        runner_env_name = _runner.resolve_env(job) or ""
        if runner_env_name:
            health_ttl = int(os.environ.get("DP_HARNESS_HEALTH_CACHE_SECONDS", "60"))
            try:
                env_health = services.probe_health_cached(runner_env_name, health_ttl)
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
            bundle_backstop=bundle_backstop,
            signature_stickiness=signature_stickiness,
        )
        tier = dec.tier
        ctx.state["decision"] = dec
        ctx.state["tier"] = tier

        services.activity_log(
            queue_root, "decision",
            dec.reason,
            job_id=ctx.job_id,
            extra={**dec.extra, "action": dec.action, "tier": tier.name},
        )

        # ----- route -----
        # The cached_health_broken check overrides any happy outcome:
        # if the env is known-broken, the job is DEAD-env_broken
        # regardless of what the LLM said. Mirrors the legacy
        # _completion_events_for cache check.
        cached_broken = bool(services.cached_health_broken(runner_env_name or None))

        if dec.action == "skip" or cached_broken:
            return StepOutcome(
                status="success",
                next_event=JobEvent.ENV_BROKEN,
                detail={"status_str": "skipped_env_broken", "action": dec.action},
            )

        if dec.action == "escalate_manual":
            run_id = job.get("run_id", "")
            iteration = int(job.get("iteration", "1"))
            max_iterations = int(job.get("max_iterations", "3"))
            if services.write_manual_handoff is not None:
                # Two paths land here: classification → MANUAL, and
                # retry-cap. ``dec.extra["recent_failures"]`` is only
                # set on the retry-cap branch; use that as the
                # discriminator so the handoff renders the right
                # operator question.
                handoff_reason = (
                    "retry_cap" if "recent_failures" in dec.extra
                    else "manual_tier"
                )
                try:
                    services.write_manual_handoff(
                        bundle_dir,
                        bundle_id or (bundle_dir.name if bundle_dir else None),
                        origin=origin,
                        target=job.get("target", "") or "",
                        reason=handoff_reason,
                        reason_detail=dec.reason,
                        decision_extra=dec.extra,
                        run_id=run_id or None,
                    )
                except Exception:
                    pass
            services.upsert_user_context_request(
                queue_root,
                run_id=run_id,
                origin=origin,
                bundle_id=bundle_id or (bundle_dir.name if bundle_dir else ""),
                classification=result.classification,
                confidence=result.confidence,
                iteration=iteration,
                max_iterations=max_iterations,
            )
            services.activity_log(
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
            services.update_runner_status(
                "waiting", job_id=ctx.job_id, stage="waiting_user_context",
                extra={"origin": origin, "type": "triage", "tier": tier.name},
            )
            return StepOutcome(
                status="success",
                next_event=JobEvent.TRIAGE_OK,
                extra_events=[JobEvent.ESCALATE_MANUAL],
                detail={"status_str": "manual_tier", "action": dec.action},
            )

        # auto_patch
        from dportsv3.agent import runner as _runner  # noqa: PLC0415
        services.enqueue_patch_job(
            queue_root, job,
            tier_name=tier.name,
            dev_env=_runner.resolve_env(job),
        )
        services.activity_log(
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
            next_event=JobEvent.TRIAGE_OK,
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
        services: TriageServices | None = ctx.state.get("services")
        if bundle_id and services is not None:
            tmd = materialized_tmp / "analysis" / "triage.md"
            if tmd.exists():
                try:
                    services.artifact_store_put(
                        bundle_id, "analysis/triage.md",
                        tmd.read_bytes(), "text",
                    )
                except Exception:
                    pass
        shutil.rmtree(materialized_tmp, ignore_errors=True)


def _try_write_proposed_fix(
    services: Any,
    ctx: StepCtx,
    origin: str,
    *,
    model: str,
    patch_result: object | None = None,
) -> None:
    """Best-effort proposed_fix.md writer for the successful patch
    path. Swallows all errors — lifecycle bookkeeping must not be
    blocked by an artifact-write failure. The injected callable is
    optional; legacy callers without it get a no-op."""
    fn = getattr(services, "write_proposed_fix", None)
    if fn is None:
        return
    bundle_id = ctx.bundle_id or ctx.job.get("bundle_id")
    tier = ctx.state.get("tier")
    attempts_max = int(getattr(tier, "max_iterations", 0) or 0)
    try:
        fn(
            ctx.bundle_dir,
            bundle_id,
            origin=origin,
            target=ctx.job.get("target", "") or "",
            model=model,
            attempts_max=attempts_max,
            patch_result=patch_result,
        )
    except Exception:
        pass


def _try_write_handoff(
    services: Any,
    ctx: StepCtx,
    origin: str,
    *,
    reason: str,
    reason_detail: str = "",
    patch_result: object | None = None,
) -> None:
    """Best-effort manual_handoff.md writer for terminal patch paths.

    Swallows all errors — lifecycle bookkeeping must not be blocked
    by an artifact-write failure. The injected callable is optional;
    if a service was constructed without it (e.g. legacy callers),
    this is a no-op."""
    fn = getattr(services, "write_manual_handoff", None)
    if fn is None:
        return
    bundle_id = ctx.bundle_id or ctx.job.get("bundle_id")
    try:
        fn(
            ctx.bundle_dir,
            bundle_id,
            origin=origin,
            target=ctx.job.get("target", "") or "",
            reason=reason,
            reason_detail=reason_detail,
            patch_result=patch_result,
            run_id=ctx.job.get("run_id") or None,
        )
    except Exception:
        pass


def _err(
    msg: str,
    services: Any,
    job_path: Path,
    failure_event: JobEvent,
) -> StepOutcome:
    """Build a failure StepOutcome + write the .job.error note.

    ``failure_event`` is the lifecycle event the orchestrator will
    fire on this outcome — TRIAGE_FAIL for the triage step,
    PATCH_GAVE_UP for the patch step (the catchall DEAD route).
    """
    try:
        services.write_error_note(job_path, msg)
    except Exception:
        pass
    return StepOutcome(
        status="failed",
        next_event=failure_event,
        detail={"status_str": msg, "error": True},
    )


# -----------------------------------------------------------------------------
# PatchAttemptStep — one patch run via the dportsv3.agent.patch harness.
# -----------------------------------------------------------------------------


@dataclass
class PatchServices:
    read_bundle_text: Callable[..., Any]
    write_error_note: Callable[..., Any]
    write_patch_audit: Callable[..., Any]
    write_tool_trace: Callable[..., Any]
    write_changes_diff: Callable[..., Any]
    looks_env_suspicious: Callable[..., bool]
    invalidate_health_cache: Callable[..., Any]
    cached_health_broken: Callable[..., bool]
    summarize_tool_call: Callable[..., str]
    activity_log: Callable[..., Any]
    log: Callable[..., Any]
    load_port_history: Callable[..., Any]
    write_manual_handoff: Callable[..., Any] | None = None
    write_proposed_fix: Callable[..., Any] | None = None


@dataclass
class PatchAttemptStep:
    """One patch attempt against a triage-classified failure.

    ``ctx.state`` carries:

      - ``job_path``       : Path of the .job file
      - ``payload``        : str (build_patch_payload output)
      - ``origin``         : str (job.origin)
      - ``policy_path``    : str (resolved DP_HARNESS_POLICY)
      - ``services``       : PatchServices with injected callables

    Required ``services`` fields:

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
        services: PatchServices = ctx.state["services"]
        model = os.environ.get("DP_HARNESS_PATCH_MODEL")
        if not model:
            model = os.environ.get("DP_HARNESS_TRIAGE_MODEL")
            if not model:
                return StepReadiness(
                    status="fail",
                    reason="neither DP_HARNESS_PATCH_MODEL nor DP_HARNESS_TRIAGE_MODEL set",
                )
            services.log(
                ctx.queue_root, "WARN",
                f"DP_HARNESS_PATCH_MODEL unset; falling back to triage model "
                f"({model}) for patch — set DP_HARNESS_PATCH_MODEL "
                f"to silence this and likely improve patch quality",
            )
        ctx.state["model"] = model

        # dev_env: env_resolver decides (job field → tracker active env
        # → --env CLI flag → auto-pick if exactly one env). Required.
        from dportsv3.agent import runner as _runner  # noqa: PLC0415
        env_res = _runner.resolve_env_or_reason(ctx.job)
        env = env_res.env or ""
        if not env:
            return StepReadiness(
                status="fail",
                reason=f"patch job has no resolvable dev-env: {env_res.refusal_reason}",
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

        # Hand-fired patch path: derive tier from the typed
        # ``TriageResult`` (Step 36-5) via decide(). Empty history +
        # None env_health preserve legacy tier_for semantics (no cap,
        # no env short-circuit) for operator-triggered patches.
        from dportsv3.agent.decision import PortHistory, decide  # noqa: PLC0415
        from dportsv3.agent.phase_result import (  # noqa: PLC0415
            TriageResult, load_phase_result,
        )

        bundle_id = ctx.bundle_id or ctx.job.get("bundle_id")
        classification = ""
        confidence = ""
        try:
            triage_res = load_phase_result(
                ctx.bundle_dir, bundle_id, "triage", TriageResult,
            )
            if triage_res is not None:
                classification = triage_res.classification or ""
                confidence = triage_res.confidence or ""
        except Exception:
            # Missing / version-mismatched typed result → fall through
            # with empty classification + confidence; decide() will
            # land on MANUAL via the tier-cascade.
            pass
        history = PortHistory.empty(
            target=ctx.job.get("target", "") or "",
            origin=ctx.state.get("origin") or ctx.job.get("origin", ""),
        )
        dec = decide(
            classification=classification,
            confidence=confidence,
            history=history,
            env_health=None,
            policy=pol,
        )
        ctx.state["tier"] = dec.tier
        return StepReadiness(status="ready")

    def run(self, ctx: StepCtx) -> StepOutcome:
        from dportsv3.agent import patch as harness_patch  # noqa: PLC0415

        services: PatchServices = ctx.state["services"]
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

        services.activity_log(
            queue_root, "api_call_start",
            f"Calling harness patch for {origin} (tier={tier.name}, env={env})",
            job_id=ctx.job_id,
            extra={"agent": "dports-patch", "model": model, "tier": tier.name},
        )

        # Pre-job clean assertion: refuse to start a patch job if
        # ports/<origin>/ has leftover edits from a prior run.
        # Diff capture depends on a clean baseline; starting from a
        # dirty state silently mixes accumulated state into the
        # bundle's changes.diff. Operator escape:
        # `dportsv3 dev-env reset-port ENV ORIGIN`.
        from dportsv3.agent import worker as _worker  # noqa: PLC0415
        # design §5.1 makes the pre-job clean check a HARD rule —
        # "if not clean, BEGIN aborts". A failure of the check
        # itself (chroot not mounted, env gone, subprocess raised)
        # means we DON'T KNOW if the port is clean, so the safe
        # answer is refuse, not proceed.
        try:
            clean = _worker.assert_port_clean(env, origin)
        except Exception as exc:
            msg = (
                f"patch refused: assert_port_clean({origin}) "
                f"raised — env state is unknown so we can't "
                f"safely start a patch transaction. Resolve "
                f"the env (verify chroot is mounted, run "
                f"`dportsv3 dev-env status {env}`) and retry. "
                f"Underlying error: {str(exc)[:300]}"
            )
            services.activity_log(
                queue_root, "patch_preflight_error",
                msg, job_id=ctx.job_id,
                extra={"origin": origin, "error": str(exc)[:500]},
            )
            services.write_error_note(job_path, msg)
            return _err(msg, services, job_path,
                        JobEvent.PATCH_GAVE_UP)
        if not clean.get("ok"):
            dirty = clean.get("dirty_paths") or []
            msg = (
                f"patch refused: ports/{origin}/ has "
                f"{len(dirty)} uncommitted change(s) from a "
                f"prior run; resolve before starting a new "
                f"patch transaction. Run "
                f"`dportsv3 dev-env reset-port {env} {origin}` "
                f"or `git stash` in the env."
            )
            services.activity_log(
                queue_root, "patch_preflight_dirty",
                msg, job_id=ctx.job_id,
                extra={"origin": origin, "dirty_paths": dirty[:20]},
            )
            services.write_error_note(job_path, msg)
            return _err(msg, services, job_path,
                        JobEvent.PATCH_GAVE_UP)

        dispatcher = PatchEventDispatcher(
            queue_root=queue_root,
            job_id=ctx.job_id,
            origin=origin,
            activity_log=services.activity_log,
            looks_env_suspicious=services.looks_env_suspicious,
            invalidate_health_cache=services.invalidate_health_cache,
            summarize_tool_call=services.summarize_tool_call,
        )

        start = time.time()
        from dportsv3.agent import session_dump as _sd  # noqa: PLC0415
        from dportsv3.agent.runner import artifact_store_put  # noqa: PLC0415
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
                origin=origin,
                session_dump=_sd.make_dumper(
                    bundle_id=ctx.bundle_id or job.get("bundle_id"),
                    job_id=ctx.job_id,
                    put_artifact=artifact_store_put,
                ),
            )
        except Exception as exc:
            services.activity_log(
                queue_root, "api_error",
                f"Harness patch failed for {origin}: {str(exc)[:200]}",
                job_id=ctx.job_id,
            )
            _try_write_handoff(
                services, ctx, origin,
                reason="patch_gave_up",
                reason_detail=f"harness raised before producing a result: {str(exc)[:200]}",
                patch_result=None,
            )
            return _err(str(exc), services, job_path, JobEvent.PATCH_GAVE_UP)
        duration_ms = int((time.time() - start) * 1000)

        services.activity_log(
            queue_root, "api_call_complete",
            f"Harness patch finished for {origin} (status={result.status}, "
            f"attempts={len(result.attempts)}, tokens={result.usage.total_tokens})",
            job_id=ctx.job_id, duration_ms=duration_ms,
        )

        # Stash for record() — and for any downstream step.
        ctx.state["patch_result"] = result
        ctx.state["trace_events"] = list(dispatcher.trace_events)

        # Persist outputs.
        services.write_patch_audit(ctx.bundle_dir, bundle_id, result, model)
        services.write_tool_trace(ctx.bundle_dir, bundle_id, dispatcher.trace_events)
        # Step 30 slice 5: changes.diff is now branch-vs-base
        # (the former delivery.diff shape) and is the single
        # canonical artifact for delivery + verify + operator
        # recipe.
        services.write_changes_diff(ctx.bundle_dir, bundle_id, env, origin)
        services.activity_log(
            queue_root, "write_output",
            f"Wrote harness patch outputs for {origin}",
            job_id=ctx.job_id,
        )

        # C1: capture the resulting dops state BEFORE the workspace reset
        # below wipes the agent's edits. The success gate requires the
        # port to have reached a valid 'converted' overlay — rebuild_ok
        # alone accepts compat writes (Makefile.DragonFly / bare
        # dragonfly/*) that build but don't advance the dops migration.
        try:
            ctx.state["post_patch_dops_state"] = _worker.classify_dops(env, origin)
        except Exception as exc:
            ctx.state["post_patch_dops_state"] = None
            services.activity_log(
                queue_root, "patch_post_classify_failed",
                f"classify_dops failed for {origin} after patch: {str(exc)[:200]}",
                job_id=ctx.job_id,
            )

        # Post-job workspace reset. changes.diff is the canonical
        # record; the env's port subtree no longer needs to carry
        # the agent's edits. Reset to git HEAD so the next patch job
        # (or operator inspection) starts from a clean baseline that
        # the pre-job preflight will accept. Best-effort: a reset
        # failure surfaces as an activity row but doesn't affect the
        # patch outcome.
        try:
            reset = _worker.reset_port(env, origin)
        except Exception as exc:
            reset = {"ok": False, "error": str(exc)[:200]}
        if not reset.get("ok"):
            services.activity_log(
                queue_root, "patch_post_reset_failed",
                f"reset_port failed for {origin} after patch "
                f"({reset.get('error', 'unknown')[:200]}); env "
                f"may have leftover edits — next patch will "
                f"refuse via preflight",
                job_id=ctx.job_id,
            )
        else:
            services.activity_log(
                queue_root, "patch_post_reset",
                f"reset ports/{origin}/ to baseline after patch",
                job_id=ctx.job_id,
            )

        # If the cached health probe shows broken, the env poisoned
        # the result — ENV_BROKEN overrides whatever the LLM said.
        if services.cached_health_broken(env):
            return StepOutcome(
                status="success",
                next_event=JobEvent.ENV_BROKEN,
                detail={"status_str": "env_broken", "patch_status": result.status},
            )

        status_l = (result.status or "").lower()
        if result.status == "success":
            # C1: rebuild_ok is necessary but not sufficient — the port
            # must also have reached a 'converted' dops state. A build
            # that passed via compat artifacts (Makefile.DragonFly / bare
            # dragonfly/*) is not a dops fix; route it to MANUAL with the
            # diff attached rather than stamping agent_fixed.
            post_state = ctx.state.get("post_patch_dops_state")
            if post_state != "converted":
                _try_write_handoff(
                    services, ctx, origin,
                    reason="patch_non_dops_substrate",
                    reason_detail=(
                        f"build passed but port dops_state={post_state!r} "
                        f"(expected 'converted'); fix likely written as "
                        f"compat artifacts instead of overlay.dops"
                    ),
                    patch_result=result,
                )
                return StepOutcome(
                    status="success",
                    next_event=JobEvent.ESCALATE_MANUAL,
                    detail={
                        "status_str": "non_dops_substrate",
                        "patch_status": result.status,
                        "post_patch_dops_state": post_state,
                    },
                )
            # Step 37-4: rebuild_ok=true is necessary but not sufficient
            # for full agent_fixed. The resolver computes the canonical
            # verdicts list, synthesizing "escalated: no verdict
            # provided" for any deferred patch the agent ignored, so
            # an agent that skipped its deferred work doesn't silently
            # route to agent_fixed. Routing decision: ANY escalated
            # verdict (real or synthesized) → MANUAL.
            from dportsv3.agent.runner import (  # noqa: PLC0415
                _resolve_deferred_verdicts_for_patch,
                cleanup_resolved_deferred_patches,
            )
            verdicts = _resolve_deferred_verdicts_for_patch(
                ctx.bundle_dir, bundle_id, result.final_text or "",
            )
            # Step 37 #4-fix: clean up the framework diff files
            # whose verdicts resolved to regenerated/dropped — they
            # were dead weight once the agent landed alternate
            # edits (or proved they weren't needed). Runs only on
            # the rebuild_ok=true path because failure-path drops
            # are speculative.
            if verdicts:
                cleanup_resolved_deferred_patches(
                    env=env, origin=origin, verdicts=verdicts,
                    queue_root=queue_root, job_id=ctx.job_id,
                )
            escalated_paths = [
                v.path for v in verdicts if v.verdict == "escalated"
            ]
            if escalated_paths:
                _try_write_handoff(
                    services, ctx, origin,
                    reason="patch_escalated_verdicts",
                    reason_detail=(
                        f"rebuild ok but {len(escalated_paths)} deferred "
                        f"patch(es) escalated: "
                        f"{', '.join(escalated_paths[:5])}"
                    ),
                    patch_result=result,
                )
                return StepOutcome(
                    status="success",
                    next_event=JobEvent.ESCALATE_MANUAL,
                    detail={
                        "status_str": "escalated_verdicts",
                        "patch_status": result.status,
                        "escalated_paths": escalated_paths,
                    },
                )
            _try_write_proposed_fix(
                services, ctx, origin,
                model=model,
                patch_result=result,
            )
            return StepOutcome(
                status="success",
                next_event=JobEvent.PATCH_OK,
                extra_events=[JobEvent.VERIFY_OK],
                detail={"status_str": "done", "patch_status": result.status},
            )
        if "budget" in status_l:
            _try_write_handoff(
                services, ctx, origin,
                reason="patch_budget_exhausted",
                reason_detail=f"patch ended with status={result.status}",
                patch_result=result,
            )
            return StepOutcome(
                status="success",
                next_event=JobEvent.PATCH_BUDGET_OUT,
                detail={"status_str": result.status, "patch_status": result.status},
            )
        # needs-help / gave-up / anything else — catchall DEAD.
        _try_write_handoff(
            services, ctx, origin,
            reason="patch_gave_up",
            reason_detail=f"patch ended with status={result.status}",
            patch_result=result,
        )
        return StepOutcome(
            status="success",
            next_event=JobEvent.PATCH_GAVE_UP,
            detail={"status_str": result.status, "patch_status": result.status},
        )

    def record(self, ctx: StepCtx, outcome: StepOutcome) -> None:
        # Patch doesn't materialize the bundle into a tempdir
        # (unlike triage), so there's no cleanup to do here. Outputs
        # are persisted inside run() because they depend on the
        # tool_trace + dispatcher state captured during the LLM call.
        return None
