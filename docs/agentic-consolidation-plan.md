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

**Out of scope**
- Branching, push, `gh pr create`. The loop ends at a local
  `rebuild_proof.json` in the bundle. The existing `process_pr_job` is
  untouched and remains an out-of-band manual step.
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
           dispatch each tool (workspace_verify, get_file, put_file,
             dupe, genpatch, install_patches, commit, dsynth_build, ...)
           append tool_result, re-call LLM
       parse Rebuild Proof JSON from final response
       if rebuild_ok: success → break
       if tokens_used >= tier.max_tokens: budget_exhausted → break
       append failure context for next attempt
   → write rebuild_proof.json + audit log to bundle
   → job marked success | needs-help | budget-exhausted
```

The patch flow does not push, branch, or open a PR.

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
| `worker.py` | Refactored body of `scripts/agentic-worker`. Each subcommand becomes a function returning a dict: `workspace_verify()`, `checkout_branch(origin)`, `commit(origin, message)`, `get_file(path)`, `put_file(path, content, expected_sha256=None)`, `emit_diff(origin, relpath)`, `grep(pattern, path, include=None, max_bytes=8192)`, `materialize_closure(origin)`, `extract(origin)`, `dupe(path)`, `genpatch(path)`, `install_patches(origin, patches=None)`, `dsynth_build(origin, profile=None)`. |
| `tools.py` | Tool registry. Maps tool name → (Python function in `worker.py`, JSON schema). 12 entries matching the existing TS plugin tool surface. Schemas generated from inspecting the function signatures (use the stdlib `inspect` module + manual JSON schema strings — no extra deps). |
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

## agentic-worker refactor

Today: `scripts/agentic-worker` is a 596-line standalone Python
script with subcommand dispatch + workspace logic mixed together.
The TS plugin SSHes to it; the runner doesn't import it.

After: same file becomes a ~40-line CLI wrapper. All function bodies
move to `dportsv3.agent.worker`. The wrapper is:

```python
#!/usr/bin/env python3.11
import argparse, json, sys
from dportsv3.agent import worker

DISPATCH = {
    "workspace-verify":     worker.workspace_verify,
    "checkout-branch":      worker.checkout_branch,
    # ... 12 entries total
}

def main():
    parser = argparse.ArgumentParser(...)
    parser.add_argument("subcommand", choices=DISPATCH)
    # ... pass remaining args through
    args, rest = parser.parse_known_args()
    fn = DISPATCH[args.subcommand]
    result = fn(**parse_kwargs(rest))
    print(json.dumps({"ok": True, "result": result}))
```

CLI stays alive so manual debugging
(`agentic-worker materialize-closure --origin editors/vim`) still
works during and after Phase 3. The runner imports
`dportsv3.agent.worker` directly; tools never go through the CLI.

## Concrete edits to `agent-queue-runner`

Line numbers below are current-tree (HEAD).

| Lines | Action |
|---|---|
| 1024 | `OPENCODE_MAX_SNIPPET_ROUNDS` → `DP_HARNESS_MAX_SNIPPET_ROUNDS` |
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

The `scripts/agentic-worker` file survives but shrinks (~556 LOC of
logic moves into `dportsv3.agent.worker`; ~40 LOC of CLI wrapper
remains).

## Implementation order

Each step is independently testable; ship them as separate commits.

1. **Scaffold + triage flow.** Add `dportsv3.agent.{llm, prompts,
   policy, snippets, triage}` and the `agent` extra to
   `pyproject.toml`. Wire `process_triage_job` to call
   `dportsv3.agent.triage.run`. Verify Classification + Confidence on
   a known failing bundle match what opencode produced for the same
   payload.

2. **Worker refactor.** Move `scripts/agentic-worker` bodies to
   `dportsv3.agent.worker`; reduce the script to a CLI wrapper. Run
   `agentic-worker materialize-closure --origin <something>` and
   confirm byte-identical JSON output before and after.

3. **Tools + tool loop.** Add `dportsv3.agent.{tools, tool_loop}`.
   Drive with a synthetic LLM response that calls
   `workspace_verify` followed by `get_file`; assert the dispatch
   produces the expected `tool` messages.

4. **Attempt loop + patch flow.** Add `dportsv3.agent.{attempt_loop,
   patch}`. Wire `process_patch_job` to call
   `dportsv3.agent.patch.run`. End-to-end smoke: trigger a known-
   fixable port, confirm `rebuild_proof.json` with `rebuild_ok=true`
   lands in the bundle.

5. **Trust-tier dispatch + budget enforcement.** Add
   `config/agentic-policy.json`. `process_triage_job` consults
   `policy.tier_for` to auto-enqueue patch only for AUTO/ASSIST.
   Verify budget enforcement: an unfixable port terminates with
   `budget-exhausted`; `tokens_used` in audit equals sum of
   `response.usage.total_tokens` across attempts.

6. **Retire opencode.** Delete `config/opencode/`, the `call_opencode`
   family of functions, `OPENCODE_*` env reads. Confirm
   `pgrep opencode` empty and `git grep -E 'opencode|OPENCODE_' --
   scripts/ config/` returns nothing live.

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
   attempt runs the tool loop (workspace_verify → checkout → … →
   dsynth_build), parses `## Rebuild Proof (JSON)` from the final
   response. Stops on `rebuild_ok=true` or budget exhaustion.
5. `rebuild_proof.json` + per-attempt audit (`tokens_used`,
   `attempts`, `status`) written to bundle. No PR, no push.

Negative checks:

- `pgrep opencode` empty.
- `git grep -E 'opencode|OPENCODE_' -- scripts/ config/` returns
  nothing live.
- LiteLLM model swappable via env var: `openai/gpt-5-nano` ↔
  `nvidia_nim/meta/llama-3.1-70b-instruct` ↔
  `anthropic/claude-sonnet-4` without code changes.
- Manual `dportsv3 dev-env exec <env> -- agentic-worker
  materialize-closure --origin <something>` still produces the same
  JSON envelope as before the refactor.
- `process_pr_job` still runs when a `type=pr` job is hand-enqueued
  with `rebuild_ok=true` — confirms PR path is intentionally out-of-
  loop, not broken.
