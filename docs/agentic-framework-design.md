# Agentic framework — design sketch

> **Status:** draft, no code yet. Captures the layered design that the
> ad-hoc fixes accumulated in May 2026 (sibling batching, env_broken
> detection, retry cap, automation-context prompt, jobs-table backfill,
> tool tracing) point toward. Goal of this doc is to name the layers,
> draw the interfaces, and lay out a migration that replaces the
> hacks one batch at a time — not to ship the whole framework in one
> commit.

## Why

Every fix landed between commits `8eab9aeb` and `fc2837fd` was reactive
to a specific operational failure:

| Symptom | Fix | Layer it really belongs in |
|---|---|---|
| Duplicate hook-fired triage jobs | sibling batching in `claim_next_job_batch` | **Job lifecycle / orchestration** |
| Harness needs local bundle dir | tempdir + `_materialize_bundle` inlined in runner | **IO / step contract** |
| UI shows no jobs | `_post_job_upsert` sprinkled at 4 call sites | **Observability (event stream)** |
| Patches loop forever on same port | `recent_failure_count` cap in `_process_triage_job_harness` | **Policy engine** |
| Agent thrashes without editing | "Automation Context" section hand-rolled in `build_patch_payload` | **Context assembly** |
| Chroot misconfigured → every job fails | `error_category=env_broken` sentinel + sticky gate | **Health / readiness contract** |
| Patch model unset | env-var fallback in `process_patch_job` | **Configuration resolution** |
| Tool calls invisible mid-run | `on_event` callback + `tool_trace.jsonl` + activity_log raise | **Observability (event stream)** |

Each fix is right. None has a home. So the next symptom needs another
inline patch in another file, and the diff between two "agentic"
operations gets harder to read.

The framework is the missing spine these fixes share.

## Five layers

The proposal is to draw five orthogonal layers, each with one job.
The existing code stays — it moves into a labeled box with a clear
interface.

### 1. Job lifecycle (state machine)

**Today:** a job's state is inferred from three places:
- the `.job` filename's directory (`pending/`, `inflight/`, `done/`, `failed/`)
- the `jobs.state` column in `state.db` (sometimes — only set when
  `_post_job_upsert` is called)
- the `bundles.result` field on the related bundle

The runner code threads through transitions implicitly via `move_job`
calls and `_post_job_upsert` calls. Restart semantics are fuzzy — an
inflight `.job` after a crash has no recovery contract.

**Framework:** explicit states + typed transitions.

```
queued → claimed → triaging → triaged →
                                       ├─→ patching → verifying → done
                                       └─→ escalated (MANUAL)
                                       └─→ dead (env_broken | parse failure)
```

A `Job` carries `(id, type, target, origin, flavor, bundle_id, state,
created_at, claimed_at, last_transition_at, attempt_count,
parent_job_id, retire_reason)`. Single source of truth: state.db's
`jobs` table. Filesystem queue becomes a *durable inbox* (hook → file
→ inserted as `queued` → file deletable). Transitions are pure
functions: `claim(job) → claimed`, `triage_complete(job, outcome) →
triaged | dead`, etc. Each transition writes one row to an
`event_log` table; the activity_log + jobs.state row + UI all read
from that.

### 2. Step contract

**Today:** the runner is a sequence of inline blocks:
`process_triage_job` calls `_process_triage_job_harness` which calls
`_run_harness_triage_inner` which assembles a payload, runs the LLM,
parses, decides tier, maybe enqueues a follow-up. Each step's
preconditions are checked ad-hoc.

**Framework:** every step implements one interface.

```python
class Step(Protocol):
    name: str
    def precheck(self, ctx: JobContext) -> StepReadiness: ...
    def run(self, ctx: JobContext) -> StepOutcome: ...
    def record(self, ctx: JobContext, outcome: StepOutcome) -> None: ...
```

`StepReadiness` ∈ {ready, deferred(reason), refused(reason, fatal=bool)}.
`StepOutcome` carries a typed result + the new job state + any
follow-up jobs to enqueue + audit artifacts to persist.

Concrete steps map to today's work:
- `TriageStep` → today's triage flow
- `PatchAttemptStep` → today's patch flow (one attempt)
- `RebuildVerifyStep` → currently fused into the patch LLM call;
  could split out so verification is independent of the LLM run that
  produced the edits
- `EscalateToManualStep` → today's "force MANUAL" branch

The orchestrator runs `step.precheck()` then `step.run()` then
`step.record()`. It does not know about LLMs, dsynth, or bundles —
those live inside steps. Adding a step (e.g. `KEDBLookupStep` to
short-circuit known fixes, `SecondOpinionStep` to run a stronger
model on low-confidence triage) becomes implementing the protocol.

### 3. Health / readiness as a typed precondition

**Today:** `env_broken` is a stderr regex (`_classify_env_error` in
`worker.py`) that taints one tool result; the runner's callback
notices and sets a sticky flag. Two reactive layers: detection in the
worker, gate in the runner. Other forms of brokenness (missing
distfiles cache, wrong dsynth profile, stale FPORTS pin) have no
mechanism yet.

**Framework:** one structured health check.

```python
@dataclass
class EnvHealth:
    status: Literal["ready", "degraded", "broken"]
    checks: list[HealthCheck]      # each with name, status, detail
    operator_action: str | None     # only when broken
```

Called by every `Step.precheck()` and by the runner-level gate.
`HealthCheck` is per-aspect (`python_runtime`, `dsynth_profile`,
`writable_overlay`, `freebsd_ports_pin`, `dports_compose`). Broken
checks have an `operator_action`. The runner pauses on broken; steps
refuse on broken. No more sentinel-string-matching in tool results;
the health probe is its own thing that runs explicitly when needed.

Implementation hint: dev-env already has `dportsv3 dev-env status
NAME` (JSON output). Extend it to include the deeper checks, and the
agent package consumes that JSON.

### 4. Context assembly as a pipeline

**Today:** `build_triage_payload` and `build_patch_payload` are walls
of `parts.append(...)`. Sections are decided by `if` branches scattered
across 100+ lines. Prompt-size budget is implicit — we hope it fits.

**Framework:** composable sections + explicit budget.

```python
class ContextSection(Protocol):
    name: str
    priority: int                    # higher → kept first under budget pressure
    max_bytes: int                   # cap for this section alone
    def render(self, ctx: JobContext) -> str | None: ...
```

The assembler runs all sections in priority order, accumulates their
rendered text, and truncates the lowest-priority sections when the
total exceeds the model's context budget (minus a margin for the
system prompt and the response).

Today's sections become typed classes: `TriageSection`,
`SiblingBundlesSection`, `PriorAttemptsSection`,
`AutomationContextSection`, `BuildErrorsSection`, `PortFilesSection`,
`KEDBSection`. Each is independently testable. Adding a new section
(e.g. `RelatedPortsSection`, `DragonFlyPortingNotesSection`) is one
class.

### 5. Policy engine

**Today:** tier resolution is in `policy.py:tier_for`. The retry cap
is in `_process_triage_job_harness`. The MANUAL-on-low-confidence
downgrade is in `policy.py`. The "should we even attempt this port"
gate doesn't exist as a thing.

**Framework:** one function.

```python
@dataclass
class Decision:
    action: Literal["auto_patch", "escalate_manual", "skip", "kedb_short_circuit"]
    tier: Tier
    reason: str
    budget: Budget

def decide(
    triage: TriageOutcome,
    history: PortHistory,
    env_health: EnvHealth,
    config: AgenticPolicyConfig,
) -> Decision: ...
```

`PortHistory` carries the count of recent failures, last success
time, prior tiers tried. `Budget` is the resolved `(max_iterations,
max_tokens, max_tool_calls, wall_clock_s)` for this attempt. The
decision is a single value the orchestrator acts on. Easy to
unit-test against frozen `(triage, history, env)` tuples — today's
behavior is regression-tested by feeding in known states and
asserting on the `Decision`.

## Cross-cutting

### Budget accounting

One `Budget` object. Tokens, wall-clock, tool calls, attempts. Each
step opens a `BudgetWindow(budget)` context manager; each LLM call
and each tool call charges it; on exhaustion the step returns
`budget-exhausted` cleanly. Today the same concept is spread across
`tier.max_tokens`, `tier.max_iterations`, `max_tool_turns`,
`max_snippet_rounds`, the `tool_loop`'s internal cap, and the runner's
dsynth lock.

### Event stream

One typed event log. Every transition (job, step, tool call,
decision) emits a typed event. Activity_log, tool_trace.jsonl, jobs
table, UI activity feed are all *consumers* of the same stream. Today
they're three separate writers, each at four call sites, drifting
silently when one is missed.

Storage: `state.db.events` already exists as a rough JSON-blob table;
formalize the event schemas and have consumers query/project from
there.

### Idempotency contract

Each step declares whether it's safe to re-run, at what granularity.
`TriageStep` is idempotent per-bundle (same input → same prompt).
`PatchAttemptStep` is not idempotent (it writes to the overlay).
Recovery after crash uses these declarations: re-run idempotent
steps, mark non-idempotent steps that crashed as `dead` with a
"crashed mid-attempt" reason and require operator decision to retry.

## Migration plan

Order matters: every step depends on the layers below it.

1. **Job lifecycle (layer 1) first.** It's the spine. Add the explicit
   state column, write transitions as named functions even if their
   bodies are still the old inline code, get the state-machine
   invariants tested. Existing UI keeps working because we keep
   writing the jobs table at the same call sites; new column reads
   pull from a real state.

2. **Health/readiness (layer 3) next.** Smallest surface area, useful
   immediately. Replace `env_broken` regex with `EnvHealth.check()`.
   Wire it into the existing gate. Now adding a new health check
   (missing distfile dir, stale FPORTS pin) is one function.

3. **Policy engine (layer 5).** Lift today's tier resolution + retry
   cap into one `decide()` function. Replace the cap inline in
   `_process_triage_job_harness` with a single `policy.decide()`
   call. Unit-test the function against frozen states. This locks in
   the policy as something operators can reason about, not something
   embedded in runner code.

4. **Context assembly (layer 4).** Replace `build_*_payload` with a
   `ContextAssembler.render(sections, budget)`. Each section is one
   class. Once this lands, prompt-budget overruns become a layer
   concern, not a "we hope it fits" concern.

5. **Step contract (layer 2) last.** Biggest refactor, only worth it
   once 1, 3, 4, 5 have stabilized the interfaces it depends on.
   Replace `process_triage_job` + `process_patch_job` with
   `Orchestrator.run(job, [TriageStep(), PatchAttemptStep(),
   RebuildVerifyStep()])`.

Each migration is *additive at first* (new layer side-by-side with
old code) then *cutover* (delete the inline equivalent) only after
end-to-end tests confirm parity. No single commit should both add
the new layer and delete the old. We've learned this from previous
phases — partial migrations leave both code paths live and confusing.

## Pilot

Before committing to migration 1, pilot the design on **one new
feature** that would have been another hack:

> "Try the patch with a cheap model first; if it returns
> `gave-up` after one attempt, escalate to a stronger model for one
> more attempt."

Build it inside the framework even though the framework doesn't
exist yet — write a `TwoModelEscalationStep`, an `EnvHealth.check()`,
a `decide()` that resolves which model. Implementation will surface
the interface shape. If it feels natural to express, the layer
boundaries are right. If we fight them, redesign before committing
to the migration.

## Non-goals (explicit)

- No new dependencies. No state-machine library, no DI container, no
  workflow engine. Plain Python protocols + dataclasses + sqlite.
- No microservices. The artifact-store split (Phase 4) was forced by
  the "one writer to state.db" rule, not by deployment topology. The
  agentic loop stays one process.
- No new ports backend, no Docker, no Kubernetes. The dev-env chroot
  is the execution substrate.
- No abstract base classes where Protocol + duck typing works.

## What this doc isn't

A spec. The interfaces above are sketches — the real signatures will
shift as the pilot exposes constraints. The point is to agree on the
*layering* before refactoring. Code-level details live in per-layer
implementation plans (TBD, one per migration step).

## Status of follow-up

| Item | Status |
|---|---|
| Pilot the two-model escalation idea | TODO |
| Detailed layer-1 implementation plan | TODO (after pilot) |
| Per-layer test plan | TODO |
| Decision: this doc → ADR (architecture decision record) or stays exploratory? | TODO |

## Future work (parking lot)

Ideas surfaced during phase implementation that don't fit the
current layer's scope but should be revisited later. These are
notes, not commitments — the next phase decides whether each item
is worth promoting into its plan.

| Idea | Surfaced in | Why it's parked |
|---|---|---|
| **Operator notification on env-broken / persistent failure.** When the runner skips work because the env is broken, or when a (target, origin) hits the retry cap, the operator currently has to discover this by polling the UI / activity log. A push channel (email/webhook/Slack/badge) would let the operator act sooner. | Phase 3 (decision: env-broken → skip) | Needs a notification framework; out of scope for the policy engine. Candidate for a dedicated layer-6 or post-phase-5 polish. |
