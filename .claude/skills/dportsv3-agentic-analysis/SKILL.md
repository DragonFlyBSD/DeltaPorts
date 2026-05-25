---
name: dportsv3-agentic-analysis
description: Analyze how the DeltaPorts agentic loop (triage → patch → convert) handled a given port. Use when the user says "analyze port X" or "analyze the agentic run for X". Produces a structured report covering correctness, efficiency, and bugs against the current code's expected behavior. This skill is constantly improving — when you spot a new failure mode or expected-behavior gap, note it at the end of the report under "Skill update suggestions" so it can be folded back in.
---

# DeltaPorts agentic analysis

## What this skill is for

The DeltaPorts agentic loop watches dsynth failures and tries to fix them. Each failure produces a *bundle* with triage output, patch attempts, tool trace, and a rebuild proof. This skill walks you through pulling that data for a given port and judging what the loop did well, did badly, or got silently wrong.

Inputs you need before starting:
- **Port origin** (e.g. `devel/gperf`) or a bundle ID.
- **Tracker base URL** — read from the `DP_TRACKER_URL` env var, or ask the user. There is no hardcoded default; the tracker host is operator-specific.

Output: a report with **what the agent did**, **whether that matches the expected contract**, **inefficiencies**, **bugs**, and **skill update suggestions**.

If the analysis will read large traces (>50 KB of artifacts), delegate the read+summarize step to a sonnet 4.6 subagent via the Agent tool so the main context stays clean. Give the subagent the artifact URLs and a copy of the "Expected behavior" + "Known failure modes" sections from this file.

## How to fetch data

The tracker exposes both rendered HTML pages and a JSON-ish API. **Prefer the API**; only scrape HTML when no API exists for what you need. URLs are plain HTTP — do not use WebFetch (it force-upgrades to HTTPS); use `curl` via Bash.

### API endpoints (prefer these)

| Purpose | URL |
|---|---|
| Raw artifact bytes | `GET /api/bundles/<bundle-id>/artifacts/<path>` |
| (probe for more API routes if needed) | `curl -sS <base>/api/ \| head` |

### HTML pages (use to locate bundles, fall back when no API)

| Purpose | URL |
|---|---|
| Agentic dashboard (recent bundles per port) | `GET /agentic` |
| Bundle index for a port | grep the dashboard HTML for `<port-origin-with-_>-<timestamp>Z` rows |
| Bundle detail (artifact list + tool trace table) | `GET /agentic/bundles/<bundle-id>` |
| Single artifact rendered | `GET /agentic/bundles/<bundle-id>?artifact=<path>` |

### Discovery flow

1. `curl -sS http://<base>/agentic | grep -A2 "devel/<port>"` — find recent bundle IDs for the port.
2. For each interesting bundle, fetch the artifacts listed below.
3. If multiple bundles exist for the same port, analyze the most recent **and** scan timestamps of prior failures to see if the loop kept retrying.

## Artifacts that matter

All under `/api/bundles/<bundle-id>/artifacts/`:

| Path | What it tells you |
|---|---|
| `meta.txt` | Origin, target, timestamps, dsynth profile. |
| `logs/errors.txt` | dsynth tail — the actual failure the agent reacted to. |
| `analysis/triage.md` + `triage.json` | Classification, confidence, root cause, suggested fix. |
| `analysis/patch.md` | Agent's narrative of what it did and why. |
| `analysis/patch_audit.json` | Status, model, token usage, attempts breakdown. |
| `analysis/rebuild_proof.json` | `rebuild_ok` + build command — the success gate. |
| `analysis/tool_trace.jsonl` | Per-turn tool calls. Where you find inefficiencies. |
| `analysis/changes.diff` | The diff operators would land. **Empty diff with `rebuild_ok=true` is always a bug** (legacy flow). In intent flow this is derived from `intent_log.json`. |
| `analysis/intent_log.json` | **Canonical record of an intent-flow patch attempt** (Step 25). Schema: `{schema_version, origin, target, mode_at_apply, baseline_commit, intents: [{seq, intent, applied_at, ok, substrate_diff, error}]}`. Present iff the agent used the intent DSL. When present, this — not `changes.diff` — is the source of truth verify-fix replays. |
| `analysis/proposed_fix.md` | Operator-facing summary the tracker generates. |
| `port/Makefile`, `port/distinfo`, `port/pkg-plist` | Snapshot of the port at failure time. |

Bulk-fetch recipe:
```sh
for f in meta.txt logs/errors.txt analysis/triage.md analysis/patch.md \
         analysis/patch_audit.json analysis/rebuild_proof.json \
         analysis/changes.diff analysis/tool_trace.jsonl \
         analysis/intent_log.json; do
  echo "===== $f ====="
  curl -sS "http://<base>/api/bundles/<bundle-id>/artifacts/$f"
done
```

Note: `analysis/intent_log.json` 404s on legacy-flow bundles — that's
fine, it means the bundle predates Step 25 or the gate was off.

## Expected behavior (current code contract)

This section is what the agent *should* be doing if the code is working. Update it when the code evolves.

### Lifecycle
- A failure bundle is written by the dsynth hook. The runner enqueues a triage job.
- Triage job runs `dportsv3.agent.triage.run` — single LLM call (no tools), may do up to `DP_HARNESS_MAX_SNIPPET_ROUNDS` (default 5) snippet-extractor rounds.
- Triage emits `classification`, `confidence`, root-cause text. `config/agentic-policy.json` resolves a tier (AUTO / ASSIST / MANUAL).
- AUTO/ASSIST → auto-enqueue patch job. MANUAL → stop.
- Patch job runs `dportsv3.agent.patch.run` → `attempt_loop.run` up to `tier.max_iterations` attempts, each driving a `tool_loop` until the LLM stops requesting tools.
- Success gate: `rebuild_proof.json` parsed from the LLM's `## Rebuild Proof (JSON)` block with `rebuild_ok=true`. Convert jobs additionally require `validate_dops_ok=true`.

### Tool surface (patch agent)

The patch agent has **two surfaces** gated by `DP_HARNESS_PATCH_USE_INTENT`:

- **Legacy flow** (gate off): `env_verify`, `materialize_dports`, `extract`, `dupe`, `genpatch`, `install_patches`, `dsynth_build`, `get_file`, `put_file`, `emit_diff`, `grep`.
- **Intent flow** (gate on, Step 25): `env_verify`, `materialize_dports`, `extract`, `dsynth_build`, `get_file`, `grep`, **`apply_intent`**, **`intent_reference`**. The edit surface collapses to `apply_intent` only — no `put_file`/`install_patches`/`emit_diff`. Selection drives the system prompt too: `PATCH_INTENT_SYSTEM` vs `PATCH_SYSTEM`.

Convert agent (unchanged by Step 25): `env_verify`, `list_dir`, `get_file`, `put_file`, `grep`, `dops_reference`, `validate_dops`. Convert intentionally keeps the direct edit surface — its job *is* to edit `overlay.dops`.

Check `dportsv3/agent/tools.py::tools_for_patch_agent` + `patch_use_intent_enabled` if uncertain which surface a given bundle saw.

### Substrate contract
- All chroot-bound operations route through `dportsv3 dev-env exec <env> -- ...`.
- Host filesystem ops only on `env_dir/writable/...` (resolved via `dportsv3 dev-env path <env> --writable`).
- No git commits, branches, push, or PRs from the loop. The writable overlay is the workspace; `analysis/changes.diff` is the audit trail.

### Mode handling (dops vs compat)
- `classify_dops` decides if a port is `compat`, `dops`, or `needs_judgment` (overlay.dops present but ambiguous).
- For dops-mode ports the agent should edit `overlay.dops` directly (using `file.materialize` / `file.copy` / `patch.apply` statements). Editing `dragonfly/*` files directly on a dops port is wrong because they're regenerated at compose time.
- For compat-mode ports the agent edits `dragonfly/*` and uses `install_patches`.
- Bundle metadata surfaces the classification as `dops: compat | dops | needs LLM judgment`.

### dops grammar (what `overlay.dops` looks like)

The dops DSL is the source of truth for dops-mode ports. **The canonical reference is `scripts/generator/dportsv3/agent/dops_quickref.md`** — read it before judging dops edits. Real examples live at `ports/devel/readline/overlay.dops`, `ports/editors/vim/overlay.dops`, `ports/ports-mgmt/pkg/overlay.dops`.

Quick orientation (full grammar in the quickref):

- `file.materialize { source = "src/path"; dest = "dest/path" }` — copy a file from the dragonfly source tree into the port at compose time. Used for porting in DragonFly-specific replacements of upstream files.
- `file.copy { source = "..."; dest = "..." }` — copy a file from port-local resources (no dragonfly source tree).
- `patch.apply { target = "path/in/wrksrc"; diff = """..."""  }` — apply an inline unified diff at patch phase. The `diff` payload is the actual patch content — when context drifts, the *diff string inside overlay.dops* is what needs editing, not a `dragonfly/patch-*` file.

**Bright-line rule:** if the upstream-source file lives in the dragonfly source tree (e.g. things mirrored from FreeBSD-style port layouts), prefer `file.materialize`; if it's port-local, use `file.copy`. The convert agent's system prompt teaches this — when judging a convert run, check it followed the rule.

### Mode-correctness checks for the analyzer

When the port is dops-mode, verify in the trace:
- Did the agent read `overlay.dops` early? (It should — that's the source of truth.)
- If a patch hunk drifted: did the agent edit the `diff = """..."""` block inside `overlay.dops`, or did it incorrectly `put_file` to `dragonfly/patch-*`? The latter is silent wrongness — the edit will be clobbered on next reapply.
- If a patch became obsolete: did the agent remove the corresponding `patch.apply` / `file.materialize` block from `overlay.dops`, or did it `put_file` an empty `dragonfly/patch-*`? Only the first is durable.
- Did `validate_dops` run after the edit? It should — that's the dops equivalent of "does this even parse."
- Did `materialize_dports` re-run between the dops edit and `dsynth_build`? Compose needs to re-render the port tree from the edited overlay before dsynth sees the change.

When the port is compat-mode but the agent reached for dops tools (`validate_dops`, edited `overlay.dops`): also wrong direction. Flag it.

### Intent flow (Step 25 — DP_HARNESS_PATCH_USE_INTENT)

When `analysis/intent_log.json` is present, the bundle used the intent DSL. Different rules apply:

- **Canonical record:** `intent_log.json` is the source of truth, not `changes.diff`. Verify-fix replays the intent log; `changes.diff` is a derived audit artifact (ordered concatenation of per-intent `substrate_diff` values).
- **Intent grammar:** seven intent types — `replace_in_patch`, `drop_patch`, `add_patch`, `add_file`, `change_makefile`, `bump_portrevision`, `convert_to_dops`. Full schemas at `scripts/generator/dportsv3/agent/edit_intent/schemas/`.
- **Mode is fixed per transaction:** `mode_at_apply` ∈ `{compat, dops, convert}`, set at the first `apply_intent` call from `worker.assess_dops`. Mid-transaction mode drift is refused (`blocked_by: transaction_mode_drift`).
- **Half-migration invariant:** if both `Makefile.DragonFly` AND `overlay.dops` exist on the port, `apply_intent` refuses (`blocked_by: substrate_invariant`). The substrate must be all-compat or all-dops before edits begin.
- **Canonical-log invariant:** every substrate change has a matching log row. Cap-overflow refusals revert substrate. `intent_log_full=True` in a tool result means the agent should escalate, not retry.
- **Pre-job clean assertion (25d-1 / design §5.1):** patch jobs refuse to start (`PATCH_GAVE_UP`, `patch_preflight_dirty` activity row) if `ports/<origin>/` has uncommitted edits from a prior run. `patch_preflight_error` means the clean check itself raised — env in unknown state.
- **Post-build cleanup (25g):** after dsynth, the env's `ports/<origin>/` is reset to HEAD so the next attempt starts clean. Operator escape: `dportsv3 dev-env reset-port ENV ORIGIN`.
- **Telemetry:** every `apply_intent` call emits a dedicated `intent_applied` activity row alongside the generic `tool:apply_intent` row, carrying `intent_type`, `intent_target`, `ok`, `blocked_by`, `substrate_diff_sha256`, `substrate_diff_bytes`, and inline `substrate_diff` if ≤ 4 KB.
- **Bundle UI:** the "Intent sequence" card on the bundle detail page renders the intent log as a structured table — mode, baseline commit, per-intent rows.

### Mode-correctness checks for intent flow

When `intent_log.json` is present, check:
- Does `mode_at_apply` match what `classify_dops` would return for the port? (If the substrate was half-migrated, the agent should have hit `substrate_invariant` instead.)
- For each intent row, is `ok=true`? Failed-intent rows are forensics — the agent saw the error and should have either retried with a different intent or escalated. Repeated identical failed intents = loop.
- For dops-mode bundles: does the agent's intent sequence touch `overlay.dops` (via `change_makefile`/`add_file` against the overlay) and NOT `dragonfly/patch-*`? Editing `dragonfly/*` on a dops port via `add_patch`/`replace_in_patch` is wrong (regenerated at compose).
- Does `changes.diff` match the concatenation of `substrate_diff` values from the ok=true rows? Mismatch = canonical-log invariant broken (look for `canonical_log_broken=true` in tool results).
- Is `baseline_commit` a real commit in the env's writable DeltaPorts? (Replay refuses against a missing baseline.)

### Output contract for operators
- `changes.diff` must contain the operator-applyable diff. **Empty diff with `rebuild_ok=true` is a contract violation** (legacy flow).
- In intent flow, `intent_log.json` is the operator-applyable record. `changes.diff` is still produced (concat of intent diffs) but its emptiness is benign iff no intents fired (e.g. agent escalated immediately).
- `proposed_fix.md` must reference a non-zero diff (legacy) OR a non-empty intent sequence (intent flow).

## Analysis checklist

For each bundle, work through these questions and write the report against them. Skip ones that don't apply, but say so.

### 1. Triage correctness
- Does the classification match what `logs/errors.txt` actually shows? (e.g. a fetch failure misclassified as compile-error is a triage bug.)
- Is the confidence appropriate?
- Did snippet rounds happen, and were they useful? (Look for `snippets/round_N/` artifacts.)

### 2. Patch correctness
- Did the agent reach `rebuild_ok=true`?
- Does the **fix actually fix the root cause**, or did it bypass the problem? (E.g. removing a patch the agent declared obsolete vs. actually adapting it — both may produce `rebuild_ok=true`, but only one is right. Cross-check `patch.md`'s reasoning against the upstream code it read.)
- For dops-mode ports: did the agent edit `overlay.dops` (correct) or `dragonfly/*` files directly (wrong, edits will be clobbered)?
- For compat-mode ports (legacy flow): did the agent run `install_patches` after `genpatch`?
- **Intent flow:** does the intent sequence make sense? Did the agent escalate when blocked (e.g. on `intent_log_full`, `substrate_invariant`, `transaction_mode_drift`) or did it keep retrying? Did it pick the right intent type for the fix shape (e.g. `drop_patch` for an obsolete patch, not a no-op `replace_in_patch`)?

### 3. Output contract
- Is `analysis/changes.diff` non-empty when `rebuild_ok=true`?
- Does `proposed_fix.md` give the operator a usable recipe?
- Does the diff actually match what `patch.md` says was changed?

### 4. Efficiency
- Token usage vs. tier budget — was the loop expensive relative to the fix size?
- Redundant tool calls — e.g. multiple `emit_diff` calls in a row, or `materialize_dports` called twice when once would do.
- Did the agent re-read files it had already read?
- Did it call tools with the wrong args (e.g. passing origin where relpath was expected)?

### 5. Lifecycle hygiene
- Was this port previously bundled? (Scan `/agentic` for older timestamps.) If so, did the loop converge or thrash?
- If MANUAL tier: was the classification one that should have been AUTO/ASSIST?

## Known failure modes (extend this list)

Patterns seen in the wild. When you see a new one, append it here and flag it in your report's "Skill update suggestions" section so the operator folds it in.

- **Empty `changes.diff` with `rebuild_ok=true`.** Agent edited files inside the writable overlay but the runner's diff capture didn't pick it up. Suspected causes: overlay isn't a git working tree, or the diff scope path is wrong. Operator gets "agent fixed" with nothing to land. **Always flag this as a bug.** Confirmed in `devel_gperf-20260523-094119Z` for **dops-mode `put_file` writes to `overlay.dops`**: the `put_file` return shows a changed sha256, yet `emit_diff` immediately after returns empty. Current hypothesis: the diff baseline is snapshotted at job-start rather than re-read at emit time. Also confirmed in `multimedia_v4l_compat-20260523-101601Z` for **freshly-created `overlay.dops` files** (not just edits to an existing one) — the bug fires on any `put_file` write the overlay's baseline misses, regardless of whether the file existed before the job started.
- **Agent passes origin where `emit_diff` wants a relpath.** Tool signature is `emit_diff(env, origin, relpath)`; the LLM sometimes passes only `origin` (e.g. `"devel/gperf"`). Either the prompt is unclear or the schema is.
- **Dops port classified as `needs_judgment` but patch agent proceeds anyway.** Today the patch agent has dops-aware tools, so it sometimes works. But it's an architectural gap — the convert/patch boundary is still soft for dops ports.
- **Agent declares a patch "obsolete" based on shallow upstream inspection.** Removing a patch and getting a green dsynth is not proof the patch was actually obsolete — it may have addressed a runtime or platform-specific issue dsynth doesn't catch. Flag when the agent's justification is thin.
- **Wasted `get_file` turn from a mis-guessed offset.** When inspecting a C source file for include directives, the agent sometimes first reads from a non-zero offset and then re-reads from the top, burning a turn. The patch prompt could steer the agent toward `grep` for include-presence checks instead of speculative offset reads. Seen in `devel_gperf-20260523-094119Z` turn 6 → turn 7.
- **Intent-flow: agent retries past `intent_log_full=True`.** When a tool result carries `intent_log_full: true` the agent is supposed to escalate (MANUAL), not retry — the log either hit the count cap (~loop) or a byte cap that revert already undid. If the trace shows further `apply_intent` calls after this flag fires, it's a prompt/behavior bug.
- **Intent-flow: agent ignores `blocked_by: substrate_invariant`.** Half-migrated substrate (`Makefile.DragonFly` + `overlay.dops` both present) must be resolved by the operator or by `convert_to_dops` BEFORE other intents can land. Retries that don't first emit `convert_to_dops` will keep getting refused.
- **Intent-flow: agent ignores `blocked_by: transaction_mode_drift`.** Once the first `apply_intent` pins `mode_at_apply`, subsequent calls in a different mode are refused. Drift usually means the agent's mental model of the port flipped mid-job — flag if `assess_dops` would now return something different from `mode_at_apply`.
- **Intent-flow: substrate_diff disagrees with the rendered changes.diff.** Concat of ok=true `substrate_diff` values should equal `changes.diff`. Drift here means either a bug in the diff accumulator or a bypass of the canonical-log path (`canonical_log_broken=true` in some tool result).
- **Intent-flow: patch job aborted with `patch_preflight_dirty` or `patch_preflight_error`.** Not a bug — design §5.1's hard pre-job clean assertion. Operator either has uncommitted edits in the env or the env is in unknown state (chroot unmounted, etc.). Flag as lifecycle hygiene, not a patch agent bug.
- **Premature `materialize_dports` on the consumer origin before the provider overlay is activated.** When a port uses `MASTERDIR` (or otherwise shares compose artifacts with a sibling origin), the agent sometimes materializes the *consumer* origin immediately after writing the dops overlay for the *provider*, before materializing the provider itself. Compose runs against the wrong origin, shows `modes: compat=1`, and the wasted call is only caught because the agent then self-corrects with a second call to the right origin. Seen in `multimedia_v4l_compat-20260523-101601Z` turn 13: agent wrote `overlay.dops` for `multimedia/libv4l`, then materialized `multimedia/v4l_compat` (the consumer), got compat-mode compose, then re-materialized `multimedia/libv4l`. Prompt should steer the agent to always `materialize_dports` the origin that owns `overlay.dops` first; the MASTERDIR consumer can ride the shared compose artifacts.

## Report shape

Produce something like this (markdown, no fluff):

```
# Agentic analysis — <origin> (<bundle-id>)

## Summary
<2-3 sentences: what the agent tried to do, did it land, is the result trustworthy>

## Triage
- Classification: <X> (confidence: <Y>) — <assessment: correct / questionable / wrong>
- Root cause as stated: <quote>
- Actual root cause from logs: <if different>

## Patch
- Status: <success / needs-help / budget-exhausted>
- Flow: <legacy | intent>  ← intent if analysis/intent_log.json exists
- Attempts: <N> / tier max
- Tokens: <prompt / completion / total>
- Mode: <compat | dops | needs_judgment>  (intent: also `mode_at_apply` from intent_log)
- Tool sequence: <one-line summary of the trace>
- Intent sequence (intent flow only): <seq N: type(target) ok|FAIL[reason]; ...>
- Fix narrative: <what patch.md claims>
- Fix verdict: <is the fix real?>

## Output contract
- changes.diff: <bytes> — <ok / empty-bug / mismatched>
- intent_log.json (intent flow): <N intents, M ok> — <canonical / canonical_log_broken / missing>
- proposed_fix.md: <usable / broken>

## Inefficiencies
- <bullets>

## Bugs / contract violations
- <bullets, each tagged with severity: low / medium / high>

## Lifecycle context
- Prior bundles for this port: <count, dates>
- Loop behavior: <one-shot / retrying / thrashing>

## Skill update suggestions
- <anything this analysis surfaced that this SKILL.md should have warned about>
```

Keep it terse. The operator skims this.

## Delegating to a subagent

If the bundle is large or you have several to analyze, spawn a sonnet 4.6 subagent:

```
Agent({
  subagent_type: "general-purpose",
  model: "sonnet",
  description: "Analyze agentic bundle <id>",
  prompt: "<paste the Expected behavior + Known failure modes + Report shape sections from
           .claude/skills/dportsv3-agentic-analysis/SKILL.md, plus the bundle ID and tracker
           base URL>. Fetch the artifacts via /api/bundles/<id>/artifacts/<path> with curl
           (HTTP only, no HTTPS upgrade). Produce the report in the shape specified."
})
```

Read the returned report, sanity-check the bug claims against the raw artifacts if anything looks wrong, then hand it to the user.
