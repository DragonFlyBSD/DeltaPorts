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

#### 9a — token-cost columns on the activity table

Surfaced during smoke: ``llm_turn`` events emit structured data
(``prompt_tokens``, ``completion_tokens``, ``total_tokens``,
``cumulative_total_tokens``) into ``activity_log.extra_json``, but the
UI crams them into the ``message`` column as prose:

```
A1.T6 in=15896 out=1920 total=17816 cumulative=75170 → dupe
```

That's hostile to scanning. The structured fields are already there;
the UI just doesn't render them as columns. Concrete shape:

- The activity table on ``/agentic/jobs/{job_id}`` gains four columns:
  ``prompt``, ``completion``, ``total``, ``cumulative``. Filled for
  ``llm_turn`` rows; empty for ``tool:*`` and other rows.
- The crammed message becomes a clean "→ tool1, tool2" affordance.
- A small "Token usage" summary card lands above the activity table:

```
┌──────────────────────────────────────────────┐
│ Token usage                                  │
│   Prompt:        478,940    (94.7%)          │
│   Completion:     26,610    (5.3%)           │
│   Total:         505,550                     │
│   LLM turns:     19                          │
│   Largest turn:  T8 (593,472 — after dupe)   │
└──────────────────────────────────────────────┘
```

Aggregated from the same event stream, no new write path. Layered
addition: once Step 12's pricing config lands, the card grows a
``$cost`` line derived from the model + token totals.

Same columns on ``/agentic/bundles/{id}`` for the *bundle's* lifetime
cost summed across all attempts that touched it — useful for "is this
port costing too much?"

#### 9b — sorting + filtering on llm_turn

Once tokens are columns, sorting by ``prompt`` lets the operator
find the prompt-explosion turn in two clicks instead of scanning
the message text. A ``filter: llm_turn only`` or ``filter: tool calls
only`` toggle on the activity table makes per-turn analysis viable
without scrolling past tool rows.

#### 9c — live-refresh on active jobs

The job and bundle detail pages should auto-update while the
underlying job is in a non-terminal state, and stop the moment it
retires. No dropdown selector, no operator config — the page knows
when it's interesting from ``jobs.state``.

Design:

- Page renders a discreet ``● live · last update 2s ago``
  affordance in the header when ``jobs.state`` is non-terminal
  (queued/claimed/triaging/triaged/patching/verifying). Goes
  ``○ idle`` and stops polling when state moves to done/dead/
  escalated.
- Polling mechanism: JS calls ``/api/activity?job_id=X&since_id=N``
  every 3s with a monotonic cursor (``N`` = highest id rendered
  so far). Server returns only new rows. Client prepends them with
  a brief fade so the operator can see what just landed.
- Per-page-visibility: pause polling when ``document.hidden`` is
  true (tab not active). Auto-resume on visibility change. Saves
  bandwidth without operator interaction.
- One manual control: a ``[pause]`` ↔ ``[resume]`` text link in
  the corner for operators who want the table to stop scrolling
  while reading. Default is auto.

What it does NOT replace:
- The page initial render still happens server-side (no SPA
  rewrite). Live-refresh only adds *new* rows; existing rows
  stay where they are.
- The bundle list and the agentic dashboard don't get live polling
  — they refresh on operator action only.
- SSE could be a future swap (the ``/api/events`` endpoint
  already exists), but the current implementation goes with
  polling + cursor because the activity_log → server-sent-events
  bridge would be a bigger change than the polling JS.

Pairs with 9a structurally — same template, same activity-table
JS, same cursor logic. Ship them in the same commit.

#### Rationale

Manual intervention should be fast and local to the tracker, not a hunt
through API endpoints and raw artifacts. Per-turn token cost is
specifically high-value because it's the variable that operators
need to read to make budget + model decisions; cramming it into prose
defeats the telemetry that smoke testing put in place.

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

### Step 11 — fix delivery & verification

The plan's earlier phases stop at "agent says `rebuild_ok=true` and writes
`analysis/changes.diff`". Everything past that — the operator extracting
the diff into their own clone, reviewing, signing, committing, pushing,
opening a PR — is improvised and undocumented beyond a paragraph in
`docs/AGENTIC_BUILDS.md` ("When a fix lands").

Step 11 closes that gap. The goal is a documented, trackable path from
"agent thinks it fixed it" to "the change is in front of a code reviewer
on the upstream code-hosting platform" — without violating the plan's
hard rule that the agent loop itself does not branch, commit, push, or
PR. Delivery is a *separate, explicit, operator-triggered* phase that
runs after the agent's local work is complete and (ideally) verified.

The agent's own ``rebuild_ok=true`` is from ``dsynth_build`` inside the
same env it just edited. That's not the same as "this fix works on a
clean tree." Step 11 distinguishes the agent's claim from a real
independent verification, then formalizes accept → submit.

#### 11a — proposed-fix artifact

When ``analysis/rebuild_proof.json`` lands with ``rebuild_ok=true``,
write ``analysis/proposed_fix.md`` summarizing what the operator needs
to act on:

- Origin and target.
- One-line agent summary (from ``analysis/patch.md``, capped).
- The diff path and a copy-paste-ready ``patch -p1`` / ``git apply``
  recipe against a fresh DeltaPorts clone.
- The exact ``dportsv3 dev-env verify-fix <bundle_id>`` invocation
  the operator can run to independently confirm.
- Token cost, attempts, model used (for audit).

Mirrors the ``manual_handoff.md`` machinery: pure render + lazy build
helper, write at PATCH_OK in ``steps.py``, surface in the bundle's
artifact list and as the default preview when
``resolution='agent_fixed'``.

Tests:

- Bundle with ``rebuild_ok=true`` produces ``proposed_fix.md``.
- ``rebuild_ok=false`` does *not* produce it.
- Renders cleanly with manual handoff's markdown extensions.
- Diff path + verify command reference the actual bundle_id.

#### 11b — independent verification

Add a new dev-env subcommand:

```
dportsv3 dev-env verify-fix BUNDLE_ID [--target ...] [--keep]
```

What it does:

1. Resolves the bundle's origin + target.
2. Provisions or selects a *fresh* dev-env (clean writable overlay,
   no agent edits in flight). ``--keep`` preserves the verification
   env for inspection; default is throwaway.
3. Fetches the bundle's ``analysis/changes.diff`` from artifact-store.
4. Applies the diff to the verification env's DeltaPorts overlay.
5. Runs ``dsynth -S -y -p $PROFILE build <origin>`` in that env, with
   the same ``DPORTSV3_HOOKS_FLAG_FILE`` discipline as the patch
   agent (so hooks don't recursively trigger).
6. Reports back to the tracker via a new endpoint:
   ``POST /api/bundles/{bundle_id}/verification`` with
   ``{ok: bool, dsynth_log: str, verified_at: iso}``.
7. The bundle row grows a ``verification_status`` column with values
   ``verified`` / ``verification_failed`` / NULL (not yet attempted).

Surface in the UI:

- Bundle detail and bundle list show the verification status as a
  pill alongside ``resolution``.
- ``proposed_fix.md`` updates to include the verification badge once
  it's set (lazy render at view time).

Tests:

- A trivially-applying diff against a freshly-failing port produces
  ``verified``.
- A diff that doesn't apply cleanly (context mismatch, missing file)
  produces ``verification_failed`` with the patch error in the log.
- A diff that applies but doesn't actually fix the build produces
  ``verification_failed`` with the dsynth log tail.
- Tracker endpoint validation: 404 unknown bundle, 400 missing fields,
  200 happy path.

#### 11c — accept / reject in the tracker

Two buttons on any bundle with ``resolution='agent_fixed'``
(stronger UX when ``verification_status='verified'``):

- **Accept** → ``POST /api/bundles/{bundle_id}/accept`` with optional
  operator note. Sets ``resolution='accepted'``, records
  ``accepted_at`` + ``accepted_by`` (when auth lands), emits an
  ``accepted`` event. The diff is now considered the authoritative
  fix for that origin.
- **Reject** → ``POST /api/bundles/{bundle_id}/reject`` with reason.
  Sets ``resolution='rejected'`` and *reopens* the loop: enqueues a
  fresh triage job with the rejection reason injected as
  ``user_context`` (so the next agent attempt knows what humans
  didn't like about the last one).

State machine on ``bundles.resolution``:

```
   ┌─ NULL ──────────────► agent_fixed ───► verified ──► accepted (terminal)
   │                            │              │
   │                            ▼              ▼
   │                       rejected ─┐    verification_failed ─┐
   │                                 │                          │
   │                                 ▼                          ▼
   └──────────────────────── (re-triage, new bundle) ───────────┘
```

Tests: accept/reject endpoints (happy path, 404, 409 if already
terminal); reject path enqueues a follow-up triage with user_context
populated; UI surfaces buttons only on appropriate states.

#### 11d — push to code-hosting providers

On Accept (11c), optionally drive the change all the way to a review
request on the upstream code repository. **Operator-triggered, not
automatic.** Provider-agnostic by design — GitHub is the primary
target but the abstraction must support GitLab, Gitea, Forgejo, and
self-hosted variants.

##### Abstraction

A new module ``scripts/generator/dportsv3/delivery/`` (sibling to
``dportsv3.agent``) with a ``ReviewProvider`` protocol:

```python
class ReviewProvider(Protocol):
    name: str   # "github" / "gitlab" / "gitea" / ...

    def create_review_request(
        self,
        *,
        clone_dir: Path,          # local DeltaPorts clone with applied diff
        branch_name: str,         # already-created local branch
        base_branch: str,         # "master" / "main" / target-specific
        title: str,
        body: str,
        labels: list[str],
        draft: bool = False,
    ) -> ReviewRequestResult:
        """Push the branch + open a review request. Returns the
        provider-side URL + ID for tracker storage. Idempotent: if
        an open request already exists for the same (origin, target,
        signature), update its body and return the existing URL."""
```

Concrete implementations:

- ``GitHubProvider`` — uses ``gh`` CLI by default (already installed
  on most operator boxes, handles auth via ``gh auth``). Falls back
  to the REST API with a personal-access-token if ``gh`` is absent.
- ``GitLabProvider`` — REST API + token. Project ID configurable.
- ``GiteaProvider`` — REST API + token. Same shape as GitLab.
- ``LocalPatchProvider`` — fallback no-network provider that writes
  the proposed patch to a designated outbox directory
  (``$DPORTSV3_DELIVERY_OUTBOX``) for manual fetch. The default
  when no provider is configured. Keeps the "diff via copy-paste"
  story intact for operators who don't want any push at all.

Selection:

```toml
# config/delivery.toml (new)
[provider]
type = "github"          # "github" | "gitlab" | "gitea" | "local-patch"
repo = "DragonFlyBSD/DeltaPorts"
base_branch = "master"
draft = true             # open PRs as draft by default — operator un-drafts
labels = ["agentic-fix", "needs-review"]
branch_template = "agentic/{origin_safe}-{bundle_short}"
```

Env-var overrides for the secret (``DPORTSV3_DELIVERY_TOKEN``).
Per-target overrides via TOML sections so quarter branches can
land on different repos / base branches.

##### Mechanism

On Accept, if a provider is configured:

1. Resolve the operator's local DeltaPorts clone path
   (``$DPORTSV3_OPERATOR_CLONE`` env var, or operator selects per-call).
   *The agent does not touch this clone* — the delivery module
   uses it strictly as a normal git working copy on the operator's
   behalf.
2. Verify it's clean (no staged/unstaged changes) and on
   ``base_branch``. Abort with a clear error otherwise.
3. ``git fetch origin``, then ``git checkout -b <branch_name>
   origin/<base_branch>``.
4. Apply the bundle's ``analysis/changes.diff`` (``git apply --3way``).
5. ``git commit -s`` with a templated message:

   ```
   {origin}: fix dsynth build under {target}

   {one-line agent summary}

   Verified by `dportsv3 dev-env verify-fix {bundle_id}` ({timestamp}).
   Operator: {accepted_by}
   Agent: model={model} attempts={n} tokens={total}
   Bundle: {bundle_url}
   ```

   ``-s`` adds the operator's Signed-off-by, which is what we want
   when a human accepts. The agent itself does *not* sign.
6. Call ``provider.create_review_request(...)`` to push + open the
   review request.
7. Record the returned URL + ID in a new ``bundle_review_requests``
   table linked back to the bundle. The bundle detail page links
   to the review request.

##### Idempotency + retry

If the operator clicks Accept twice, or the push fails partway:

- The provider implementation looks for an existing open review
  request matching ``(origin, target, signature)`` (the same
  ``error_signature`` we added in Step 6). If found, updates the
  body instead of opening a duplicate.
- ``git apply --3way`` either succeeds, conflicts (operator notified
  on tracker, given the conflict in the response body), or no-ops
  if already applied.

##### Safety rails

- No automatic accept-then-push without an explicit operator click.
  Auto-push would re-introduce the very thing the consolidation
  plan ruled out.
- Tracker must show a "delivery in progress" / "delivery succeeded
  with URL X" / "delivery failed: Y" state distinctly. Operator
  shouldn't have to refresh and pray.
- Token storage: read from env var or a 0400 file under
  ``$DPORTSV3_CONFIG_DIR/delivery.token``. Never committed to the
  repo, never written to artifact-store.
- Each provider has a ``--dry-run`` mode that prints what it would
  do without pushing — useful for the first run against a new
  target repo, and required for the test suite.

##### Tests

- ``LocalPatchProvider`` (no network): writes a patch file to the
  outbox; happy path + collision (same origin twice) + outbox-doesnt-
  exist error.
- Each network provider: monkeypatched HTTP layer; verify it sends
  the right shape of request, parses the right shape of response,
  handles auth-failed and rate-limit responses gracefully.
- The Accept-with-delivery flow: stubs the provider, asserts the
  bundle row gets the URL recorded and the lifecycle event fires.
- Idempotency: clicking Accept twice produces one URL, not two
  open PRs.

##### Out of scope for 11d

- Auto-merging PRs after CI passes. That stays a human call.
- Posting PR status (CI green/red) back into the tracker. Belongs
  in a separate "review-status feedback" step if we want it later.
- Multi-PR per bundle (one diff → one review request). If the
  operator wants to split the diff into multiple PRs, they do that
  in their clone — outside this loop.

#### Order

11a → 11b → 11c → 11d. Each builds on the previous:

- 11a is independent and useful even without verification (operator
  reads a structured artifact instead of grepping diffs).
- 11b's verification result feeds 11c's accept UX (verified bundles
  get a stronger Accept affordance).
- 11c's accept lifecycle is the trigger 11d hooks into.

11d can stay disabled (LocalPatchProvider default) until the team
agrees on which upstream repo / which base branch / which review
discipline. Shipping 11a–11c without 11d still closes most of the
"how does this get delivered?" gap; 11d is the optional final mile.

#### Constraint preservation

11a–11d do *not* violate the plan's "Out of scope (actively removed)"
section. The agent loop itself still doesn't branch, commit, push, or
PR. All of that happens in the *delivery* phase, which:

- runs in a separate module (``dportsv3.delivery``), not
  ``dportsv3.agent``;
- triggers only on explicit operator action via the tracker;
- operates on the operator's own clone, never on the agent's
  writable overlay.

The agent stays scoped to "produce a fix in a sandbox". Delivery is
a thin operator-facing layer that walks that fix out of the sandbox
and onto a review platform — with the operator's signature attached.

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
11. Fix delivery & verification (11a proposed-fix artifact,
    11b verify-fix subcommand, 11c accept/reject UI,
    11d push to code-hosting providers).
12. Telemetry bus + sinks (replace ad-hoc event plumbing).
13. Tool guardrail middleware (replace inline per-tool refusals).
14. Context budget + KEDB metadata (cap payload growth, gate entries
    by classification).
15. Payload cost optimization pass (15a system-prompt trim,
    15b don't-reread directive, 15c KEDB classification gating,
    15d history elision, 15e model-tier experiment).

This order improves operator visibility first, then improves agent
context, then adds the retry loop, then refines policy, then hardens
the edges that the first real smoke test surfaced, then walks the
agent's local fix all the way out to a review request on the
upstream code-hosting platform, then pays down the architectural
debt that smoke surfaced, and finally uses that new machinery to
drive the per-fix token cost down from "more than operator time" to
"less than operator time."

---

## Architectural follow-ups (steps 12–14)

Steps 12–14 are different in shape from 1–11. Steps 1–11 were
*missing features*; 12–14 are *missing abstractions*. Smoke testing
made the shapes visible: each new metric, each new guardrail, each
new context section landed as an in-place edit to multiple files,
because the underlying mechanisms weren't compositional. These
three steps refactor the offending seams.

### Step 12 — telemetry bus + sinks

Today every new metric is its own code path: emit-via-callback,
handle-in-dispatcher, write-to-activity-log. Adding ``llm_turn``
required touching ``tool_loop.py`` (emit), ``triage.py`` (emit
again, separately), ``steps.py`` PatchEventDispatcher (route), and
``steps.py`` TriageStep (route again, also separately). N metrics
× M flows = N×M edits.

The cleaner shape:

```
emit_event(LLMTurn(prompt=..., completion=..., turn=...))
    │
    ▼
TelemetryBus.fanout
    │
    ├──► ActivityLogSink     (existing activity_log table)
    ├──► ToolTraceSink       (existing tool_trace.jsonl artifact)
    ├──► PrometheusSink      (later)
    └──► CostSink            (computes $ via per-model pricing config)
```

Components:

- **Typed events.** ``TelemetryEvent`` dataclasses, one per kind:
  ``AttemptStart``, ``AttemptEnd``, ``LLMTurn``, ``ToolCall``,
  ``ResolutionWritten``, etc. Fields are typed. Schema evolution
  goes through normal dataclass updates rather than dict-key
  drift.
- **Sink Protocol.** ``Sink.emit(event)`` is the only contract.
  Implementations decide whether they care about a given event
  type (most sinks filter; some — like an aggregate-tokens sink —
  consume everything).
- **TelemetryBus.** Owns the sink list and the per-job context
  (job_id, origin, target). One ``emit`` call fans out to every
  sink with the context attached.
- **Pricing config.** ``config/model-pricing.json`` mapping model
  name → ``{ in_per_mtoken, out_per_mtoken }``. CostSink derives
  ``$cost`` as a field on cost-bearing events.
- **Aggregator helpers.** ``metrics.cost_per_port(target)``,
  ``metrics.median_attempts(target)``, etc. — derived from the
  event stream, queryable from the tracker UI.

What it replaces:

- ``PatchEventDispatcher`` and the duplicated ``_triage_event``
  closure in ``steps.py`` both retire — they become sink
  instances.
- The ad-hoc ``activity_log(...)`` calls inside ``runner.py`` and
  ``steps.py`` route through the bus instead, with the
  ActivityLogSink doing the table write.
- New metrics ship as one new dataclass + zero downstream edits
  if the existing sinks cover them.

Tests:

- Sink registration + fanout: emit one event, all registered
  sinks see it.
- Each existing sink emits the same rows it did before (parity
  with current activity_log content).
- Pricing config: a malformed pricing entry surfaces an explicit
  warning, doesn't silently zero out cost.
- Schema evolution: an event field added later doesn't break old
  sinks (Pydantic ``extra='ignore'`` or equivalent).

Rationale:

Adding ``llm_turn`` cost ~50 LOC + careful audit of two dispatchers
to make sure it landed in both places. With a bus, the same change
is ~10 LOC and zero audit. The next 5 metrics earn the abstraction
back; the bus has paid for itself by metric #3.

### Step 13 — tool guardrail middleware

Today every "the agent must not X" rule is a manual ``if
chroot_path.startswith(...)`` block at the top of each affected
tool. Three guardrails today:

- ``_reject_dports_write`` — called from ``put_file``
- ``_reject_dsynth_scaffolding`` — called from ``list_dir``
  and ``grep`` (two callsites)
- (implicit) get_file's line-window cap

Five forthcoming guardrails the smoke pattern hints at:

- Refuse repeated ``get_file`` on the same path within an attempt
  (prompts the agent to keep state).
- Refuse ``grep`` patterns expected to return >N matches (forces
  narrower patterns).
- Refuse ``put_file`` writes that don't match an ``expected_sha256``
  for files already read this session.
- Cap ``list_dir`` to N entries (already partly implemented inline).
- Forbid ``extract`` outside ``$DPORTS_COMPOSE_ROOT``.

Without middleware, each new guardrail edits 1–3 tool function
bodies. Five new guardrails × ~3 tools each = 15 edits, with
ordering pitfalls (which guard fires first if two apply?).

The cleaner shape:

```python
class Guardrail(Protocol):
    def check(self, tool_name: str, args: dict) -> dict | None:
        """Return a refusal envelope to block the call, or None to
        proceed. Composable; the dispatcher runs guards in order
        and returns the first refusal."""

# Registry assembles per-tool guardrail stacks declaratively:
TOOLS_WITH_GUARDS = {
    "put_file": [
        RefuseWritesUnderPrefix(["/work/DPorts/", "/work/artifacts/compose/"]),
        RequireExpectedSha256IfReadThisSession(),
    ],
    "list_dir": [RefusePathsUnderPrefix(["/work/dsynth/build/Template"])],
    "grep":     [RefusePathsUnderPrefix(["/work/dsynth/build/Template"])],
    "get_file": [LineWindowed(default_limit=200)],
}
```

The dispatcher (already in ``tools.dispatch``) runs the stack
before calling the handler. A refusal returns the envelope; the
handler is never invoked.

Components:

- **Guardrail Protocol.** Single ``check`` method. Stateless by
  default; stateful guardrails (``RequireExpectedSha256``) get a
  per-attempt scratch dict from the dispatcher.
- **Concrete guards.** ``RefuseWritesUnderPrefix``,
  ``RefusePathsUnderPrefix``, ``CapOutputSize``,
  ``RequireExpectedSha256``, ``DenyRepeatedRead``.
- **Composition.** Per-tool guardrail list, run in order. First
  refusal wins.
- **Telemetry hook.** Refusals emit a ``GuardrailFired`` event so
  operators can see "the agent tried to write under /work/DPorts/
  3 times this attempt" — useful for prompt tuning.

What it replaces:

- ``_reject_dports_write`` and ``_reject_dsynth_scaffolding`` retire
  as inline helpers; they become ``RefuseWritesUnderPrefix`` and
  ``RefusePathsUnderPrefix`` instances.
- The line-window logic in ``get_file`` becomes a ``LineWindowed``
  guard that wraps the response (technically a *response*
  middleware, not an input guard — both fit the same shape).

Tests:

- One refusal per guardrail (the existing behavior).
- Composition: two guards on the same tool, both fire when
  applicable, first-refusal-wins ordering.
- Telemetry: refusals emit a guardrail_fired event.
- Stateful guard: tracks state across calls within an attempt,
  resets between attempts.

Rationale:

The five forthcoming guardrails above would be 15+ edits across
3–4 tool function bodies without middleware. With middleware,
each is one new class + one registry entry. Same observability
(the GuardrailFired event lets us see when guards fire), better
testability (each guard is unit-testable in isolation).

### Step 14 — context budget + KEDB metadata

``context.py`` already has the cleanest abstraction of the three
(``ContextSection`` Protocol with priority-ordered render). What
fails today:

- **Triage payload grew from ~3K to ~30K tokens** as KEDB +
  prompt sections accumulated. No budget; each section just adds
  whatever it adds.
- **KEDB is read as "concatenate all *.md".** No metadata to say
  "this entry applies only to patch-error" or "this entry is 1.2K
  tokens, drop it first if we're over budget."
- **No telemetry on which sections fired or what they cost.** Adds
  to the prior bullet — operators can't even see what's bloating
  triage.
- **System prompts (PATCH_SYSTEM, TRIAGE_SYSTEM) bypass the
  section mechanism entirely.** They're string constants. We can't
  observe which fragments fired, can't compose them, can't trim
  on a budget basis.

The cleaner shape:

```python
@dataclass
class KEDBEntry:
    path: Path
    body: str
    applies_to_classifications: tuple[str, ...] = ()   # () = any
    applies_to_platforms: tuple[str, ...] = ()
    est_tokens: int                                     # computed at load
    priority: int = 100                                 # smaller = drop later
```

- KEDB loader reads frontmatter (YAML-style) from each ``*.md``
  for these fields. Old entries without frontmatter default to
  "applies to any, priority 100."
- ``KEDBSection.render`` gates entries by classification AND by
  per-section token budget, picking entries by priority until the
  budget is exhausted.
- ``SectionRenderEvent`` telemetry per section: name, included or
  skipped, estimated tokens, reason if skipped. Operators see
  "KEDB included 4 of 7 entries, skipped 3 (budget); patch-error
  filter excluded 0."

For system prompts:

- Decompose ``PATCH_SYSTEM`` into named sections (the "Directory
  layout" section, the "Mandatory opening procedure" section,
  etc.) with the same ContextSection mechanism.
- Tag each with role=system. The assembler joins them at the
  top of the messages list instead of using the monolithic
  string.
- Same telemetry: which system sections fired, what they cost.

Components:

- **Section frontmatter parser** for KEDB entries (10 LOC).
- **Token estimator** (per-section ``est_tokens`` — rough
  ``len(text) // 4`` is fine for budget enforcement).
- **Budget gate** in ``ContextAssembler`` (drop lowest-priority
  sections until under budget).
- **System prompt decomposition** — ``PATCH_SYSTEM_SECTIONS`` as
  a list of ``ContextSection`` objects.
- **SectionRenderEvent telemetry** (depends on step 12's bus).

Tests:

- KEDB frontmatter parse: with frontmatter, without it, malformed
  → safe default.
- Classification filter: ``patch-error`` triage includes only
  entries with that classification (or with ``()`` = any).
- Budget enforcement: 7 entries totaling 10K tokens, budget 5K →
  drops lowest-priority entries to fit, telemetry records what
  was dropped.
- System prompt sections assemble in the right order and produce
  byte-identical output to the current PATCH_SYSTEM string when
  budget is unlimited and all sections fire.

Rationale:

KEDB will keep growing. Without per-entry metadata + budget, every
new entry tax-es every triage. With the abstraction, KEDB scales
to dozens of entries while triage cost stays bounded.

The system-prompt decomposition is a smaller-payoff but
higher-quality change: it lets us observe and trim the prompt the
same way we observe and trim KEDB. The recent libuv smoke run
revealed PATCH_SYSTEM had grown sections (some of them mine!) that
weren't pulling their weight; without telemetry, we can't tell.

#### Order

Step 12 (telemetry) first — steps 13 and 14 both want to emit
their own events (GuardrailFired, SectionRenderEvent), and those
become free once the bus exists. Doing 13/14 first means writing
ad-hoc event plumbing twice.

Step 13 (guardrails) second — small, contained, paying down a
specific in-progress pattern (the smoke run keeps adding
inline guardrails).

Step 14 (context budget) third — most ambitious, includes a
non-trivial system-prompt decomposition that's a behavior change
the operator should be able to opt out of (env var to use the
monolithic string instead, as a safety hatch).

#### What stays ad-hoc

Some patterns look ad-hoc but are working. Not refactoring:

- **Tool result shapes** (some return ``{ok: False, kind: ...}``,
  others raise). Working; don't normalize without a concrete
  reason.
- **Per-role tool sets** (triage vs. patch with the same tools).
  No current need; would be premature.
- **Event schemas as Pydantic classes vs. dataclasses.** Step 12
  picks one and sticks; migration to the other later is cheap.
- **KEDB stored as files vs. SQLite rows.** Files are fine for
  the volume; convert only if the volume grows past a few hundred
  entries.

### Step 15 — payload cost optimization pass

Once Step 14's machinery exists (section budget, KEDB metadata,
system-prompt decomposition, render-event telemetry), use it to
actually trim the prompt and tool-result payloads.

Smoke surfaced the numbers that motivate this step. First
successful libuv fix cost 505K tokens; analysis showed ~95% was
prompt tokens, dominated by:

- the system prompt re-sent every turn (~9K × 19 turns ≈ 170K),
- redundant ``get_file`` re-reads of the same Makefile.in across
  several turns (~60K),
- one verbose 11K-completion turn that then rode in the prompt for
  every subsequent turn (~55K),
- KEDB content that didn't apply to the actual classification.

Target after this step: ~150-200K per successful fix. That moves
the agent from "more expensive than operator time" to "operator
time is more expensive."

Sub-steps (rough order of leverage):

#### 15a — System prompt audit & trim

The system prompt grew organically across smoke fixes. Audit it
section by section against Step 14's render telemetry: which
sections actually fire in real attempts? Which sections produce
behavior changes the model wouldn't otherwise exhibit?

Concrete targets:

- Condense the four-tree Directory layout to a compact table.
- Move bug-reactive paragraphs (e.g. the "Version mismatch is
  common" guidance) out of the system prompt and into a KEDB
  entry tagged ``applies_to_classifications=['patch-error']``;
  they don't need to ride every triage's system prompt.
- Drop "Overlay state (read before editing)" if the mandatory
  procedure (Step 6 in the prompt) makes ``emit_diff`` mandatory
  anyway.
- Per-section telemetry from Step 14 tells us which sections
  actually fired vs. were rendered-but-ignored — drop the latter.

Aim for ~30-40% reduction in prompt bytes without behavior change.
Measure: ``prompt_tokens`` on the first turn before/after.

#### 15b — "Don't re-read" prompt directive

Smoke pattern: agent reads ``Makefile.in`` six times across
T5/T8/T9 because each "I need more context" instinct fires a fresh
``get_file``. The earlier windows are still in conversation
history; the agent doesn't realize it has them.

Add to the procedure:

```
You already have the content of any file you have read this
session in your conversation history. Before requesting a new
``get_file`` on a path you have already read, scan back: do you
already have the lines you need? Re-requesting compounds prompt
cost.
```

Pair with a structured "files read this session" summary the
runner could prepend per turn (would require small worker change).
Skip the structured part for now; the prose nudge is the cheap
first attempt.

#### 15c — KEDB classification gating

Depends on Step 14's frontmatter. Once entries have
``applies_to_classifications``, the KEDB section only includes
entries that match the current triage classification (or have
``[]`` = applies-to-any).

Concrete: a ``patch-error`` triage doesn't need ``plist-mismatch``
or ``freebsd-only-features`` entries; trimming those out cuts
~1-2K per triage payload AND keeps the patch prompt focused.

#### 15d — History elision (layer 2)

Defer-able but high-leverage on multi-turn attempts. After N=3
turns, walk back through ``role: tool`` messages and replace
content > X bytes (say 4KB) with a stub:

```json
{
  "role": "tool",
  "tool_call_id": "call_abc",
  "name": "get_file",
  "content": "[elided: 290KB Makefile.in read at turn 6. sha256=...,
              first_line=200. Use grep or get_file(offset_lines=...)
              for specific content.]"
}
```

The model sees "I read this at turn N, here's the gist" without
paying postage on every subsequent turn. Keep the most recent N
intact (model needs immediate context). Some models cope with
rewritten history; some get confused — test against deepseek
specifically before shipping.

Expected savings: 30-50% on long attempts (10+ turns). On the
505K libuv run, the T14 11K-completion would have elided after
T17; saves ~20K. Cumulative-prompt savings compound when multiple
large reads accumulate.

#### 15e — Model-tier experiment (data, not code)

Once 15a-d are in, re-run libuv with:

1. v4-flash for both triage and patch
2. v4-pro for triage, v4-flash for patch
3. v4-pro for both (current)
4. Anthropic Claude Sonnet for patch (different family, different
   instruction-following profile)

Each on a fresh bundle. Measure: cost per successful fix, fix
success rate per attempt, total tokens. Pick the tier that
minimizes ``$/successful-fix``.

Not a code change — an operational experiment. But worth doing
before any further architectural investment because the answer
could shift the cost-effectiveness calculation entirely.

#### Order

15a → 15b → 15c → 15d → 15e. Each later substep depends on the
machinery (Step 14) plus the savings already achieved. 15a alone
might cut the bill 30%; that's a clean wedge before deciding
whether 15d (history elision) is worth the cope-risk on weaker
models.

#### Why not earlier

Doing 15a-c before Step 14 means hand-coding all the trims
without telemetry to verify each cut is a wash on behavior. The
"which sections actually fire" question requires Step 14's
SectionRenderEvent to answer cleanly. Doing it blind is how the
prompt grew bloated in the first place.
