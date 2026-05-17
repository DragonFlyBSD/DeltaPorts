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

- **Retire `apply-patch` entirely.** Its responsibilities split: the
  iterative apply+rebuild loop is the Phase 3 harness's job; the
  PR-creation step (out of scope for now) already lives in
  `process_pr_job` in `agent-queue-runner`. Bundles are internal
  artifacts; no external "fix from disk" entry-point is needed.
- **Replace opencode with a Python litellm harness, locally
  iterating.** Drops the only TypeScript piece and the only non-Python
  runtime. Tools become Python functions that call `dev-env exec`;
  `agentic-worker` dies; markdown agent configs become Python prompt
  templates. The harness owns intra-job iteration with a **trust-tier
  policy + budget** (max iterations + max tokens). No branching/PR
  in scope; the loop ends at a local `rebuild_proof.json`.
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

### Phase 2 — Retire `apply-patch`

**Pure deletion. No new code.**

`scripts/apply-patch` is a 666-line standalone CLI that bundles seven
responsibilities into one tool: read a bundle on disk → safe-clone →
git apply + commit → sync to DPorts → dsynth rebuild → git push →
`gh pr create` → write artifacts back. The re-read showed that *every
one of those responsibilities either already lives elsewhere or is out
of scope for the rebuilt loop*:

| Responsibility today | Where it lives after Phase 3 |
|---|---|
| Apply patch + commit | Phase 3 patch agent's tool surface (`put_file`, `install_patches`, `commit`) — iterative, in the harness |
| Sync DeltaPorts → DPorts | Already in `dportsv3 compose` (called inside the env via the `reapply` helper at `helpers.py:32-57`) |
| dsynth rebuild | Phase 3 patch agent's `dsynth_build` tool (uses the `dbuild` helper at `helpers.py:62-90`) |
| Git push + `gh pr create` | `process_pr_job` in `agent-queue-runner:1851`, dispatched on `type=pr` — keep, unchanged, out of scope |
| Read bundle from disk as external entry | Out of scope — bundles are internal artifacts; no human-runs-script-against-disk path is needed |

`process_apply_job` at `agent-queue-runner:1837-1848` is an 11-line
dead stub (the body literally returns `False, "apply job deprecated"`).
Never dispatched. Tombstone-only.

`grep` confirms zero callers of `apply-patch` anywhere in the repo
(`scripts/`, hooks, opencode configs, docs prose).

**Deleted**
- `scripts/apply-patch` (666 LOC)
- `process_apply_job` in `scripts/agent-queue-runner` (lines 1837-1848, ~12 LOC)

**Updated**
- `docs/AGENTIC_BUILDS.md:758` — reword the paragraph that mentions
  `apply-patch` as the deprecated path. The replacement language: the
  Phase 3 harness's tool loop is the only patch path; PR creation is
  a separate downstream step (`process_pr_job`) and is intentionally
  out of scope of the iterative loop.

**Out of scope (explicitly)**
- Branching, push, PR creation. These remain available via the
  existing `process_pr_job` path triggered manually (state-server
  "Enqueue PR" button or curl to `/enqueue/pr`), but the Phase 3
  loop does **not** drive them. The iterative process ends at a
  local `rebuild_proof.json` on the bundle.
- Any "fix from a disk-resident bundle" external tooling. Not built;
  not replaced.

**Verification**
- `git grep -E 'apply-patch|apply_patch|process_apply_job' -- scripts/ docs/`
  returns nothing live (historical commit messages don't count).
- The active loop (hook → triage → patch via harness → `rebuild_proof.json`)
  continues to function with `apply-patch` removed.
- `process_pr_job` is still reachable: enqueue a `type=pr` job by hand
  for a bundle with `rebuild_ok=true`, and the runner still pushes +
  opens the PR. Untouched.

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
- litellm responses expose `response.usage.{prompt_tokens,
  completion_tokens, total_tokens}` for every provider — token-budget
  enforcement is a per-turn check, not a provider-specific gadget.
- The agent-queue-runner already does most of the harness work
  (payload assembly, response parsing, retry/log/state writes); we're
  replacing one HTTP call + one tool-dispatch indirection, not
  rebuilding from scratch.

**Loop philosophy: trust-tier + budget, ends locally**

The harness owns iteration *within one patch job*. The runner owns
orchestration *across jobs* (triage → patch). PR creation (`type=pr`
→ `process_pr_job`) is out of scope of this iterative loop and is
not driven by the harness.

1. **Trust tier from classification.** The triage step's
   `Classification` field maps to a policy tier via a config table:

   ```
   AUTO    — plist-error, fetch-checksum, pkg-format, simple-missing-file
   ASSIST  — compile-error, patch-error, link-error, configure-error
   MANUAL  — runtime-error, dependency-conflict, unknown, low-confidence
   ```

   Each tier carries a budget (max iterations, max total tokens).
   The mapping lives in a single JSON file (e.g.
   `config/agentic-policy.json`) so it's adjustable without code
   changes. Defaults are conservative; tightening/loosening is an
   operator dial.

2. **Iteration is intra-job, budget-bounded.** Inside the patch
   harness, the loop is:

   ```
   for attempt in range(tier.max_iterations):
       run tool loop → patch agent emits final response + tool results
       run dsynth_build → rebuild_proof.json
       if rebuild_ok:                   break (success)
       if tokens_spent >= tier.max_tokens: break (over budget)
       append failure output to context; next attempt
   write final rebuild_proof.json + audit log to bundle
   mark job: success | needs-help | budget-exhausted
   ```

   Each attempt commits the agent's edits to the env's writable
   overlay (host-side, via `git -C env_dir/writable/work/DeltaPorts`).
   On failure-then-retry, the agent sees the previous attempt's diff
   and the new dsynth log as input.

3. **Tier behaviour:**
   - `AUTO`: harness runs the loop unattended; success or failure is
     recorded locally; no PR step; the result waits in the tracker
     for an operator to glance at.
   - `ASSIST`: same loop, same budget, same local result — but the
     tracker surfaces it in an "needs review" view rather than a
     normal-success view. (PR creation still out of scope of the
     loop; if the operator decides to push it, they fire a `type=pr`
     job manually.)
   - `MANUAL`: triage runs, no patch job is auto-enqueued. Operator
     reads the triage, kicks off the loop by hand (or just fixes
     manually).

4. **Token + iteration accounting.** Each LLM call's `response.usage`
   is summed into a per-job `tokens_used` counter; the loop short-
   circuits when `tokens_used >= tier.max_tokens` or
   `attempt >= tier.max_iterations`. Counters land in the bundle's
   audit log alongside `rebuild_proof.json`.

5. **No PR, no push, no branch promotion within the loop.** The loop
   ends at `rebuild_proof.json` on disk. The existing `process_pr_job`
   path still works exactly as today, triggered manually, and is
   untouched.

**New code** (suggested layout: `scripts/agent_harness/` as a module
beside `agent-queue-runner`, or inlined into the runner — TBD during
implementation)
- `llm.py` — `complete(messages, tools=None, model=..., api_base=..., api_key=...)`
  wrapping `litellm.completion`. Returns a normalized response plus
  `usage.{prompt_tokens, completion_tokens, total_tokens}` for budget
  accounting.
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
- `tool_loop.py` — inner multi-turn driver: `while response.tool_calls:
  dispatch each tool → append tool_result message → re-call LLM →
  repeat`. ~30 lines. Returns final response + cumulative usage.
- `attempt_loop.py` — outer budget-bounded driver: wraps `tool_loop`
  with the per-tier `max_iterations` and `max_tokens` budget; runs
  `dsynth_build` between attempts; collects audit log per attempt;
  short-circuits on `rebuild_ok=true` or budget exhaustion. ~60 lines.
- `policy.py` — loads `config/agentic-policy.json` and maps
  triage classification → tier (`AUTO` / `ASSIST` / `MANUAL`) +
  budget (`max_iterations`, `max_tokens`).
- `prompts.py` — system prompts for `dports-triage` and `dports-patch`
  agents (lifted from `config/opencode/agent/*.md`).
- `triage.py` — single-turn triage flow (no tools, uses existing payload
  builder). Returns classification + confidence; runner consults `policy.py`
  to decide whether to auto-enqueue the patch job.
- `patch.py` — multi-turn patch flow that drives `attempt_loop`.

**New config file**
- `config/agentic-policy.json` — tier definitions + classification mapping.
  Conservative defaults:
  ```json
  {
    "tiers": {
      "AUTO":   {"max_iterations": 2, "max_tokens": 30000},
      "ASSIST": {"max_iterations": 4, "max_tokens": 120000},
      "MANUAL": {}
    },
    "classification_to_tier": {
      "plist-error": "AUTO",
      "fetch-checksum": "AUTO",
      "pkg-format": "AUTO",
      "compile-error": "ASSIST",
      "patch-error": "ASSIST",
      "link-error": "ASSIST",
      "configure-error": "ASSIST",
      "runtime-error": "MANUAL",
      "dependency-conflict": "MANUAL",
      "unknown": "MANUAL"
    },
    "confidence_floor": {"AUTO": "high", "ASSIST": "medium"}
  }
  ```
  `confidence_floor` downgrades a tier if the triage LLM's reported
  confidence is below the floor (e.g., AUTO classification with `low`
  confidence drops to ASSIST).

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
  3. `policy.py` maps classification → tier; runner auto-enqueues
     patch job only for tiers `AUTO` and `ASSIST`
  4. Patch flow's `attempt_loop` runs up to `tier.max_iterations`
     attempts within `tier.max_tokens`. Each attempt: tool loop →
     `dsynth_build` → check `rebuild_ok`. Stops on success or
     budget exhaustion.
  5. Final `rebuild_proof.json` + per-attempt audit log written to
     the bundle. Job marked `success` / `needs-help` /
     `budget-exhausted` accordingly. No PR, no push.
- Trust-tier behaviour:
  - Trigger a `plist-error` failure (AUTO tier) — runs to completion
    automatically; appears in tracker as success.
  - Trigger a `compile-error` failure (ASSIST tier) — same automation,
    but tracker surfaces it in the "needs review" view.
  - Trigger a `runtime-error` failure (MANUAL tier) — only triage
    runs; no patch job auto-enqueued.
- Budget enforcement: an artificially unfixable failure exhausts the
  budget (iterations or tokens) and terminates cleanly with
  `budget-exhausted` status. `tokens_used` value in audit log matches
  sum of per-turn `response.usage.total_tokens`.
- No opencode process running; `pgrep opencode` empty.
- LiteLLM model can be swapped (`openai/gpt-5-nano` ↔
  `nvidia_nim/meta/llama-3.1-70b-instruct` ↔ `anthropic/claude-sonnet-4`)
  via env var without code changes.
- `process_pr_job` still functions when triggered manually with a
  `type=pr` job — confirms PR path is intentionally out-of-loop, not
  broken.

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

### Phase 5 — Tracker UI redesign in the dsynth-progress aesthetic

Phase 4 replaces the state-server SPA with jinja templates inside the
tracker, but says nothing about how those pages look. This phase pulls
the visual language from the existing `www/example/` build-progress
page into the tracker so every tracker view reads as a sibling of
that page: same palette, same typography, same component vocabulary.
The reference page itself is left untouched — only its design is
borrowed.

**Reference (do not modify):** `www/example/{index.html,progress.css,progress.js}`
- Cream/parchment background (`--bg: #f7f5f0`), card surfaces white
- Amber accent (`--amber: #c47000`) for links, primary chrome, key data
- Monospace throughout (Menlo / Consolas / Courier New, 12px base)
- Status palette: `--c-built` green, `--c-failed` red, `--c-skipped`
  amber, `--c-ignored` ochre, `--c-meta` purple — each with a matching
  `*-bg` pastel
- Sticky two-row header (logo + meta + load/swap/elapsed strip, then
  stat-card grid)
- Stat cards: large numeric, monogram icon, clickable as filter
- Compact tables with hover-row highlight
- CSS-only segmented progress bar pinned to footer

**Approach: tokens + components, copied (not symlinked) into tracker.**

1. Create `scripts/generator/dportsv3/tracker/static/`:
   - `tokens.css` — lift the `:root` variable block verbatim from
     `www/example/progress.css` (palette, font stack, status colors).
     Tracker pages `@import` this first; future colour tweaks happen
     here only.
   - `base.css` — body/typography/link styling, sticky header skeleton,
     status-pill mixin, table styling, segmented progress bar.
   - `components.css` — `.stat-card`, `.status-pill`, `.preset-tag`,
     `.report-controls`, `.builders-table` reusable pieces.
   - `tracker.css` — page-specific styles (bundle detail, jobs queue,
     runner status) layered on top.
   - Assets: `favicon.png` (copy from example), no logo image
     (tracker is operator-facing, not branded).

2. Touch every existing tracker template + add the new Phase 4 ones:
   - `index.html` (target picker / dashboard) — apply tokens, replace
     ad-hoc styles with `base.css` + `components.css` classes.
   - `target.html`, `builds.html`, `build_detail.html`, `diff.html` —
     same conversion.
   - **New Phase 4 templates** (`bundle_detail.html`, `jobs.html`,
     `runner.html`, `events.html`) — designed natively in the new
     vocabulary, no legacy styling to peel off.

3. Header pattern: tracker's header gets the two-row treatment from
   the example — top row carries process info (tracker version, DB
   path, last refresh, "live" indicator from SSE); the second row is
   a stat-card grid showing roll-up counts (active builds, failed
   ports today, jobs pending, jobs failed). Each card filters the
   page below by clicking.

4. Footer pattern: the segmented progress bar (`.pb-built`, `.pb-meta`,
   `.pb-failed`, `.pb-ignored`, `.pb-skipped`) becomes the per-build
   header element on `build_detail.html` — already exactly what the
   example renders.

5. Tables: keep the example's compact monospace look (12px, tight
   padding, status pill in the result column). Hover-row highlight
   stays; status filtering becomes URL-driven (`?result=failed`)
   rather than the example's pure-JS approach, so it works with
   tracker's server-rendered jinja + HTMX.

6. JS minimization: `www/example/progress.js` is 459 lines of pure
   state polling + filtering for a single-page app. Tracker doesn't
   need most of it — server-rendered pages do the filtering. Keep
   only:
   - SSE subscriber for live counters (~30 lines)
   - HTMX swap helpers if needed (~10 lines)
   - No client-side sort/filter/pagination logic — tracker passes
     those as URL params and the server returns rendered HTML.

**Files modified**

| File | Change |
|---|---|
| `scripts/generator/dportsv3/tracker/static/tokens.css` | NEW — palette/typography variables |
| `scripts/generator/dportsv3/tracker/static/base.css` | NEW — body/header/footer/table primitives |
| `scripts/generator/dportsv3/tracker/static/components.css` | NEW — stat-card, status-pill, preset-tag |
| `scripts/generator/dportsv3/tracker/static/tracker.css` | NEW — page-specific styles |
| `scripts/generator/dportsv3/tracker/static/favicon.png` | NEW — copied from www/example |
| `scripts/generator/dportsv3/tracker/templates/*.html` | UPDATED — adopt new tokens + components |
| New Phase 4 templates | DESIGNED native in the new style |
| `scripts/generator/dportsv3/tracker/server.py` | `StaticFiles` mount at `/static/` |

**Untouched**
- `www/example/` — reference only; not deleted, not edited, not
  referenced by tracker. It remains a self-contained build-progress
  page for whatever other purpose it serves today.
- Provider colour conventions in the rest of the project — this phase
  is scoped to tracker.

**Verification**
- Every tracker URL renders with `--bg`, monospace body, amber links —
  visually indistinguishable in palette/type from `www/example/index.html`.
- Browser dev-tools "Computed" panel shows the new CSS variables
  resolving (no fallbacks).
- Stat cards on the tracker home filter the table below when clicked
  (URL changes, server re-renders).
- `www/example/index.html` opens unchanged (cross-check after the work).
- No CSS variable is defined in more than one of `tokens.css` /
  `base.css` / `components.css` (single source of truth).

---

## Critical files reference

| File | Role | Phase |
|---|---|---|
| `scripts/tools/dev-env/dports_dev_env/cli.py` | `exec` subcommand | 1 (shipped) |
| `scripts/tools/dev-env/dports_dev_env/session.py` | `prepare()` + `exec_command()` | 1 (shipped) |
| `scripts/tools/dev-env/dports_dev_env/helpers.py` | `build_env_dict()` | 1 (shipped) |
| `scripts/apply-patch` | DELETE entirely (responsibilities split: iterative loop → Phase 3 harness; PR → existing `process_pr_job`; "bundle from disk" → out of scope) | 2 |
| `scripts/agent-queue-runner` | delete dead `process_apply_job` (Phase 2); replace opencode calls with harness module + add trust-tier dispatch (Phase 3) | 2, 3 |
| `docs/AGENTIC_BUILDS.md` | reword line 758 to drop `apply-patch` deprecation language | 2 |
| New: `scripts/agent_harness/` (or inlined) | `llm.py` / `tools.py` / `tool_loop.py` / `attempt_loop.py` / `policy.py` / `prompts.py` / `triage.py` / `patch.py` — Python + litellm | 3 |
| New: `config/agentic-policy.json` | trust-tier table (AUTO/ASSIST/MANUAL) + per-tier budget + classification mapping | 3 |
| (Phase 4 files TBD until A/B/C decision) | tracker server/db/client OR new FastAPI daemon | 4 |
| `scripts/generator/dportsv3/tracker/static/{tokens,base,components,tracker}.css` | NEW design system lifted from `www/example` | 5 |
| `scripts/generator/dportsv3/tracker/templates/*.html` | Apply new tokens + components | 5 |

### Code that goes away (firm)

| File | LOC | Phase | Notes |
|---|---|---|---|
| `process_apply_job` in `scripts/agent-queue-runner` | 12 | 2 | Dead code, never dispatched |
| `scripts/apply-patch` (entire file) | 666 | 2 | Iterative legs → Phase 3 harness; PR legs → existing `process_pr_job`; bundle-on-disk entry → out of scope |
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

- **Phase 2:** ~680 LOC retired (apply-patch + process_apply_job stub)
- **Phase 3:** ~1180 LOC retired (TS plugin + agent configs + agentic-worker + opencode HTTP plumbing) plus opencode runtime gone; +~300 LOC added (harness module + policy config)
- **Phase 4:** ~4400 → ~1500 (state-server + SPA replaced by jinja in tracker), so ~2900 net
- **Combined:** ~4760 LOC retired, opencode runtime + TypeScript plugin removed entirely

---

## End-to-end verification (after all phases)

1. `dportsv3 dev-env create --name e2e --target @main`
2. `dportsv3 dev-env exec e2e -- regen` (Phase 1 — already passes)
3. **Phase 2:** `git grep -E 'apply-patch|apply_patch|process_apply_job' -- scripts/ docs/`
   returns nothing live; running the agentic loop end-to-end still
   works (apply-patch removal didn't break the active path).
4. **Phase 3 trust-tier dispatch:** point dsynth at the hooks, fail a
   known port. Runner picks up the triage job, calls litellm with
   `DP_HARNESS_TRIAGE_MODEL`, parses Classification + Confidence.
   `policy.py` maps to a tier; an AUTO-tier failure auto-enqueues
   patch, an ASSIST-tier failure also auto-enqueues but lands in the
   "needs review" tracker view, a MANUAL-tier failure stops at triage.
5. **Phase 3 budget loop:** auto-enqueued patch job runs
   `attempt_loop` up to `tier.max_iterations`. On a known-fixable
   port, `rebuild_proof.json` with `rebuild_ok=true` lands in the
   bundle within budget; audit log records `tokens_used` and
   `attempts`. On an unfixable port, terminates with
   `budget-exhausted` status.
6. `pgrep opencode` empty; `ls config/opencode/` empty;
   `git grep -E 'agentic-worker|dports\.ts' scripts/` empty
7. **Phase 4:** Only `artifact-store` and `tracker serve` running.
   `pgrep state-server` empty. Tracker UI shows both cross-build
   dashboards and per-failure bundle detail, all sourced from
   `state.db`. New dsynth failures appear in the tracker dashboard
   within one SSE tick. Tracker process has no DB write paths
   (verify via `lsof` / `strace` — only reads on state.db).
8. **Phase 5:** Every tracker URL renders in the dsynth-progress
   palette (cream bg, amber accent, monospace) sourced from
   `scripts/generator/dportsv3/tracker/static/tokens.css`. Stat
   cards on the home page filter the table below on click.
   `www/example/index.html` opens unchanged.
