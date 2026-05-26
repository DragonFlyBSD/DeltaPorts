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

**Prefer the `dportsv3 tracker get-*` CLI** over curl. It talks to the same tracker HTTP API but returns clean structured output and handles URL encoding for you. Set `DPORTSV3_TRACKER_URL` once and every command picks it up; or pass `--server URL`.

### Locating the `dportsv3` binary

The CLI is not always on `$PATH`. **At the start of your analysis**, resolve the binary once and reuse it:

```sh
# Repo-root-anchored fallback. The venv binary is the project's
# own and is the safe deterministic location.
DPORTSV3="${DPORTSV3:-$(command -v dportsv3 || echo /Users/tuxillo/s/DeltaPorts/scripts/generator/.venv/bin/dportsv3)}"
# Verify before using it:
"$DPORTSV3" tracker --help >/dev/null \
    || { echo "dportsv3 unusable at $DPORTSV3" >&2; exit 1; }
```

Then every later call uses `"$DPORTSV3" tracker get-bundle …` etc. If you can't make the CLI work after that probe, fall back to `curl` and flag the gap.

### CLI commands (prefer these)

| Purpose | Command |
|---|---|
| List recent bundles for a port | `dportsv3 tracker list-bundles --origin <category/port> --limit 10` |
| Get one bundle's detail (incl. artifact list) | `dportsv3 tracker get-bundle <bundle-id>` |
| Same, structured JSON | `dportsv3 tracker get-bundle <bundle-id> --json` |
| Fetch one artifact's raw bytes (logs/diffs/JSON) | `dportsv3 tracker fetch-artifact <bundle-id> <relpath>` |
| Get one job by ID | `dportsv3 tracker get-job <job-id>` |
| List jobs (filter by state) | `dportsv3 tracker list-jobs --state dead --limit 20` |
| Activity log for one job | `dportsv3 tracker get-activity --job <job-id> --limit 200` |
| Activity log filtered by stage | `dportsv3 tracker get-activity --job <id> --stage tool:` |

### HTML pages (use only when no equivalent API exists)

| Purpose | URL |
|---|---|
| Agentic dashboard (rendered overview) | `GET /agentic` |
| Bundle detail rendering | `GET /agentic/bundles/<bundle-id>` |

### Discovery flow

1. `dportsv3 tracker list-bundles --origin devel/<port> --limit 5` — find recent bundle IDs.
2. `dportsv3 tracker get-bundle <id> --json` — full detail with the artifact list.
3. For each artifact you actually need to read: `dportsv3 tracker fetch-artifact <id> <relpath>`.
4. If multiple bundles exist for the same port, analyze the most recent **and** scan timestamps of prior failures to see if the loop kept retrying.

### Why not curl?

- The CLI handles URL encoding of slashed paths (`devel/gperf` → `devel%2Fgperf`) without you remembering.
- Structured output (or `--json` for full JSON) means no grep/sed/jq pipelines that break on whitespace or special characters in error messages.
- The single binary already knows the tracker URL (`DPORTSV3_TRACKER_URL`) so you don't repeat it.

Fall back to `curl` only if the CLI is missing a command you need; then file a SKILL-update suggestion so the next analyzer doesn't have to fall back.

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

**Bulk-fetch the bundle in ONE shell command, not N small calls.**

Every Bash invocation costs a permission prompt and a tool turn. The whole analysis should fit in 2–4 Bash calls total:

1. **List + pick** (1 call): `"$DPORTSV3" tracker list-bundles --origin <port> --limit 5`
2. **Full bundle dump in one shell pipeline** (1 call):

   ```sh
   BID=<bundle-id>
   echo "===== bundle detail + jobs ====="
   "$DPORTSV3" tracker get-bundle "$BID" --jobs --json
   for f in meta.txt logs/errors.txt analysis/triage.md analysis/patch.md \
            analysis/patch_audit.json analysis/rebuild_proof.json \
            analysis/changes.diff analysis/tool_trace.jsonl \
            analysis/intent_log.json; do
     echo "===== $f ====="
     "$DPORTSV3" tracker fetch-artifact "$BID" "$f" 2>/dev/null \
       || echo "(absent)"
   done
   ```

3. **Activity log if needed** (1 call): `"$DPORTSV3" tracker get-activity --job <job-id> --limit 200`

That's it. Three Bash calls covers ~95% of analyses. Do NOT make one call per artifact, one call per job ID, one call per stage filter — the shell loop above is a single Bash invocation from the permission system's perspective. Reach for more calls only when the first dump leaves a specific gap (e.g. an unexpected stage you want to grep for).

Note: `analysis/intent_log.json` 404s on legacy-flow bundles — the `|| echo "(absent)"` handles it.

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

### Mode handling (dops-only as of Step C, 2026-05-26)

**Step C** (commit `0b7ed09fc26` onward): the patch agent operates ONLY on dops-converted substrate. Compat-mode editing was removed entirely. Compat-shaped ports (`Makefile.DragonFly` + `dragonfly/patch-*` without `overlay.dops`) get converted by the convert agent first; the patch flow refuses non-converted substrate with `blocked_by: state:<state>` and the agent escalates to MANUAL.

The points below describe the older dual-mode model for historical bundle analysis (bundles created before commit `0b7ed09fc26` may show compat-mode behavior).

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
- **Intent grammar:** seven intent types — `replace_in_patch`, `drop_patch`, `add_patch`, `add_file`, `change_makefile`, `bump_portrevision`, `replace_in_dops_block`. Full schemas at `scripts/generator/dportsv3/agent/edit_intent/schemas/`. Canonical list at `dportsv3/agent/edit_intent/grammar.py::INTENT_TYPES`.
- **Mode is fixed per transaction:** `mode_at_apply` is `dops` for any patch-agent bundle post-Step-C. Older bundles may also show `compat` or `convert` in this field. The runner refuses cross-mode transactions; under Step C drift is moot because only `dops` mode is reachable from the patch surface.
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

### Playbook library (Step 27)

The legacy KEDB (`docs/known-errors/`) and the prompt-embedded pattern content have been replaced by a single playbook library at `docs/agent-playbooks/` (markdown entries with YAML-subset frontmatter triggers). The selector is `dportsv3/agent/playbooks.py::load_playbooks`. Four entry categories — filename prefix is authoritative:

- `error-*.md` — triggered by triage classification (`triggers.classifications:`); attached to triage + patch payloads.
- `intent-*.md` — triggered by intent type; **not attached to the system payload**. Pulled on demand when the patch agent calls the `intent_reference(intent_type)` tool (`dportsv3/agent/worker.py::intent_reference`). The recipe lands in conversation context only for intent types the agent actually uses.
- `convert-*.md` — attached to convert payloads when `triggers.flows` contains `convert`.
- `toolchain-*.md` — triggered by mechanical toolchain detection on the port Makefile (`playbooks.py::detect_toolchains` — parses `USES=`, `GNU_CONFIGURE=`, file-presence signals like `CMakeLists.txt`, `Cargo.toml`).

Two attachment paths to keep straight when analyzing a bundle:
1. **Payload-build-time** (system prompt): error-*, convert-*, toolchain-* via classification/toolchain/convert-phase triggers. Fires once when the job's system prompt is built.
2. **On-demand** (mid-conversation): intent-* via `intent_reference` tool calls. Fires per-intent-type the agent reaches for.

**Telemetry signal — `playbooks_selected` activity row.** Every payload build emits one. The row carries `role` (triage / patch / convert), `included` (list of entry filenames that fired), `skipped_count`, and a `skipped_sample` of up to 8 `{file, reason}` pairs. Fetch via `dportsv3 tracker get-activity --job <id>` and filter for `event=playbooks_selected`. Reasons take shapes like `flow:patch-not-in-['convert']`, `classification:'patch-error'-not-in-['compile-error']`, `toolchains:no-overlap-with-['autoconf']`, or `budget:N+M>BUDGET`.

**What to check in an analysis:**
- For a triage/patch bundle: did `playbooks_selected` fire, and does `included` include the entries you'd expect given the classification and the port's Makefile? E.g. a `patch-error` triage on a `USES=cmake` port should pull `error-*` entries gated on `patch-error` plus `toolchain-cmake.md`. Empty `included` on a port with a recognized toolchain is a red flag — likely a missing trigger or a `find_playbooks_dir()` failure.
- For each intent type the agent actually emitted, did the trace include an `intent_reference(intent_type=X)` call BEFORE the first `apply_intent` of that type? `prompts.py` instructs the agent to call it first; skipping it isn't fatal but is a discipline regression worth flagging if the agent then misused the intent.
- Convert bundles: did `playbooks_selected` with `role=convert` fire and include `convert-target-directive.md` and `convert-classify-patch-domain.md`?

**Known parser quirks** (smoke-testing surface, mention if you spot symptoms):
- `_parse_inline_list` only parses inline-form YAML (`[a, b]`). Block-form list (`- a\n  - b`) silently yields `()`, which means *wildcard* in the selector. A new entry that "fires on everything" probably has block-form triggers.
- `intent_reference` deliberately ignores the `flows` and `classifications` gates and only filters on `triggers.intents`. An entry with `intents: [add_patch]` will surface via the tool regardless of whether the bundle "is" in patch flow.

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

### 5. Playbook coverage (Step 27)
- Did `playbooks_selected` fire for each role this bundle ran? (triage always; patch if it reached patch; convert if it's a convert bundle.)
- Does `included` look right for the bundle's classification + detected toolchains? Empty `included` on a port with recognized USES= is a red flag — investigate.
- For each distinct `intent_type` the agent emitted, was there a preceding `intent_reference(intent_type=X)` tool call in the trace? Skipping it is a discipline regression worth a note.
- Does the `skipped_sample` reveal a likely-buggy entry (e.g. a `toolchain-cmake.md` skipped with `toolchains:no-overlap-with-['cmake']` when the port's Makefile clearly has `USES=cmake`)? That points at `detect_toolchains()` not seeing the Makefile.

### 6. Lifecycle hygiene
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
- **Intent-flow: agent ignores `blocked_by: substrate_invariant`.** Half-migrated substrate (`Makefile.DragonFly` + `overlay.dops` both present) must be resolved by an operator (or by the convert agent, which produces the dops overlay) BEFORE other intents can land. The patch agent has no intent that fixes this state — retries that ignore the block will keep getting refused. Correct response is MANUAL escalation.
- **Intent-flow: agent ignores `blocked_by: transaction_mode_drift`.** Once the first `apply_intent` pins `mode_at_apply`, subsequent calls in a different mode are refused. Drift usually means the agent's mental model of the port flipped mid-job — flag if `assess_dops` would now return something different from `mode_at_apply`.
- **Intent-flow: substrate_diff disagrees with the rendered changes.diff.** Concat of ok=true `substrate_diff` values should equal `changes.diff`. Drift here means either a bug in the diff accumulator or a bypass of the canonical-log path (`canonical_log_broken=true` in some tool result).
- **Intent-flow: patch job aborted with `patch_preflight_dirty` or `patch_preflight_error`.** Not a bug — design §5.1's hard pre-job clean assertion. Operator either has uncommitted edits in the env or the env is in unknown state (chroot unmounted, etc.). Flag as lifecycle hygiene, not a patch agent bug.
- **Intent-flow: `change_makefile op=set` appends `mk set` lines to `overlay.dops` instead of replacing the existing directive for the same key.** Two or more sequential `set` intents on the same key produce additive substrate_diffs (`+mk set KEY "..."` per call) and a final overlay with duplicate `mk set KEY` lines. Each intent returns `ok=true`, the canonical-log invariant holds, and the agent typically rationalizes the result in `patch.md` as "in dops mode the last `mk set` wins, so the effective value is …" — which is the agent papering over a substrate the intent shouldn't have produced. Confirmed in `databases_redis-20260526-205826Z` (seqs 0/1/2 on `BINARY_ALIAS`). Likely fix space: either `change_makefile op=set` should rewrite the existing `mk set` line, or the dops engine should collapse multiple `mk set` for the same key, or the intent should refuse with a clearer op (`replace_value` / `remove` + `set`). Flag whenever an intent log shows ≥2 `change_makefile` set-ops on the same key with all `ok=true` but `rebuild_ok` never reached.
- **Step-27 telemetry: `playbooks_selected` activity rows missing from a bundle's job activity logs.** The runner is supposed to emit one row per payload build (triage / patch / convert). Confirmed absent for all three job types in `databases_redis-20260526-205826Z`. Likely cause: `_log_playbook_selection` no-ops when `queue_root_for_log(job)` returns `None` (the job dict at payload-build time lacks `queue_root`); the convert call site passes `Path(job.get("queue_root") or ".")` which can't be `None`, so its absence specifically points at the `activity_log` write going somewhere the tracker doesn't index when `queue_root="."`. Without this event the analyzer has no signal at all about playbook coverage; treat absence as a Step-27 telemetry regression to flag.
- **`rebuild_proof.json` missing on budget-exhaustion / give-up bundles.** Expected per the success-gate contract: even on terminal failure the runner should emit a proof JSON with `rebuild_ok=false` and a reason. Operator skimming the artifact list can't distinguish a clean "agent gave up" from "agent crashed mid-attempt." Confirmed in `databases_redis-20260526-205826Z` (only `patch_audit.json` carries the budget-exhausted status). Flag whenever `patch_audit.json` reports a terminal status but `rebuild_proof.json` is absent.
- **Knowledge gap: `.for`-parsed Makefile list variables and value-with-spaces.** Variables like `BINARY_ALIAS`, `MAKE_ENV`, `PLIST_SUB` are iterated by `.for var1 var2 in ${VAR}` which tokenizes on whitespace and expects N words per row. A value with an embedded space (e.g. `BINARY_ALIAS=gmd5sum=md5 -r`) produces the compose error `Wrong number of words (N) in .for substitution list with M variables`. No current playbook or prompt warns about this; the agent typically misdiagnoses by toggling flags rather than escaping the value or switching to a wrapper script. Worth adding a "value tokenization" note to `intent-change_makefile.md` and/or a new `error-for-substitution-list.md`.
- **Attempt-boundary amnesia.** When attempt 1 fails on a specific value and attempt 2 receives a fresh context, the agent sometimes re-emits the exact same intent that already failed in attempt 1's first turn. Suggests the attempt-2 system prompt's "prior failures" section either doesn't carry the intent sequence in a form the model attends to, or the model treats attempt boundaries as a hard reset. Confirmed in `databases_redis-20260526-205826Z`: seq 0 (attempt 1) and seq 2 (attempt 2) emit identical `change_makefile(BINARY_ALIAS=gmd5sum=md5 -r)` intents.
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

## Playbooks (Step 27)
- Triage: included=<list or "—">, skipped=<count> — <looks right / suspicious / missing event>
- Patch: included=<list or "—">, skipped=<count> — <…>
- Convert (if convert bundle): included=<list>, skipped=<count> — <…>
- `intent_reference` discipline: <every intent type preceded by ref / N skips: …>

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
