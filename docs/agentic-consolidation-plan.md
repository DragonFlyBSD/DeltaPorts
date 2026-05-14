# Consolidate the agentic-dsynth-evidence-hooks branch into dportsv3

## Context

The `agentic-dsynth-evidence-hooks` branch landed ~8.3k lines of Python/JS
(plus 1460 lines of docs) months ago, before `dportsv3 tracker` and
`dportsv3 dev-env` existed. It built its own:

- dsynth hook integration → `scripts/artifact-store` (stdlib HTTP daemon, custom SQLite schema)
- read-only observer → `scripts/state-server` + 1983-line vanilla-JS SPA
- workspace manager → `scripts/agentic-worker` (596 lines, called from a TS
  opencode plugin over SSH; reinvents what dev-env chroots now do)
- standalone patch CLI → `scripts/apply-patch` (666 lines, **already marked
  deprecated** in `scripts/agent-queue-runner:1843`)

Result: two parallel universes that overlap on dsynth telemetry, workspace
management, and dashboarding. Only one can own `Hooks_Directory` per dsynth
profile, schemas don't interoperate, ~8k LOC duplicates capability that now
exists elsewhere.

The goal of this plan is to consolidate that branch into the modern dportsv3
stack so the merge to master leaves us with **one tracker, one dev-env, one
hook target**.

---

## Approach (5 phases — each phase shippable on its own)

### Phase 1 — `dportsv3 dev-env exec NAME -- CMD...`

Add a non-interactive subcommand to dev-env. Unblocks Phase 3.

**Files**
- `scripts/tools/dev-env/dports_dev_env/cli.py` — new `exec` parser + `cmd_exec`
- `scripts/tools/dev-env/dports_dev_env/session.py` — extract a `gate_for_use(state)`
  helper from `EnvironmentSession.enter` so exec reuses the same status/mount checks
- `scripts/tools/dev-env/dports_dev_env/helpers.py` — extract `build_env_dict(state)`
  returning the dict currently written into `/root/.dports-dev-env.sh` (TARGET,
  COMPOSE_ROOT, LOCK_ROOT, DSYNTH_PROFILE, ORACLE_PROFILE, ORIGIN, HELPER_BIN,
  DELTAPORTS_ROOT, FREEBSD_PORTS_ROOT, DISTDIR, PATH)
- `scripts/tools/dev-env/dports_dev_env/chroot.py` — reuse existing
  `ChrootRunner.run(argv, env=...)` (chroot.py:31-39); no new path needed

**CLI surface**
```
dportsv3 dev-env exec NAME [--cwd DIR] [--quiet] -- CMD [ARGS...]
```
- gates identically to `shell` (rejects creating/destroying, warns on failed)
- ensures root mounted via existing `EnvironmentSession.ensure_root_mounted`
- exports the full `DPORTS_*` env block; PATH includes `$DPORTS_HELPER_BIN`
- forwards stdout/stderr; returns child's exit code

**Verification**
```
dportsv3 dev-env create --name foo --target @main
dportsv3 dev-env exec foo -- regen                  # same as running it in shell
dportsv3 dev-env exec foo -- dbuild devel/readline
dportsv3 dev-env exec foo -- env | grep ^DPORTS_    # confirm env block
echo $?                                              # propagated exit code
```

---

### Phase 2 — Delete `apply-patch` + `agent-queue-runner` apply handler

Already deprecated; no callers found.

**Files**
- delete `scripts/apply-patch`
- delete the apply-job branch in `scripts/agent-queue-runner` (the deprecated
  handler at line 1843, plus whatever dispatch leads to it)
- `git grep sync1.sh` — if `scripts/generator/sync1.sh` has no callers after
  apply-patch is gone, delete it; otherwise leave alone with a comment
- `docs/AGENTIC_BUILDS.md` — remove the "apply-patch" lifecycle references

**Verification**
- `git grep -E 'apply-patch|apply_patch'` returns only deletion-related hits
- `agent-queue-runner --once` still drains a triage + patch job pair end-to-end

---

### Phase 3 — Collapse `agentic-worker` via `dev-env exec`

Once Phase 1 exists, the workspace tool surface in `config/opencode/tool/dports.ts`
can dispatch to `dportsv3 dev-env exec` over SSH instead of routing through
`scripts/agentic-worker`.

**Files**
- `config/opencode/tool/dports.ts` — each `dports_*` tool becomes
  `ssh $DP_SSH_HOST 'dportsv3 dev-env exec $env -- <inner-cmd>'` instead of
  `ssh $DP_SSH_HOST '/build/synth/DeltaPorts/scripts/agentic-worker ...'`
- `scripts/agentic-worker` — fate decided during implementation: either
  delete entirely (TS plugin SSHes directly to `dportsv3 dev-env exec ...`)
  or keep as a small ≤100-line SSH-side shim if specific tools need pre/post
  logic that's awkward to express in TS. Decision deferred until the TS
  retarget is actually written and we can see what's natural.
- May need a couple of dev-env helpers (e.g., `dev-env genpatch NAME ORIGIN`,
  `dev-env install-patches NAME ORIGIN`) if those operations are too awkward
  to express as `dev-env exec NAME -- <shell>`
- `config/opencode/agent/dports-patch.md` — update the tool-flow examples
  in the system prompt if the JSON tool signatures changed

**Verification**
- One smoke run of the opencode dports-patch agent end-to-end against a
  known-fixable port; rebuild_proof.json shows `rebuild_ok=true`

---

### Phase 4 — Merge artifact-store schema into tracker (the big one)

Move all dsynth-failure telemetry off the standalone artifact-store onto
the FastAPI tracker. This is the unification the user signaled is
"no doubt" possible.

**Schema strategy** (per the Explore report)

Tracker keeps its existing tables (`build_runs`, `build_results`,
`port_status`, `build_types`).

New tables added in `scripts/generator/dportsv3/tracker/db.py`:
- `bundles(bundle_id TEXT PK, build_run_id INTEGER FK, origin, flavor,
  ts_utc, result, path)` — replaces artifact-store's `bundles`, but FK
  retargeted from `runs(run_id TEXT)` to `build_runs(id INTEGER)`
- `artifact_refs(bundle_id, relpath, backend, sha256, fs_path, kind, size, created_at)`
- `blob_objects(sha256 PK, size, created_at)`
- `jobs`, `activity_log`, `runner_status`, `events`, `user_context`,
  `user_context_requests` — copied as-is from artifact-store (they're
  orthogonal to tracker's existing model)

`runs` table from artifact-store collapses into `build_runs` — a dsynth
run *is* a build_run, just identified by an INTEGER id. Hooks resolve
(target, build_type) → INTEGER on first call.

**Files**
- `scripts/generator/dportsv3/tracker/db.py` — add new tables to schema,
  add CRUD for bundles/artifacts/jobs/etc.
- `scripts/generator/dportsv3/tracker/server.py` — new FastAPI routes
  (FastAPI's `EventSourceResponse` for SSE):
  ```
  POST /api/builds/{run_id}/bundles
  GET  /api/bundles/{bundle_id}
  POST /api/bundles/{bundle_id}/artifacts        (body = bytes; headers = relpath, kind)
  GET  /api/bundles/{bundle_id}/artifacts/{relpath:path}
  POST /api/bundles/{bundle_id}/artifacts/fs      (filesystem-backed ref, for full.log.gz)
  GET  /api/jobs                                  (list + filter)
  POST /api/jobs                                  (enqueue)
  PATCH /api/jobs/{job_id}                        (state transitions)
  GET  /api/runner-status
  POST /api/runner-status                         (heartbeat)
  POST /api/user-context
  GET  /api/user-context, /api/user-context-request
  GET  /api/events                                (SSE stream)
  ```
- `scripts/generator/dportsv3/tracker/client.py` — extend with `upsert_bundle`,
  `put_artifact_blob`, `put_artifact_fs`, `record_activity`, etc.
- `scripts/dsynth-hooks/hook_common.sh` — rewrite to POST to tracker URL
  (resolve build_run via existing `start-build`/`record-result` client, plus
  new `bundles` + `artifacts` routes). Use the new `dportsv3 tracker` CLI
  subcommands instead of curl where possible.
- `scripts/agent-queue-runner` — replace artifact-store HTTP client calls
  with tracker client calls; replace direct sqlite writes for `activity_log` /
  `runner_status` with the new tracker endpoints. The file-based queue
  (`evidence/queue/{pending,inflight,done,failed}`) can either stay (and the
  tracker just observes it via the `jobs` table written by the runner) or
  migrate fully to DB-backed queue. **Recommendation:** keep the file queue
  as the system of record (atomic-rename semantics are nice) and treat the
  `jobs` table as a read-side projection updated by the runner.
- delete `scripts/artifact-store` (448 lines)
- delete `scripts/artifact-store-client` (124 lines) — or keep as a thin
  shim that calls `dportsv3 tracker` subcommands

**`dportsv3 tracker` CLI subcommands to add** (parallel to existing
`start-build`/`record-result`/`enqueue-ports`):
- `tracker put-bundle --run-id ID --origin O --result R [--bundle-id ID] [...]`
- `tracker put-artifact --bundle-id ID --relpath R [--file F | --stdin] [--kind K]`
- `tracker put-artifact-fs --bundle-id ID --relpath R --fs-path P [--kind K]`
- `tracker record-activity --job-id ID --stage S --message M [--duration-ms N]`

**Verification**
- dsynth profile pointed at the rewritten hooks; force a known-failing build;
  bundle and artifacts visible via tracker API
- `agent-queue-runner --once` processes the bundle and writes triage/patch
  artifacts back via tracker
- `scripts/artifact-store` is deleted; nothing breaks

---

### Phase 5 — Fold state-server UI into the tracker dashboard

Tracker already serves a jinja2 dashboard (`/`, `/target/{target}`,
`/builds`, `/builds/{run_id}`, `/diff`). Add the views state-server uniquely
provided.

**Files**
- `scripts/generator/dportsv3/tracker/server.py` — new HTML routes:
  - `/builds/{run_id}/bundles/{bundle_id}` — bundle detail (logs,
    triage.md, patch.diff, rebuild_proof.json, artifact list)
  - `/jobs` — agentic job queue with state filter
  - `/runner` — runner heartbeat + activity tail
- `scripts/generator/dportsv3/tracker/templates/` — new jinja2 templates
  for the above
- **Drop the 1983-line vanilla-JS SPA.** Rebuild its views (live job
  queue with state filter, bundle detail, runner heartbeat) as jinja2
  templates in tracker with a small bit of HTMX or vanilla fetch-polling
  hung off the new routes. SSE was already added in Phase 4 so live
  updates are cheap. Target: ≤300 lines of templates+JS to replace 1983
  lines of SPA.
- delete `scripts/state-server` (1369 lines)
- delete `scripts/state-server-ui/`
- `docs/AGENTIC_BUILDS.md` — update references to state-server → tracker

**Verification**
- tracker dashboard live-updates the queue during an `agent-queue-runner --once`
- bundle detail page renders triage.md, patch.diff, and rebuild_proof.json
  for a known bundle
- state-server is deleted; no broken links

---

## Critical files reference

| Existing | Role |
|---|---|
| `scripts/tools/dev-env/dports_dev_env/cli.py` | dev-env subcommand entry — extend with `exec` |
| `scripts/tools/dev-env/dports_dev_env/session.py` | `EnvironmentSession.enter` — extract gating |
| `scripts/tools/dev-env/dports_dev_env/chroot.py` | `ChrootRunner.run` already does what `exec` needs |
| `scripts/tools/dev-env/dports_dev_env/helpers.py` | `write_shell_rc` — extract `build_env_dict` |
| `scripts/generator/dportsv3/tracker/db.py` | tracker SQLite — add bundles/jobs/etc. |
| `scripts/generator/dportsv3/tracker/server.py` | tracker FastAPI — add new routes + templates |
| `scripts/generator/dportsv3/tracker/client.py` | tracker HTTP client — add bundle/artifact methods |
| `scripts/generator/dportsv3/commands/tracker.py` | `dportsv3 tracker` CLI — add new subcommands |
| `scripts/dsynth-hooks/hook_common.sh` | rewrite to talk tracker |
| `scripts/agent-queue-runner` | retarget HTTP client; delete apply handler |
| `config/opencode/tool/dports.ts` | retarget to `dportsv3 dev-env exec` |
| `dportsv3` (wrapper) | no changes — `dev-env` route already exists |

| Deleted at end | LOC |
|---|---|
| `scripts/apply-patch` | 666 |
| `scripts/artifact-store` | 448 |
| `scripts/artifact-store-client` | 124 (or kept as shim) |
| `scripts/state-server` | 1369 |
| `scripts/state-server-ui/app.js` | 1983 |
| `scripts/state-server-ui/{app.css,index.html}` | ~400 |
| `scripts/agentic-worker` | 596 → 0 (delete) or ≤100 (shim) — decided in Phase 3 |
| **Total** | **~5.5k LOC removed** |

Net: ~5.5k LOC of duplication retired; agentic flow runs on tracker + dev-env;
single dsynth-hook target; single dashboard.

---

## Verification (end-to-end, after all phases)

1. `dportsv3 dev-env create --name e2e --target @main`
2. `dportsv3 dev-env exec e2e -- regen` (Phase 1)
3. Build a known-failing port via dsynth in the env with `Hooks_Directory`
   pointed at `scripts/dsynth-hooks/`
4. Tracker dashboard shows the build_run, the failing port's bundle, the
   triage job appearing in pending → inflight → done (Phase 4 + 5)
5. opencode dports-patch agent runs (via TS tool → `dev-env exec` over SSH);
   rebuild_proof.json with `rebuild_ok=true` lands in the bundle (Phase 3)
6. PR opened via the tracker's PR-enqueue path
7. `git grep apply-patch` / `git grep artifact-store` / `git grep state-server`
   returns nothing live (Phase 2 + 4 + 5)
