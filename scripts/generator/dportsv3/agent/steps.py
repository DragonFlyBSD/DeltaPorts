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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .step import Step, StepCtx, StepOutcome, StepReadiness


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
