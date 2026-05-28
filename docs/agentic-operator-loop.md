# Agentic operator loop — manual escalation, verify/accept, delivery

> **Agentic plan set:** [Roadmap & priority order](agentic-consolidation-plan.md) · [Phase 4 — DB consolidation](agentic-phase4-db.md) · [Operator loop](agentic-operator-loop.md) · [Architecture backlog](agentic-architecture-backlog.md)

> Steps 1–11, 28, 29, 30. The operator-facing half of the loop:
> bundle reading, manual handoff, verify/accept/reject, GitHub
> delivery, context-aware re-triage, and per-bundle branch
> isolation. For status and what to work on next see the
> [roadmap](agentic-consolidation-plan.md).

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

### Step 9 — tracker UX polish for manual work — shipped

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

### Step 10 — kick out stale queued jobs — shipped

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

### Step 11 — fix delivery & verification — shipped (11a–c + 11d; 11d-4 deferred)

> **Status (2026-05-28): shipped.**
> - **11a** — verification claim vs. reality split. Shipped.
> - **11b** — four slices: dev-env apply-and-build primitive
>   (`6800f9c5216`), bundle verification endpoint + columns
>   (`1454a55ca11`), verify-fix orchestrator (`ef584cf6937`),
>   verification pill + proposed_fix badge (`ee3afa36a70`). Plus
>   orchestrator robustness fixes (`a77e2500a60`, `bfd0d68473b`,
>   `ed8e97b6007`, `1776bc894ab`, `b376a58f47b`).
> - **11c** — verify/accept/reject operator buttons + `verify` job
>   type (`775cb2a8e6b`); button-wiring fix (`328f00e3f80`); env
>   picker for Verify (`b2143aab447`). Verify is the gate — Accept
>   stays disabled until verified.
> - **11d** — delivery module (`dportsv3/delivery/`): schema +
>   `ReviewProvider` Protocol + `LocalPatchProvider` (`9ae293c6b7d`,
>   11d-1); Accept-with-delivery + UI card (`39a29072c7b`, 11d-2);
>   `GitHubProvider` over httpx + local-clone git driver
>   (`7b2f42f76cd`, 11d-3); operator manual status update
>   (`3b1aa957f9f`, 11d-5); plus config + push-auth fixes
>   (`9f62a8cdc8f`, `4502ac61e63`, `54c286d5ef7`, `5bd1a86e9d6`,
>   `aaa1baa4310`, `61cec76d2d5`).
> - **11d-4** (GitLab + Gitea providers) — **deferred**, not yet
>   built. `_http.py` + `_git.py` are provider-agnostic, so the
>   marginal cost is small when a non-GitHub target appears.

> *Original plan body below; preserved as the design record.*

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

**Layering note (added 2026-05-24).** The original draft placed the
verifier as `dportsv3 dev-env verify-fix BUNDLE_ID`. That couples
the dev-env subcommand to bundles, which are an artifact-store /
tracker concept the dev-env layer otherwise knows nothing about. Of
the six steps in verification — resolve origin+target, provision
env, fetch diff, apply diff, run dsynth, POST result — only steps 2
and 5 are pure dev-env concerns; the rest are tracker/git
orchestration. Folding all of that into `dev-env` makes the
subcommand the wrong shape and pollutes a layer whose only job is
chroot substrate.

Split the work:

1. **`dev-env` exposes a thin primitive** that takes substrate
   inputs (an env, an origin, optionally a diff path on disk) and
   returns dsynth's result. No bundles, no tracker calls, no
   artifact-store knowledge:

   ```
   dportsv3 dev-env apply-and-build ENV ORIGIN \
       [--diff PATH] [--clean] [--json]
   ```

   Runs `git apply` (or `patch`) on the env's DeltaPorts overlay if
   `--diff` is given, then `dsynth -S -y -p $PROFILE build ORIGIN`,
   then prints a JSON record of the outcome (`{ok, log_path,
   dsynth_exit, applied_diff_sha256?}`). `--clean` provisions a
   throwaway env; default reuses the named one. Substrate-level
   only.

2. **Top-level orchestrator** owns the bundle resolution and the
   tracker round-trip:

   ```
   dportsv3 verify-fix BUNDLE_ID [--keep]
   ```

   What it does:

   a. Resolves the bundle's origin + target via the tracker API.
   b. Fetches `analysis/changes.diff` from the artifact-store into
      a tmpfile.
   c. Provisions a fresh dev-env (clean writable overlay, no agent
      edits in flight). `--keep` preserves it for inspection;
      default is throwaway.
   d. Invokes `dportsv3 dev-env apply-and-build ENV ORIGIN --diff
      DIFFPATH --json` and parses the result.
   e. POSTs back to the tracker:
      `POST /api/bundles/{bundle_id}/verification` with
      `{ok: bool, dsynth_log: str, verified_at: iso,
        applied_diff_sha256: str}`.
   f. Each layer enforces its own discipline: the runner sets
      `DPORTSV3_HOOKS_FLAG_FILE` to prevent recursive hook
      triggers; the dev-env layer doesn't need to know why.

The bundle row grows a `verification_status` column with values
`verified` / `verification_failed` / NULL (not yet attempted).

Surface in the UI:

- Bundle detail and bundle list show the verification status as a
  pill alongside ``resolution``.
- ``proposed_fix.md`` updates to include the verification badge once
  it's set (lazy render at view time).

Tests:

- **`dev-env apply-and-build` primitive**: builds without `--diff`
  succeed/fail correctly against a known-good/known-bad port; with
  `--diff` applying a trivially-passing fix flips a failing port to
  green; with a non-applying diff returns `ok=false` with the
  `git apply` error in the log; JSON output is parseable.
- **`verify-fix` orchestrator**: a trivially-applying diff against a
  freshly-failing port produces `verified`; a diff that doesn't
  apply cleanly produces `verification_failed` with the patch error;
  a diff that applies but doesn't fix the build produces
  `verification_failed` with the dsynth log tail; unknown bundle_id
  exits non-zero with a clear message; `--keep` preserves the env.
- **Tracker endpoint**: 404 unknown bundle, 400 missing fields, 200
  happy path; applied_diff_sha256 is recorded so re-verification of
  the same diff is deduplicable.

Why this split matters for later steps: Step 17 (remote runners)
will want to delegate verification to a non-colocated builder. A
substrate-only `apply-and-build` primitive ships over SSH (or a
runner-API) trivially; a bundle-aware monolithic subcommand
doesn't.

#### 11c — verify / accept / reject in the tracker

**Revised 2026-05-25 after 11b Slices 1-4 shipped.** Original draft
had only two buttons (Accept / Reject) on ``agent_fixed`` and called
verify-first "stronger UX". That was too lenient — an operator
could accept an unverified claim and 11d would happily push it to
upstream. Verify is now the **gate**: Accept is structurally
impossible on an unverified bundle.

Three operator buttons on the bundle detail page, enabled per state:

| State | Verify | Accept | Reject |
|---|---|---|---|
| ``agent_fixed`` (no verify yet) | ✓ | ✗ (gated) | ✓ |
| ``verified`` | ✓ (re-run) | ✓ | ✓ |
| ``verification_failed`` | ✓ (re-run) | ✗ | ✓ |
| ``accepted`` / ``rejected`` | — | — | — |

State machine on ``bundles.resolution`` + ``bundles.verification_status``::

    NULL ──PATCH_OK──► agent_fixed
                           │
                           ├──[Verify]──► verified ──[Accept]──► accepted (terminal)
                           │                  │
                           │                  └──[Reject]──► rejected (re-triage)
                           │
                           ├──[Verify]──► verification_failed
                           │                  │
                           │                  ├──[Verify again]──► (retry)
                           │                  └──[Reject]──► rejected
                           │
                           └──[Reject]──► rejected (skip verify entirely)
                              (for obviously-wrong fixes)

##### Endpoints

- ``POST /api/bundles/{bundle_id}/verify`` → enqueues a ``verify``
  job (new job type). Body: ``{"env": "..."}`` (the dev-env to
  verify in). Returns the enqueued ``job_id`` immediately; result
  POSTs back to ``/verification`` (Slice 2) when the runner
  finishes and the SSE stream picks it up.
- ``POST /api/bundles/{bundle_id}/accept`` → synchronous. Rejects
  (409) if ``verification_status != 'verified'``. Sets
  ``resolution='accepted'`` + ``accepted_at`` + (later)
  ``accepted_by``. Emits ``bundle_accepted`` event.
- ``POST /api/bundles/{bundle_id}/reject`` → synchronous. Body:
  ``{"reason": "..."}``. Sets ``resolution='rejected'``, enqueues
  a fresh triage with the rejection reason injected as
  ``user_context``. Emits ``bundle_rejected`` event.

##### New job type: ``verify``

The runner gets a new dispatch arm. Verify jobs carry
``{bundle_id, env, target}``; the worker calls
``dportsv3.verify_fix.run_verify_fix(...)`` **in-process** — no
subprocess, no shell-out. ``run_verify_fix`` already exists as a
public function from 11b Slice 3; the CLI was its only consumer
so far.

Lifecycle events: reuse ``CONVERT_*`` shape — ``VERIFY_START`` /
``VERIFY_OK`` / ``VERIFY_GAVE_UP`` (or shoehorn into existing
events with a detail field; decision at implementation time).
The job's terminal state mirrors the verification outcome but is
*independent* of ``bundles.verification_status`` (which Slice 2's
endpoint owns).

##### SSE event wiring

The runner's existing ``bundle_verified`` event (emitted by the
Slice 2 endpoint) is what triggers the UI refresh. New event types
``bundle_accepted`` / ``bundle_rejected`` follow the same pattern.

##### Scope estimate

- New ``verify`` job type + lifecycle events: ~60 LOC.
- Runner dispatch arm calling ``run_verify_fix`` in-process: ~40 LOC.
- Three POST endpoints + body validation: ~100 LOC + tests.
- UI button matrix in ``agentic_bundle.html`` with state-aware
  enabling: ~50 LOC.
- SSE event additions: mostly reuse, ~10 LOC.

~300 LOC total + tests. Single coherent slice, larger than the
original 11c but covers the full verify→accept/reject flow.

##### Tests

- Verify endpoint: enqueue happy path; 404 unknown bundle; 409 on
  terminal states.
- Accept endpoint: happy path from ``verified``; 409 from
  ``agent_fixed`` (not verified); 409 from terminal; 404.
- Reject endpoint: happy path enqueues triage with user_context;
  404; works from any non-terminal state.
- Lifecycle: VERIFY_START / VERIFY_OK / VERIFY_GAVE_UP transitions
  + matrix of illegal-transition rejections.
- UI: button matrix renders correct buttons per state; disabled
  buttons render disabled (not absent).
- Runner: verify job picks up, calls run_verify_fix, lifecycle
  transitions correctly.

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

Concrete implementations all use **direct REST via ``httpx``** (the
HTTP client already in the venv via FastAPI). The original draft
defaulted GitHub to the ``gh`` CLI on the assumption it would be
installed on operator boxes — but the tracker runs as a daemon on a
known host with a configured token, not in an operator's interactive
shell. Direct REST keeps the provider abstraction symmetric across
GitHub / GitLab / Gitea (all REST-only anyway) and avoids the
parse-CLI-output brittleness of ``gh``.

- ``GitHubProvider`` — ``httpx`` against
  ``https://api.github.com/repos/{owner}/{repo}/pulls``. Three
  operations: create PR, find-open-PR-by-head (idempotency check),
  patch PR body. ~80 LOC including error/rate-limit handling.
- ``GitLabProvider`` — ``httpx`` against
  ``https://gitlab.com/api/v4/projects/{id}/merge_requests``. Same
  three operations.
- ``GiteaProvider`` — ``httpx`` against the configured Gitea host's
  ``/api/v1/repos/{owner}/{repo}/pulls``. Same three operations.
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

##### Schema — ``bundle_review_requests``

New table linked to ``bundles``. Mirrors the ``verify_requests``
shape Step 11c established. Append-only — every delivery attempt
gets a row; the latest row's status drives the bundle UI.

```sql
CREATE TABLE bundle_review_requests (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id       TEXT NOT NULL,
    provider        TEXT NOT NULL,         -- "github" / "gitlab" / "gitea" / "local-patch"
    provider_pr_id  TEXT,                  -- PR number / MR iid / outbox filename
    url             TEXT,                  -- web URL (NULL for local-patch)
    branch          TEXT,                  -- branch name on the remote
    title           TEXT,
    status          TEXT NOT NULL DEFAULT 'created',
                                            -- 'created' / 'create_failed' /
                                            -- 'updated' / 'closed' / 'merged'
    created_at      TEXT NOT NULL,
    last_synced_at  TEXT,                  -- for future status-polling
    error           TEXT,                  -- on create_failed: exception summary
    operator        TEXT,                  -- who clicked Accept
    error_signature TEXT                    -- idempotency key (Step 6)
);
CREATE INDEX idx_brr_bundle ON bundle_review_requests(bundle_id);
-- At most one OPEN PR per (provider, error_signature).
CREATE UNIQUE INDEX uq_brr_open_signature
    ON bundle_review_requests(provider, error_signature)
    WHERE status NOT IN ('closed', 'merged', 'create_failed');
```

Rows are written:

- ``status='created'`` on first successful PR open (with ``url`` +
  ``provider_pr_id``).
- ``status='create_failed'`` if push or PR-create errored. ``error``
  carries the exception summary so the bundle UI can show it.
- ``status='updated'`` if the idempotency check found an existing
  PR and we patched its body instead of creating a duplicate.
- ``status='closed'`` / ``'merged'`` only via operator action (UI
  button) for v1; PR-status polling is out of scope.

##### Idempotency + retry

If the operator clicks Accept twice, or the push fails partway:

- The unique index ``uq_brr_open_signature`` blocks a duplicate
  open delivery at the DB level — the provider's create call
  refuses with a 409 returning the existing row's URL.
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

##### Slicing — 11d-1 through 11d-5

11d ships in five slices. Each is independently usable: the loop
keeps closing the operator-fix arc one provider at a time, with
LocalPatchProvider serving as the always-available fallback so no
slice is gated on the next.

###### 11d-1 — schema + Protocol + LocalPatchProvider + config loading

The foundation, no network:

- Schema migration: ``bundle_review_requests`` table (per
  "Schema" subsection above) + index + partial-unique on
  ``(provider, error_signature)``.
- ``scripts/generator/dportsv3/delivery/__init__.py`` exposing
  ``ReviewProvider`` Protocol (per "Abstraction" subsection above)
  and ``ReviewRequestResult`` dataclass.
- ``LocalPatchProvider`` — writes ``analysis/changes.diff`` to
  ``$DPORTSV3_DELIVERY_OUTBOX/<branch_name>.patch`` plus a
  ``.metadata.json`` carrying the commit-message templated fields.
  Idempotency: if ``<branch_name>.patch`` already exists at the
  same SHA, returns the existing record instead of writing twice.
- ``config/delivery.toml`` loader (TOML stdlib in Python 3.11+);
  reads provider type / repo / base_branch / labels /
  branch_template / draft. Per-target overrides via TOML sections
  (e.g. ``[target."@2026Q2"]``). Env-var precedence:
  ``DPORTSV3_DELIVERY_PROVIDER`` overrides TOML; the token comes
  from ``DPORTSV3_DELIVERY_TOKEN`` env var or a 0400 file under
  ``$DPORTSV3_CONFIG_DIR/delivery.token``.
- Tests: schema migration applies cleanly; partial-unique index
  enforces one-open-row-per-signature; LocalPatchProvider happy
  path + collision (same patch twice → same outbox file) +
  outbox-doesn't-exist error + outbox-not-writable error;
  delivery.toml parsing happy paths + missing-required-field
  rejection + env-var override; ``ReviewProvider`` Protocol
  type-check via a fake provider in the test suite.

~250 LOC + ~150 LOC tests. Independently shippable — the slice
ends with LocalPatchProvider working end-to-end against the
DB schema, no UI yet.

###### 11d-2 — Accept-endpoint integration + Delivery UI card

Wire LocalPatchProvider into the Accept flow:

- ``POST /api/bundles/{id}/accept`` (Step 11c endpoint, recently
  extended for 28e operator_owned-verified) gains a delivery
  step. Body adds optional ``deliver: bool`` (default true) so
  operators can accept-without-delivering via curl. UI always
  delivers.
- On Accept-with-delivery: resolve provider from delivery.toml,
  call ``provider.create_review_request(...)``, write a
  ``bundle_review_requests`` row, emit ``bundle_delivered``
  event. On failure, write a ``create_failed`` row with the
  exception summary — the accept itself still succeeds (the
  bundle is accepted, just not delivered).
- Bundle detail page gains a "Delivery" card showing the most-
  recent ``bundle_review_requests`` row for the bundle. Status
  pill (created / failed / updated / closed / merged), URL link,
  branch name, sent_by, sent_at. For ``create_failed`` rows, the
  error column renders inline so the operator sees what went
  wrong without grepping logs.
- Tests: accept-with-delivery happy path (stub LocalPatchProvider,
  assert row written + event emitted); accept-without-delivery
  (``deliver=false``); provider-raises (asserts ``create_failed``
  row + bundle still accepted); UI card renders all five status
  shapes; UI card absent on bundles with no delivery row.

~120 LOC + ~80 LOC tests. After this slice ships, the full
operator arc (failure → fix → verify → accept → delivered to
outbox) is end-to-end usable on the default LocalPatch path.

###### 11d-3 — HTTP base + GitHubProvider

Add the first network provider:

- ``delivery/_http.py`` — thin ``httpx``-based REST client
  wrapper. Common bits: token auth header injection, rate-limit
  detection (HTTP 429 + ``X-RateLimit-Remaining: 0``), retry
  with exponential backoff (max 3 attempts), structured
  exceptions (``DeliveryAuthError``, ``DeliveryRateLimitError``,
  ``DeliveryConflictError``, ``DeliveryError``).
- ``delivery/github.py`` — ``GitHubProvider`` using the base.
  Three REST calls: list-open-PRs filtered by head branch
  (idempotency), create-PR, patch-PR-body. Returns
  ``ReviewRequestResult`` with PR number + URL.
- Local-clone driver: ``delivery/_git.py`` — ``git fetch`` /
  ``git checkout -b`` / ``git apply --3way`` / ``git commit -s``
  with the templated commit message (per the "Mechanism"
  subsection). Operator clone path from ``$DPORTSV3_OPERATOR_CLONE``
  env var. Refuses if the clone is dirty or off-base-branch.
- Tests: monkeypatched ``httpx`` — verifies request shapes
  (URL, headers, body JSON) for each call; happy paths for
  create + find + update; 401 → ``DeliveryAuthError``;
  429 → ``DeliveryRateLimitError`` (no automatic retry past the
  cap); 422 (PR-exists) → looks up existing and updates body.
  ``_git`` tests use a real temporary git repo + diff fixture.

~250 LOC + ~200 LOC tests. After 11d-3 ships, ``provider.type
= "github"`` in ``delivery.toml`` drives the full flow.

###### 11d-4 — GitLab + Gitea providers

Add the remaining two REST providers:

- ``delivery/gitlab.py`` — same three operations against the
  GitLab API. Project ID configurable in TOML.
- ``delivery/gitea.py`` — same three operations against the
  Gitea API. Host configurable in TOML.
- Both reuse ``_http.py`` and ``_git.py`` from 11d-3 — only
  the REST endpoint URLs and the JSON shapes differ.

~180 LOC + ~150 LOC tests. The two providers ship together
because they're substantially identical to GitHub's
implementation modulo URL shapes.

###### 11d-5 — Operator manual status update

Small UI affordance for the "I just merged this PR" case until
PR-status polling lands (deferred):

- Bundle detail page's Delivery card gains a "Mark as merged"
  / "Mark as closed" button (text input optional for a note).
  Updates the row's status + writes ``last_synced_at`` so the
  bundle UI reflects the real-world state.
- New endpoint: ``POST /api/bundles/{id}/delivery/status`` body
  ``{"status": "merged" | "closed", "note"?: "..."}``.
- Tests: status update writes row; idempotent re-update with
  same status; rejects setting status back to ``created`` (one-
  way state machine).

~60 LOC + ~40 LOC tests. Last slice for v1 11d. Future
PR-status polling (out of scope for 11d entirely) would slot
in as a runner-side ``process_delivery_status`` poll loop —
parallel to ``process_user_context_updates`` / ``process_verify_requests``.

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

### Step 28 — failed-bundle operator action matrix — shipped (28a–e)

> **Status (2026-05-28): shipped.** All five slices landed:
> 28a take-over + origin skip flag (`883e40014c9`); 28b discard
> endpoint + button (`df9a7c99d7c`); 28a/28b review-pass
> (`c0bf126dae5`); 28c retry-with-context on failure bundles
> (`fd8149a8633`); 28d terminal-state reopen override
> (`10cdc1abe73`); 28e operator release of `operator_owned` +
> Verify on manual fixes (`d33d81463d8`). Convert-dispatcher
> noise + Accept-on-`operator_owned`-verified fix (`86cbaa23ec5`).
> Skip-check enforcement on patch/convert dispatch (`19ad6291076`).

> *Original plan body below; preserved as the design record.*

Closes a long-standing asymmetry in the operator surface:

| Terminal event           | State        | `manual_handoff.md` | `user_context_requests` row | Reachable from `/agentic/manual` | Operator can act? |
|--------------------------|--------------|---------------------|-----------------------------|----------------------------------|-------------------|
| `ESCALATE_MANUAL` (triage) | `escalated` | written             | written                     | yes                              | yes (context / discard) |
| `PATCH_BUDGET_OUT`       | `dead`       | written             | **not written**             | **no**                           | **no**            |
| `PATCH_GAVE_UP`          | `dead`       | written             | **not written**             | **no**                           | **no**            |
| `CONVERT_GAVE_UP`        | `dead`       | (varies)            | **not written**             | **no**                           | **no**            |

Patch-side terminal failures and convert-side give-ups write a
forensics handoff but no first-class operator action surface
exists for them. Recovery only happens if the dsynth hook
re-fires for the same port AND the new triage decides
differently. The operator cannot stake a bundle ("I'm taking
over"), cannot mark a port unsalvageable ("don't try this
again"), and cannot retrigger triage with their context except
by going through the manual-queue path that DEAD bundles never
reach.

Step 28 mirrors Step 11c on the failure side. 11c gave success
bundles a three-button matrix (Verify / Accept / Reject); 28
gives failure bundles a three-button matrix (Take over / Discard
/ Retry with context). Same UI infra, same lifecycle event
plumbing, same SSE pattern.

#### State machine on `bundles.resolution`

Extends 11c's state machine (which only covered the
`agent_fixed` lane) with a parallel failure lane::

    agent_budget_exhausted | agent_gave_up | convert_gave_up
        │
        ├──[Take over]──► operator_owned ──► (then Verify/Accept
        │                                      via 11c's path)
        │
        ├──[Discard]──► discarded (terminal; per-origin skip flag
        │                          on by default until reset)
        │
        └──[Retry with context]──► (re-triage; same machinery as
                                    Step 5's manual-queue retry,
                                    just reachable from the
                                    bundle detail page itself)

`agent_fixed` lane (Step 11c) is unchanged.

#### Endpoints

Three new POSTs, mirroring 11c's shape (synchronous where the
operation is pure metadata; asynchronous where it enqueues a
job):

- `POST /api/bundles/{bundle_id}/take-over` — synchronous. Body:
  `{"operator": "..."}` (optional; defaults to anonymous).
  Refuses (409) if `resolution` is already terminal
  (`accepted` / `discarded`) or if `operator_owned`. Sets
  `resolution='operator_owned'`, `taken_over_at`, `taken_over_by`.
  Sets a per-`(target, origin)` lock flag so subsequent triage
  for that port short-circuits (see "New job-side handling" below
  for the runner-side mechanism — the original draft proposed a
  hook-emitted "tombstone bundle" but the dsynth hook is a sealed
  shell contract that can't query state.db). Emits
  `bundle_taken_over` event.

- `POST /api/bundles/{bundle_id}/discard` — synchronous. Body:
  `{"reason": "...", "skip_origin": bool}`. `reason` is required;
  `skip_origin` defaults to true. Refuses (409) if terminal. Sets
  `resolution='discarded'`, `discarded_at`, `discard_reason`. If
  `skip_origin=true`, sets the per-`(target, origin)` lock flag
  (same flag take-over uses) — operator says "don't bother
  trying this port again." Emits `bundle_discarded` event.

- `POST /api/bundles/{bundle_id}/retry` — asynchronous. Body:
  `{"context": "..."}` (required, non-empty). Refuses (409) if
  terminal. Equivalent in effect to Step 5's
  `/api/manual-requests/{run_id}/{origin}/context` but reachable
  from the bundle page without round-tripping through the
  manual-queue UI. Writes a `user_context` row, marks the
  bundle's resolution `retry_requested`, and lets the existing
  Step 5 retry-loop pick it up on the next sweep. Emits
  `bundle_retry_requested` event.

The per-`(target, origin)` lock flag is a new
`origin_skip_flags` table (`target`, `origin`, `set_by`,
`set_at`, `reason`, `cleared_at NULL`). One row = "don't
auto-process bundles for this `(target, origin)` until
cleared." A small `POST /api/origins/{target}/{origin}/unskip`
endpoint clears it; UI surfaces a one-click un-skip on the
origin detail page (Step 16's territory if not present).

#### Bundle row changes

New columns on `bundles`:

- `taken_over_at` TIMESTAMP NULL
- `taken_over_by` TEXT NULL
- `discarded_at` TIMESTAMP NULL
- `discard_reason` TEXT NULL

New resolutions accepted in `resolution`:

- `operator_owned` (non-terminal — Verify/Accept can still fire)
- `discarded` (terminal)
- `retry_requested` (transient — clears when the retry triage
  enqueues)

The `accepted` / `rejected` resolutions from 11c remain
unchanged.

#### New job-side handling

**Where the skip-flag check lives.** The dsynth hook is a sealed
shell contract that has no access to `state.db` — modifying it to
query the lock would couple the hook tree to the tracker DB schema
(big architectural cost). The check lives one level later instead,
at the top of `process_triage_job` (and parallel checks in
`process_patch_job` / `process_convert_job` per 28-extra). When
the (target, origin) is locked, the runner fires
`JobEvent.SKIP_ORIGIN_LOCKED` (Step 28a) with
`retire_reason='origin_locked'` and emits a
`triage_skipped_origin_locked` activity row (parallel:
`patch_skipped_origin_locked` / `convert_skipped_origin_locked`).
The bundle row itself is unchanged — the hook's failure record
stays as-is; only the per-job dispatch short-circuits.

Functionally equivalent to the "tombstone bundle" wording in the
original draft (no triage burns LLM tokens, the origin is honored
as locked) but the artifact-store row is a full bundle, not a
lightweight `result=skipped` stub. Worth recording explicitly so
the next reader doesn't grep for tombstone code that isn't there.

#### SSE event wiring

`bundle_taken_over` / `bundle_discarded` / `bundle_retry_requested`
follow the existing `bundle_verified` / `bundle_accepted`
pattern from 11c. The bundle detail page's existing live-refresh
infra picks them up without further work.

#### UI surface

Bundle detail page (`agentic_bundle.html`) gains a state-aware
"Operator actions" panel:

- For `agent_fixed` / `verified` / `verification_failed`: the
  11c matrix (Verify / Accept / Reject).
- For `agent_budget_exhausted` / `agent_gave_up` /
  `convert_gave_up`: the new 28 matrix (Take over / Discard /
  Retry with context).
- For `operator_owned`: a "Verify your fix" button (drops into
  11c's Verify path) plus a "Hand back to the loop" un-skip
  control.
- For terminal states (`accepted`, `discarded`, `rejected`):
  a read-only badge plus a "Reopen for retry" override that
  clears the terminal flag (gated behind a confirmation modal
  since it's an undo).

Buttons render disabled (not absent) when not applicable so the
operator sees the full action surface and the reason a specific
action is blocked (tooltip on the disabled state). Matches 11c.

#### Sub-steps (slicing for ship-ability)

The full Step 28 is one coherent slice but can be sliced for
incremental delivery:

- **28a — origin skip flag + take-over endpoint.** The
  highest-value piece: gives operators the "stop competing with
  me" guarantee. ~120 LOC + tests.
- **28b — discard endpoint + per-origin skip on by default.**
  Closes the "this port is hopeless" gap. ~80 LOC + tests.
- **28c — retry endpoint on bundle page (UI surfacing of Step
  5).** Smallest slice; mostly a UI wiring. ~50 LOC + tests.
- **28d — terminal-state reopen override.** Cheap once 28a-c land.
  ~40 LOC + tests.
- **28e — release endpoint + operator_owned UI completion.**
  Bundle-scoped "Hand back to the loop" action, plus the
  Verify-on-operator_owned extension the plan called for. ~150
  LOC + tests.
- **28-extra — patch/convert skip-check.** Parallel
  `_maybe_skip_locked_origin` calls at the top of
  `process_patch_job` / `process_convert_job` so in-flight jobs
  enqueued before the take-over also short-circuit. ~30 LOC each.

Each slice is independently shippable. 28a–c are the load-bearing
ones; 28d-e are polish/completion; 28-extra closes the race window.

#### Lifecycle events (lifecycle.py / SSE)

Two distinct event surfaces:

**SSE event topics** (emitted via `emit_event`) for the UI's
live-refresh + the runner's bundle-resolution lane:

- `bundle_taken_over` — bundle → `operator_owned`.
- `bundle_discarded` — bundle → `discarded`.
- `bundle_retry_requested` — bundle → `retry_requested`; clears
  on next triage enqueue.
- `bundle_reopened` — bundle terminal → NULL (28d).
- `bundle_released` — bundle `operator_owned` → NULL (28e).

These are bundle-resolution events, not `JobState` transitions —
they extend the resolution lane that 11c introduced (`accepted` /
`rejected`) rather than the `JobState` enum. Each endpoint emits
exactly one event with a structured payload (bundle_id, origin,
target, plus per-action fields like `taken_over_by` /
`reopened_from` / `skip_action`).

**JobEvent additions** (`lifecycle.py`):

- `SKIP_ORIGIN_LOCKED` — fires on triage / patch / convert jobs
  when the (target, origin) is locked. Lands at `DEAD` with
  `retire_reason='origin_locked'`. Distinct from `ABANDON` (Step
  10b's operator job-kill) so lineage queries can tell the cases
  apart. Permitted from QUEUED / CLAIMED / TRIAGING / TRIAGED /
  PATCHING / CONVERTING.

#### Tests

- Take-over endpoint: happy path from each failure resolution;
  409 on already-terminal; 409 on already-operator_owned; lock
  flag observable; subsequent triage produces
  `triage_skipped_origin_locked` (or patch/convert variants for
  in-flight jobs) instead of running.
- Discard endpoint: happy path; reason required (422); 409 on
  terminal; `skip_origin=true` writes the lock flag,
  `skip_origin=false` does not.
- Retry endpoint: happy path enqueues triage with context;
  409 on terminal; empty context rejected; the
  `bundle_retry_requested` resolution clears when triage picks
  up.
- Skip-flag check in runner path: a triage job for a locked
  `(target, origin)` short-circuits at `process_triage_job`
  entry (firing `SKIP_ORIGIN_LOCKED` with
  `retire_reason='origin_locked'`) and emits a
  `triage_skipped_origin_locked` activity row. The bundle row
  itself is unchanged. Parallel checks at the patch/convert
  dispatch tops handle in-flight jobs queued before the take-over.
- Reopen override (28d): all three terminal resolutions reopen
  cleanly; the resolution returns to the prior non-terminal
  state.
- UI button matrix: each resolution renders the correct subset
  of buttons in the correct enabled/disabled state.
- Lifecycle: BUNDLE_TAKEN_OVER / BUNDLE_DISCARDED /
  BUNDLE_RETRY_REQUESTED transitions accepted from valid
  prior resolutions, rejected from invalid ones (matrix).

#### Scope estimate

~300–400 LOC across the runner, tracker server, and
templates, plus ~250 LOC of tests. Comparable to Step 11c.
The lifecycle / SSE / button-matrix infra is reused, not
re-built.

#### Rationale

Three concrete problems Step 28 fixes:

1. **Budget-exhausted bundles are operationally invisible.**
   The handoff file is written but never surfaces in any
   queue an operator routinely watches. The bundle sits dead
   until the same port fails again.
2. **No way to tell the loop "I'm working on it."** An
   operator inspecting a failed bundle and fixing it locally
   races against the next dsynth hook firing fresh triage.
   No primitive prevents that today.
3. **No way to mark a port unsalvageable.** Hopeless ports
   (deprecated upstream, vendored binary, legal issues)
   generate failure bundles indefinitely. The skip flag is
   the off-switch.

Could be deferred behind 11c if priority order requires
it, but should not be deferred indefinitely — every operator
hour spent triaging failures manually is an hour the surface
gap costs.

#### Out of scope

- Operator authentication / identity. The `taken_over_by`
  column is a freeform string for now; integrating with the
  auth model is Step 17 territory.
- A general "policy override per origin" engine. The skip
  flag is a single boolean; multi-axis policy
  (tier-per-origin, model-per-origin) is a separate concern.
- Discarded-bundle archival. Discarded bundles stay in the
  artifact store; an eventual GC pass is its own project.

---

### Step 29 — context-aware re-triage + operator-context history — shipped (29a–e + A1)

> **Status (2026-05-28): shipped.** 29a triage-consults-context
> prompt (`629f658ccf4`); 29b append-only `user_context_history`
> table (`b2aa33850ba`); 29c operator-context history in
> `manual_handoff.md` (`97b6d0812f3`); 29d prior patch artifacts in
> triage payload (`fcc4657e240`); 29e full history rendering in
> triage + patch payloads (`c3c3ee0f91f`); 29-A1 MANUAL → ASSIST
> promotion under fresh operator context (`73138ce7ee8`). Manual
> queue visibility fixes alongside (`b26744d922f`, `83e4e8c65f6`).

> *Original plan body below; preserved as the design record.*

Closes a UX dead-end surfaced by smoke-testing the
`databases/redis` re-escalation loop:

- Operator submits context via `/api/manual-requests/.../context`
  or Step 28c's `/api/bundles/{bundle_id}/retry`.
- `process_user_context_updates` enqueues a fresh triage job.
- Triage LLM receives the operator text via the `## User Context`
  section but treats it as supporting prose, not as evidence
  worth changing its mind over. Lands the same classification
  as the prior run.
- `policy.tier_for` is a pure classification → tier lookup. If
  the prior tier was MANUAL (e.g. `missing-dep`,
  `dependency-conflict`, `runtime-error`), the re-triage routes
  back to MANUAL. The operator's effort accomplishes nothing.

Compounding: `manual_handoff.md` is regenerated from triage
output only and contains zero reference to the operator's
input, so on re-escalation the handoff document is
indistinguishable from the first one. Operator cannot tell
whether their context was received, what it said, or how the
agent reasoned about it. Multi-round operator context is
silently lossy at the storage layer too — `upsert_user_context_text`
**overwrites** the prior `context_text` for the
`(run_id, origin)` row on every submission.

Step 29 reframes the operator-context loop end-to-end:

1. **Triage prompt prioritizes operator context.** When a
   `## User Context` section is present in the payload, the
   triage prompt instructs the model to **consult operator
   context before classifying** — not as a reclassification
   afterthought but as a first-class input weighted ahead of
   the bundle's mechanical signals. The model is told the
   operator has direct knowledge the bundle artifacts can't
   convey (e.g. "this is a configure failure, not a missing
   dep; the dep is there as `gmd5sum` from `coreutils`").
   Classification follows from synthesizing the operator's
   evidence with the bundle, not from defaulting to the bundle
   alone with operator text as decoration.

2. **Append-only operator-context history.** New
   `user_context_history` table. `upsert_user_context_text`
   continues to write the current row to `user_context` (no
   read-site changes elsewhere) AND appends an immutable row
   to `user_context_history` capturing
   `(run_id, origin, context_rev, submitted_at, text,
   submitted_by)`. Each round preserved verbatim; no schema
   change to the read-heavy `user_context` table.

3. **`manual_handoff.md` surfaces operator context history.**
   `manual_handoff.build_handoff_ctx` reads
   `user_context_history` for the bundle's `(run_id, origin)`
   and renders an "## Operator context" section showing each
   submission as a round-numbered block with timestamp.
   Renders nothing when no rows exist. Tracker UI gets this
   automatically since the bundle detail page already renders
   `manual_handoff.md`.

#### Why Step 29 is distinct from Step 28

Step 28 gives operators the *buttons* to act on failure
bundles (take-over / discard / retry). What happens *after*
the button is the loop's existing re-triage behavior. Step 28c
specifically wires `/retry` → existing `process_user_context_updates`
poll loop, inheriting whatever (lossy, ineffective) behavior
that loop has. Step 29 fixes that downstream behavior.

#### Implementation

**29a — triage prompt change** (prompts.py, ~15 lines):

In `TRIAGE_SYSTEM`, add a section near the top of the
classification instructions that says approximately:

> If this payload contains a `## User Context` section, an
> operator has reviewed the prior triage and added direct
> knowledge of the failure shape. Consult that section
> **before** picking a classification. The operator has access
> to evidence the bundle artifacts don't expose — they may be
> correcting a mis-classification, naming a hidden cause, or
> pointing at a fix path. Synthesize their evidence with the
> mechanical signals; do not default to the prior
> classification just because the bundle hasn't changed.

No policy-layer change. If the model lands a new
classification under operator context, the policy table
naturally routes the new tier (`missing-dep` → MANUAL,
`compile-error` → ASSIST, etc.). The lever is purely at the
classification step.

**29b — `user_context_history` table + write site** (db/schema.py,
agentic_queries.py, ~40 lines):

```sql
CREATE TABLE IF NOT EXISTS user_context_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    context_rev INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    text TEXT NOT NULL,
    submitted_by TEXT
);
CREATE INDEX idx_user_context_history_lookup
    ON user_context_history(run_id, origin, context_rev);
```

`upsert_user_context_text` appends a row to the history table
immediately before the existing INSERT/UPDATE on
`user_context`. `submitted_by` is the operator string from
the request body when present (Step 28c's `/retry` already
collects this; Step 5's `/context` endpoint takes it as a
no-op for now), NULL otherwise. New query
`list_user_context_history(conn, run_id, origin) -> list[dict]`
returns rows ordered by `context_rev` ascending.

**29c — `manual_handoff.md` operator-context section**
(manual_handoff.py, ~30 lines):

`build_handoff_ctx` reads `list_user_context_history` for the
bundle's `(run_id, origin)`. `render_handoff` renders an
"## Operator context" section when the history is non-empty,
with each row as:

```
### Round N — 2026-05-27T11:54:32Z (operator: tuxillo)

<verbatim text>
```

Rounds appear in submission order. Section omitted entirely
when history is empty. Existing renderers (bundle detail
page) pick up the new content for free.

#### Tests

- **29a**: triage-flow integration test with a `## User
  Context` section asserting the model is *permitted* to land
  a different classification than the prior run. Cannot
  assert outcome of a real LLM call, but can assert payload
  shape and the prompt-section presence.
- **29b**: write site appends to history on each submission;
  history rows are immutable (no UPDATE path);
  `context_rev` matches the value `user_context` lands at;
  multi-round write produces N history rows.
- **29c**: `build_handoff_ctx` returns an empty list when no
  history exists; renders nothing in that case. With N rounds
  in history, the rendered section contains exactly N
  round-blocks in order, with the right timestamps and
  texts.
- End-to-end: submit context twice via `/retry`, verify the
  bundle's re-rendered `manual_handoff.md` shows both rounds.

**29-A1 — policy-layer MANUAL → ASSIST promotion under operator
context** (decision.py, ~25 lines):

Added after the redis smoke test demonstrated that 29a's prompt-
side instruction was insufficient on its own. The structural
issue: `policy.tier_for` is a pure classification → tier lookup,
and `missing-dep` / `fetch-error` / `runtime-error` /
`dependency-conflict` / `unknown` are unconditional MANUAL in
`agentic-policy.json`. Even when the triage model produces a
classification that fits semantically (e.g. "Missing md5sum" →
`missing-dep`), the operator cannot escape MANUAL routing —
every /retry-with-context loop re-classifies and re-escalates.

The fix lives in `decide()`'s rule (2): when classification
resolves to MANUAL **and** `PortHistory.has_fresh_user_context`
is true, return an `auto_patch` Decision at ASSIST tier instead
of `escalate_manual`. The patch agent then runs and gets the
operator's directive via `UserContextSection` (already in
`PATCH_SECTIONS`) plus any prior `changes.diff` via
`PriorAttemptsSection`.

`has_fresh_user_context` is the existing signal (already used
by rule (3), the patch-cap-with-context branch). No new wiring;
just a sub-branch inside rule (2).

Trade-off accepted: operator with bad context can push a
hopeless port into the patch agent and burn ASSIST budget. The
patch agent's "give up cleanly" path handles this; the budget
caps it.

**29d — extend `PriorTriagesSection` with prior patch artifacts**
(context.py, ~30 lines):

Triage payload currently pulls only `triage.md` +
`rebuild_proof.json` from past bundles. After the redis case
(operator Round 3: "i don't see you tried to find gmd5sum in
the extracted source"), the model needs to see what the patch
agent already tried — but `analysis/changes.diff`, `patch.md`,
`patch_audit.json`, `tool_trace.jsonl` are pulled only by
`PATCH_SECTIONS.PriorAttemptsSection`, and patch never runs on
MANUAL.

Extend `PriorTriagesSection` (or add a sibling
`PriorPatchEvidenceSection` to `TRIAGE_SECTIONS`) that pulls
`changes.diff` and a `patch.md` tail from past bundles, with
tighter char caps than the patch flow's section. Useful even
with 29-A1 in place — gives the first-pass triage better
evidence so the Suggested Fix is informed.

**29e — render operator-context history in the triage payload**
(context.py, ~15 lines):

`UserContextSection` renders only the single overwriting
`user_context.context_text`. The history table populated by 29b
is read by `manual_handoff.py` but not by the triage payload.
Change the section to render all rounds in submission order
(or add a sibling section keyed off `operator_context_history`)
so the model has continuity across operator submissions —
Round 3's "consider what I said before" only makes sense with
Round 1+2 visible.

#### Out of scope

- ~~Policy-layer override that promotes MANUAL → ASSIST when
  operator context is present.~~ **Pulled back in as Step 29-A1
  after the redis smoke test (commit 629f658ccf4 …). 29a alone
  was insufficient: three rounds of operator context on
  databases/redis, all three triages classified `missing-dep`
  (a semantically obvious fit for "Missing md5sum"), all
  routed to MANUAL. The prompt instruction couldn't overcome
  the classification-list ambiguity. See Step 29-A1 below.**
- Operator identity / auth. `submitted_by` is a freeform
  string for now, matching Step 28's `taken_over_by`. Step 17
  territory.
- Surfacing per-round LLM responses (what the model said
  about each round of operator context). The triage output
  already lands in `triage.md`; a per-round response history
  would require an artifact-store layout change. Defer until
  operators ask for it.

#### Scope estimate

~90 LOC across `prompts.py`, `db/schema.py`,
`tracker/agentic_queries.py`, `agent/manual_handoff.py`, plus
~120 LOC of tests.

---

### Step 30 — per-bundle branch isolation in the dev-env — shipped

> **Status (2026-05-28): shipped.**

Convert jobs land `overlay.dops` by committing it into the dev-env's
`/work/DeltaPorts` git so the auto-enqueued patch job doesn't trip the
dirty-tree preflight. Those commits accumulated on the env's working
branch across bundles, so `changes.diff` (computed relative to the
latest commit) drifted: a later bundle's diff silently included an
earlier bundle's committed overlay, and delivery on a converted port
carried edits it never made.

Step 30 gives every bundle its own throwaway git branch in the dev-env
so no job's commits leak into the next.

- **Branch model.** `bundle/<bundle_id>` cut from the env's own base
  branch (resolved via `git symbolic-ref refs/remotes/origin/HEAD`,
  falling back to `master`; cached per env). Convert and patch jobs for
  the same bundle share the branch. A new bundle id ⇒ a new branch.
- **`changes.diff` is the single canonical artifact.** Computed
  branch-vs-base via `git diff <base> -- <rel>` with an
  `--intent-to-add` pass so newly-created files (the convert overlay)
  show up. The short-lived `delivery.diff` sibling introduced in
  slice 2 was retired in slice 5 — accept/verify/delivery all read
  `changes.diff`.
- **Per-job drop policy.** Patch is terminal → drop either way. Convert
  success keeps the branch (the post-convert re-triage's patch job
  reuses it); convert failure drops it (partial commits are useless,
  next attempt starts from base). If re-triage routes to MANUAL with no
  patch following, the branch persists until the env is rebuilt — the
  explicit "stale branch out of scope" case.
- **Verify runs on its own throwaway branch.** `bundle/<id>-verify`,
  recreated from base via `git checkout -B` every run, replays
  `changes.diff` on a clean base, then drops and restores the prior
  ref. Decoupled from the patch/convert branch (which may already be
  dropped) — `changes.diff` being complete is what makes this safe.

Slices: per-bundle branch lifecycle (`d7cef088be1`, slice 1);
`delivery.diff` artifact (`9c0d6848925`, slice 2, later retired);
`_accept_delivery_step` reads it (`e8e46b58101`, slice 3);
per-job branch cleanup at terminal job end (`5df287e3158`, slice 4,
rebuilt from a periodic-sweep false start);
`changes.diff` as single canonical diff (`3f2b6ff0540`, slice 5);
dead intent_log path cleanup (`c3ccc0e9fa4`); verify on isolated
branch (`51c4e52ef81`).

The agent loop still never branches/commits/pushes at the git-remote
level — these branches are dev-env-local scratch, never pushed.

#### Adjacent fixes shipped alongside Step 30

- **Q1 — `make clean` on reset.** `reset_port` now also wipes the
  per-origin WRKDIR and invalidates the materialize/WRKSRC caches
  (`a3c7b2ca44c`); the dev-env `apply-and-build` post-build cleanup
  gained the matching `make clean` for parity (`dea2c695149`).
- **Q2 — STATUS → `overlay.dops` port-type integration.** Convert now
  reads the legacy `STATUS` port type (mask/block/normal) and surfaces
  it as an `## Expected port type` payload section, with a safety guard
  that refuses to remove `STATUS` if the resulting `.dops` type doesn't
  match (`35a94a49538`).
- **Config sample/local split.** `config/agentic-policy.json` →
  `config/agentic-policy.json.sample`; the live copy is gitignored.
  Loaders prefer the local copy and fall back to `.sample` so a fresh
  checkout works unconfigured (`6f4ef38f3a2`).

#### Doc-debt left behind (cosmetic, not action-blocking)

A handful of comments still reference the retired `delivery.diff` as a
live artifact (`runner.py:2332`/`3263`/`4357`, `worker.py:658`,
`steps.py:957`). The code is correct; only the comments are stale.
Fold into Step 24's cleanup pass.

---

