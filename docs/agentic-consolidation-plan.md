# Phase 3 — Replace opencode with a Python harness

## Status of adjacent phases

- **Phase 1** (`dportsv3 dev-env exec`) — shipped.
- **Phase 2** (retire `apply-patch` + dead `process_apply_job`) — shipped.
- **Phase 4** (tracker absorbs state-server, one DB) — deferred, not in scope here.
- **Phase 5** (tracker UI redesign) — deferred, not in scope here.

## Context

Today's agentic loop has a TypeScript dependency (`opencode` runtime + the
`config/opencode/tool/dports.ts` plugin) sitting between
`agent-queue-runner` and the actual tool execution. The runner POSTs a
markdown prompt to `opencode serve`, opencode invokes the TS plugin's
tools, each tool SSHes into a builder VM and runs
`scripts/agentic-worker` subcommands, the worker prints JSON, the TS
plugin returns it to opencode, opencode finalizes the assistant message,
the runner parses the response.

That chain is brittle and over-engineered for the deployment target:
everything is supposed to run natively on DragonFly, not split across a
Linux opencode host and a DragonFly VM. The TypeScript piece is the
only non-Python runtime in the stack. Provider choice (opencode.ai/zen
free models, NVIDIA NIM, Anthropic direct) is locked behind opencode's
own provider abstraction.

Phase 3 replaces that path with a Python harness that lives inside the
generator package, calls LLM providers through `litellm`, dispatches
tool calls in-process, and runs an iterative apply-rebuild loop with
budget enforcement. Snippet rounds collapse into the same harness call.
Provider choice becomes an env-var. Trust-tier policy from a JSON
config decides which failures auto-iterate vs. stop at triage.

## Scope

**In scope**
- New Python package `scripts/generator/dportsv3/agent/` containing the
  harness, tool registry, and policy.
- Refactor of `scripts/agentic-worker` into a module + thin CLI wrapper.
- Replace opencode-specific code paths in `scripts/agent-queue-runner`
  with calls into the new package.
- Delete `config/opencode/` (TS plugin + agent markdown).
- Trust-tier + token/iteration budget policy in
  `config/agentic-policy.json`.

**Out of scope (and actively removed)**
- Branching, commits, push, `gh pr create`. The loop is purely local
  in the dev-env's writable overlay. `process_pr_job` and the
  `type=pr` dispatch arm in `agent-queue-runner` are deleted as
  part of Phase 3 step 2; the state-server's `/enqueue/pr` endpoint
  stays alive only as long as state-server itself does (Phase 4
  removes it). Promoting a fix into a real PR is a manual,
  outside-the-loop operator step using their own DeltaPorts clone.
- The "agentic-workspace" concept (`/build/synth/agentic-workspace/`,
  `workspace.json`, pinned `FPORTS/`, materialized `DPorts/`). All
  of it dies; the dev-env's writable overlay replaces it.
- "Safe clone" / separate isolated checkouts. Dev-env's writable
  copy-on-write overlay is the isolation primitive.
- Tracker UI changes (Phase 5).
- DB consolidation (Phase 4).

## Loop philosophy

The runner orchestrates *between* jobs (triage → patch). The harness
orchestrates *within* a job: tool calls inside one attempt, attempts
inside one patch job, snippet rounds inside one triage call.

```
[ failure → hook → bundle ]
        ↓
[ triage job ]
   harness.triage.run(payload):
     loop up to N snippet rounds:
       LLM call (no tools)
       if response has ## Snippet Requests:
         snippet-extractor → append snippets → continue
       else: stop
   → (classification, confidence, response_text, usage)
   policy.tier_for(classification, confidence) → AUTO | ASSIST | MANUAL
   if AUTO or ASSIST: auto-enqueue patch job
   if MANUAL:         stop after triage
        ↓
[ patch job ]
   harness.patch.run(payload, tier):
     for attempt in range(tier.max_iterations):
       tool_loop:
         while response.tool_calls:
           dispatch each tool (env_verify, get_file, put_file,
             materialize_dports, extract, dupe, genpatch,
             install_patches, emit_diff, grep, dsynth_build)
           append tool_result, re-call LLM
       parse Rebuild Proof JSON from final response
       if rebuild_ok: success → break
       if tokens_used >= tier.max_tokens: budget_exhausted → break
       append failure context for next attempt
   → write rebuild_proof.json + analysis/changes.diff + audit log to bundle
   → job marked success | needs-help | budget-exhausted
```

The patch flow does not commit, branch, push, or open a PR. The
dev-env's writable overlay holds the dirty edits; `emit_diff` is the
audit trail.

## Trust-tier + budget

`config/agentic-policy.json` (new, top-level config):

```json
{
  "tiers": {
    "AUTO":   {"max_iterations": 2, "max_tokens": 30000},
    "ASSIST": {"max_iterations": 4, "max_tokens": 120000},
    "MANUAL": {}
  },
  "classification_to_tier": {
    "plist-error":         "AUTO",
    "fetch-checksum":      "AUTO",
    "pkg-format":          "AUTO",
    "compile-error":       "ASSIST",
    "patch-error":         "ASSIST",
    "link-error":          "ASSIST",
    "configure-error":     "ASSIST",
    "missing-dep":         "MANUAL",
    "fetch-error":         "MANUAL",
    "runtime-error":       "MANUAL",
    "dependency-conflict": "MANUAL",
    "unknown":             "MANUAL"
  },
  "confidence_floor": {"AUTO": "high", "ASSIST": "medium"}
}
```

`confidence_floor` downgrades the tier if the triage LLM's reported
confidence is below the floor (AUTO with `low` confidence → ASSIST;
ASSIST with `low` → MANUAL).

`tier.max_tokens` is summed across every `response.usage.total_tokens`
within the patch job (litellm normalizes this field across providers).
`tier.max_iterations` is the outer attempt count, not the inner tool-
loop turn count.

`MANUAL` means: triage runs, no patch job is auto-enqueued. An operator
can still hand-fire a patch job; the policy file is a default, not a
hard lock.

## Module layout

`scripts/generator/dportsv3/agent/` (new package, sibling to
`dportsv3.tracker`):

| File | Role |
|---|---|
| `__init__.py` | empty marker |
| `llm.py` | `complete(messages, tools=None, model=..., api_base=..., api_key=...)` wrapping `litellm.completion`. Returns normalized response with `text`, `tool_calls: list[{id, name, arguments}]`, `usage: {prompt_tokens, completion_tokens, total_tokens}`. |
| `prompts.py` | `TRIAGE_SYSTEM`, `PATCH_SYSTEM` — system prompt strings, lifted verbatim from `config/opencode/agent/dports-{triage,patch}.md` (YAML frontmatter stripped). |
| `policy.py` | `load_policy(path)`, `tier_for(classification, confidence) -> Tier`. Loads `config/agentic-policy.json`, applies `confidence_floor` downgrade. |
| `worker.py` | New module implementing the tool surface on top of `dev-env` primitives. Every function takes an `env: str` (dev-env name) as its first arg. Host-side filesystem operations work on `env_dir/writable/...`; chroot-bound commands shell out to `dportsv3 dev-env exec ENV -- ...`. No git operations — the dev-env's writable overlay is the workspace, and dirty edits stay dirty (no branching, no commits, no push). Functions: `env_verify(env)`, `get_file(env, path)`, `put_file(env, path, content, expected_sha256=None)`, `emit_diff(env, origin, relpath)`, `grep(env, pattern, path, include=None, max_bytes=8192)`, `materialize_dports(env, origin)`, `extract(env, origin)`, `dupe(env, path)`, `genpatch(env, path)`, `install_patches(env, origin, patches=None)`, `dsynth_build(env, origin)`. |
| `tools.py` | Tool registry. Maps tool name → (Python function in `worker.py`, JSON schema). 11 entries. The `env` arg is bound by `patch.py` before tool invocation (not exposed to the LLM); the LLM sees a tool surface that operates against "the env" implicitly. Schemas generated from inspecting function signatures (stdlib `inspect` + manual JSON schema strings — no extra deps). |
| `tool_loop.py` | `run(messages, tools, model, ...)` — multi-turn driver: call LLM, if `tool_calls` is non-empty dispatch each via `tools.dispatch`, append `tool` messages, re-call. Stops when LLM returns text-only response. Returns `(final_response, accumulated_usage)`. |
| `attempt_loop.py` | `run(payload, tier, env)` — outer loop. Each iteration: copy `messages = [system, user]`; call `tool_loop.run`; parse `## Rebuild Proof (JSON)` from response; if `rebuild_ok` true → return success; else if `usage.total >= tier.max_tokens` → return budget_exhausted; else append failure summary + latest dsynth log tail to messages and retry. Caps at `tier.max_iterations`. Returns `(final_response, usage, attempts, status)` where status ∈ `{success, needs-help, budget-exhausted}`. |
| `snippets.py` | Thin wrapper that runs `scripts/snippet-extractor` as a subprocess for a list of snippet request specs, returns the extracted text + metadata. Used by `triage.py`. |
| `triage.py` | `run(payload, env) -> TriageResult`. Single-LLM-call flow with **snippet rounds folded in-process**: loop up to `DP_HARNESS_MAX_SNIPPET_ROUNDS` (default 5) re-calling the triage LLM with snippets appended each time the response contains `## Snippet Requests`. Writes `snippets/round_N/` directories to the bundle for audit. Returns parsed `classification`, `confidence`, raw response text, accumulated `usage`. |
| `patch.py` | `run(payload, tier, env) -> PatchResult`. Wraps `attempt_loop.run`. Returns the final response + audit log. |

`scripts/generator/pyproject.toml` gets:

```toml
[project.optional-dependencies]
agent = ["litellm"]
```

`litellm`'s only Rust-built transitive dep is `pydantic-core`, already
satisfied by `py311-pydantic-core` via the generator venv's
`--system-site-packages`.

## Worker module on top of dev-env

Today, `scripts/agentic-worker` is a 596-line standalone script that
manages its own "workspace" at `/build/synth/agentic-workspace/`
(separate `DeltaPorts/`, `FPORTS/`, `DPorts/`, pinned via
`workspace.json`). It is a second isolation primitive that exists in
parallel with `dev-env`. The TS plugin SSHes to it.

`dev-env` is the better primitive: chroot + writable copy-on-write
overlay + per-target FPORTS pinning + `compose`-based materialization
+ existing helpers (`reapply`, `dbuild`). Phase 3 converges on it.

**The whole workspace concept retires.** `scripts/agentic-worker`,
`/build/synth/agentic-workspace/`, and `workspace.json` all go away.
The patch agent's tool surface is reimplemented in
`dportsv3.agent.worker` against `dev-env`, with the dev-env's
writable overlay serving as the agent's edit scratch space.

**No branching, no commits, no push, no PR.** The dev-env's writable
overlay holds the agent's dirty edits. `rebuild_proof.json` records
whether dsynth liked them; `analysis/changes.diff` captures what
changed (via `git -C env_dir/writable/work/DeltaPorts diff`) for
operator audit. If the operator wants to promote a successful fix
into a real PR, they do that manually, outside the loop, using their
own clone — not via this system.

**Prerequisite: two new dev-env subcommands.**

Step 2 needs a clean way to query env state and resolve paths from
the harness. Two small additions to `scripts/tools/dev-env/dports_dev_env/cli.py`:

- `dportsv3 dev-env status NAME` — prints a single JSON line, e.g.
  `{"name": "foo", "target": "@main", "origin": "editors/vim", "status": "ready", "backend": "chroot", "root_mounted": true, "env_dir": "/var/cache/dports-dev/foo"}`. Backed by `EnvironmentStore.load` + `mounts.mounts_under`. ~15 lines including the parser.
- `dportsv3 dev-env path NAME [--writable]` — prints `env_dir` (default) or `env_dir/writable` (with `--writable`). ~10 lines.

Both are pure reads. Both serve as the harness's interface to dev-env
state; the harness never re-parses dev-env's internal config or
filesystem layout.

**Function → primitive mapping**

| Worker function | Implementation |
|---|---|
| `env_verify(env)` | `dportsv3 dev-env status NAME` (parse JSON); fail if status ≠ `ready` or `root_mounted` is false; check the target matches what the bundle expects. |
| `materialize_dports(env, origin)` | `subprocess.run(["dportsv3", "dev-env", "exec", env, "--", "reapply", origin])` — `reapply` is the existing helper at `scripts/tools/dev-env/dports_dev_env/helpers.py:32-57`, wrapping `dportsv3 compose`. |
| `extract(env, origin)` | `dev-env exec env -- make -C /work/DPorts/<origin> extract`. |
| `dsynth_build(env, origin)` | `dev-env exec env -- dbuild <origin>` — `dbuild` helper at `helpers.py:62-90` already does `dsynth -p $DPORTS_DSYNTH_PROFILE build`. |
| `dupe(env, path)` | `dev-env exec env --cwd <wrksrc> -- dupe <path>`. |
| `genpatch(env, path)` | `dev-env exec env --cwd <wrksrc> -- genpatch <path>`. |
| `install_patches(env, origin, patches)` | Host-side file copy from the env's `genpatch-out/` into `<env_dir/writable>/work/DeltaPorts/ports/<origin>/dragonfly/`. `env_dir` from `dportsv3 dev-env path NAME --writable`. |
| `get_file(env, path)` | Host-side read from `<env_dir/writable>/<path>`. Returns content + sha256. |
| `put_file(env, path, content, expected_sha256=None)` | Host-side write to `<env_dir/writable>/<path>`. Optimistic-lock check against `expected_sha256` if provided. |
| `emit_diff(env, origin, relpath)` | Host-side `git -C <env_dir/writable>/work/DeltaPorts diff -- ports/<origin>/<relpath>`. Pure read, no commits made. |
| `grep(env, pattern, path, include, max_bytes)` | Host-side `rg` on `<env_dir/writable>/<path>`. |

The harness caches the result of `dportsv3 dev-env path NAME --writable`
once per job to avoid the subprocess hop on every tool call.

**Side effect: `process_pr_job` goes away.**

Phase 2 left `process_pr_job` in place "for manual use." With "no PR
ever" locked in, it's dead weight. Delete it from
`agent-queue-runner` along with the `type=pr` dispatch arm. The
state-server's `/enqueue/pr` endpoint stays alive (the state-server
itself dies in Phase 4 anyway); enqueueing a `type=pr` job becomes a
no-op that the runner rejects as "unknown job type."

## Concrete edits to `agent-queue-runner`

Line numbers below are current-tree (HEAD).

| Lines | Action |
|---|---|
| 17-21 (docstring `VM_SSH_*` block) | **Delete.** Harness runs natively on dfly; no SSH. |
| 71-73 (`DEFAULT_VM_SSH_KEY`/`PORT`/`HOST`) | **Delete.** Same. |
| 76 (`DEFAULT_WORKSPACE_CONFIG`) | **Delete.** Workspace concept retires. |
| 167 (`workspace.json` loader fn) | **Delete.** Same. |
| 721-761 (snippet-extractor SSH dispatch) | **Delete.** Snippet rounds fold into `dportsv3.agent.triage` (in-process); the harness calls `scripts/snippet-extractor` locally via `dportsv3.agent.snippets`. |
| 1024 (`OPENCODE_MAX_SNIPPET_ROUNDS`) | Rename → `DP_HARNESS_MAX_SNIPPET_ROUNDS`. |
| 999-1050 (`check_and_handle_snippet_requests`) | **Delete.** Snippet rounds fold into `dportsv3.agent.triage`. |
| 956-997 (`enqueue_followup_job`) | **Keep.** Still used by triage → patch auto-enqueue. |
| 1057-1170 (`build_triage_payload`) | **Keep.** Same markdown payload, consumed by the new harness. |
| 1173-1293 (`build_patch_payload`) | **Keep.** Same. |
| 1338-1395 (`call_opencode`) | **Delete.** Triage and patch jobs call `dportsv3.agent.triage.run` / `dportsv3.agent.patch.run` directly. |
| 1398-1419 (`extract_response_text`) | **Delete.** Harness returns clean text. |
| 1430-1437 (`extract_json_block`) | **Keep.** Used to parse JSON blocks from response. |
| 1444-1473 (`write_triage_outputs`) | **Keep.** Bundle output layout unchanged. |
| 1476-1537 (`write_patch_outputs`) | **Keep.** Same. |
| 1638-1746 (`process_triage_job`) | Trim: drop the snippet re-enqueue branch. Replace `call_opencode(...)` with `dportsv3.agent.triage.run(payload, env)`. After parsing classification/confidence, call `dportsv3.agent.policy.tier_for(...)` to decide auto-enqueue: AUTO/ASSIST → `enqueue_followup_job(patch, ...)`, MANUAL → stop. |
| 1749-1834 (`process_patch_job`) | Trim: drop snippet re-enqueue branch. Replace `call_opencode(...)` with `dportsv3.agent.patch.run(payload, tier, env)`. Store `tokens_used`, `attempts`, `status` from the returned audit alongside the existing `rebuild_proof.json`. |
| 1851-1960 (`process_pr_job`, incl. line 1873 `deltaports_path` workspace ref) | **Delete.** "No PRs, no branches, no push" — the loop is purely local. |
| dispatch table (`type == "pr"` arm) | **Delete.** `type=pr` becomes "unknown job type" if anything still enqueues one. |
| 2086-2095 (`OPENCODE_*` env reads) | **Delete.** Replace with `DP_HARNESS_*` reads scoped to the new harness. |

## Env vars

New:
- `DP_HARNESS_TRIAGE_MODEL` — litellm model string, e.g.
  `openai/gpt-5-nano` or `openai/MODEL` paired with `_API_BASE` for
  opencode.ai/zen.
- `DP_HARNESS_PATCH_MODEL` — e.g. `anthropic/claude-sonnet-4`.
- `DP_HARNESS_TRIAGE_API_BASE`, `DP_HARNESS_PATCH_API_BASE` — optional
  custom endpoints.
- `DP_HARNESS_TRIAGE_API_KEY`, `DP_HARNESS_PATCH_API_KEY` — provider
  keys; fall back to provider's standard env var if unset.
- `DP_HARNESS_MAX_SNIPPET_ROUNDS` — default 5.

Retired: every `OPENCODE_*` env var in `agent-queue-runner` (lines
2086-2095 + line 1024).

## Files deleted

| Path | LOC | Notes |
|---|---|---|
| `config/opencode/tool/dports.ts` | 256 | TS plugin retires |
| `config/opencode/agent/dports-triage.md` | ~50 | Prompt body moves to `dportsv3.agent.prompts` |
| `config/opencode/agent/dports-patch.md` | ~80 | Same |
| `call_opencode` + `extract_response_text` + `check_and_handle_snippet_requests` + `OPENCODE_*` env reads in `agent-queue-runner` | ~160 | Replaced by harness module |
| `VM_SSH_*` constants, env reads, SSH snippet dispatch, docstring header lines in `agent-queue-runner` | ~50 | Harness runs natively on dfly; no SSH |
| `workspace.json` loader + `DEFAULT_WORKSPACE_CONFIG` in `agent-queue-runner` | ~15 | Workspace concept retires |
| `scripts/agentic-worker` | 596 | Workspace concept retires; functions reimplemented on top of dev-env in `dportsv3.agent.worker` |
| `process_pr_job` + `type=pr` dispatch arm in `agent-queue-runner` | ~80 | No branching, no PR — the loop is purely local |
| `/build/synth/agentic-workspace/` (runtime data) | — | Dev-env writable overlays replace it |

## Implementation order

Each step is independently testable; ship them as separate commits.

1. **Scaffold + triage flow.** Add `dportsv3.agent.{llm, prompts,
   policy, snippets, triage}` and the `agent` extra to
   `pyproject.toml`. Wire `process_triage_job` to call
   `dportsv3.agent.triage.run`. Verify Classification + Confidence on
   a known failing bundle match what opencode produced for the same
   payload.

2. **Worker on top of dev-env.** First add the two prerequisite
   dev-env subcommands (`dportsv3 dev-env status NAME` JSON output,
   `dportsv3 dev-env path NAME [--writable]`) — ~25 LOC in
   `scripts/tools/dev-env/dports_dev_env/cli.py`. Then write
   `dportsv3.agent.worker` as 11 Python functions that delegate to
   dev-env primitives: subprocess `dportsv3 dev-env exec ENV -- CMD`
   for chroot ops, host-side filesystem ops on `env_dir/writable/...`
   for file/diff/grep. No git operations. Delete
   `scripts/agentic-worker` (596 LOC), `process_pr_job` + the
   `type=pr` dispatch arm (~80 LOC), and anything under
   `/build/synth/agentic-workspace/`. Verify by running each function
   against a real `dev-env`: `dsynth_build` on a small port produces
   a buildable output; `materialize_dports` regenerates the origin's
   DPorts tree; `emit_diff` returns the working-tree diff after a
   `put_file` edit.

3. **Tools + tool loop.** Add `dportsv3.agent.{tools, tool_loop}`.
   Drive with a synthetic LLM response that calls `env_verify`
   followed by `get_file`; assert the dispatch produces the expected
   `tool` messages.

4. **Attempt loop + patch flow.** Write the `PATCH_SYSTEM` prompt
   that defines the agent's tool surface (the 11 tools from step 2)
   and pins the new `rebuild_proof.json` schema:

   ```
   {
     "origin":         "category/portname",
     "rebuild_ok":     true | false,
     "dsynth_profile": "DragonFly",
     "build_command":  "dsynth -p ... build category/portname",
     "timestamp_utc":  "2026-05-18T12:00:00Z"
   }
   ```

   No `deltaports_branch`, `deltaports_head`, `fports_ref`, or
   `fports_head` fields — the loop is purely local and the env's
   target encodes any "FPORTS pinning" via the dev-env's existing
   target manifest. Then add `dportsv3.agent.{attempt_loop, patch}`.
   Wire `process_patch_job` to call `dportsv3.agent.patch.run`.
   End-to-end smoke: trigger a known-fixable port; confirm
   `rebuild_proof.json` with `rebuild_ok=true` and
   `analysis/changes.diff` (from `emit_diff`-style host-side
   `git diff`) land in the bundle.

5. **Trust-tier dispatch + budget enforcement.** Add
   `config/agentic-policy.json`. `process_triage_job` consults
   `policy.tier_for` to auto-enqueue patch only for AUTO/ASSIST.
   Verify budget enforcement: an unfixable port terminates with
   `budget-exhausted`; `tokens_used` in audit equals sum of
   `response.usage.total_tokens` across attempts.

6. **Retire opencode + VM_SSH + workspace cruft.** Delete
   `config/opencode/`, `call_opencode` family of functions,
   `OPENCODE_*` env reads, the `VM_SSH_*` constants + env reads + SSH
   snippet dispatch, the `workspace.json` loader + default constant,
   and the docstring header lines that mention them. Confirm
   `pgrep opencode` empty and
   `git grep -E 'opencode|OPENCODE_|VM_SSH|workspace\.json|agentic-workspace' -- scripts/ config/`
   returns nothing live.

## Verification

End-to-end on a known-fixable port:

1. Hook fires on dsynth failure → bundle written to artifact-store.
2. `agent-queue-runner` picks up triage job; `dportsv3.agent.triage.run`
   calls litellm with `DP_HARNESS_TRIAGE_MODEL`, runs snippet rounds
   in-process (audit files in `snippets/round_N/` appear without any
   re-enqueue traffic in `pending/`), returns classification +
   confidence.
3. `policy.tier_for` resolves a tier; runner auto-enqueues patch only
   if tier ∈ {AUTO, ASSIST}.
4. Patch job: `dportsv3.agent.patch.run` invokes `attempt_loop`. Each
   attempt runs the tool loop (`env_verify` → `materialize_dports` →
   `extract` → `dupe`/`genpatch`/`put_file` edits → `install_patches`
   → `dsynth_build`), parses `## Rebuild Proof (JSON)` from the final
   response. Stops on `rebuild_ok=true` or budget exhaustion.
5. `rebuild_proof.json` + per-attempt audit (`tokens_used`,
   `attempts`, `status`) + `analysis/changes.diff` (host-side
   `git -C env_dir/writable/work/DeltaPorts diff`) written to bundle.
   No commits made, no branch created, no push, no PR.

Negative checks:

- `pgrep opencode` empty.
- `git grep -E 'opencode|OPENCODE_' -- scripts/ config/` returns
  nothing live.
- `git grep -E 'agentic-worker|workspace\.json|process_pr_job|type.?=.?.?pr' -- scripts/`
  returns nothing live.
- `ls /build/synth/agentic-workspace/` returns no such directory (or
  is orphaned cruft).
- LiteLLM model swappable via env var: `openai/gpt-5-nano` ↔
  `nvidia_nim/meta/llama-3.1-70b-instruct` ↔
  `anthropic/claude-sonnet-4` without code changes.
