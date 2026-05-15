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

#### CLI surface (MVP)

```
dportsv3 dev-env exec NAME [--cwd DIR] -- CMD [ARGS...]
```

- gates identically to `shell` (rejects creating/destroying, warns on failed)
- ensures root is mounted (reuses `EnvironmentSession.ensure_root_mounted`)
- exports the full `DPORTS_*` env block into the child; PATH includes
  `$DPORTS_HELPER_BIN` so helper scripts resolve
- forwards stdout/stderr to the caller's terminal (no capture)
- returns the child's exit code as the process exit code

#### Step sequence

Split into two commits: a behavior-preserving refactor (S1 + S2), then the new
feature (S3 + S4 + S5).

**S1. Extract `build_env_dict(state)` in `helpers.py`** *(refactor; no behavior change)*

- Today `write_shell_rc` (helpers.py:113-184) inlines env exports as f-string
  shell text.
- Add `build_env_dict(state: EnvironmentState) -> dict[str, str]` returning:
  `DELTAPORTS_ROOT`, `FREEBSD_PORTS_ROOT`, `DPORTS_DEV_ENV`, `DPORTS_TARGET`,
  `DPORTS_ORIGIN`, `DPORTS_COMPOSE_ROOT`, `DPORTS_LOCK_ROOT`,
  `DPORTS_DSYNTH_ROOT`, `DPORTS_DSYNTH_PROFILE`, `DPORTS_TOUCHED_ORIGINS_FILE`,
  `DPORTS_HELPER_BIN`, `DPORTS_ORACLE_PROFILE`, `DISTDIR`, `PATH`.
- Rewrite `write_shell_rc` to loop over the dict:
  `"\n".join(f"export {k}={quote(v)}" for k, v in build_env_dict(state).items())`.
- PATH stays the same: `"$DPORTS_HELPER_BIN:/usr/local/bin:/usr/local/sbin:/bin:/sbin:/usr/bin:/usr/sbin"`.

**S2. Factor `prepare()` out of `EnvironmentSession.enter()` in `session.py`** *(refactor)*

- `enter()` (session.py:23-50) currently does: status gate → mount → write
  dsynth.ini / rcfile if missing → write helper scripts → `prepare_root_runtime`
  → `exec_shell`. Split so the new method does everything except the final
  `exec_shell`.
- New: `prepare(name, *, refresh: bool = False) -> EnvironmentState` does all
  the pre-shell work and returns the loaded state.
- Rewrite `enter`:
  ```python
  def enter(self, name, *, refresh=False):
      state = self.prepare(name, refresh=refresh)
      if not command_exists(state.root_dir, "bash"):
          warn("bash is unavailable; falling back to /bin/sh")
      exec_shell(state.root_dir)
  ```

**S3. Add the `exec` subparser in `cli.py`** *(new feature)*

- Add the parser block alongside the others (around cli.py:77):
  ```python
  exec_ = subparsers.add_parser("exec", help="Run a command inside an env non-interactively")
  exec_.add_argument("--cwd", default="/work/DeltaPorts",
                     help="Working directory inside the chroot")
  exec_.add_argument("name", help="Environment name")
  exec_.add_argument("argv", nargs=argparse.REMAINDER,
                     help="-- CMD [ARGS...] to run inside the env")
  ```
- Register in the `commands` dict in `dispatch()` (cli.py:215).

**S4. New `EnvironmentSession.exec_command(state, argv, *, cwd)` in `session.py`** *(new feature)*

- Build env dict by merging `chroot_env()` (chroot.py:12-17) with
  `build_env_dict(state)` — helper env wins on collisions (so PATH includes
  `$DPORTS_HELPER_BIN`).
- Wrap argv with cwd via `/bin/sh -c`:
  ```python
  wrapped = ["/bin/sh", "-c", f"cd {shlex.quote(cwd)} && exec \"$@\"", "_", *argv]
  ```
  (`/bin/sh -c '... && exec "$@"' _ arg1 arg2 ...` — the `_` is `$0`; remaining
  args become `$@`.)
- Call `ChrootRunner(state.root_dir).run(wrapped, env=env_dict)` and return
  `result.returncode`.

**S5. New `cmd_exec(args)` in `cli.py`** *(new feature)*

- Mirror `cmd_shell` (cli.py:173-179):
  ```python
  def cmd_exec(args):
      require_root()
      config = load_config()
      validate_cache_root(config.cache_root)
      store = EnvironmentStore(config)
      if not args.argv:
          raise UsageError("dev-env exec requires a command after '--'")
      argv = args.argv[1:] if args.argv and args.argv[0] == "--" else args.argv
      session = EnvironmentSession(config, store)
      state = session.prepare(args.name)
      return session.exec_command(state, argv, cwd=args.cwd)
  ```
- `cli.main` already wraps the return in `SystemExit` (cli.py:230), so exit
  codes propagate naturally.

#### Design decisions (locked-in for MVP)

| Question | Choice | Rationale |
|---|---|---|
| Argparse separator | `nargs=argparse.REMAINDER` after positional `name`; strip a leading `--` if present | REMAINDER is the standard idiom for "everything after"; leading `--` strip handles the ergonomic form `dev-env exec foo -- regen` |
| Empty argv | Raise `UsageError` ("dev-env exec requires a command after '--'") | `shell` exists for the interactive use case |
| Output handling | Stream to caller's terminal; no `--quiet` flag in MVP | SSH callers want live output; capture mode adds complexity without a current need |
| Status == "failed" | Warn + proceed (same as `enter`) | Symmetry; SSH callers can pre-check via `dev-env list` if they want to refuse |
| Repeated `exec` calls writing helpers | Accept — `write_helper_scripts` is idempotent and the dsynth.ini/rcfile writes are gated on `refresh or missing` | First exec pays the cost; subsequent ones are no-ops |
| `cwd` semantics | Path is inside the chroot. Default `/work/DeltaPorts` (matches the rcfile's `cd` logic) | Matches interactive shell behavior |

#### Deferred (out of MVP scope)

**`--script PATH`** — copy a host-side script into the env's writable overlay,
chmod 0755, execute, unlink on exit (try/finally). Preserves shebangs, args,
and exit codes. ~30 lines. Add iff Phase 3 (agentic-worker collapse) reveals
the SSH-quoting-multiline-script case is common enough to warrant it.

In the meantime the same effect is achievable with one extra step:

```sh
cp build-recipe.sh "$env_dir/writable/tmp/x.sh"
dportsv3 dev-env exec foo -- /tmp/x.sh arg1 arg2
```

`--stdin` mode (pipe stdin to `/bin/sh -s`) is even cheaper but loses shebangs;
not preferred over `--script` if/when we add either.

#### Verification

```sh
sudo dportsv3 dev-env create --name e2e --target @main
sudo dportsv3 dev-env exec e2e -- env | grep ^DPORTS_   # S1: env exported
sudo dportsv3 dev-env exec e2e -- pwd                    # S4: default cwd == /work/DeltaPorts
sudo dportsv3 dev-env exec e2e --cwd /tmp -- pwd         # S4: cwd override
sudo dportsv3 dev-env exec e2e -- false; echo $?         # S5: exit code → 1
sudo dportsv3 dev-env exec e2e -- regen                  # full helper run, parity with shell
sudo dportsv3 dev-env exec e2e -- dbuild devel/readline  # end-to-end helper invocation
```

Plus negative cases:
```sh
sudo dportsv3 dev-env exec e2e                           # missing argv → UsageError
sudo dportsv3 dev-env exec nonexistent -- pwd            # unknown env → StateError
sudo dportsv3 dev-env destroy --yes e2e
sudo dportsv3 dev-env exec e2e -- pwd                    # destroyed env → StateError
```

#### Files touched

| File | Change |
|---|---|
| `scripts/tools/dev-env/dports_dev_env/helpers.py` | S1: factor `build_env_dict(state)`; `write_shell_rc` loops over it |
| `scripts/tools/dev-env/dports_dev_env/session.py` | S2: factor `prepare()` from `enter()`. S4: new `exec_command(state, argv, *, cwd)` |
| `scripts/tools/dev-env/dports_dev_env/cli.py` | S3: `exec` subparser. S5: `cmd_exec`. Register in `dispatch()`. |
| `scripts/tools/dev-env/dports_dev_env/chroot.py` | No changes — `ChrootRunner.run` (chroot.py:31-39) already accepts the env dict |

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
