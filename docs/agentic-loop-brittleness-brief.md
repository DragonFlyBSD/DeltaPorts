# Agentic loop — lifecycle brittleness brief

Hand-off context for an investigating agent. Self-contained: read this
plus the cited files and you should be able to identify additional
issues without prior session history.

## Repo layout the brief assumes

- `scripts/generator/dportsv3/agent/lifecycle.py` — the per-job FSM.
- `scripts/generator/dportsv3/agent/runner.py` — the queue orchestrator
  (claim, dispatch, enqueue follow-up jobs, reap).
- `scripts/generator/dportsv3/agent/worker.py` — the agent's tool
  surface against the dev-env (chroot ops + host-side overlay IO).
- `scripts/generator/dportsv3/migration/convert.py` — deterministic
  Makefile.DragonFly → overlay.dops conversion.
- `scripts/generator/dportsv3/agent/dops/classify.py` — classifies a
  port's overlay state (`converted` / `auto_safe_pending` /
  `needs_judgment` / `stale` / `not_in_scope`).
- `docs/agentic-consolidation-plan.md` — the in-progress plan tracking
  Steps 1-25. Step 20 family covers the dops conversion subsystem;
  Step 25 is the (unimplemented) edit-intent DSL.

The agentic loop runs *inside* a DragonFly dev-env chroot (chroot +
nullfs writable overlay). Tool calls that touch the substrate
(`classify-dops`, build, extract, install_patches) must go through
`dportsv3 dev-env exec ENV -- ...`. Host-side filesystem IO on
`env_dir/writable/...` exists for `get_file`/`put_file`/`emit_diff`
but is being migrated; see Bug B note below.

The loop produces no commits, branches, pushes, or PRs. Output is the
writable overlay + bundle artifacts (`rebuild_proof.json`,
`changes.diff`, `analysis/*.md`). Promotion is a manual operator step.

## The per-job state machine (`lifecycle.py`)

States (`JobState`, lifecycle.py:31-44):

```
non-terminal:  QUEUED  CLAIMED  TRIAGING  TRIAGED
               PATCHING  VERIFYING  CONVERTING
terminal:      DONE  ESCALATED  DEAD
```

Events (`JobEvent`, lifecycle.py:47-80) — selected:

- Entry / claim: `HOOK_ENQUEUED`, `CLAIM`
- Triage: `TRIAGE_START`, `TRIAGE_OK`, `TRIAGE_FAIL`, `TRIAGE_DEFER`
- Patch: `PATCH_START`, `PATCH_OK`, `PATCH_GAVE_UP`, `PATCH_BUDGET_OUT`
- Verify: `VERIFY_OK`, `VERIFY_FAIL`
- Convert: `CONVERT_START`, `CONVERT_OK`, `CONVERT_GAVE_UP`
- Interrupt: `ENV_BROKEN`, `REAP_ORPHAN`, `ABANDON`, `ESCALATE_MANUAL`

Transition table (`TRANSITIONS`, lifecycle.py:85-152). Three near-
identical 6-row blocks at lines 119-151 send every in-flight state to
DEAD on each of `ENV_BROKEN` / `REAP_ORPHAN` / `ABANDON`. Drift hazard.

Terminal-reason map (`_TERMINAL_REASONS`, lifecycle.py:156-167) fills
`jobs.retire_reason`. Resolution propagation (`_EVENT_TO_RESOLUTION`,
lifecycle.py:175-180) writes `bundles.resolution` *only if* the
caller passes `detail={"bundle_id": ...}` (lifecycle.py:302-309).

Atomic apply: `apply()` at lifecycle.py:228-321 — opens
`BEGIN IMMEDIATE`, reads current state (log first, cache fallback),
checks the transition, writes `job_events` row + upserts `jobs`. Two
sources of truth (`job_events` log + `jobs.state` cache) with opposite
reader priorities: `_read_current_locked` is log-first
(lifecycle.py:200-225); `current()` is cache-first (lifecycle.py:324-346).

Reapers: `reap_orphans` (in-flight → DEAD on runner restart,
lifecycle.py:370-393); `reap_stale_queued` (QUEUED rows whose `.job`
file is missing AND older than 1h, lifecycle.py:396-448).

## Cross-job orchestration (NOT in the FSM)

The state machine handles one job at a time. The loops live in
`runner.py`.

### Triage → patch

After `TRIAGE_OK`, `process_triage_job` calls
`policy.tier_for(classification, confidence)` and, for `AUTO`/`ASSIST`
tiers, calls `enqueue_followup_job(patch, ...)` to enqueue a fresh
patch job. `MANUAL` → `ESCALATE_MANUAL` instead. See
`runner.py:_register_new_job` (runner.py:361) and the patch enqueue
path in `process_triage_job`.

### Triage → convert (Step 20d defer/resume — the buggy loop)

`_maybe_defer_to_convert` (runner.py:1840+) is called at the top of
`process_triage_job`. It runs `worker.classify_dops(env, origin)`
(routed through `dportsv3 dev-env exec`). If the result is
`auto_safe_pending` or `needs_judgment`, it:

1. Enqueues a convert job via `enqueue_convert_job` (runner.py:1699+),
   unless one is already in-flight per `_find_active_convert_job`
   (runner.py:1635+).
2. Fires `TRIAGE_DEFER` to park this triage at DEAD with
   `retire_reason='deferred_for_convert'` (lifecycle.py:117, 166).

After the convert job lands DONE via `CONVERT_OK`,
`_resume_deferred_triage` (runner.py:1541+) finds the parked triage
in the DB and enqueues a fresh triage job carrying the same
bundle/run/origin metadata. The new triage runs through
`_maybe_defer_to_convert` again.

The intended invariant: after a successful convert, classify will
return `converted` (not `auto_safe_pending`), so the resumed triage
proceeds normally.

### The libunistring incident (2026-05-24)

`devel/libunistring` generated 100+ paired convert/triage jobs in
~13 minutes. Root cause: `convert_record` (convert.py) wrote
`overlay.dops` but never removed `Makefile.DragonFly`. The
`classify_dops` rule `has_dops AND NOT has_unmigrated` requires the
source file to be gone for the port to be `converted`; with both
files present it returned `auto_safe_pending` forever, looping the
runner. Convert iteration 2+ hit `dops_path.exists()` early-return —
no-op success — then `_resume_deferred_triage` fired again,
`_maybe_defer_to_convert` re-deferred, repeat.

Fixed at commit `5369db9fd4e`:
- `convert.py:_render_dops` / `convert_record` now `mk_path.unlink()`
  after a successful `overlay.dops` write (and on the validate-existing
  branch if a stray copy reappears). `dry_run` leaves the source alone.
- `runner.py:_recent_successful_convert` (new) is a wall-clock circuit
  breaker: if a convert for `(origin, target)` reached DONE within the
  last 10 minutes but classify still says
  `auto_safe_pending`/`needs_judgment`, `_maybe_defer_to_convert`
  refuses to re-defer and logs `triage_defer_circuit_break`.

The FSM transitions were all individually legal throughout the loop.
The bug was structural — orchestration, not state machine.

### Empty changes.diff (separate, earlier fix — commit `2d9de6c4edc`)

`worker.emit_diff` / `runner._write_changes_diff` ran plain `git diff`,
which is silent on untracked files. Bundles where the agent created a
fresh file (new `overlay.dops` on a compat-mode port) landed an empty
`changes.diff` despite `rebuild_ok=true`. Fixed with a helper
`worker._git_diff_with_untracked` that wraps `git add --intent-to-add`
+ `git diff` + `git reset` to keep the index clean. See test
`scripts/generator/tests/test_worker_emit_diff_untracked.py`.

## Open hardening backlog (un-implemented)

Identified during the post-libunistring lifecycle review. None of
these are required for the patch to ship; they're the structural
work to make whole classes of bugs impossible.

1. **Lineage + attempt counter.** Add `originating_bundle_id` and
   `attempt_n` (or `lineage_id`) columns to `jobs`. Cap defers per
   lineage in `_maybe_defer_to_convert`. Removes the need for the
   wall-clock circuit breaker.

2. **TRANSIENT_FAIL → re-queue edge.** Today every failure goes
   straight to DEAD with no path back. A transient verifier crash
   or chroot blip kills the job. Add an event that loops back to
   CLAIMED, gated by the attempt counter above.

3. **Per-state timeout sweep.** Equivalent to `reap_stale_queued` but
   for in-flight states. A PATCHING job hung indefinitely only dies
   on the next runner restart.

4. **Originating-bundle column.** `_EVENT_TO_RESOLUTION` only fires
   when callers thread `detail={"bundle_id": ...}`. Convert jobs
   have no bundle; resumed triages may have empty-string bundle_id.
   The bundle's `resolution` can stay NULL after a fix lands. A DB
   column + join replaces the thread-the-needle convention.

5. **Collapse the three interrupt blocks.** ENV_BROKEN, REAP_ORPHAN,
   and ABANDON each enumerate 6 hand-typed rows over the in-flight
   states. Derive from `_INFLIGHT_STATES` instead.

6. **Cache vs log readers disagree.** `_read_current_locked` is
   log-first, `current()` is cache-first. Pick one.

7. **CONVERT_START before vs after the work.** Today convert_record
   writes the file *then* fires CONVERT_START → CONVERT_OK quickly.
   Idempotent so a crash mid-sequence is fine, but the log doesn't
   distinguish "work attempted, not confirmed" from "work confirmed."

8. **TRIAGING → ESCALATE_MANUAL missing.** Triage can only escalate
   from TRIAGED. An unparseable LLM response can't ask for operator
   help; it lands TRIAGE_FAIL → DEAD instead.

9. **QUEUED→DEAD-via-REAP_ORPHAN is documented as "don't actually
   fire from the obvious helper."** Split into REAP_STALE_QUEUED
   (QUEUED only) and REAP_ORPHAN (in-flight only) so the FSM
   enforces the split.

## Other concurrent open work

- **Bug B (Step 23 expansion):** `emit_diff`, `get_file`, `put_file`,
  `install_patches` are still host-side IO against
  `env_dir/writable/...`. Memory rule from the operator: "interact
  with the dev-env, do not rely on the host for this or anything
  related to the contents of the trees being worked on." Migration to
  `dportsv3 dev-env exec` everywhere is deferred but tracked.

- **Step 25 (edit-intent DSL):** Designed in
  `docs/agentic-consolidation-plan.md`. Replaces the patch agent's
  free-form `put_file` edit surface with a small DSL the agent emits
  and the runner applies. Not yet implemented.

- **Plan doc is cumulative-allowed** (operator memory exception).
  Other plan files are per-phase rewrites; this one accumulates
  status markers and a current priority order.

## Investigative hints for the receiving agent

- Sweep `git grep -n "apply(.*JobEvent\." -- scripts/generator/` to
  find every call site that drives the FSM. Each one is a chance to
  forget `detail={"bundle_id": ...}` and break resolution
  propagation.
- The full transition table is ~30 rows in lifecycle.py:85-152. Read
  it end-to-end; the brittleness summary above is opinionated, not
  exhaustive.
- `scripts/generator/tests/test_runner_triage_defer.py` and
  `tests/test_lifecycle_convert.py` are the closest things to a
  spec for the convert flow.
- The tracker UI is operator-specific (HTTP, no LAN IPs baked
  anywhere); use `DP_TRACKER_URL` env var if you need to inspect
  bundle/job state from outside.
- For port-specific investigations there is a dedicated subagent:
  `.claude/agents/dportsv3-agentic-analyzer.md` with the procedure
  in `.claude/skills/dportsv3-agentic-analysis/SKILL.md`.
