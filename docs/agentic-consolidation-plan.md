# Consolidate the agentic-dsynth-evidence-hooks branch into dportsv3

> **Status (post-Phase-1 review):** Phase 1 (`dportsv3 dev-env exec`) shipped
> in commits `71e26142ae3` + `8eab9aeb5d2`. While reviewing Phase 2 we
> discovered the original plan was built on two wrong assumptions:
>
> 1. `scripts/apply-patch` is **not** dead. The "deprecated" wording only
>    applies to an unused queue-job type called `apply`; the standalone CLI
>    is alive and is the only path that takes a bundle on disk and produces
>    a PR without needing the full opencode stack running.
> 2. `process_apply_job` *is* dead, but the deletion is cosmetic — it's
>    never dispatched, so removing it doesn't change behavior.
>
> Phases 2–5 below need re-evaluation against the system map. Treat the
> phases as historical drafts pending revision; the map is the new
> ground truth.

---

## System map

### Topology

```
+----------------------- Linux dev host -----------------------+
|                                                               |
|  opencode serve  <----HTTP POST /session, /session/{id}/--+   |
|     (LLM gateway)                                  message |   |
|        |                                                    |   |
|        | loads on startup:                                  |   |
|        v                                                    |   |
|  config/opencode/agent/                                    |   |
|    dports-triage.md     (subagent, no tools)               |   |
|    dports-patch.md      (subagent, calls dports_* tools)   |   |
|                                                            |   |
|  config/opencode/tool/dports.ts                            |   |
|    (TS plugin: each dports_* tool SSHes to the VM         |   |
|     and runs /build/synth/.../agentic-worker)              |   |
|                                                            |   |
+------------------------------------------------------------|---+
                                                             |
                                                       SSH + HTTP
                                                             |
+--------------------- DragonFly builder VM -----------------|---+
|                                                            |   |
|  HUMAN OPERATOR                                            |   |
|     |                                                      |   |
|     v                                                      |   |
|  dsynth -p PROFILE build|test ...                          |   |
|     |  (reads Hooks_Directory from dsynth.ini)             |   |
|     v                                                      |   |
|  hook_run_start, hook_pkg_failure (per failed port),       |   |
|  hook_run_end                                              |   |
|     |                                                      |   |
|     +-- POST to artifact-store :8788 (bundle, blobs)      |   |
|     +-- write file: ${queue_root}/pending/<job>.job       |   |
|         (key=value, type=triage)                          |   |
|                                                            |   |
|  artifact-store :8788 (HTTP daemon)                       |   |
|     - sqlite state.db (runs, bundles, jobs, artifact_refs,|   |
|                       blob_objects, events,               |   |
|                       activity_log, runner_status,        |   |
|                       user_context, ...)                  |   |
|     - blobstore/<sha256> (content-addressed)              |   |
|     - full-logs/<bundle>.gz (filesystem ref)              |   |
|                                                            |   |
|  agent-queue-runner (loop or --once)                      |   |
|     atomically claims pending/<job> -> inflight/          |   |
|     dispatch on `type`:                                   |   |
|        triage  -> POST opencode (dports-triage)  ---------+   |
|        patch   -> POST opencode (dports-patch)   ---------+   |
|        pr      -> git push + gh pr create                 |   |
|        (apply  -> defined but unreachable; dead code)     |   |
|     writes outputs back to artifact-store                 |   |
|     enqueues follow-ups (snippet rounds, patch, pr)       |   |
|     moves job to done/ or failed/                         |   |
|                                                            |   |
|  snippet-extractor (called by runner when                 |   |
|     the triage response asks for more code/log context)   |   |
|                                                            |   |
|  state-server :8787 (read-only observer)                  |   |
|     polls filesystem + state.db, emits SSE                |   |
|     serves the 1983-line JS SPA at /                      |   |
|     UNAUTH POST /enqueue/pr  <--- the manual button       |   |
|     that turns rebuild_ok=true into a `type=pr` job       |   |
|                                                            |   |
|  /build/synth/agentic-workspace/  (used by dports.ts via  |   |
|     DeltaPorts/   (ai-work/<origin> branches)   the SSH   |   |
|     FPORTS/       (pinned to fports_ref)        worker)   |   |
|     DPorts/       (materialized per-origin)               |   |
|                                                            |   |
+------------------------------------------------------------+   |
                                                                  |
+-------- DORMANT or PARALLEL (not wired into above) -------------+
|                                                                  |
|  dportsv3 tracker (FastAPI :8080) — built, schema populated     |
|     by manual tests (~38k rows in tracker.db); **zero**         |
|     production callers. compose/dsl/migrate never write to it.  |
|                                                                  |
|  dportsv3 dev-env (chroot manager) — built, used only by         |
|     humans creating throwaway envs. The new `exec` subcommand   |
|     from Phase 1 is the foundation for routing agentic work     |
|     through it, but nothing calls it yet.                       |
|                                                                  |
|  scripts/apply-patch — standalone CLI, **alive**.               |
|     Takes a bundle dir off disk, applies patch.diff to a        |
|     "safe clone" of DeltaPorts, runs sync1.sh, dsynth rebuild,  |
|     gh pr create. Has safety guards (protected-paths list,      |
|     --dry-run, --no-push, --no-rebuild, --no-pr). NOT invoked   |
|     by hooks, runner, or any opencode agent. Triggered by       |
|     humans (or shell scripts) operating on bundles directly.    |
|                                                                  |
+------------------------------------------------------------------+
```

### Data flow: one failed port end-to-end

1. **dsynth fails a port** on the VM. dsynth fires `hook_pkg_failure`.
2. **Evidence capture (bash hook)** — `hook_pkg_failure` writes meta.txt,
   errors.txt (bounded), full.log.gz, port snapshot (Makefile, distinfo,
   pkg-plist, patches), all via HTTP to `artifact-store`. Then
   `enqueue_job()` (hook_common.sh:162-218) writes
   `${queue_root}/pending/<ts>-<profile>-<origin>-<pid>.job`.
3. **Triage** — `agent-queue-runner` claims the job, builds the payload
   (errors + port files + KEDB + user_context + snippets if any),
   POSTs to `opencode serve` as the `dports-triage` agent. Response
   parsed for Classification + Confidence.
4. **Snippet rounds (0-5)** — if the response contains a
   `## Snippet Requests` section and round < 5, `snippet-extractor`
   pulls bounded snippets from preserved workdir / distfiles / log,
   and the runner re-enqueues with `snippet_round++`.
5. **Patch enqueue** — if confidence ∈ {high, medium} and classification
   ∈ {compile, configure, patch, plist}-error, a `type=patch` job is
   auto-enqueued. Otherwise: triage marked done, no further automation.
6. **Patch** — `agent-queue-runner` POSTs the patch payload to opencode
   as `dports-patch` agent. The agent calls `dports_*` tools (from the
   TS plugin), which SSH into the VM and run `agentic-worker`:
   workspace_verify → checkout_branch → materialize_closure → extract →
   [edits via dupe/genpatch/get_file/put_file] → install_patches →
   commit → dsynth_build. Outputs `rebuild_proof.json` to the bundle.
7. **PR (manual)** — `rebuild_proof.json` with `rebuild_ok=true` does
   **not** auto-progress. Either a human clicks the state-server UI
   button (which POSTs `/enqueue/pr`) or runs the equivalent curl, and
   then `agent-queue-runner` processes the `type=pr` job: git push
   branch, `gh pr create`, write `pr_url.txt`.
8. **On failure** — job moves to `failed/`, `.job.error` file written
   next to it. No automatic retry. Humans can provide user_context
   (via the UI) which causes the runner to re-enqueue with bumped
   iteration count.

### `apply-patch` — separate manual path

`scripts/apply-patch --bundle <path>` runs the same conceptual flow as
steps 6-7 above, but **without** opencode, the runner, or the
agentic-worker tool surface:

```
bundle/analysis/patch.diff
    |
    v
ensure_safe_clone() -> /build/synth/DeltaPorts-ai-fix
    (NEVER touches ~/s/DeltaPorts or /build/synth/DeltaPorts)
    |
    v
git checkout -b ai-fix/<origin>/<classification>
git apply patch.diff
git commit
git push origin <branch>     (--no-push to skip)
    |
    v
sync1.sh <origin>            (local on DragonFly)
    OR ssh root@vm "cd /build/synth/DeltaPorts/scripts/generator && ./sync1.sh ..."
    |
    v
dsynth force <origin>        (--no-rebuild to skip)
    |
    v
gh pr create                 (--no-pr to skip; gated on rebuild ok)
    |
    v
write back to bundle: branch.txt, commit.txt,
                      rebuild_status.txt, pr_url.txt
```

This is a complete alternate route from bundle → PR. It bypasses every
piece of the agentic loop. Its only external dependency outside its own
file is `scripts/generator/sync1.sh` (which `scripts/generator/quicksync.sh`
also calls — so sync1.sh is not orphaned by apply-patch).

### What the system is missing today

- **No bridge between the active agentic stack and the dormant tracker.**
  Build runs, port failures, and rebuild outcomes all live in
  artifact-store's `state.db`; the tracker's `tracker.db` is populated
  only by manual `dportsv3 tracker` CLI invocations.
- **No bridge between the agentic workspace and `dportsv3 dev-env`.**
  `agentic-worker` does its own git checkouts and dsynth calls; `dev-env`
  creates chroots with overlay mounts. They share the goal but not the
  code.
- **No auto-PR.** PR creation is a UI button or curl call, gated on a
  human reading `rebuild_proof.json`.
- **Two human entry points to "fix a port from a bundle":** the agentic
  loop (start dsynth, wait for it to fail, wait for triage+patch+manual
  enqueue), or `apply-patch --bundle <dir>` (one-shot, scriptable).

---

## Findings since the initial plan

| Claim in the original plan | Reality |
|---|---|
| `scripts/apply-patch` is deprecated and superseded | False. The CLI is the only one-shot bundle→PR tool; "deprecated" refers to an unused job-type wrapper in the runner. |
| `process_apply_job` in agent-queue-runner needs to be removed | True it's dead code, but deletion is cosmetic — no dispatch arm reaches it; a `type=apply` job already gets `"unknown job type"`. |
| `scripts/generator/sync1.sh` orphaned after apply-patch goes | Wrong even if apply-patch went: `quicksync.sh:4` also calls it. |
| Tracker is part of the live stack | False. Tracker is built but never written to in production. |
| `dev-env` overlaps with agentic-worker enough to collapse | Still true, but the user-facing impact is smaller than implied: agentic-worker is invoked by tool calls from a remote opencode session, not by humans. The Phase 3 collapse is a refactor, not a UX change. |
| Phase 4 merges two SQLite-backed services | True but the asymmetry is now clear: artifact-store has 11 tables of live state; tracker has 4 tables of hypothetical state. The merge is "absorb agentic schema into tracker," not "merge two live datasets." |

---

## Approach (revised against the system map)

Four phases. Phase 1 has shipped. Phases 2, 3, and 4 are concrete reworks.

Direction in plain terms:

- **Keep `apply-patch`**, but route its sync+rebuild legs through
  `dportsv3 dev-env exec`. Drops its platform-bifurcation + SSH plumbing.
- **Replace opencode with a Python litellm harness.** Drops the only
  TypeScript piece and the only non-Python runtime in the stack.
  Tools become Python functions that call `dev-env exec`; `agentic-worker`
  dies; the two markdown agent configs become Python prompt templates.
- **One database, two processes.** `artifact-store` stays as the single
  writer; `state-server` dies and its UI / read API / SSE move into the
  tracker, which becomes a read-only FastAPI app against the same
  `state.db`. Tracker's four existing tables fold into `state.db`;
  `tracker.db` retires.

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

### Phase 2 — `apply-patch` routes sync + rebuild through `dev-env exec`

**Revised from the original Phase 2**, which proposed deletion. The
re-read showed `apply-patch` is alive and is the only one-shot
"bundle → PR" path; the "deprecated" wording referred to the unused
runner job-type `apply` (`process_apply_job` in `agent-queue-runner:1837-1848`,
never dispatched), not the CLI.

Goal: keep `apply-patch`'s behaviour, drop its platform-mode bifurcation
(DragonFly-local vs Linux-via-SSH) and the SSH plumbing it carries today,
by routing sync + rebuild through `dportsv3 dev-env exec`.

**Today** (`scripts/apply-patch:36-58, 300-426`)
- Detects platform (`IS_DRAGONFLY = platform.system() == "DragonFly"`).
- Local mode: invokes `scripts/generator/sync1.sh ORIGIN` + `dsynth force ORIGIN` directly.
- SSH mode: runs the same commands over SSH to a VM via `VM_SSH_KEY` /
  `VM_SSH_PORT` / `VM_SSH_HOST` env vars.
- Uses a "safe clone" at `/build/synth/DeltaPorts-ai-fix` (or
  `~/s/DeltaPorts-ai-fix`) that's explicitly protected against being
  the real checkout.

**After this phase**
- One path: an existing `dev-env` is the workspace.
- Sync: `dportsv3 dev-env exec ENV -- /work/DeltaPorts/scripts/generator/sync1.sh ORIGIN`.
- Rebuild: `dportsv3 dev-env exec ENV -- dbuild ORIGIN` (existing helper
  in `helpers.py:62-90` — calls `dsynth -p $DPORTS_DSYNTH_PROFILE build`).
- The safe-clone concept goes away — the env's writable overlay at
  `env_dir/writable/work/DeltaPorts` is already an isolated checkout.
  apply-patch applies the patch there directly.
- Platform detection and SSH plumbing both deleted.
- `process_apply_job` in `agent-queue-runner` deleted (cosmetic — never
  dispatched, harmless either way, but tidy).

**CLI shape**
```
apply-patch --bundle PATH [--env NAME] [--dry-run] [--no-push] [--no-rebuild] [--no-pr]
```
- `--env NAME` is new. If omitted, apply-patch creates an ephemeral env
  (`apply-patch-<rand>`) via `dportsv3 dev-env create`, applies, rebuilds,
  destroys. If supplied, reuses it.

**Files modified**
- `scripts/apply-patch` — remove `IS_DRAGONFLY`, the `local_*` /
  `vm_*` helpers, all `ssh_cmd` / `scp_cmd` plumbing, the safe-clone
  ensure logic. Replace with calls to `dportsv3 dev-env`. Reduces the
  file from 666 lines to ~250-300.
- `scripts/agent-queue-runner` — delete `process_apply_job` (lines
  1837-1848).
- `docs/AGENTIC_BUILDS.md:758` — reword: "This phase replaces the
  earlier `analysis/patch.diff` flow with workspace-driven rebuilds."
  (drop the `apply-patch` mention since it's no longer deprecated.)

**Untouched**
- `scripts/generator/sync1.sh` — still called by `quicksync.sh:4`, unrelated to apply-patch's lifecycle. Stays.

**Verification**
- `apply-patch --bundle BUNDLE` produces the same outputs as before
  (`branch.txt`, `commit.txt`, `rebuild_status.txt`, `pr_url.txt` in
  the bundle). `--dry-run`, `--no-push`, `--no-rebuild`, `--no-pr`
  gates still work.
- No SSH calls happen; tool runs purely through `dportsv3 dev-env exec`.
- `git grep -E 'IS_DRAGONFLY|VM_SSH_|safe_clone' scripts/apply-patch`
  returns nothing.

---

### Phase 3 — Replace opencode with a litellm-based Python harness

**Replaces the original "collapse agentic-worker" framing.** Once we
drop opencode entirely, agentic-worker has no client and dies as a
side effect.

Drop the opencode runtime, the TypeScript plugin, and the markdown
agent configs. The agent harness becomes pure Python:
[`litellm`](https://github.com/BerriAI/litellm) for provider-agnostic
LLM calls (keeps access to opencode.ai/zen, NVIDIA NIM, Anthropic
direct, OpenAI, etc.), Python functions for tools that wrap
`dportsv3 dev-env exec`, Python templates for system prompts.

**Why this is the right pivot**
- opencode is the only non-Python piece in the stack. Removing it
  consolidates everything under one runtime.
- All three providers we care about are reachable from Python with no
  CLI dependency: opencode.ai/zen exposes both OpenAI-compatible
  (`/v1/chat/completions`) and Anthropic-compatible (`/v1/messages`)
  endpoints; NVIDIA NIM has first-class litellm support; Anthropic
  direct works obviously.
- litellm normalizes tool-use across providers, so we can mix freely
  (cheap free model for triage, stronger tool-use model for patch).
- The agent-queue-runner already does most of the harness work
  (payload assembly, response parsing, retry/log/state writes); we're
  replacing one HTTP call + one tool-dispatch indirection, not
  rebuilding from scratch.

**New code** (suggested layout: `scripts/agent_harness/` as a module
beside `agent-queue-runner`, or inlined into the runner — TBD during
implementation)
- `llm.py` — `complete(messages, tools=None, model=..., api_base=..., api_key=...)`
  wrapping `litellm.completion` and returning a normalized response.
- `tools.py` — tool registry: each `dports_*` tool from `dports.ts`
  becomes a Python function with a JSON schema, calling
  `subprocess.run(["dportsv3", "dev-env", "exec", env, "--", ...])`.
  Examples:
  - `workspace_verify(env)` → exec `git status` + sanity checks
  - `checkout_branch(env, origin)` → exec `git checkout -b ai-work/ORIGIN`
  - `dupe(env, path)` → exec `dupe PATH`
  - `genpatch(env, path)` → exec `genpatch PATH`
  - `install_patches(env, origin, patches)` → exec `install-patches ...`
  - `commit(env, origin, message)` → exec `git -C /work/DeltaPorts commit ...`
  - `dsynth_build(env, origin)` → exec `dbuild ORIGIN` (existing helper)
  - `get_file(env, path)` / `put_file(env, path, content)` — operate on
    the env's writable overlay directly from the host (`env_dir/writable/...`),
    no exec needed
- `loop.py` — multi-turn driver: `while response.tool_calls: dispatch
  each tool → append tool_result message → re-call LLM → repeat`.
  ~30 lines.
- `prompts.py` — system prompts for `dports-triage` and `dports-patch`
  agents (lifted from `config/opencode/agent/*.md`).
- `triage.py` — single-turn triage flow (no tools, uses existing payload
  builder).
- `patch.py` — multi-turn patch flow (uses tool loop).

**Config (new env vars)**
- `DP_HARNESS_TRIAGE_MODEL` (e.g., `openai/gpt-5-nano`)
- `DP_HARNESS_PATCH_MODEL` (e.g., `anthropic/claude-sonnet-4`)
- `DP_HARNESS_TRIAGE_API_BASE` (e.g., `https://opencode.ai/zen/v1`)
- `DP_HARNESS_PATCH_API_BASE` (default = provider's standard endpoint)
- `DP_HARNESS_*_API_KEY` for each provider
- `OPENCODE_URL` / `OPENCODE_AGENT` / `OPENCODE_*` env vars in the
  runner all retire.

**Files modified**
- `scripts/agent-queue-runner` — replace the opencode-specific code
  paths (`call_opencode` around lines 1338-1395, response parsing 1398-1437)
  with calls into the new `harness` module. Keep payload assembly
  (`build_triage_payload`, `build_patch_payload` lines 1057-1318),
  queue handling, snippet logic, state writes, activity logging.
- `dportsv3` (wrapper) — no changes needed; harness ships with the
  existing dev-env or generator venv via `pyproject.toml` `[harness]`
  extra.

**Where the harness runs**
- **Default: on the DragonFly VM**, co-located with dsynth, hooks,
  dev-env. No SSH needed; tool functions call `dportsv3 dev-env exec`
  locally. Talks to LLM providers over HTTPS.
- Alternative: on a separate dev host with SSH back to the VM for tool
  ops. Heavier; only worth it if the dev host has resources the VM
  lacks. **Not the recommended default.**

**Deleted**
- `config/opencode/tool/dports.ts` (256 lines TypeScript)
- `config/opencode/agent/dports-triage.md`
- `config/opencode/agent/dports-patch.md`
- `scripts/agentic-worker` (596 lines) — no client left after the TS
  plugin goes away
- the opencode-specific paths inside `agent-queue-runner` (~200 lines)

**Verification**
- End-to-end smoke run on a known-fixable port:
  1. dsynth fails the port → hook writes bundle
  2. agent-queue-runner picks up triage job → calls litellm with
     `DP_HARNESS_TRIAGE_MODEL`, parses Classification + Confidence
  3. Auto-enqueues patch job (assuming patchable classification)
  4. Patch agent runs the tool loop via litellm → each tool function
     calls `dportsv3 dev-env exec` locally → writes
     `rebuild_proof.json` with `rebuild_ok=true`
- No opencode process running; `pgrep opencode` empty
- LiteLLM model can be swapped (`openai/gpt-5-nano` ↔
  `nvidia_nim/meta/llama-3.1-70b-instruct` ↔ `anthropic/claude-sonnet-4`)
  via env var without code changes
- Phase 2's apply-patch flow still works because it doesn't depend on
  the harness — it's a separate human path through `dev-env exec`

---

### Phase 4 — One database, two processes: artifact-store writes, tracker reads

**Decision locked in.** The split is:

- **`artifact-store` stays.** Keeps its single-writer role — owns
  `state.db`, `blobstore/<sha256>`, `full-logs/*.gz`. Hooks and harness
  keep POSTing to it. State-server's two write endpoints
  (`/user-context`, `/enqueue/pr`) get re-exposed on artifact-store so
  the single-writer invariant holds.
- **`state-server` dies.** All read endpoints, the SSE stream, and the
  operator UI move into tracker.
- **`scripts/state-server-ui/` (the 1983-line SPA) dies.** Rebuilt as
  jinja2 templates + HTMX (or vanilla fetch-polling) inside the tracker
  app.
- **Tracker becomes a pure read-only FastAPI app.** Opens `state.db`
  in read mode (SQLite WAL allows N readers + 1 writer concurrently).
  Serves cross-build dashboards (its existing schema, now folded into
  `state.db`) plus per-failure forensics (the agentic schema). Never
  writes to the DB.
- **`tracker.db` goes away.** Its 4 tables (`build_runs`,
  `build_results`, `port_status`, `build_types`) are folded into
  `state.db` and become artifact-store-owned.

#### Schema fold-in

The four existing tracker tables move into `state.db` as-is — same
column definitions, same indexes. Artifact-store gains a small
`schema_migrations.sql` (or inline DDL) that creates them on startup
if missing.

Open coupling question: should the artifact-store `runs` table
(TEXT `run_id`, one row per dsynth invocation) and the tracker
`build_runs` table (INTEGER `id`, scoped to target + build_type)
become the same table?

**Recommendation: keep them separate.** They model different things
even though both are "runs":
- `runs` = one dsynth invocation (unique per hook fire)
- `build_runs` = a logical build campaign across many invocations
  (target × build_type, e.g. "2026Q1 release")

A weak link is fine: `runs.build_run_id` (nullable FK) lets a dsynth
run be associated with a campaign if one was started. Hooks pass the
`build_run_id` via env var (`DPORTSV3_BUILD_RUN_ID`) when CI/manual
orchestration set it; otherwise NULL.

#### Tracker write paths (none)

Tracker is read-only. The UI's two write actions go directly to
artifact-store from the browser (or proxied through tracker as
transparent HTTP — taste call, no architectural difference):

- `POST /v1/user-context` (operator types a hint)
- `POST /v1/jobs/enqueue/pr` (operator clicks "open PR" after rebuild_ok=true)

These are new artifact-store endpoints (today they live in state-server).
The body shape is identical; same SQLite rows are written.

#### Hooks and tracker

The dsynth hooks already write to artifact-store. We optionally have
them *also* call `dportsv3 tracker record-result` (or its
equivalent — once schemas are unified we can just have artifact-store
update `build_results` from the hook's existing POST). That populates
cross-build dashboards as a side effect of normal agentic activity,
no separate hook set needed.

The "start a build_run" step (today: `dportsv3 tracker start-build`)
stays as a CLI command for the operator/CI to run before kicking off
a dsynth campaign. Hooks consume the resulting `build_run_id` via env
var.

#### What gets built

- `scripts/artifact-store`:
  - Add `build_runs`, `build_results`, `port_status`, `build_types`
    table creation on startup (lifted verbatim from `tracker/db.py`)
  - Add `POST /v1/user-context` and `POST /v1/jobs/enqueue/pr` write
    endpoints (lifted from state-server)
  - Add `POST /v1/build-results/upsert` (or extend `bundle-upsert`) so
    hooks populate `build_results` while writing the bundle
- `scripts/generator/dportsv3/tracker/`:
  - `db.py` — switch to read-only connection against `state.db`; drop
    write helpers; keep read queries (now using merged schema)
  - `server.py` — gain the agentic read endpoints (`/api/runs`,
    `/api/jobs`, `/api/bundles`, `/api/events` SSE) and HTML views
    (`/builds/{run_id}/bundles/{bundle_id}`, `/jobs`, `/runner`)
  - `client.py` — retire (or trim to the CLI's needs only)
  - `commands/tracker.py` — `serve` subcommand now points at
    `state.db` instead of `tracker.db`; `start-build` and friends
    keep working (they POST to artifact-store now, not directly to
    a DB they own)
- `scripts/state-server`: delete (1369 lines)
- `scripts/state-server-ui/`: delete (~2580 lines), replaced with
  tracker jinja templates
- `scripts/dsynth-hooks/hook_common.sh`: optionally extend to call
  the new `build_results` write endpoint on artifact-store when
  `DPORTSV3_BUILD_RUN_ID` is set

#### Migration concerns

- **`tracker.db` data:** the file currently holds ~38k rows of test
  data. Drop it; not production data. If we ever want it, a one-shot
  `sqlite3` ATTACH + INSERT-SELECT migration takes 10 minutes.
- **artifact-store schema bump:** add the four tables. SQLite is
  permissive about adding tables to an existing DB; no destructive
  migration.
- **Tracker UI is currently jinja-based already** (`/`, `/target/{target}`,
  `/builds`, `/builds/{run_id}`, `/diff`) — extending it with bundle
  detail + jobs queue + runner views fits its existing pattern.

#### Verification

- After Phase 4: only `artifact-store` and `tracker serve` are
  running. `state-server` and `state-server-ui` are gone.
- `tracker serve --db /path/to/state.db` opens state.db read-only,
  serves the unified dashboard (cross-build + per-failure views).
- A new dsynth failure produces a bundle visible in tracker's UI
  within one poll/SSE tick.
- A `build_run` started via `dportsv3 tracker start-build` shows up
  in tracker's dashboard, and any bundles produced during that run
  (with `DPORTSV3_BUILD_RUN_ID` exported) are linked to it.
- Tracker process has zero write paths to the DB
  (verify with `lsof` / `strace` — only reads on state.db).

---

## Critical files reference

| File | Role | Phase |
|---|---|---|
| `scripts/tools/dev-env/dports_dev_env/cli.py` | `exec` subcommand | 1 (shipped) |
| `scripts/tools/dev-env/dports_dev_env/session.py` | `prepare()` + `exec_command()` | 1 (shipped) |
| `scripts/tools/dev-env/dports_dev_env/helpers.py` | `build_env_dict()` | 1 (shipped) |
| `scripts/apply-patch` | strip platform-bifurcation + SSH; route sync+rebuild through `dev-env exec` | 2 |
| `scripts/agent-queue-runner` | delete dead `process_apply_job`; later replace opencode calls with harness module | 2, 3 |
| `docs/AGENTIC_BUILDS.md` | reword line 758 to drop `apply-patch` deprecation language | 2 |
| New: `scripts/agent_harness/` (or inlined) | `llm.py` / `tools.py` / `loop.py` / `prompts.py` / `triage.py` / `patch.py` — Python + litellm | 3 |
| (Phase 4 files TBD until A/B/C decision) | tracker server/db/client OR new FastAPI daemon | 4 |

### Code that goes away (firm)

| File | LOC | Phase | Notes |
|---|---|---|---|
| `process_apply_job` in `scripts/agent-queue-runner` | 12 | 2 | Dead code, never dispatched |
| `scripts/apply-patch` SSH + safe-clone helpers | ~150 | 2 | Replaced with `dev-env exec` calls |
| `config/opencode/tool/dports.ts` | 256 | 3 | TS plugin retired |
| `config/opencode/agent/dports-triage.md` | ~50 | 3 | Moves to Python template |
| `config/opencode/agent/dports-patch.md` | ~80 | 3 | Moves to Python template |
| `scripts/agentic-worker` | 596 | 3 | No client left after TS plugin retires |
| opencode HTTP plumbing in `agent-queue-runner` | ~200 | 3 | Replaced by harness module |

### Code that goes away in Phase 4

| File | LOC | Notes |
|---|---|---|
| `scripts/state-server` | 1369 | Functionality moves into tracker (read API, SSE, HTML views) |
| `scripts/state-server-ui/app.js` | 1983 | Replaced by jinja templates + HTMX in tracker |
| `scripts/state-server-ui/{app.css,index.html}` | ~400 | Replaced by tracker templates |
| `tracker.db` (data file) | — | Drop; was test-data only |

### Code that survives but gets touched

| File | Change |
|---|---|
| `scripts/artifact-store` | +4 tables (build_runs, build_results, port_status, build_types) on startup; +2 write endpoints (user-context, enqueue-pr); optionally +1 endpoint (build_results upsert) |
| `scripts/artifact-store-client` | Possibly +helpers for the new endpoints |
| `scripts/generator/dportsv3/tracker/db.py` | Switch to read-only against state.db |
| `scripts/generator/dportsv3/tracker/server.py` | Add agentic read endpoints + HTML views + SSE |
| `scripts/generator/dportsv3/tracker/client.py` | Trim to CLI needs only |
| `scripts/generator/dportsv3/commands/tracker.py` | `serve` points at state.db; `start-build` etc. POST to artifact-store |
| `scripts/dsynth-hooks/hook_common.sh` | Optionally call build_results upsert when `DPORTSV3_BUILD_RUN_ID` is set |

### Total LOC reduction

- **Phase 2 + 3:** ~1350 LOC retired + opencode runtime removed
- **Phase 4:** ~4400 → ~1500 (state-server + SPA replaced by jinja in tracker), so ~2900 net
- **Combined:** ~4250 LOC retired across all three phases, plus the opencode runtime + TypeScript plugin removed entirely

---

## End-to-end verification (after all phases)

1. `dportsv3 dev-env create --name e2e --target @main`
2. `dportsv3 dev-env exec e2e -- regen` (Phase 1 — already passes)
3. **Phase 2:** `apply-patch --bundle BUNDLE --env e2e` produces
   `branch.txt`, `commit.txt`, `rebuild_status.txt`, `pr_url.txt` in
   the bundle without any SSH activity; no IS_DRAGONFLY / VM_SSH_*
   code paths involved
4. **Phase 3:** point dsynth at the hooks, fail a known port; the
   runner picks up the triage job, calls litellm with
   `DP_HARNESS_TRIAGE_MODEL`, parses Classification + Confidence
5. Auto-enqueued patch job runs through the Python tool loop;
   `rebuild_proof.json` with `rebuild_ok=true` lands in the bundle
6. `pgrep opencode` empty; `ls config/opencode/` empty;
   `git grep -E 'agentic-worker|dports\.ts' scripts/` empty
7. **Phase 4:** Only `artifact-store` and `tracker serve` running.
   `pgrep state-server` empty. Tracker UI shows both cross-build
   dashboards and per-failure bundle detail, all sourced from
   `state.db`. New dsynth failures appear in the tracker dashboard
   within one SSE tick. Tracker process has no DB write paths
   (verify via `lsof` / `strace` — only reads on state.db).
