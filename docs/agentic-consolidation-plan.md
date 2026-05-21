# Phase 4 — Consolidate state-server + tracker onto one DB

> **Phases 1–3 shipped.** Phase 1 (`dportsv3 dev-env exec`), Phase 2
> (`apply-patch` + `process_apply_job` retired), and Phase 3 (opencode
> replaced by `dportsv3.agent`) all landed in the
> `agentic-dsynth-evidence-hooks` branch. Their commits are the
> authoritative record; this document only carries the *current* plan.

## Goal

Today the agentic side runs on `state-server` (stdlib `http.server`,
~1500 LOC) reading/writing `state.db`, while build tracking runs on
the FastAPI `dportsv3 tracker serve` against a separate `tracker.db`.
Two processes, two DBs, two SPAs, overlapping concerns.

Phase 4 collapses this into:

- **One DB**: `state.db` (the existing one — `tracker.db` retires).
  Artifact-store writes failure evidence; tracker writes build runs +
  ports status. SQLite WAL + `busy_timeout=5000` + `foreign_keys=ON`
  on every connection lets both writers share the file safely.
- **One serve process** for the read/UI surface: the FastAPI tracker
  absorbs state-server's read endpoints, then state-server +
  `state-server-ui` retire.
- **One hook set**: `dsynth-hooks/` writes both the artifact-store
  failure bundle *and* the tracker run state on every event.

## Status

| Step | What | State |
|---|---|---|
| 1 | Artifact-store grows tracker tables in `state.db` schema | shipped |
| 1.5 | Artifact-store extracted into `dportsv3.artifact_store` (shim retained) | shipped |
| 2 | `POST /v1/user-context` on artifact-store | shipped |
| 3a | `dsynth-hooks/` + `builderhooks/` merged into one hook set | shipped |
| 4a | `dportsv3 dev-env update` + cache bind-mount inside chroot | shipped |
| 4 | Tracker reads/writes `state.db`; `tracker.db` retires | shipped (26ac0ae1e03) |
| 5 | Tracker agentic-read endpoints + target column on bundles/jobs/runs | shipped (8797550a6ac) |
| 6 | Tracker HTML views (bundle detail, jobs queue, runner) | shipped (fdc7528a24f) |
| 8 | Decommission `state-server` + `state-server-ui` SPA | shipped |

> **Phase 4 complete.** One DB (`state.db`), one serve process
> (FastAPI tracker), one hook set. Agent queue runner now points
> exclusively at the tracker for bundle/artifact lookups via
> `DPORTSV3_TRACKER_URL` (default `http://127.0.0.1:8080`).

(There is no step 7 — slot reserved during planning, folded into 6/8.)

## Step 5 — agentic read endpoints + target column

Two coupled changes ship together because the endpoints exist to
expose target-scoped views; landing the endpoints without the target
column would harden a known gap that step 8 then can't undo without
schema migration on top of decommission.

### Schema additions

`dportsv3.db.schema` gains (idempotent — applied via `MIGRATIONS`,
not `SCHEMA`, since legacy rows exist):

```sql
ALTER TABLE bundles ADD COLUMN target TEXT;
ALTER TABLE jobs    ADD COLUMN target TEXT;
ALTER TABLE runs    ADD COLUMN target TEXT;
CREATE INDEX IF NOT EXISTS idx_bundles_target ON bundles(target);
CREATE INDEX IF NOT EXISTS idx_jobs_target    ON jobs(target);
CREATE INDEX IF NOT EXISTS idx_runs_target    ON runs(target);
```

Target is nullable: pre-step-5 rows stay `NULL` and surface as target
`unknown` in filtered queries. Going forward every new row is
written with a target.

### Hook + writer changes

`scripts/dsynth-hooks/hook_common.sh` already knows the target — it
defaults `DPORTSV3_TRACKER_TARGET` from `$PROFILE`. The bundle upload
to artifact-store carries it in the POST body. Artifact-store writes
it on the `bundles` row (and on the `runs` row when creating one).
`scripts/agent-queue-runner` writes target on the `jobs` row when
enqueueing.

### Endpoints to port from state-server

All read; all live in `dportsv3/tracker/server.py` next to the
existing routes. Per-request SQLite connections (matching the
a14fe9c4dab pattern).

| state-server route | tracker route | target filter? |
|---|---|---|
| `GET /health` | `GET /api/health` | no |
| `GET /status` | `GET /api/agentic-status` | no (global counts) |
| `GET /activity` | `GET /api/activity?limit=N` | `&target=` |
| `GET /runner-status` | `GET /api/runner-status` | no |
| `GET /runs`, `/runs/<id>` | `GET /api/runs[?target=]`, `/api/runs/{id}` | yes |
| `GET /jobs?state=`, `/jobs/<id>` | `GET /api/jobs[?state=&target=]`, `/api/jobs/{id}` | yes |
| `GET /bundles`, `/bundles/<id>` | `GET /api/bundles[?target=&origin=]`, `/api/bundles/{id}` | yes |
| `GET /ports/<origin>` | `GET /api/ports/{origin}[?target=]` | yes |
| `GET /bundles/<id>/artifacts/<relpath>` | `GET /api/bundles/{id}/artifacts/{relpath:path}` | no |
| `GET /events` (SSE) | `GET /api/events[?target=]` (SSE) | yes |

`user-context*` stays on artifact-store (write path, step 2 settled
it there).

### Artifact root discovery

`GET /api/bundles/{id}/artifacts/...` streams from artifact-store's
`blob_root`. The tracker doesn't know that path today. Resolution:
`DPORTSV3_ARTIFACT_ROOT` env var on the tracker process, defaulting
to the artifact-store default (`/build/synth/logs/evidence`).
Symmetric with `DPORTSV3_STATE_DB`.

### SSE on FastAPI

State-server hand-rolls SSE on `http.server`. Tracker uses
`StreamingResponse` + async generator that:
- Polls `events` table on a 1s tick.
- Tracks last-seen `id` per connection.
- Emits each new row as `event: <type>\ndata: <json>\n\n`.
- Filters by target if `?target=` is supplied.

Async polling is fine here — single FastAPI process, low event rate.
No LISTEN/NOTIFY, no external pub/sub.

### Tests

One pytest module per route group under `scripts/generator/tests/`
(`test_tracker_agentic_runs.py`, `test_tracker_agentic_bundles.py`,
`test_tracker_agentic_jobs.py`, `test_tracker_agentic_events.py`).
Each seeds `state.db` with rows including a mix of target values
(some `NULL` to verify legacy-row tolerance) and asserts:
- response shape matches the state-server equivalent (no field-name
  drift),
- `?target=` filtering works,
- `NULL`-target rows surface only when no filter is set.

### LOC estimate

| Area | Added | Notes |
|---|---|---|
| `dportsv3/db/schema.py` migrations | ~10 | three ALTERs + three indexes |
| `dportsv3/tracker/server.py` routes | ~350 | ten read endpoints + SSE |
| `dportsv3/tracker/queries.py` (new) | ~150 | agentic-read SQL helpers |
| `dportsv3/artifact_store.py` target writes | ~20 | bundle/run insert paths |
| `scripts/dsynth-hooks/hook_common.sh` | ~5 | target in POST body |
| `scripts/agent-queue-runner` job-enqueue paths | ~10 | target in jobs insert |
| tests | ~250 | one module per route group |
| **Total** | **~795** | |

No deletions in this step. State-server keeps running in parallel
until step 8.

## Step 6 — HTML views

Add target-scoped views to the tracker:

- `/agentic/bundles[?target=]` — bundle list
- `/agentic/bundles/{id}` — bundle detail with linked artifacts
- `/agentic/jobs[?target=&state=]` — job queue
- `/agentic/runner` — runner heartbeat / status

Templates live next to existing `dportsv3/tracker/templates/`.
Server-rendered, no SPA. Style follows the existing tracker
dashboard.

## Step 8 — decommission state-server + state-server-ui

- Delete `scripts/state-server` (~1500 LOC).
- Delete `scripts/state-server-ui/` (the React SPA).
- Remove the systemd/rc unit references in `docs/dportsv3-user-guide.md`.
- Update any docs/scripts that point at the state-server port.
- One follow-up commit; predicated on step 5 + step 6 being in
  production for at least one build cycle.

## Out of scope (Phase 4)

- Tracker UI redesign — Phase 5.
- Authn/authz on agentic-read endpoints — operator-internal for now.
- Migration of historical `state.db` rows on machines that already
  ran pre-step-5: those rows keep `target = NULL` and surface in
  unfiltered views. No backfill.

## Verification

End-to-end check after step 5 lands:

1. `dportsv3 dev-env exec foo -- /etc/dsynth/05_failed.sh ...` triggers
   the hook, uploading a bundle to artifact-store with
   `target=$PROFILE` in the POST body.
2. `sqlite3 state.db 'select bundle_id, target from bundles order by
   id desc limit 1;'` shows the new row with non-NULL target.
3. `curl tracker:8080/api/bundles?target=@2026Q2` returns it.
4. `curl tracker:8080/api/bundles?target=@main` excludes it.
5. `curl -N tracker:8080/api/events?target=@2026Q2` streams the
   `bundle_upserted` event for the same upload.
6. Same checks against `state-server`'s legacy endpoints still pass
   (parallel operation until step 8).

## Post-implementation follow-up — manual escalation and operator loop

Phase 4 shipped the unified DB/tracker surface, but the first real
agentic runs exposed gaps in the manual escalation path. The current
system can mark work as escalated and record a `user_context_request`,
but the operator handoff is not yet good enough: artifacts are hard to
read, prior attempts are not summarized well, retry caps are too coarse,
and providing new context is not a first-class tracker workflow.

### Goal

Turn manual escalation from a terminal dead end into an operator-guided
retry loop:

1. The tracker makes bundle artifacts easy to inspect inline.
2. Escalated jobs produce a concise handoff artifact explaining what
   happened and what input is needed.
3. Operators can provide context from the tracker and explicitly ask the
   runner to try again.
4. The runner re-enters triage/patch only when no same-origin job is
   already queued or running.
5. Prior patch attempts are included in future patch prompts using the
   artifacts the system actually writes today.
6. Retry caps distinguish repeated build failures from failed automated
   patch attempts.

### Step 1 — improve bundle artifact reading — done

Status: shipped via tracker artifact viewer, inline bundle-page previews,
and Markdown rendering.

Make bundle artifacts readable from the tracker before changing manual
flow. Operators need to inspect the evidence quickly before they can
provide useful context.

Changes:

- Add an artifact viewer page under
  `/agentic/bundles/{bundle_id}/artifacts/{relpath:path}`.
- Render common text artifacts inline instead of forcing downloads:
  `*.txt`, `*.md`, `*.json`, `*.diff`, `*.patch`, `*.log`, `*.rej`,
  `*.dops`, `Makefile`, `distinfo`, `pkg-plist`, `pkg-descr`.
- Pretty-print JSON artifacts.
- Render diff/patch artifacts in a readable `<pre>` block.
- For gzip full logs, keep download behavior, but add a clear label that
  the artifact is compressed.
- Link artifact names in bundle detail to the viewer page, not directly
  to raw download, while retaining a raw/download link.
- Add tests for inline text, JSON, binary/download, and missing artifact
  behavior.

Rationale:

Manual escalation starts with reading evidence. The current artifact
links are inconsistent: some render as text, some download, and some
lack enough context for quick triage.

### Step 2 — fix prior patch attempt ingestion — done

Status: shipped in `f34ef92ed90 fix(agent): summarize prior patch
attempts`.

The patch prompt currently has a `Prior Attempts` section, but it looks
for legacy artifact names:

- `analysis/patch_plan.json`
- `analysis/patch.log`
- `analysis/rebuild_status.txt`

Current patch jobs write:

- `analysis/patch.md`
- `analysis/patch_audit.json`
- `analysis/changes.diff`
- `analysis/tool_trace.jsonl`

Update `PriorAttemptsSection` to consume the current artifacts.

Changes:

- Include `analysis/patch.md` when present.
- Include `analysis/patch_audit.json`, especially status, token usage,
  attempts, and rebuild result.
- Include `analysis/changes.diff`, capped to a safe size.
- Include a compact summary of `analysis/tool_trace.jsonl`, not the full
  trace.
- Prefer the most recent three patch-producing bundles for the same
  `(target, origin)`.
- Add tests proving prior attempts appear in patch payloads.

Rationale:

Agents are currently told to check prior attempts, but may receive no
useful prior attempt content. This encourages repeated failed strategies
and token burn.

### Step 3 — write a manual handoff artifact

When a job escalates to manual, write a structured
`analysis/manual_handoff.md` artifact.

Contents:

- Origin and target.
- Why manual was triggered.
- Whether the trigger was policy/manual classification, retry cap,
  budget exhaustion, or repeated patch failure.
- Latest triage classification and confidence.
- Latest triage suggested fix.
- Recent attempt count and window.
- Previous patch job summary.
- Files touched by the last patch attempt.
- Latest `changes.diff` summary.
- Last failing build/log summary.
- Specific question for the operator.

Example operator question:

```text
The agent tried updating dragonfly/patch-terminal.c but the rebuild
still failed. Should the fix remain a static patch, or should this be
converted to a semantic dops / REINPLACE_CMD operation?
```

Add tests for handoff generation on retry-cap escalation and direct
MANUAL-tier escalation.

Rationale:

Manual escalation should produce a useful handoff, not just a terminal
state and scattered artifacts.

### Step 4 — expose manual requests in tracker

Add a first-class manual work queue in the tracker.

Routes:

- `/agentic/manual`
- `/agentic/manual/{run_id}/{origin}`
- `GET /api/manual-requests`
- `POST /api/manual-requests/{run_id}/{origin}/context`

The tracker API can forward context to artifact-store's existing
`POST /v1/user-context` endpoint or write directly to the shared DB if
we decide tracker should own this write path.

Display:

- Origin, target, run, bundle.
- Classification/confidence.
- Escalation reason.
- Latest manual handoff.
- Links to relevant artifacts.
- Text box for operator context.
- Button: `Try again with this context`.

Rationale:

`user_context_requests` exists, but operators currently have no obvious
UI to see pending requests or provide context.

### Step 5 — operator-guided retry loop

When the operator submits context and clicks `Try again with this
context`, the runner should enqueue a new triage job only if the same
origin is not already active.

Rules:

- Check for existing queued/inflight jobs for the same `(target, origin)`.
- Treat these states as active:
  `queued`, `claimed`, `triaging`, `triaged`, `patching`, `verifying`.
- If active work exists, reject or mark the request as waiting, and show
  the blocking job.
- If no active work exists, enqueue a new triage job with:
  `user_context_rev`,
  `previous_bundle`,
  same run/origin/target,
  incremented manual retry metadata.
- The next triage/patch payload includes `## User Context`.

State changes:

- Keep `escalated` as a terminal state for the old job.
- The retry is a new job linked by `previous_bundle` and
  `user_context_rev`.
- Add enough metadata for tracker to show the lineage.

Rationale:

The operator needs an explicit “try again with this info” mechanism, but
we must avoid duplicate same-origin jobs racing.

### Step 6 — refine retry cap

The current retry cap is based on recent bundle failures:

```text
recent bundle failures >= max attempts
```

That is too coarse. Replace or supplement it with agentic attempt
history.

Better signals:

- Number of failed patch jobs for the same `(target, origin)`.
- Number of patch jobs that produced non-empty `changes.diff`.
- Number of patch jobs ending in `patch_budget_exhausted`.
- Number of patch jobs ending in `patch_gave_up`.
- Whether the last failure signature changed.
- Whether user context has been provided since the last escalation.

Policy proposal:

- Keep bundle-failure cap as a safety backstop.
- Do not force manual solely from bundle count if there has been no
  meaningful patch attempt.
- Escalate when there are repeated failed patch attempts with similar
  failure signatures.
- Reset or relax the cap when fresh operator context is provided.

Tests:

- Bundle failures alone do not escalate if no patch attempt was made.
- Repeated failed patch jobs escalate.
- Fresh user context permits another attempt.
- Active same-origin job prevents duplicate retry.

Rationale:

Manual escalation should mean “automation is stuck”, not merely “dsynth
has failed N times”.

### Step 7 — synthesize patch reports on empty/budget-exhausted output

If a patch attempt ends with empty `patch.md`, invalid final sections, or
budget exhaustion, synthesize a fallback report.

Fallback artifact:

- `analysis/patch.md` with generated summary.
- `analysis/patch_audit.json` already exists; keep it authoritative.
- Optional `analysis/patch_summary.json` for machine-readable UI use.

Summary sources:

- `patch_audit.json`
- `changes.diff`
- last N `tool_trace.jsonl` events
- last `dsynth_build` result
- last `dsynth_log` result if available

The synthesized report should say clearly that it is generated by the
runner, not by the LLM.

Rationale:

The operator and future agents need an actionable trail even when the
LLM exhausts budget without producing a final response.

### Step 8 — lifecycle naming cleanup

Keep `escalated` for completed handoff, but introduce clearer UI
language around manual waiting and retry.

Options:

- Keep DB state as `escalated`, but show UI status:
  `waiting for operator context`.
- Or add a new lifecycle state:
  `blocked_user_context`.

Preferred minimal approach:

- Keep lifecycle states unchanged for now.
- Use `retire_reason`, `user_context_requests`, and UI labels to
  distinguish:
  `manual_requested`,
  `waiting_for_context`,
  `context_received`,
  `retry_enqueued`.

Rationale:

Avoid a larger state-machine migration until the operator loop is proven.

### Step 9 — tracker UX polish for manual work

Add job and bundle page affordances:

- Prominent manual handoff panel on escalated jobs.
- “Open latest artifacts” shortcuts:
  triage, handoff, patch audit, changes diff, tool trace, errors.
- Prior attempts table for same origin.
- Inline indication when an origin already has queued/running work.
- Link from `/agentic` dashboard to pending manual requests.

Rationale:

Manual intervention should be fast and local to the tracker, not a hunt
through API endpoints and raw artifacts.

### Step 10 — kick out stale queued jobs

Surfaced during the first real smoke test: a 4-hour-old `state=queued`
job for `devel/readline` was blocking the operator-triggered retriage
guard (`_has_active_same_origin_job` in
`scripts/generator/dportsv3/agent/runner.py`). The row was an
abandoned tombstone — its `.job` file no longer existed on the host,
nothing was ever going to claim it, but the guard saw it as "active"
and refused to enqueue the new triage.

Today the runner's `REAP_ORPHAN` event only covers in-flight states
(CLAIMED/TRIAGING/TRIAGED/PATCHING/VERIFYING) on the assumption that
queued rows are always recent and claimable. That assumption breaks
when the `.job` file goes missing or the runner restarts without
processing.

Two complementary fixes.

#### 10a — automatic reap of stale queued (defensive)

Extend the lifecycle transitions to include
`(JobState.QUEUED, JobEvent.REAP_ORPHAN) → DEAD`, but the runner-
startup reap path adds a guard: only reap a queued row when
*both* (a) `last_transition_at` is older than a configurable
threshold (default 1 hour) AND (b) the corresponding `.job` file
is missing from the host's `pending/` directory. Either condition
alone is too aggressive — a fresh runner restart must not reap
brand-new legitimate queued work, and a file present + recent
timestamp means the runner just hasn't gotten to it yet.

Tests:

- Stale (>1h, missing file) → reaped to DEAD with
  `retire_reason='runner_restart'`.
- Stale but file present → not reaped (claim will pick it up).
- Recent (<1h) regardless of file → not reaped.
- The threshold is overridable via env var so operators can shorten
  it for high-churn deployments.

#### 10b — operator-triggered abandon

A queued or in-flight job that the operator decides is unwanted
(stuck, superseded, wrong origin, etc.) needs an explicit kill
mechanism. Add a new `JobEvent.ABANDON` with transitions from
QUEUED + every in-flight state → DEAD with
`retire_reason='abandoned'`.

Tracker side:

- `POST /api/jobs/{job_id}/abandon` — fires the transition.
- Button on `/agentic/jobs/{job_id}` for any non-terminal state.
- On the manual queue detail page, if the request is blocked by an
  in-flight job, surface "blocked by job X — [Abandon job X]" so
  the operator can clear the blocker without sqlite spelunking.
- Activity-log + events row so the abandonment is auditable.

Tests:

- Abandon from QUEUED, CLAIMED, TRIAGING, TRIAGED, PATCHING,
  VERIFYING → all land at DEAD with `retire_reason='abandoned'`.
- Abandon from DONE/DEAD/ESCALATED → rejected (IllegalTransition).
- Abandon endpoint returns 404 for unknown job_id.
- UI: button hidden on terminal jobs, shown on non-terminal.

#### Order

10a first (defensive — fixes the recurrence at runner startup),
10b after (gives the operator the agency for the cases 10a's
heuristics don't cover, e.g. a 30-minute-old job that's
legitimately stuck).

### Suggested implementation order

1. Artifact viewer and artifact-link cleanup.
2. Prior attempt ingestion fix.
3. Manual handoff artifact generation.
4. Manual requests tracker page/API.
5. Operator “try again with this context” flow with duplicate-job guard.
6. Retry-cap refinement.
7. Synthesized patch reports for empty/budget-exhausted outputs.
8. Lifecycle/UI naming cleanup.
9. Tracker UX polish for manual work.
10. Kick out stale queued jobs (10a automatic reap, 10b operator abandon).

This order improves operator visibility first, then improves agent
context, then adds the retry loop, then refines policy, and
finally hardens the edges that the first real smoke test surfaced.
