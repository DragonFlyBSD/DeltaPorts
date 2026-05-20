# Agentic framework — Phase 1 plan: lifecycle + pilot

> **Phase:** 1 of N (see `agentic-framework-design.md` for the arc).
> **Goal:** install the job lifecycle layer (layer 1) and validate the
> design via one pilot feature. Nothing else.
>
> When Phase 1 ships, this file gets **rewritten** to be Phase 2's
> plan. The arc and historical scaffolding stay in
> `agentic-framework-design.md`.

## Scope of Phase 1

**In scope:**
- Layer 1: a typed job lifecycle state machine backed by `state.db`.
- The pilot: `TwoModelEscalationStep` — first attempt uses a cheap
  model; on `gave-up` outcome, second attempt uses a strong model.
  Built using whatever interface shape feels natural for the
  *future* `Step` protocol, not against today's `_process_patch_job_harness`.
- Tests: state-machine invariants + pilot end-to-end on a known port.

**Out of scope (deferred to later phases):**
- Layer 2 (Step contract as a formal Protocol) — the pilot will hand-
  shape one step; we won't generalize until layer 3, 4, 5 settle.
- Layer 3 (Health/readiness) — keep today's `env_broken` regex.
- Layer 4 (Context assembly) — keep today's `build_*_payload`.
- Layer 5 (Policy engine) — keep today's `policy.tier_for` + the cap.
- UI changes — the new state column surfaces through existing
  queries; templates already render `job.state`.

The hard cutover principle (per user preference): when each piece in
this phase lands, the equivalent old code is **deleted in the same
commit**. No dual code paths.

## Pre-conditions before starting

These must be true at HEAD of master before Phase 1 begins:

1. The May 20 operational fixes (sibling batching, env_broken,
   retry cap, loop-aware prompt) are verified working on a real
   dsynth run end-to-end. Without that we'll mistake framework bugs
   for inherited bugs.
2. `dportsv3.agent.policy.Tier` is the single source of truth for tier
   names. Today `_process_triage_job_harness` and `process_patch_job`
   both consult `policy.tier_for`; if any other code path hard-codes
   tier names, fix that first.
3. `state.db`'s `jobs` table is being populated for new jobs (the
   `_post_job_upsert` plumbing from `f1272152971`). Verify with the
   smoke `curl http://127.0.0.1:8788/v1/jobs/upsert ...` and a
   `SELECT * FROM jobs LIMIT 5`.

If any precondition fails, fix it first, don't paper over.

## Layer 1: Job lifecycle

### The state set

Final, explicit, exhaustive:

```
queued       — written by hook on dsynth failure, .job file in pending/
claimed      — runner has moved .job to inflight/, before any step starts
triaging     — TriageStep running
triaged      — TriageStep complete, awaiting next-step decision
patching     — PatchAttemptStep running (one attempt)
verifying    — RebuildVerifyStep running (separate from patching;
               currently fused into the patch LLM call — pilot will
               split them)
done         — rebuild_ok=true, no further work
escalated    — MANUAL tier resolved; operator must act
dead         — terminal failure: env_broken, parse error, exhausted
               budget without progress
```

Allowed transitions are a small fixed table; anything else is a
bug. Codified as a Python `dict[tuple[State, Event], State]`.

### The schema

One migration:

```sql
ALTER TABLE jobs ADD COLUMN state_machine_state TEXT;  -- the new typed value
ALTER TABLE jobs ADD COLUMN last_transition_at TEXT;
ALTER TABLE jobs ADD COLUMN retire_reason TEXT;        -- why dead/escalated
```

The existing `jobs.state` column stays — it's a coarse filesystem-
queue mirror used by the UI today. The new `state_machine_state`
column is the typed source of truth. UI templates will eventually
read the new column; in Phase 1 we just populate both and let the
old column become read-only legacy.

`event_log` table — new, not folding into existing `activity_log`
(different schemas, different consumers):

```sql
CREATE TABLE job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    job_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    event_name TEXT NOT NULL,
    detail_json TEXT
);
CREATE INDEX idx_job_events_job ON job_events(job_id, id);
```

One row per transition. The job's current `state_machine_state` is
`SELECT to_state FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT 1`.
Caching that in `jobs.state_machine_state` is a denormalization for
query speed; `job_events` is authoritative.

### The transitions module

New file `scripts/generator/dportsv3/agent/lifecycle.py`:

```python
class JobState(StrEnum): ...
class JobEvent(StrEnum): ...
@dataclass
class Transition: from_state, event, to_state, guard?

TRANSITIONS: dict[tuple[JobState, JobEvent], JobState] = {...}

def apply(conn, job_id, event, detail=None) -> JobState:
    """Atomic state transition.

    Reads current state, validates (state, event) is in TRANSITIONS,
    writes a row to job_events, updates jobs.state_machine_state, all
    under one transaction. Raises IllegalTransition if invalid.
    """

def current(conn, job_id) -> JobState: ...
```

No file I/O, no LLM calls, no subprocess. Pure state-machine logic
+ sqlite. ~150 LOC.

### Wiring into the runner

Hard cutover: the existing `move_job(path, dest)` + `_post_job_upsert(job_id, state)` pairs are replaced by a single `lifecycle.apply(conn, job_id, event)` call. The filesystem move stays
(`.job` files are still the inbox); the state update goes through
the new module.

Touched files:
- `scripts/agent-queue-runner` — the four upsert call sites become
  `lifecycle.apply()` calls with explicit event names (`claimed`,
  `triage_started`, `triage_succeeded`, `patch_succeeded`, etc.).
  `_post_job_upsert` is **deleted** in the same commit.
- `scripts/generator/dportsv3/artifact_store.py` — the
  `/v1/jobs/upsert` endpoint stays for hook-fired writes (the
  `queued` transition), but internally it calls `lifecycle.apply()`.
  No more direct `INSERT INTO jobs`.

### Tests

`scripts/generator/tests/test_lifecycle.py`:

1. Every defined `(state, event) → state` round-trips through
   `apply()` and writes one event row.
2. Disallowed transitions raise `IllegalTransition` and write no row.
3. `current()` returns the latest event's `to_state` even if the
   denormalized `jobs.state_machine_state` is stale (simulate by
   updating only `job_events`).
4. Concurrent `apply()` from two threads: exactly one wins,
   the other sees the post-conditional state and either raises or
   no-ops based on event semantics.
5. End-to-end through a synthetic job: `queued → claimed → triaging →
   triaged → patching → verifying → done` produces 6 event rows in
   order.

## The pilot: TwoModelEscalationStep

### What it does

A patch job today runs one attempt loop with one model (the
fallback handles unset patch model). The pilot adds a step that:

1. Runs the patch agent with a **cheap model** for one attempt.
2. If the outcome is `gave-up` (the Patch Log explicitly states the
   agent couldn't make progress), runs a **second attempt with a
   strong model**, seeding the prompt with the cheap model's
   gave-up reasoning.
3. Returns the final outcome.

This is the smallest feature that exercises:
- The `Decision` shape (which model to use for which attempt)
- The `Budget` shape (each attempt has its own budget)
- The `Step` protocol's `run(ctx) -> Outcome` shape
- An event stream consumer (each attempt's outcome is an event)

It's **not** the full Step Protocol; we're hand-shaping one step to
see what the protocol should look like.

### Implementation

New file `scripts/generator/dportsv3/agent/steps/two_model_escalation.py`:

```python
@dataclass
class TwoModelEscalationConfig:
    cheap_model: str               # DP_HARNESS_PATCH_MODEL_CHEAP or fallback
    strong_model: str              # DP_HARNESS_PATCH_MODEL_STRONG (required for escalation)
    cheap_budget: Budget           # max_iterations=1, smaller max_tokens
    strong_budget: Budget          # max_iterations=2, larger max_tokens

def run(ctx: JobContext, config: TwoModelEscalationConfig) -> StepOutcome:
    cheap_result = run_patch_attempt(ctx, model=config.cheap_model, budget=config.cheap_budget)
    if cheap_result.status == "success":
        return success_outcome(cheap_result)
    if not _is_gave_up(cheap_result):
        # budget-exhausted, needs-help — don't waste the strong model on a thrasher
        return needs_help_outcome(cheap_result)
    # Strong model gets the cheap model's gave-up reasoning as seed context
    seed = _format_gave_up_context(cheap_result)
    strong_result = run_patch_attempt(ctx, model=config.strong_model, budget=config.strong_budget, seed_context=seed)
    return outcome_from(strong_result, prior_attempts=[cheap_result])
```

`run_patch_attempt` is a refactor extraction of today's
`harness_patch.run(...)` call — the existing code becomes a single
function the new step calls. No behavior change in the existing path.

### Configuration

Two new env vars:

```
DP_HARNESS_PATCH_MODEL_CHEAP   — defaults to DP_HARNESS_TRIAGE_MODEL
DP_HARNESS_PATCH_MODEL_STRONG  — required to enable escalation;
                                 if unset, only the cheap attempt runs
                                 (one-shot, today's behavior)
```

Feature flag: `DP_HARNESS_TWO_MODEL_ESCALATION=1` to enable. Off by
default during Phase 1 so we don't change production behavior; the
operator turns it on per test.

### Tests

`scripts/generator/tests/test_two_model_escalation.py`:

1. Cheap succeeds → strong is never called.
2. Cheap returns gave-up → strong is called with seed context that
   contains the cheap model's Patch Log.
3. Cheap returns budget-exhausted → strong is not called (escalation
   is only for gave-up, not for thrash).
4. `DP_HARNESS_PATCH_MODEL_STRONG` unset → escalation skipped, even
   if `TWO_MODEL_ESCALATION=1`.

### What the pilot teaches us

The interfaces that emerge will inform the future `Step` protocol:

- Does `JobContext` need to carry prior attempts? (Likely yes —
  seed context.)
- Should `Budget` be per-step or per-attempt? (Pilot says
  per-attempt because we have two budgets here.)
- How does a step emit events without coupling to the runner's
  callback shape? (Pilot will probably show that the callback shape
  needs to be a typed protocol, not `dict[str, Any]`.)
- What's the right `StepOutcome` shape — single `status` enum, or a
  union of named outcomes? (Pilot has at least `success`,
  `gave-up`, `needs-help`, `budget-exhausted` — does the
  orchestrator branch on these, or does the step pre-resolve them
  into one of {`continue_next`, `stop_done`, `stop_escalate`}?)

These questions get sketched in code, not on paper. If the pilot
feels natural to write, the layering is right. If it's painful,
redesign the layer before generalizing.

## Test strategy

Two test suites land with this phase:

1. **Unit:** `test_lifecycle.py` (state machine), `test_two_model_escalation.py`
   (pilot).
2. **Integration:** one new test in `scripts/generator/tests/` that
   runs a full job through: hook-fired (synthetic .job file
   creation) → runner picks it up → lifecycle transitions all fire
   → ends in `done` or `escalated` with the right event rows in
   `job_events`. Uses a stubbed LLM (deterministic responses) and a
   throwaway sqlite DB.

CI considerations:
- No DragonFly-specific assumptions in unit tests — they must pass
  on macOS and Linux for the generator venv.
- Integration tests can require dfly (the worker calls subprocess
  `dportsv3 dev-env exec`) — gate them with a marker.

## Cutover criteria

Phase 1 is "done" when:

1. `jobs.state_machine_state` is populated for every new job by
   the runner. (`SELECT DISTINCT state_machine_state FROM jobs
   WHERE last_seen_at > '<phase-start>'` shows only the typed
   states, no NULLs.)
2. `_post_job_upsert` is **deleted** from
   `scripts/agent-queue-runner`. (`grep -n _post_job_upsert scripts/` returns nothing.)
3. `lifecycle.py` test suite is green on CI.
4. The pilot runs end-to-end on at least one real failing port:
   cheap model attempts, returns `gave-up`, strong model picks up
   the seed context, produces a patch. `job_events` table contains
   the expected transition rows.
5. The existing UI's `/agentic/jobs` page renders without errors
   against the new schema (it can keep reading `jobs.state` —
   that column still exists and is populated; we just add
   `state_machine_state` alongside).

## Risk + rollback

The hard cutover means there's no "old code path" to fall back to
within Phase 1. Rollback is git revert of the Phase 1 commits.

Specific risks:

| Risk | Mitigation |
|---|---|
| `lifecycle.apply()` deadlocks under contention | sqlite WAL + `busy_timeout=5000` already in `init_db`; tests cover concurrent transitions. |
| Existing UI breaks when reading `jobs.state` because we changed semantics | We don't change `jobs.state` semantics. We add a new column. The old column keeps its today behavior until Phase N retires it. |
| Pilot's strong model is expensive to invoke per test | Feature-flagged off by default; only operator-triggered tests exercise it. Synthetic LLM in unit tests. |
| Migration script fails partway on a non-empty DB | `ALTER TABLE ... ADD COLUMN` is idempotent under the existing `MIGRATIONS` tuple pattern in `db/schema.py`; the migration is one line. No data backfill needed (NULL is fine for the legacy rows). |

## Commit structure

Five commits, ordered:

1. `feat(db): add job_events table + state_machine_state column`
   — schema only, no consumers. Lands the migration.
2. `feat(agent): job lifecycle state machine`
   — `lifecycle.py` + tests. No call sites yet.
3. `refactor(runner): route job state changes through lifecycle.apply()`
   — cutover. Deletes `_post_job_upsert`. Touches the four runner
   call sites + the artifact-store endpoint.
4. `feat(agent): TwoModelEscalationStep pilot`
   — new step + env-var wiring + unit tests. Feature-flagged off.
5. `test(agent): end-to-end lifecycle + pilot`
   — integration test that proves the full chain works on a stubbed
   LLM + throwaway DB.

Each commit is independently reviewable and runnable. CI must pass
on each.

## What's explicitly not in Phase 1

If you find yourself wanting to do these during Phase 1, stop and
defer to Phase 2:

- A `Step` Protocol formalized as an ABC. Wait for two more steps
  to exist before drawing the abstraction.
- Replacing `build_patch_payload`. Phase 4.
- Replacing `policy.tier_for` + the cap with `decide()`. Phase 3.
- Surfacing `state_machine_state` in the UI. After Phase 1 ships,
  small follow-up commit.
- Generalizing `EnvHealth`. Phase 2.

## Decision points before starting

Two things the operator should confirm before the first commit lands:

1. **Pilot scope:** is "cheap → strong on gave-up" the right pilot?
   The alternative is a pilot tied to layer 3 instead (a structured
   `EnvHealth` probe). Two-model escalation exercises layer-1 + a
   forward sketch of layer-2; health probe exercises layer-3 alone.
   Two-model gives broader interface coverage; health is smaller
   and safer.
2. **Schema migration timing:** the new columns + table are zero-
   cost on an existing DB (NULL columns + empty table). Confirm
   that production state.db can absorb the migration without
   downtime. Should be fine — sqlite ALTER TABLE ADD COLUMN is O(1)
   — but worth a one-line confirmation.
