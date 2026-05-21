# Agentic framework — plan (rolling ledger)

Rolling-ledger format: shipped phases summarized at top, current phase
detail below. Full arc and rationale in
`agentic-framework-design.md`; overview of remaining phases at the
bottom of this file.

---

## Shipped phases

### Phase 1 — Lifecycle (shipped 2026-05-20)

Typed `JobState` + `JobEvent` enums and transition table backed by a
new `job_events` table. `lifecycle.apply()` is the single entry point
for state changes; `current()`, `history()`, and `reap_orphans()`
round out the module. Runner cutover deleted `_post_job_upsert` and
the artifact-store's `upsert_job`; the `/v1/jobs/upsert` endpoint
renamed to `/v1/jobs/transition`. UI templates + count queries
updated for typed values. As a bonus refactor, the 2330-LOC
`scripts/agent-queue-runner` script became a 23-line shim over the
new `dportsv3.agent.runner` module, dropping the `execv` workaround
in `dportsv3 agent-queue-runner` and making the runner internals
importable for tests.

Commits: `42df53620f6` (schema) · `71ed4a38945` (lifecycle module) ·
`407795d1793` (cutover) · `365344cc329` (runner module move) ·
`164df2bb8b2` (e2e tests + policy-path fix).

Test delta: +26. 275 total green at phase end.

### Phase 2 — Health / readiness (shipped 2026-05-21)

Replaced the inferred-from-stderr `env_broken` path with a direct
structured probe. New `dportsv3.agent.health` module with
`EnvHealth` + `HealthCheck` dataclasses and three named checks:
`python_runtime` (py311-* deps via `pkg info -e`), `writable_overlay`
(sentinel-file touch), and `dports_compose` (`dportsv3 --version`
inside the env). Aggregate status is `ready`/`degraded`/`broken`.

Runner cutover deleted `_env_broken_reason`, `_classify_env_error`,
and `_ENV_BROKEN_SENTINELS`. The gate now consults
`probe_health_cached(env, ttl_seconds)` (default 60s TTL,
`DP_HARNESS_HEALTH_CACHE_SECONDS`). Tool errors matching a known
suspicious pattern force a cache re-probe but never set state — the
probe is authoritative. **Operator no longer has to restart the
runner after fixing the chroot**; the next gate cycle picks it up.

New operator-facing CLI: `dportsv3 dev-env health NAME` emits JSON
and exits 0/1/2 (ready/broken/degraded).

Commits: `b47918ba311` (module) · `93dd9f5244d` (cutover) ·
`f221a0a154f` (CLI subcommand) · `f11fe8d1c6b` (cache integration
tests + `probe_health_cached` extraction).

Test delta: +33. 310 total green at phase end.

### Phase 3 — Policy engine (shipped 2026-05-21)

Consolidated tier resolution + retry cap + env-broken short-circuit
into one `decide(classification, confidence, history, env_health,
policy) -> Decision` function. The orchestrator routes on
`decision.action` ∈ {`auto_patch`, `escalate_manual`, `skip`}.

New module `dportsv3.agent.decision` with `Decision`, `PortHistory`,
and `decide()`. `PortHistory.load(conn, target, origin,
window_hours)` absorbs the runner's `recent_failure_count` query;
that helper got deleted. The lone remaining `tier_for` call site
is `_process_patch_job_harness`'s legacy fallback for hand-fired
patch jobs missing the `tier` field — explicitly out of scope; Phase
5 absorbs it.

Parity test sweeps every `(classification, confidence)` in the
shipped `config/agentic-policy.json` against legacy `tier_for`.
Side artifact: future-work parking lot in
`agentic-framework-design.md` for operator notification on
env-broken / cap.

Commits: `2f6d4c604d8` (design doc parking-lot entry) ·
`89d66e2d4a3` (module) · `30f7a50bc76` (runner cutover) ·
`69fd1be477c` (parity smoke).

Test delta: +27 (19 unit + 8 parity). 337 total green at phase end.

### Phase 4 — Context assembly (shipped 2026-05-21)

Replaced the two `build_*_payload` walls of `parts.append(...)`
with composable `ContextSection` classes the `ContextAssembler`
renders in priority order. Strict byte-for-byte parity — no LLM
prompt changes, just structure.

New module `dportsv3.agent.context` with `ContextCtx`,
`ContextSection` Protocol, `render_payload`, and 13 concrete
section classes:

- Reused across triage + patch (7): `SnippetsRoundSection`,
  `KEDBSection`, `UserContextSection`, `MetadataSection`,
  `BuildErrorsSection`, `PortFilesSection`,
  `ExistingPatchesSection`. `SiblingBundlesSection` is shared with
  a `with_intro` flag (triage uses True; patch uses False).
- Triage-specific (3): `PriorTriagesSection`,
  `TriagePromptFooterSection`, plus the shared sibling section.
- Patch-specific (4): `AutomationContextSection`,
  `TriageSummarySection`, `PriorAttemptsSection`,
  `PatchPromptFooterSection`.

I/O isolation: sections never query DB or network at render time.
Callers pre-load fields (`prior_*_bundle_ids`, `user_context_text`,
`kedb_text`, `prior_failure_count`) and bind runner-side helpers as
callables into `ContextCtx` (`read_bundle_text`,
`bundle_artifact_list`, `snippet_feedback`, `snippet_content`).

`build_triage_payload` shrunk from ~155 LOC to ~36 LOC of pre-load
+ render. `build_patch_payload` shrunk from ~190 LOC to ~50 LOC.

12 parity test cases (6 triage + 6 patch) lock byte-equivalence.

Commits: `2d3cb9e367c` (module) · `331e526fa54` (triage cutover) ·
`399224ea029` (patch cutover).

Test delta: +25 (13 assembler + 6 triage parity + 6 patch parity).
362 total green at phase end.

---

## Current phase: Phase 5 — Step contract

> **Goal:** formalize what Phases 1–4 sketched. Define the `Step`
> Protocol with `precheck → run → record` hooks, build an
> `Orchestrator` that drives steps in sequence, and replace the
> hand-coded `_process_{triage,patch}_job_harness` /
> `_run_harness_triage_inner` / `process_job` dispatch with
> orchestrator-driven flows. End state: the runner is a small
> driver that hands `(job, [steps])` to the orchestrator; the
> heavyweight per-step logic lives in named classes with explicit
> preconditions and outcomes.

### Decisions captured up front

- **Step Protocol shape.** Each step exposes `name`,
  `precheck(ctx) -> StepReadiness`, `run(ctx) -> StepOutcome`,
  `record(ctx, outcome) -> None`. The orchestrator calls precheck
  (skips if not ready), run (the actual work), then record
  (persists artifacts + fires lifecycle events).
- **No verify split in this phase.** Today `dsynth_build` is fused
  inside the patch LLM call (the agent runs it as a tool). Splitting
  verification into a separate `RebuildVerifyStep` would require the
  agent to *stop* before verifying and the orchestrator to verify
  itself — meaningful behavior change. Out of scope; Phase 5 only
  ships the Step protocol with `RebuildVerifyStep` as a *named*
  step that's fused into `PatchAttemptStep` for now. The actual
  split is a follow-up.
- **The legacy `tier_for` fallback in `_process_patch_job_harness`
  gets folded.** When a hand-fired patch job arrives with no
  `tier` field, the orchestrator's `decide()` call uses
  classification + confidence parsed from the bundle's `triage.md`
  exactly as the legacy code does, via the Phase-3 decision engine.
- **Hard cutover.** Same as Phases 1–4: when each step lands, the
  legacy equivalent is deleted in the same commit. The five-commit
  budget plans accordingly.
- **Parity through end-to-end.** The Phase-5 cutover is the only
  one that touches the full triage→patch dispatch. The existing
  e2e lifecycle tests + the 12 parity tests from Phase 4 catch
  byte-level regressions. Add a fresh e2e test for the
  orchestrator flow itself.

### Pre-conditions

- All 362 tests currently green.
- `lifecycle.apply` is the single state-write boundary. Verified
  end of Phase 1.
- `decide()` is the single decision boundary. Verified end of
  Phase 3.
- `render_payload` is the single payload-assembly boundary.
  Verified end of Phase 4.

### Step Protocol shape

```python
@dataclass
class StepReadiness:
    status: Literal["ready", "skip", "fail"]
    reason: str = ""
    # When status="skip", orchestrator moves to the next step.
    # When status="fail", orchestrator halts with the reason.

@dataclass
class StepOutcome:
    status: Literal["success", "needs-help", "failed", "skipped"]
    next_event: JobEvent | None
    detail: dict = field(default_factory=dict)
    # next_event is the lifecycle transition to fire after this step.
    # None means "step is internal; no transition."

@runtime_checkable
class Step(Protocol):
    name: str
    def precheck(self, ctx: StepCtx) -> StepReadiness: ...
    def run(self, ctx: StepCtx) -> StepOutcome: ...
    def record(self, ctx: StepCtx, outcome: StepOutcome) -> None: ...
```

`StepCtx` carries job + DB conn + lifecycle helpers + the
`dportsv3.agent.{health,decision,context}` machinery. Likely
absorbs `ContextCtx` (Phase 4) since payload assembly is one
step's concern.

### Step 1 — `step.py` module

**Goal:** the protocol, `StepReadiness` / `StepOutcome` /
`StepCtx`, and an `Orchestrator.run(job_id, [steps])` that:

1. Loads job state from `lifecycle.current()`.
2. For each step in order:
   - calls `precheck(ctx)`; if `skip`, continues; if `fail`,
     halts and fires the appropriate failure event;
   - calls `run(ctx)`;
   - calls `record(ctx, outcome)`;
   - fires `outcome.next_event` via `lifecycle.apply()` if set.
3. Returns a summary of step outcomes.

Plus unit tests covering the dispatch logic (skip propagation,
fail-halt behavior, event firing, exception isolation).

**Done criteria:** module importable + tested, **no consumers wired**.

**Commit:** `feat(agent): step orchestrator module`

---

### Step 2 — `TriageStep`

**Goal:** lift `_run_harness_triage_inner`'s body into a
`TriageStep` class. The runner's `_process_triage_job_harness`
becomes a wrapper that constructs a `StepCtx`, instantiates
`TriageStep`, and hands it to the orchestrator.

**What lives in `TriageStep.run`:**
- The bundle materialization (`_materialize_bundle` for
  artifact-store bundles)
- The LLM call (`harness_triage.run`)
- The `_write_triage_audit_harness` write
- The `decide()` call
- The `enqueue_patch_job` / `upsert_user_context_request` branches

`TriageStep.record` writes the triage.md to the bundle if
materialized, cleans up the tempdir, fires the activity_log
`decision` entry.

**Parity:** the existing `test_runner_e2e_lifecycle.py` cases
(`test_full_triage_path_to_triaged`, `test_triage_manual_escalates`)
must still pass with the orchestrator-driven flow. Add a new test
that asserts `TriageStep.precheck` skips if env_health is broken.

**Cutover criteria:**
- `_run_harness_triage_inner` deleted; `_process_triage_job_harness`
  is a thin wrapper around the orchestrator.
- e2e tests green; parity tests green.

**Commit:** `refactor(agent): triage as a Step`

---

### Step 3 — `PatchAttemptStep`

**Goal:** lift `_process_patch_job_harness` body into
`PatchAttemptStep`. Today's body is bigger — it includes
`_write_patch_audit_harness`, `_write_changes_diff`,
`_write_tool_trace`, the `on_event` callback infrastructure, and
the policy fallback that this phase absorbs.

The legacy `tier_for` fallback (when a hand-fired job has no
`tier` field) becomes: `PatchAttemptStep.precheck` parses
`triage.md` if needed and calls `decide()` to set the tier.

**Parity:** the existing patch parity tests + e2e tests must pass.
Add a new test for the precheck path (hand-fired patch job →
`decide()` resolves tier from triage.md).

**Cutover criteria:**
- `_process_patch_job_harness` and `_run_harness_patch_inner` (if
  any) deleted.
- `_process_patch_job` is a thin wrapper.
- The lone `harness_policy.tier_for(pol, ...)` call from Phase 3
  is gone (now flows through `decide`).
- All tests green.

**Commit:** `refactor(agent): patch as a Step`

---

### Step 4 — `process_job` orchestrator cutover

**Goal:** the runner's `process_job` becomes a small dispatcher
that constructs the right step list for the job type and hands it
to the orchestrator. Today's hard-coded `if job_type == "patch":
process_patch_job(...) elif "triage": ...` block is replaced.

**What still lives in `process_job`:**
- `.job` file parsing (`parse_job_file`)
- The filesystem move (pending→inflight→done/failed)
- Sibling-paths bookkeeping (Phase 1)
- The `_completion_events_for` mapping — moves into orchestrator
  as the post-run event resolution

**What moves into the orchestrator:**
- TRIAGE_START / PATCH_START / completion event firing
- The decision routing (auto_patch / escalate_manual / skip)
- The siblings-also-get-events fan-out

**Cutover criteria:**
- `process_job` is ≤ 60 LOC.
- `_completion_events_for` either deleted or simplified to a
  helper consumed by the orchestrator.
- All e2e tests green.

**Commit:** `refactor(runner): orchestrator-driven process_job`

---

### Step 5 — Cleanup + parity smoke

**Goal:** delete any vestigial helpers superseded by the
orchestrator. Add a fresh e2e parity test that walks a full
triage→auto_patch chain through the orchestrator (stubbed LLM)
and asserts identical lifecycle event sequences vs. pre-Phase-5.

Final pass: check `grep` for anything stale —
`_run_harness_triage_inner`, `_run_harness_patch_inner` (if it
ever existed), the legacy `tier_for` call in patch code, etc.

**Commit:** `test(agent): orchestrator e2e parity`

---

### Phase 5 cutover criteria (overall)

Phase 5 is "done" when all of:

1. All steps committed.
2. `pytest scripts/generator/tests/` green.
3. `grep -nE "_run_harness_(triage|patch)_inner|_process_(triage|patch)_job_harness" scripts/`
   returns nothing live in code.
4. `grep -nE "harness_policy\\.tier_for" scripts/generator/dportsv3/agent/runner.py`
   returns nothing — the Phase-3 leftover is folded.
5. e2e lifecycle test + new orchestrator parity test cover the
   happy-path triage→auto_patch chain end to end with stubbed LLM.
6. Manual smoke on dfly: trigger one real triage and one real
   patch, eyeball the activity log + bundle artifacts —
   indistinguishable from pre-Phase-5.
7. This plan file gets updated: Phase 5 ledger entry written;
   Phases overview shows all 5 shipped.

### Risk + rollback

| Step | Risk | Mitigation |
|---|---|---|
| 1 | Protocol shape too restrictive once first real step is built | Land protocol with no consumers; Steps 2–3 exercise it immediately. Iterate in same commit if needed. |
| 2 | Triage refactor breaks bundle materialization edge cases | `TriageStep.record` handles tempdir cleanup just as today; e2e test catches regressions. |
| 3 | Patch refactor loses the `_on_event` callback chain that feeds activity_log + tool trace | `PatchAttemptStep.run` wires the same callback; existing tool-trace test catches gaps. |
| 4 | `process_job` cutover misses an edge case (sibling bookkeeping, error-note writing) | Step 5 e2e test exercises siblings + failure paths; manual smoke confirms. |
| 5 | Hand-fired patch jobs (no `tier` field) regress because Phase-3 leftover removed | Step 3 includes a test for this exact case. |

---

## Phases overview (status)

| # | Phase | Layer(s) | Status |
|---|---|---|---|
| 1 | Lifecycle | Layer 1 | ✅ shipped |
| 2 | Health / readiness | Layer 3 | ✅ shipped |
| 3 | Policy engine | Layer 5 | ✅ shipped |
| 4 | Context assembly | Layer 4 | ✅ shipped |
| **5** | **Step contract** | **Layer 2** | **active (this doc)** |

Estimated sizing for Phase 5:

| Step | LOC delta | Risk |
|---|---|---|
| 1 (protocol + orchestrator) | +200, −0 | Low |
| 2 (TriageStep) | +200, −250 | Medium |
| 3 (PatchAttemptStep) | +250, −300 | Medium |
| 4 (process_job cutover) | +100, −200 | Medium |
| 5 (cleanup + e2e parity) | +80, −50 | Low |
| **Total** | **+830, −800** | **Medium** |

Each step has a **parity check** against today's behavior. No
regression on something that works.

## Future-work parking lot (in design doc)

- Operator notification on env-broken / cap (`2f6d4c604d8`).
- `RebuildVerifyStep` actually splits verification out of the
  patch LLM call (named in Phase 5 but kept fused; the split is a
  follow-up that requires changing the agent's contract).
