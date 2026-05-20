# Agentic framework — Phase 1 plan: lifecycle

> **Phase:** 1 of N (see `agentic-framework-design.md` for the arc).
> **Goal:** install the job lifecycle layer (layer 1). Four
> independently-reviewable steps. No pilot, no parallel code paths,
> no schema migrations preserving old shapes — we're in alpha, the
> cutover *is* the change.
>
> When Phase 1 ships, this file gets **rewritten** to be Phase 2's
> plan.
>
> **Status:** draft. Awaiting operator review of step plans below
> before implementation starts.

## Operator decisions captured in this revision

- `JobEvent` is a constrained enum — no free-form `event_name`. The
  set is finite, code that wants a new transition adds a new enum
  value.
- No crash-recovery event. On runner startup, any job stuck in an
  inflight-ish state (CLAIMED / TRIAGING / TRIAGED / PATCHING /
  VERIFYING) gets transitioned to DEAD with `retire_reason=
  "runner_restart"`. Operator re-enqueues from the original bundle
  if they want it retried — much simpler than threading recovery
  semantics through every step.
- Yolo mode: hard cutover everywhere. No "alongside" columns. The
  existing `jobs.state` column gets **repurposed** to hold the
  typed `JobState` values directly. UI templates and queries get
  updated in the same commit. No legacy fallback.
- **No pilot.** Layer 1 lands on its own; later phases validate
  later layers.

## Scope of Phase 1

**In scope:**
- Typed `JobState` + `JobEvent` enums.
- `lifecycle.py` module: transitions table, `apply()`, `current()`,
  `history()`, startup `reap_orphans()`.
- `job_events` table + supporting columns.
- Runner + artifact-store cutover: all state writes go through
  `lifecycle.apply()`. `_post_job_upsert` and `upsert_job` deleted.
- UI templates updated to render the new typed values.
- Integration test of the full transition sequence.

**Out of scope (deferred to later phases):**
- Layer 2 (formal Step Protocol).
- Layer 3 (Health/readiness as a typed precondition).
- Layer 4 (Context assembly).
- Layer 5 (Policy engine).
- Pulling claim ordering out of the filesystem into the DB. The
  `.job` file stays the payload container; only the *state* moves
  into the typed lifecycle. Filesystem-as-claim-queue can move in a
  later phase if useful.

## Pre-conditions before starting

1. May 20 operational fixes (sibling batching, env_broken, retry
   cap, loop-aware prompt) verified working on a real dsynth run.
2. `state.db`'s `jobs` table is being populated for new jobs (the
   `_post_job_upsert` plumbing from `f1272152971`). This gives us a
   baseline to compare against post-cutover.

---

## The state set

```
QUEUED       — .job file written by hook; jobs row inserted
CLAIMED      — runner moved .job to inflight/, before any step starts
TRIAGING     — TriageStep running
TRIAGED      — TriageStep complete, awaiting next-step decision
PATCHING     — PatchAttemptStep running
VERIFYING    — RebuildVerifyStep running (currently fused into
               patching; we'll split when Layer 2 lands. Phase 1
               just defines the state so Layer 2 has a target.)
DONE         — rebuild_ok=true
ESCALATED    — MANUAL tier resolved; operator must act
DEAD         — terminal failure: env_broken, parse error, exhausted
               budget without progress, runner restart
```

## The event set

```
HOOK_ENQUEUED    → QUEUED                  (initial insertion)
CLAIM            QUEUED → CLAIMED
TRIAGE_START     CLAIMED → TRIAGING
TRIAGE_OK        TRIAGING → TRIAGED
TRIAGE_FAIL      TRIAGING → DEAD
PATCH_START      TRIAGED → PATCHING
PATCH_OK         PATCHING → VERIFYING
PATCH_GAVE_UP    PATCHING → DEAD
PATCH_BUDGET_OUT PATCHING → DEAD
VERIFY_OK        VERIFYING → DONE
VERIFY_FAIL      VERIFYING → DEAD
ESCALATE_MANUAL  TRIAGED → ESCALATED
ENV_BROKEN       (CLAIMED|TRIAGING|PATCHING|VERIFYING) → DEAD
REAP_ORPHAN      (CLAIMED|TRIAGING|TRIAGED|PATCHING|VERIFYING) → DEAD
```

Phase 1 only fires events corresponding to the existing flow:
`HOOK_ENQUEUED`, `CLAIM`, `TRIAGE_START`, `TRIAGE_OK`,
`PATCH_START`, `VERIFY_OK` (or `VERIFY_FAIL`), `ESCALATE_MANUAL`,
`ENV_BROKEN`, `REAP_ORPHAN`. `TRIAGE_FAIL`, `PATCH_GAVE_UP`,
`PATCH_BUDGET_OUT` are defined now but fired by later phases when
the corresponding logic exists as its own step.

---

## Step 1 — Schema

**Goal:** land the database surface. No consumers yet.

**Files:**
- `scripts/generator/dportsv3/db/schema.py` — modified

**Schema changes:**

The existing `jobs.state` column is **repurposed** to hold typed
`JobState` values directly (lowercase strings: `"queued"`,
`"claimed"`, etc.). No new column. UI queries that filter on
`state` will be updated in Step 3 to use the typed values.

Adds:

```sql
-- in SCHEMA executescript block
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    job_id TEXT NOT NULL,
    from_state TEXT,            -- NULL on initial HOOK_ENQUEUED
    to_state TEXT NOT NULL,
    event_name TEXT NOT NULL,   -- always one of the JobEvent enum values
    detail_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id);

-- in MIGRATIONS tuple
"ALTER TABLE jobs ADD COLUMN last_transition_at TEXT",
"ALTER TABLE jobs ADD COLUMN retire_reason TEXT",
```

**Tests:** `test_schema_lifecycle.py`
- Init a temp DB through `init_db()`, assert the new columns and
  table exist.
- Existing tests must still pass.

**Done criteria:** schema visible, no consumers wired.

**Dependencies:** none.

**Rollback:** revert. Empty table + null columns stay (harmless;
sqlite has no DROP COLUMN, but no code reads them either).

**Commit:** `feat(db): add job_events table for lifecycle state machine`

---

## Step 2 — Lifecycle module

**Goal:** the typed state machine. Pure logic + sqlite. Not wired
to the runner yet.

**Files:**
- `scripts/generator/dportsv3/agent/lifecycle.py` — new
- `scripts/generator/tests/test_lifecycle.py` — new

**Interface:**

```python
from __future__ import annotations
from enum import StrEnum
from typing import Iterator
import json, sqlite3
from datetime import datetime, timezone


class JobState(StrEnum):
    QUEUED    = "queued"
    CLAIMED   = "claimed"
    TRIAGING  = "triaging"
    TRIAGED   = "triaged"
    PATCHING  = "patching"
    VERIFYING = "verifying"
    DONE      = "done"
    ESCALATED = "escalated"
    DEAD      = "dead"


class JobEvent(StrEnum):
    HOOK_ENQUEUED    = "hook_enqueued"
    CLAIM            = "claim"
    TRIAGE_START     = "triage_start"
    TRIAGE_OK        = "triage_ok"
    TRIAGE_FAIL      = "triage_fail"
    PATCH_START      = "patch_start"
    PATCH_OK         = "patch_ok"
    PATCH_GAVE_UP    = "patch_gave_up"
    PATCH_BUDGET_OUT = "patch_budget_out"
    VERIFY_OK        = "verify_ok"
    VERIFY_FAIL      = "verify_fail"
    ESCALATE_MANUAL  = "escalate_manual"
    ENV_BROKEN       = "env_broken"
    REAP_ORPHAN      = "reap_orphan"


# (from_state, event) -> to_state. None as from_state means "new job"
# (only valid for HOOK_ENQUEUED).
TRANSITIONS: dict[tuple[JobState | None, JobEvent], JobState] = {
    (None,                 JobEvent.HOOK_ENQUEUED):  JobState.QUEUED,
    (JobState.QUEUED,      JobEvent.CLAIM):          JobState.CLAIMED,
    (JobState.CLAIMED,     JobEvent.TRIAGE_START):   JobState.TRIAGING,
    (JobState.TRIAGING,    JobEvent.TRIAGE_OK):      JobState.TRIAGED,
    (JobState.TRIAGING,    JobEvent.TRIAGE_FAIL):    JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.PATCH_START):    JobState.PATCHING,
    (JobState.PATCHING,    JobEvent.PATCH_OK):       JobState.VERIFYING,
    (JobState.PATCHING,    JobEvent.PATCH_GAVE_UP):  JobState.DEAD,
    (JobState.PATCHING,    JobEvent.PATCH_BUDGET_OUT): JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.VERIFY_OK):      JobState.DONE,
    (JobState.VERIFYING,   JobEvent.VERIFY_FAIL):    JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.ESCALATE_MANUAL): JobState.ESCALATED,
    # ENV_BROKEN can fire from any active state
    (JobState.CLAIMED,     JobEvent.ENV_BROKEN):     JobState.DEAD,
    (JobState.TRIAGING,    JobEvent.ENV_BROKEN):     JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.ENV_BROKEN):     JobState.DEAD,
    (JobState.PATCHING,    JobEvent.ENV_BROKEN):     JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.ENV_BROKEN):     JobState.DEAD,
    # REAP_ORPHAN: runner-startup cleanup of inflight-ish states
    (JobState.CLAIMED,     JobEvent.REAP_ORPHAN):    JobState.DEAD,
    (JobState.TRIAGING,    JobEvent.REAP_ORPHAN):    JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.REAP_ORPHAN):    JobState.DEAD,
    (JobState.PATCHING,    JobEvent.REAP_ORPHAN):    JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.REAP_ORPHAN):    JobState.DEAD,
}


class IllegalTransition(Exception):
    """Raised when (current_state, event) is not in TRANSITIONS."""


def apply(
    conn: sqlite3.Connection,
    job_id: str,
    event: JobEvent,
    detail: dict | None = None,
) -> JobState:
    """Atomic state transition. One BEGIN IMMEDIATE … COMMIT.

    Reads current state, validates the transition, writes a
    job_events row, updates jobs.state + jobs.last_transition_at
    (and jobs.retire_reason if transitioning into DEAD/ESCALATED).
    """

def current(conn: sqlite3.Connection, job_id: str) -> JobState | None:
    """Latest state. Reads jobs.state; falls back to the last
    job_events.to_state if the cache is somehow stale."""

def history(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    """All transitions for this job, oldest first."""

def reap_orphans(conn: sqlite3.Connection) -> int:
    """Transition every job in an inflight-ish state to DEAD via
    REAP_ORPHAN. Called by the runner at startup. Returns count.

    Inflight-ish: CLAIMED, TRIAGING, TRIAGED, PATCHING, VERIFYING.
    Terminal states (DONE, ESCALATED, DEAD) and QUEUED are skipped.
    """
```

**Tests** (`test_lifecycle.py`):
- Every TRANSITIONS entry round-trips: `apply()` writes one event
  row, updates `jobs.state` correctly.
- Disallowed transitions raise `IllegalTransition` and write no
  row (assert event count unchanged after the raise).
- `current()` falls back to event log when `jobs.state` mismatches.
- Concurrent `apply()` from two threads: one wins, other raises or
  no-ops. Invariant: no double-apply (event count == 1).
- `history()` returns rows in id order.
- `reap_orphans()` transitions all CLAIMED/TRIAGING/etc. jobs to
  DEAD with `retire_reason="runner_restart"`. QUEUED, DONE,
  ESCALATED, DEAD jobs are untouched.
- Full happy path: `HOOK_ENQUEUED → CLAIM → TRIAGE_START →
  TRIAGE_OK → PATCH_START → PATCH_OK → VERIFY_OK` produces 7 event
  rows.

**Cutover criteria:** `pytest test_lifecycle.py` green.

**Done criteria:** module importable, fully tested, no call sites
in the runner yet.

**Dependencies:** Step 1.

**Rollback:** revert. No consumers yet.

**Commit:** `feat(agent): job lifecycle state machine`

---

## Step 3 — Runner + UI cutover

**Goal:** the hard cutover. Replace `_post_job_upsert` calls with
`lifecycle.apply()`. Delete `_post_job_upsert` + the
artifact-store's `upsert_job` method + the `/v1/jobs/upsert`
endpoint's old shape. Update UI templates to render the new typed
state values.

**Files:**
- `scripts/agent-queue-runner` — modified (5+ call sites, deletes)
- `scripts/generator/dportsv3/artifact_store.py` — modified
- `scripts/generator/dportsv3/tracker/agentic_queries.py` — modified
  (queries that filter by `state` use new values)
- `scripts/generator/dportsv3/tracker/templates/agentic_jobs.html`
  — modified (state badges)
- `scripts/dsynth-hooks/hook_common.sh` — modified (artifact-store
  client call may need updated args; check the wire shape)

**Mapping (existing call → new call):**

| Site | Old | New |
|---|---|---|
| `enqueue_triage_job` end | `_post_job_upsert(name, "pending", ...)` | `lifecycle.apply(conn, name, JobEvent.HOOK_ENQUEUED, detail={...})` |
| `enqueue_patch_job` end | `_post_job_upsert(name, "pending", ...)` | `lifecycle.apply(conn, name, JobEvent.HOOK_ENQUEUED, detail={"type": "patch", ...})` |
| `claim_next_job_batch` lead | `_post_job_upsert(name, "inflight", ...)` | `lifecycle.apply(conn, name, JobEvent.CLAIM)` |
| `claim_next_job_batch` sibling | same | same |
| `process_job` success | `_post_job_upsert(name, "done", ...)` | `lifecycle.apply(conn, name, JobEvent.VERIFY_OK, detail={...})` |
| `process_job` failure | `_post_job_upsert(name, "failed", last_error=...)` | mapped to one of `TRIAGE_FAIL`, `PATCH_GAVE_UP`, `PATCH_BUDGET_OUT`, `VERIFY_FAIL`, `ENV_BROKEN` based on the failure reason |

For triage and patch start, the runner now also fires
`TRIAGE_START` and `PATCH_START` events just before invoking the
respective harness. This adds two events per job; total event count
per happy-path job is 7 (was implicitly 4 in the old upsert flow:
pending, inflight, done).

**Failure-event mapping:** when `process_job` reports failure
status, we route based on which step failed:

```python
def _failure_event(job_type: str, status: str) -> JobEvent:
    if "env_broken" in status or _env_broken_reason:
        return JobEvent.ENV_BROKEN
    if job_type == "triage":
        return JobEvent.TRIAGE_FAIL
    if "budget" in status:
        return JobEvent.PATCH_BUDGET_OUT
    if "gave-up" in status or "gave_up" in status or "needs-help" in status:
        return JobEvent.PATCH_GAVE_UP
    return JobEvent.VERIFY_FAIL  # default for unknown patch failure
```

**Runner startup:** call `lifecycle.reap_orphans(conn)` once before
the main loop starts. Log the count if non-zero.

**Artifact-store side:** the `/v1/jobs/upsert` endpoint becomes a
thin shim over `lifecycle.apply()`. Hook-side `job-upsert` calls
HOOK_ENQUEUED via the shim. The handler is renamed to
`/v1/jobs/transition` and takes `{job_id, event, detail}` instead
of `{job_id, state, ...}`. The old shape is **deleted** (no
backward compat).

**UI changes:**
- `agentic_queries.py:job_counts`: queries like `WHERE state =
  'pending'` change to `WHERE state = 'queued'`. The four pinned
  count rows (pending / inflight / done / failed) become
  computed groups: pending=queued, inflight=any of
  claimed/triaging/triaged/patching/verifying, done=done,
  failed=dead+escalated. Single query, GROUP BY a CASE expression.
- `agentic_jobs.html` state filter dropdown: options become the
  JobState values directly.
- `agentic_job.html` doesn't filter — just renders `job.state`,
  works automatically.

**Hook side:** the `artifact_store job-upsert --state pending`
shell call needs to be updated to the new endpoint shape:
`artifact_store job-transition --event hook_enqueued`. The
`artifact-store-client` subcommand renames.

**Tests:**
- Existing tests must pass after this commit (with updated
  expectations where state strings appear).
- New: in `test_runner_cutover.py`, mock the runner's main loop
  step-by-step (without LLM) and assert the `job_events` rows that
  land for a synthetic .job file.

**Cutover criteria:**
- `grep -nE '_post_job_upsert|upsert_job\b|"/v1/jobs/upsert"' scripts/ docs/` returns nothing live in code (docs may still reference historically — that's fine).
- A fresh dsynth failure produces `jobs.state` in the typed set;
  `SELECT DISTINCT state FROM jobs WHERE last_seen_at > '<commit
  time>'` returns only `JobState` values.
- UI `/agentic/jobs` renders without errors, counts at top of
  `/agentic` reflect the typed states.

**Done criteria:** all state writes through `lifecycle.apply()`.
Old upsert paths deleted. UI reflects typed states.

**Dependencies:** Step 1, Step 2.

**Rollback:** revert. The DB will have one set of rows in the new
shape; new writes go back to the old shape. Mixed shape will look
ugly but won't break anything.

**Commit:** `refactor(runner+ui): cutover to lifecycle.apply()`

---

## Step 4 — Integration test

**Goal:** drive a synthetic job through the runner main loop with
stubbed LLM + throwaway DB. Catches breakage that unit tests miss.

**Files:**
- `scripts/generator/tests/test_runner_e2e_lifecycle.py` — new
- Maybe `scripts/generator/tests/_stubs/llm_stub.py` if reusable.

**Test cases:**

1. **Happy path to DONE.** Stub LLM returns canned triage + canned
   patch with `Rebuild Proof JSON {rebuild_ok: true}`. Drop a .job
   file in `pending/`, run runner main with `--once`. Assert
   `lifecycle.history(conn, job_id)` is exactly:
   `HOOK_ENQUEUED → CLAIM → TRIAGE_START → TRIAGE_OK → PATCH_START
   → PATCH_OK → VERIFY_OK`. Final `jobs.state == "done"`.

2. **Triage forces MANUAL.** Stub LLM returns triage with
   classification `missing-dep` (mapped to MANUAL tier). Patch
   never enqueues. Final state: `ESCALATED`. Last event:
   `ESCALATE_MANUAL`.

3. **Reap orphans on startup.** Pre-populate a synthetic job in
   state `PATCHING`. Run runner startup. Assert the job is now
   `DEAD` with `retire_reason="runner_restart"`.

4. **env_broken trips lifecycle.** Stub the worker so
   `materialize_dports` returns `error_category=env_broken`. Run a
   patch job through. Assert final state is `DEAD`, last event is
   `ENV_BROKEN`, and the runner's `_env_broken_reason` flag is set
   (subsequent claims gated).

**No real LLM calls. No real subprocess. No DragonFly assumptions.**
Runs on Linux/macOS CI for the generator venv.

**Cutover criteria:** all four tests green on CI. Runtime under 5s.

**Done criteria:** integration test suite exists and passes.

**Dependencies:** Steps 1, 2, 3.

**Rollback:** revert. Tests vanish; no production impact.

**Commit:** `test(agent): end-to-end lifecycle integration`

---

## Phase 1 cutover criteria (overall)

Phase 1 is "done" when all of:

1. Steps 1–4 committed and reviewed.
2. `pytest scripts/generator/tests/` green.
3. `grep -nE '_post_job_upsert|upsert_job\b' scripts/` returns
   nothing live in runner/artifact-store.
4. Manual smoke: real dsynth failure end-to-end. Confirm:
   - `jobs.state` values are typed (`queued`, `triaging`, etc.).
   - `job_events` has the expected sequence.
   - UI renders correctly with typed states.
   - Runner restart mid-job reaps the orphan and the next claim
     proceeds normally.
5. This file gets **rewritten** for Phase 2 (probably layer 3
   health). Phase 1 summary moves to a one-paragraph entry in
   `agentic-framework-design.md`'s "completed phases" section.

## Risk + rollback

| Step | Risk | Mitigation |
|---|---|---|
| 1 | Schema migration broken on fresh DB | tests cover `init_db()` happy path. |
| 2 | Concurrent `apply()` races | WAL + `busy_timeout=5000` already set; concurrent test in suite. |
| 3 | UI breaks on the new state values | UI templates updated in same commit; state-filter dropdown options re-listed; tested manually before merge. |
| 3 | Failure-event mapping picks the wrong event | Default to `VERIFY_FAIL` (catchall DEAD transition); operator can re-classify later if needed via the bundle artifacts. |
| 4 | Stub LLM unrealistic | Pull canned responses from a real bundle's `triage.md` and `patch.md` rather than hand-writing. |

## Review notes for the operator

Things to specifically check before implementation:

- **Step 1:** Is the `job_events` schema enough? Specifically — do
  we need `actor` (which step/runner instance triggered the event)
  for forensics, or is `detail_json` enough room?
- **Step 2:** Is REAP_ORPHAN the right cleanup story? Alternative:
  on startup, transition orphans back to QUEUED (re-claim instead
  of give-up). Re-claim is more eager to retry but risks burning
  tokens on a job that was failing for a reason. DEAD is safer.
- **Step 3:** Are there any other call sites I missed that touch
  `jobs.state`? `grep -nE 'jobs\.state|jobs SET|jobs (' scripts/`
  before starting.
- **Step 3:** The endpoint rename (`/v1/jobs/upsert` →
  `/v1/jobs/transition`) breaks any external caller. Anyone besides
  the hook + the runner posting to it? Should be no.
- **Step 4:** Are four test cases enough? Happy / MANUAL / reap /
  env_broken covers the main transitions. Patch-gave-up and
  budget-exhausted are exercised in production but not here; worth
  adding?

Sign off on these before Step 1 starts.
