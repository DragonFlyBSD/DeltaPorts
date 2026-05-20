# Agentic framework — Phase 1 plan: lifecycle + pilot

> **Phase:** 1 of N (see `agentic-framework-design.md` for the arc).
> **Goal:** install the job lifecycle layer (layer 1) and validate the
> design via one pilot feature. Six independently-reviewable steps.
>
> When Phase 1 ships, this file gets **rewritten** to be Phase 2's
> plan. The arc and historical scaffolding stay in
> `agentic-framework-design.md`.
>
> **Status:** draft. Awaiting operator review of step plans below
> before implementation starts.

## Scope of Phase 1

**In scope:**
- Layer 1: a typed job lifecycle state machine backed by `state.db`.
- The pilot: `TwoModelEscalationStep` — first attempt uses a cheap
  model; on `gave-up` outcome, second attempt uses a strong model.
  Hand-shaped step that informs (but doesn't formalize) the future
  Step protocol.
- Tests at every step.

**Out of scope (deferred to later phases):**
- Layer 2 (formal Step Protocol).
- Layer 3 (Health/readiness).
- Layer 4 (Context assembly).
- Layer 5 (Policy engine).
- UI changes beyond what falls out for free.

**Hard cutover principle (per operator preference):** when each step
lands, the equivalent old code is **deleted in the same commit**. No
dual code paths surviving past their cutover step.

## Pre-conditions before starting

These must be true at HEAD of master before Step 1 begins:

1. The May 20 operational fixes (sibling batching, env_broken,
   retry cap, loop-aware prompt) are verified working on a real
   dsynth run end-to-end. Without that we'll mistake framework bugs
   for inherited bugs.
2. `dportsv3.agent.policy.Tier` is the single source of truth for tier
   names — no other code path hard-codes tier strings.
3. `state.db`'s `jobs` table is being populated for new jobs (the
   `_post_job_upsert` plumbing from `f1272152971`).

If any precondition fails, fix it first, don't paper over.

## Decision points before starting

Two things the operator should confirm before Step 1 lands:

1. **Pilot choice:** is "cheap → strong on gave-up" the right pilot?
   Alternative is a layer-3 health-probe pilot. Two-model gives
   broader interface coverage; health is smaller and safer.
2. **Schema migration window:** confirm production `state.db` can
   absorb `ALTER TABLE ADD COLUMN` + a new table during a runner
   restart. Should be trivial but worth a one-line ack.

---

## The state set (used by all steps)

Final, explicit, exhaustive. Codified in `lifecycle.py:JobState`:

```
queued       — written by hook on dsynth failure (.job file in pending/)
claimed      — runner has moved .job to inflight/, before any step starts
triaging     — TriageStep running
triaged      — TriageStep complete, awaiting next-step decision
patching     — PatchAttemptStep running (one attempt)
verifying    — RebuildVerifyStep running (separate from patching)
done         — rebuild_ok=true, no further work
escalated    — MANUAL tier resolved; operator must act
dead         — terminal: env_broken, parse error, exhausted budget
               without progress, etc.
```

Allowed transitions are a small fixed table; anything else is a bug.

---

## Step 1 — Schema migration

**Goal:** land the database surface for the state machine. No
consumers yet.

**Files:**
- `scripts/generator/dportsv3/db/schema.py` — modified

**Schema additions:**
```sql
-- in SCHEMA executescript block
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    job_id TEXT NOT NULL,
    from_state TEXT,
    to_state TEXT NOT NULL,
    event_name TEXT NOT NULL,
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id);

-- in MIGRATIONS tuple
"ALTER TABLE jobs ADD COLUMN state_machine_state TEXT",
"ALTER TABLE jobs ADD COLUMN last_transition_at TEXT",
"ALTER TABLE jobs ADD COLUMN retire_reason TEXT",
```

**Tests:**
- `test_schema_migration.py` (new): open a temp DB through
  `init_db()`, assert the three new `jobs` columns exist and
  `job_events` table is reachable. Existing tests must keep passing
  (idempotent ALTER).

**Cutover criteria:**
- `pytest scripts/generator/tests/` green.
- Manual: `sqlite3 production-state.db < schema.sql` (or just
  restart artifact-store, which runs `init_db()`) and `SELECT *
  FROM job_events` returns empty without error.

**Done criteria:** new columns + table visible in schema, no
consumers wired up yet. The `state_machine_state` column will be
NULL for all existing rows — that's expected; legacy rows aren't
migrated.

**Dependencies:** none.

**Rollback:** revert the commit; columns + table stay (sqlite has
no DROP COLUMN, and dropping an empty table is harmless to leave).

**Commit:** `feat(db): add job_events table + state_machine_state column`

---

## Step 2 — Lifecycle module

**Goal:** the typed state machine itself. Pure logic + sqlite, no
LLM, no subprocess, no file I/O. Not wired up yet.

**Files:**
- `scripts/generator/dportsv3/agent/lifecycle.py` — new
- `scripts/generator/tests/test_lifecycle.py` — new

**Interface:**

```python
from enum import StrEnum

class JobState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    TRIAGING = "triaging"
    TRIAGED = "triaged"
    PATCHING = "patching"
    VERIFYING = "verifying"
    DONE = "done"
    ESCALATED = "escalated"
    DEAD = "dead"

class JobEvent(StrEnum):
    HOOK_ENQUEUED       = "hook_enqueued"          # → QUEUED
    CLAIM               = "claim"                  # QUEUED → CLAIMED
    TRIAGE_START        = "triage_start"           # CLAIMED → TRIAGING
    TRIAGE_OK           = "triage_ok"              # TRIAGING → TRIAGED
    TRIAGE_FAIL         = "triage_fail"            # TRIAGING → DEAD
    PATCH_START         = "patch_start"            # TRIAGED → PATCHING
    PATCH_OK            = "patch_ok"               # PATCHING → VERIFYING
    PATCH_GAVE_UP       = "patch_gave_up"          # PATCHING → DEAD
    PATCH_BUDGET_OUT    = "patch_budget_out"       # PATCHING → DEAD
    VERIFY_OK           = "verify_ok"              # VERIFYING → DONE
    VERIFY_FAIL         = "verify_fail"            # VERIFYING → DEAD
    ESCALATE_MANUAL     = "escalate_manual"        # TRIAGED → ESCALATED
    ENV_BROKEN          = "env_broken"             # any → DEAD

TRANSITIONS: dict[tuple[JobState | None, JobEvent], JobState] = {
    (None, JobEvent.HOOK_ENQUEUED): JobState.QUEUED,
    (JobState.QUEUED, JobEvent.CLAIM): JobState.CLAIMED,
    (JobState.CLAIMED, JobEvent.TRIAGE_START): JobState.TRIAGING,
    # ... etc, exhaustive
}

class IllegalTransition(Exception): ...

def apply(
    conn: sqlite3.Connection,
    job_id: str,
    event: JobEvent,
    detail: dict | None = None,
) -> JobState:
    """Atomic state transition under one transaction.

    Reads current state from jobs.state_machine_state (or None for new
    jobs), validates (state, event) is in TRANSITIONS, writes a
    job_events row + updates jobs.state_machine_state +
    jobs.last_transition_at. Raises IllegalTransition for invalid
    transitions.
    """

def current(conn: sqlite3.Connection, job_id: str) -> JobState | None:
    """Latest state. Authoritative source is job_events; jobs.state_machine_state
    is a denormalized cache, fall back to event log on mismatch."""

def history(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    """Ordered list of job_events rows for this job, oldest first."""
```

**Tests** (`test_lifecycle.py`):
- Every defined `(state, event) → state` round-trips through
  `apply()` and writes exactly one event row.
- Disallowed transitions raise `IllegalTransition` and write no row.
- `current()` returns the latest event's `to_state` even when the
  denormalized `jobs.state_machine_state` is stale (simulate by
  inserting a `job_events` row without updating `jobs`).
- Concurrent `apply()` from two threads: under sqlite WAL + BEGIN
  IMMEDIATE, one wins, other raises `sqlite3.OperationalError` or
  `IllegalTransition` based on whether the first commit landed in
  time. Both behaviors are acceptable; the invariant is "no double
  apply."
- `history()` returns rows in id order, oldest first.
- A full path test: `QUEUED → CLAIMED → TRIAGING → TRIAGED →
  PATCHING → VERIFYING → DONE` produces 6 transitions.

**Cutover criteria:** `pytest test_lifecycle.py` green.

**Done criteria:** module exists, fully tested, **no call sites in
the runner yet**. Importable from
`scripts/generator/dportsv3/agent/lifecycle.py`.

**Dependencies:** Step 1.

**Rollback:** revert the commit; no consumers depend on it.

**Commit:** `feat(agent): job lifecycle state machine`

---

## Step 3 — Extract `run_patch_attempt`

**Goal:** refactor today's patch invocation into a single callable
function with no behavior change. Sets the stage for the pilot
(Step 5) to compose multiple attempts.

This is a **pure refactor** — same inputs, same outputs, same side
effects, just relocated.

**Files:**
- `scripts/generator/dportsv3/agent/patch.py` — modified
- `scripts/agent-queue-runner` — modified (one call site)
- `scripts/generator/tests/test_patch_attempt.py` — new (lightweight)

**Interface:**

```python
# in dportsv3/agent/patch.py
@dataclass
class PatchAttemptConfig:
    model: str
    tier: Tier
    env: str
    api_base: str | None
    api_key: str | None
    custom_llm_provider: str | None
    timeout: int
    seed_context: str | None = None   # extra user message prepended (None today)

def run_attempt(payload: str, config: PatchAttemptConfig,
                on_event=None) -> PatchResult:
    """One patch attempt, model-agnostic. Today's harness_patch.run
    becomes a one-line wrapper that builds a PatchAttemptConfig.
    """
```

Today's `harness_patch.run(payload, tier=..., env=..., model=..., ...)`
becomes:

```python
def run(payload, *, tier, env, model, ...) -> PatchResult:
    config = PatchAttemptConfig(model=model, tier=tier, env=env, ...)
    return run_attempt(payload, config, on_event=on_event)
```

**Tests** (`test_patch_attempt.py`):
- Trivial smoke test: a stubbed LLM module (monkeypatched) returns
  a canned response with `Rebuild Proof JSON {rebuild_ok: true}`;
  `run_attempt(...)` returns a `PatchResult` with `status="success"`.
- Same payload, `seed_context="foo bar"`: messages array passed to
  the LLM stub contains `"foo bar"` as a user message.

**Cutover criteria:**
- Existing patch end-to-end on a known port still succeeds (smoke
  manual test against a real bundle).
- `pytest` green.
- No diff in `analysis/patch_audit.json` shape for a frozen bundle
  before vs. after this commit.

**Done criteria:** `run_attempt` exists, `run` calls it, behavior
identical.

**Dependencies:** none (purely a refactor of existing code).

**Rollback:** revert; runner reverts to calling `harness_patch.run`
directly.

**Commit:** `refactor(agent): extract run_patch_attempt from
harness_patch.run`

---

## Step 4 — Runner cutover to `lifecycle.apply()`

**Goal:** the hard cutover. Replace `_post_job_upsert` with
`lifecycle.apply()` at every call site, **delete** `_post_job_upsert`
in the same commit.

**Files:**
- `scripts/agent-queue-runner` — modified (5 call sites:
  enqueue_triage, enqueue_patch, claim_next_job_batch, success-path,
  failure-path)
- `scripts/generator/dportsv3/artifact_store.py` — modified
  (the `/v1/jobs/upsert` endpoint internally calls
  `lifecycle.apply(...)` instead of `upsert_job(...)`)

**Mapping (existing → new):**

| Old call | Replacement |
|---|---|
| `_post_job_upsert(job_id, "pending", job=..., path=...)` after `enqueue_triage_job` | `lifecycle.apply(conn, job_id, JobEvent.HOOK_ENQUEUED, detail={...})` |
| `_post_job_upsert(job_id, "pending", ...)` after `enqueue_patch_job` | `lifecycle.apply(conn, job_id, JobEvent.HOOK_ENQUEUED, detail={"type": "patch", ...})` |
| `_post_job_upsert(lead_dest.name, "inflight", ...)` in `claim_next_job_batch` | `lifecycle.apply(conn, lead_id, JobEvent.CLAIM)` |
| `_post_job_upsert(s_dest.name, "inflight", ...)` for siblings | `lifecycle.apply(conn, sibling_id, JobEvent.CLAIM)` |
| `_post_job_upsert(job_id, "done", ...)` success path | `lifecycle.apply(conn, job_id, JobEvent.VERIFY_OK, detail={...})` |
| `_post_job_upsert(job_id, "failed", ..., last_error=msg)` failure path | `lifecycle.apply(conn, job_id, JobEvent.<appropriate fail event>, detail={"last_error": msg})` |

The failure path needs to map the failure reason to the right event:
`patch_gave_up`, `patch_budget_out`, `verify_fail`, or `env_broken`.

**Hook side (already POSTs `/v1/jobs/upsert`):** unchanged on the
shell side; the artifact-store endpoint dispatches into
`lifecycle.apply(HOOK_ENQUEUED)`.

**The legacy `jobs.state` column** stays populated for UI
backward-compat. Inside `lifecycle.apply()`, when transitioning into
a state, also update the old `jobs.state` mapping:

```
QUEUED              → "pending"
CLAIMED             → "inflight"
TRIAGING, TRIAGED   → "inflight"
PATCHING, VERIFYING → "inflight"
DONE                → "done"
ESCALATED, DEAD     → "failed"
```

This keeps the existing `/agentic/jobs` UI working without any
template change in Phase 1.

**Tests:**
- Existing tests must still pass.
- New: a small integration that enqueues a synthetic .job (via the
  artifact-store endpoint), claims it via the runner code path,
  fakes success, and asserts `current() == DONE` and the
  `job_events` row count is the expected number.

**Cutover criteria:**
- `grep -n "_post_job_upsert\|upsert_job\b" scripts/` returns no
  matches in runner/artifact-store.
- After one real run, every `jobs.state_machine_state` for newly-
  created rows is non-NULL and one of the typed values.
- UI `/agentic/jobs` renders without errors.

**Done criteria:** all state writes go through `lifecycle.apply()`;
old function deleted.

**Dependencies:** Step 1, Step 2.

**Rollback:** revert. Old code returns; no DB cleanup needed
(`state_machine_state` column will just stop getting updated for
new jobs).

**Commit:** `refactor(runner): route job state changes through
lifecycle.apply()`

---

## Step 5 — Pilot: `TwoModelEscalationStep`

**Goal:** add the pilot feature. Feature-flagged **off** by default
so production behavior doesn't change until the operator opts in.

**Files:**
- `scripts/generator/dportsv3/agent/steps/__init__.py` — new
- `scripts/generator/dportsv3/agent/steps/two_model_escalation.py` — new
- `scripts/agent-queue-runner` — modified (conditionally dispatch to
  pilot when `DP_HARNESS_TWO_MODEL_ESCALATION=1`)
- `scripts/generator/tests/test_two_model_escalation.py` — new

**Interface:**

```python
# dportsv3/agent/steps/two_model_escalation.py
@dataclass
class TwoModelConfig:
    cheap_model: str
    strong_model: str | None      # None disables escalation
    cheap_tier: Tier              # max_iterations=1, smaller max_tokens
    strong_tier: Tier             # max_iterations=2, larger max_tokens
    env: str
    api_base: str | None
    api_key: str | None
    custom_llm_provider: str | None
    timeout: int

@dataclass
class TwoModelResult:
    status: Literal["success", "needs-help", "budget-exhausted", "escalated"]
    final_result: PatchResult     # the winning (or last) attempt
    attempts: list[PatchResult]   # all attempts, ordered

def run(payload: str, config: TwoModelConfig, on_event=None) -> TwoModelResult: ...
```

Internals:

```python
cheap = run_patch_attempt(payload, PatchAttemptConfig(
    model=config.cheap_model, tier=config.cheap_tier, ...))
if cheap.status == "success":
    return TwoModelResult(status="success", final_result=cheap, attempts=[cheap])
if not _is_gave_up(cheap):
    # budget-exhausted or needs-help: don't escalate (thrasher)
    return TwoModelResult(status=cheap.status, final_result=cheap, attempts=[cheap])
if not config.strong_model:
    return TwoModelResult(status="needs-help", final_result=cheap, attempts=[cheap])
seed = _format_gave_up_seed(cheap)
strong = run_patch_attempt(payload, PatchAttemptConfig(
    model=config.strong_model, tier=config.strong_tier,
    seed_context=seed, ...))
status = "success" if strong.status == "success" else "escalated"
return TwoModelResult(status=status, final_result=strong, attempts=[cheap, strong])
```

`_is_gave_up`: parse `Rebuild Status:` from `cheap.final_text`,
return True iff value is `"gave-up"`.

`_format_gave_up_seed`: extract `## Patch Log` from `cheap.final_text`
and wrap as: `"Prior attempt with model X gave up. Their Patch Log:
\n<log>\nLearn from this and try a different approach."`

**Runner wiring:**

```python
if os.environ.get("DP_HARNESS_TWO_MODEL_ESCALATION") == "1":
    # use TwoModelEscalationStep
    cheap_model = os.environ.get("DP_HARNESS_PATCH_MODEL_CHEAP",
                                  os.environ.get("DP_HARNESS_TRIAGE_MODEL"))
    strong_model = os.environ.get("DP_HARNESS_PATCH_MODEL_STRONG") or None
    # ...
    result = two_model.run(payload, TwoModelConfig(...), on_event=_on_event)
    # adapt TwoModelResult → PatchResult for downstream code that expects PatchResult
else:
    # today's single-model path (still via run_patch_attempt)
    result = run_patch_attempt(payload, single_model_config, on_event=_on_event)
```

**Tests** (`test_two_model_escalation.py`, all with stubbed LLM):
- Cheap returns success → strong never called; `attempts == [cheap]`.
- Cheap returns gave-up, strong returns success → both called,
  strong gets the cheap log as seed, `attempts == [cheap, strong]`,
  `status == "success"`.
- Cheap returns budget-exhausted → strong not called (don't
  escalate thrashers).
- Cheap returns gave-up, `strong_model = None` → not called,
  result status is `"needs-help"`.
- Cheap returns gave-up, strong returns gave-up → result status is
  `"escalated"`.

**Cutover criteria:**
- All unit tests green.
- Feature flag is **off** by default; running with no env-var
  change uses today's path.
- Manual: enable flag, set both model env-vars, trigger a known
  fixable port, watch the activity log show two attempts.

**Done criteria:** pilot reachable behind flag; default behavior
unchanged.

**Dependencies:** Step 3 (`run_patch_attempt`), Step 4 (so
`lifecycle.apply()` emits the right events when the pilot triggers
two attempts back-to-back). Step 1, 2 transitively.

**Rollback:** revert; feature flag becomes a no-op.

**Commit:** `feat(agent): TwoModelEscalationStep pilot
(feature-flagged)`

---

## Step 6 — Integration test

**Goal:** end-to-end test that drives a synthetic job through the
runner's main loop using a stubbed LLM + throwaway sqlite DB.
Catches integration-level breakage that unit tests miss.

**Files:**
- `scripts/generator/tests/test_runner_e2e_lifecycle.py` — new
- Possibly a small `scripts/generator/tests/_stubs/` directory for
  reusable LLM stubs.

**Test shape:**

```python
def test_full_lifecycle_to_done(tmp_path, monkeypatch):
    # 1. Build a throwaway queue + state.db.
    # 2. Monkeypatch dportsv3.agent.llm.complete to return canned
    #    responses (triage + patch + rebuild_proof rebuild_ok=true).
    # 3. Monkeypatch dportsv3.agent.worker tool calls to no-op
    #    (we're not testing tools here, we're testing the loop).
    # 4. Drop a .job file in pending/.
    # 5. Run the runner main loop with --once.
    # 6. Assert:
    #    - lifecycle.history(conn, job_id) shows the expected
    #      transition sequence
    #    - jobs.state_machine_state == "done"
    #    - the bundle has triage.md, patch.md, rebuild_proof.json,
    #      tool_trace.jsonl
```

Other test cases:
- `test_full_lifecycle_to_escalated`: triage classifies as
  `missing-dep` (MANUAL tier) → no patch enqueue → final state
  ESCALATED.
- `test_pilot_escalates_to_strong`: with feature flag on, cheap LLM
  stub returns gave-up, strong LLM stub returns success → both
  attempts executed.

**Cutover criteria:**
- Tests green on CI (Linux/macOS — gate the dev-env-shelling tests
  on dfly via pytest marker).
- Test runs in under 5 seconds (no real LLM calls, no real
  subprocess).

**Done criteria:** at least three integration tests covering the
happy path, the escalation path, and the pilot. CI green.

**Dependencies:** Steps 1–5.

**Rollback:** revert; no production impact, tests just disappear.

**Commit:** `test(agent): end-to-end lifecycle + pilot integration`

---

## Phase 1 cutover criteria (overall)

Phase 1 is "done" when **all** of:

1. Steps 1–6 are all committed and reviewed.
2. `pytest scripts/generator/tests/` green.
3. `grep -n "_post_job_upsert\|upsert_job\b" scripts/` returns no
   matches in runner/artifact-store.
4. Manual smoke: run a real dsynth failure end-to-end with the
   feature flag **off**. Confirm:
   - `jobs.state_machine_state` is non-NULL and one of the typed
     values for the new row.
   - `job_events` has the expected sequence of transitions.
   - UI `/agentic/jobs` and `/agentic/jobs/<id>` render correctly.
5. Manual smoke: same run with `DP_HARNESS_TWO_MODEL_ESCALATION=1`,
   both model env-vars set. Confirm two attempts run when triage
   classifies AUTO/ASSIST and the cheap model gives up.
6. This `agentic-framework-plan.md` file gets **rewritten** for
   Phase 2 (probably layer 3 health). The Phase 1 content moves to
   a one-paragraph summary in `agentic-framework-design.md` under
   "completed phases."

## Risk + rollback

| Step | Risk | Mitigation |
|---|---|---|
| 1 | sqlite migration breaks existing DB | `ALTER TABLE ADD COLUMN` is O(1) + idempotent via existing `MIGRATIONS` tuple pattern. Tested on a copy of production state.db first. |
| 2 | State machine deadlocks under contention | WAL + `busy_timeout=5000` already set in `init_db`. Concurrent test covers it. |
| 3 | Refactor changes behavior accidentally | Parity smoke against a frozen bundle (manual). |
| 4 | Hard cutover misses a call site | `grep` checks before commit; integration test (Step 6) catches it post-facto. |
| 5 | Pilot escalates incorrectly | Feature-flagged off by default; unit tests cover all four outcome branches. |
| 6 | Tests flaky due to monkeypatch ordering | Use pytest fixtures with explicit teardown; no global state in tests. |

## What's explicitly not in Phase 1

If you find yourself wanting to do these during Phase 1, stop and
defer to Phase 2:

- A `Step` Protocol formalized as an ABC. Wait for two more steps
  to exist before drawing the abstraction.
- Replacing `build_patch_payload`.
- Replacing `policy.tier_for` + the cap with `decide()`.
- Surfacing `state_machine_state` in the UI beyond the existing
  `jobs.state` mirror.
- Generalizing `EnvHealth`.
- Refactoring the existing triage path beyond the small wiring
  changes in Step 4.

## Review notes for the operator

Things to specifically check when reviewing each step before
implementation:

- **Step 1:** is the `job_events.detail_json` field schema flexible
  enough? Should `event_name` be free-form or constrained to the
  `JobEvent` enum?
- **Step 2:** are the listed transitions exhaustive? Specifically:
  what's the right event for "the runner crashed mid-attempt"? Do
  we need a `CRASH_RECOVERY` event with a transition `* → DEAD`?
- **Step 3:** does extracting `run_patch_attempt` from
  `harness_patch.run` introduce any subtle parameter passing issue?
  In particular `on_event` threading.
- **Step 4:** does the `JobEvent` → legacy `jobs.state` mapping
  preserve every behavior the UI relies on? Worth a manual UI walk.
- **Step 5:** does `_format_gave_up_seed` extract the right text?
  Should it include the agent's `Patch Plan` JSON too, not just
  `Patch Log`?
- **Step 6:** is the stub LLM realistic enough? In particular, does
  it test the case where `_parse_rebuild_proof` finds the JSON
  block in the expected place?

Sign off on these before Step 1 starts.
