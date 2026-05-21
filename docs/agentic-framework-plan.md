# Agentic framework — plan (rolling ledger)

Rolling-ledger format: shipped phases summarized at top, current phase
detail below. Full arc and rationale in
`agentic-framework-design.md`.

**Framework migration is complete.** All five layers from the
design doc are shipped (commit `0a86d12f8ce` and earlier). The
runner is a thin orchestration shell over a `Step` protocol; the
heavyweight per-job logic lives in named classes with explicit
preconditions, outcomes, and lifecycle events.

The current "phase" is therefore **stabilization** — verify the
framework holds up under real DragonFly traffic, exercise the
parking-lot items, and decide which framework-native feature to
build first.

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
that helper got deleted. Parity test sweeps every `(classification,
confidence)` in the shipped `config/agentic-policy.json` against
legacy `tier_for`. Side artifact: future-work parking lot in
`agentic-framework-design.md` for operator notification on
env-broken / cap.

Commits: `2f6d4c604d8` (design doc parking-lot entry) ·
`89d66e2d4a3` (module) · `30f7a50bc76` (runner cutover) ·
`69fd1be477c` (parity smoke).

Test delta: +27. 337 total green at phase end.

### Phase 4 — Context assembly (shipped 2026-05-21)

Replaced the two `build_*_payload` walls of `parts.append(...)`
with composable `ContextSection` classes the `ContextAssembler`
renders in priority order. Strict byte-for-byte parity — no LLM
prompt changes, just structure.

New module `dportsv3.agent.context` with `ContextCtx`,
`ContextSection` Protocol, `render_payload`, and 13 concrete
section classes. 7 sections shared between triage + patch (Snippets,
KEDB, UserContext, Metadata, BuildErrors, PortFiles, ExistingPatches);
`SiblingBundlesSection` parameterized via `with_intro`; 3 triage-
specific (PriorTriages, TriagePromptFooter), 4 patch-specific
(AutomationContext, TriageSummary, PriorAttempts, PatchPromptFooter).

I/O isolation: sections never query DB or network at render time.
Callers pre-load fields and bind runner-side helpers as callables
into `ContextCtx`. `build_triage_payload` shrunk from ~155 LOC to
~36; `build_patch_payload` from ~190 to ~50. 12 parity test cases
lock byte-equivalence.

Commits: `2d3cb9e367c` (module) · `331e526fa54` (triage cutover) ·
`399224ea029` (patch cutover).

Test delta: +25. 362 total green at phase end.

### Phase 5 — Step contract (shipped 2026-05-21)

Formal `Step` Protocol with `precheck → run → record` hooks;
`Orchestrator.run(ctx, [steps])` drives them; lifecycle event
firing intrinsic to each step's `StepOutcome.next_event` +
`extra_events`. The legacy `_process_{triage,patch}_job_harness`,
`_run_harness_triage_inner`, and `_completion_events_for`
indirection all deleted.

New modules: `dportsv3.agent.step` (Protocol + Orchestrator) and
`dportsv3.agent.steps` (`TriageStep`, `PatchAttemptStep`,
`PatchEventDispatcher`). The Phase-3 leftover `tier_for` fallback
for hand-fired patch jobs absorbed via `decide(empty_history,
env_health=None)`.

`process_job` is now a pure dispatcher (~70 LOC): parse, dry_run,
fire START events, delegate to step wrapper, move files. Sibling
event fan-out lives in `_finish_orchestrator_run`.

Latent bug caught + fixed in Step 5: the lifecycle TRANSITIONS
table had no `(CLAIMED, PATCH_START) → PATCHING` entry, meaning
every patch job silently failed its PATCH_START transition (the
runner's IllegalTransition handler just logged a warning). No
pre-Phase-5 test exercised the patch lifecycle.

Commits: `7afe33370c1` (Orchestrator module) · `ec86d37d7f0`
(TriageStep) · `d937b17f344` (PatchEventDispatcher) ·
`a616c1b5d57` (PatchAttemptStep + tier_for absorption) ·
`9eff04962e0` (process_job cutover + _completion_events_for
retired) · `0a86d12f8ce` (e2e parity + lifecycle fix).

Test delta: +45. 407 total green at phase end.

---

## Current phase: Stabilization

> **Goal:** verify the framework holds up under real DragonFly
> traffic, gather observability on phase-shipped behaviors that
> haven't been exercised in production yet, and pick the next
> framework-native feature to build.

### Open items

1. **Manual smoke on dfly.** None of Phases 1-5 has been
   end-to-end exercised against a real dsynth failure stream.
   The 407 unit/integration tests give confidence in the code
   surface; only real traffic verifies the operational chain.
   Specifically check: lifecycle event sequence on a real
   triage→auto_patch→done chain, env_broken auto-recovery
   when chroot is repaired mid-run, retry cap firing when an
   origin loops, sibling fan-out on a batch.

2. **Parallel review.** A separate agent has been briefed to
   audit the framework against the design doc (see prompt
   shared with operator). Findings will land in a follow-up
   document; this plan file gets updated when they're triaged.

3. **Operator notification (parking lot).** Currently when
   `decide()` returns `skip` because env is broken, or
   `escalate_manual` after the retry cap fires, the operator
   has to poll the UI / activity log to find out. A push
   channel (webhook / Slack / badge) would let them act
   sooner. Surfaced during Phase 3; recorded in
   `agentic-framework-design.md`'s parking lot
   (`2f6d4c604d8`).

4. **`RebuildVerifyStep` split (parking lot).** Today
   `dsynth_build` runs inside the patch LLM call (the agent
   itself decides to invoke the rebuild tool). A real
   verify-after-the-LLM-finishes step would change the
   agent's contract — meaningful behavior change, not just a
   structural refactor. Named in the Phase 5 Step protocol but
   fused into `PatchAttemptStep` for now.

5. **Pilot feature: two-model escalation.** Originally drafted
   as the Phase 1 pilot, then dropped per yolo-mode operator
   preference. Re-evaluate now that the Step protocol exists:
   *cheap model for first attempt; on gave-up, escalate to
   strong model with the cheap model's reasoning as seed
   context.* Trivial to express as a step list: `[CheapPatchStep,
   ConditionalStrongPatchStep]`. Tests the orchestrator's
   conditional-step muscle (precheck returns skip based on
   prior step's outcome via ctx.state).

### What this phase is not

- Not a code-change phase by default. The framework migration
  is the LOC budget for the month. Stabilization adds tests +
  small follow-ups, not new layers.
- Not the place to discover new requirements. If a new
  layer is needed (e.g. operator notification), it gets a
  parking-lot entry in the design doc and a future phase plan,
  not a snuck-in change here.

### Possible next actions (operator picks one)

| Action | Effort | When useful |
|---|---|---|
| Run a real dfly triage+patch and post the event log here | 1-2 hours | Right now — pure operational validation |
| Read the parallel agent's report when it arrives | 15 min | When the agent finishes |
| Implement two-model escalation as a framework-native pilot | ~3 commits, ~+250 LOC | If pilot validates Step protocol shape and adds real value |
| Implement operator notification | ~4 commits, ~+300 LOC | If gnome_subr-style loops are still a problem in practice |
| Split `RebuildVerifyStep` out of PatchAttemptStep | ~3 commits, ~+200 LOC | Only if the operator wants separation between LLM-decided rebuild and a runner-side rebuild verification |

---

## Phases summary

| # | Phase | Layer(s) | Status |
|---|---|---|---|
| 1 | Lifecycle | Layer 1 | ✅ shipped |
| 2 | Health / readiness | Layer 3 | ✅ shipped |
| 3 | Policy engine | Layer 5 | ✅ shipped |
| 4 | Context assembly | Layer 4 | ✅ shipped |
| 5 | Step contract | Layer 2 | ✅ shipped |

Framework state: **all five layers shipped, parity-tested, 407
unit/integration tests green**. The runner is a thin orchestration
shell; per-job logic lives in named Step classes with explicit
preconditions and typed outcomes. Adding new functionality is now
"implement a Step / a section / a check / a decision rule" — not
"find the right place to splice into the runner."

## Future-work parking lot (in design doc)

`agentic-framework-design.md` carries durable notes on items that
surfaced during phase implementation but didn't fit the active
layer's scope:

- **Operator notification on env-broken / cap** — push channel
  for skip + cap escalation events.
- **`RebuildVerifyStep` actual split** — name lives in the Step
  protocol; behavioral split is a follow-up.

Both will likely become new phases when promoted, with their own
plan documents that replace this file when active.
