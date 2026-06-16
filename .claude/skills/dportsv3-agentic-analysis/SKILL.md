---
name: dportsv3-agentic-analysis
description: Analyze how the DeltaPorts agentic loop (triage → patch; convert retired in Step 48 — archived bundles may still show it) handled a given port. Use when the user says "analyze port X" or "analyze the agentic run for X". Produces a structured report covering correctness, efficiency, and bugs against the current code's expected behavior. This skill is constantly improving — when you spot a new failure mode or expected-behavior gap, note it at the end of the report under "Skill update suggestions" so it can be folded back in.
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
| `analysis/changes.diff` | **The** operator-applyable diff. **Empty diff with `rebuild_ok=true` is always a bug.** Since Step 42 there is no intent-log fallback — this is the canonical record. |
| `analysis/tool_trace.jsonl` | Per-turn tool calls + their results, lifecycle events (`attempt_start`, `llm_turn`, `tool_call`, `attempt_end`, `token_budget_exhausted`). Cheap inefficiency scan. **Does NOT contain assistant message text or `reasoning_content`** — for that, read the session dumps below. |
| `analysis/sessions/*.jsonl.gz` | **Full LLM message transcripts** (one file per attempt per job). The single most informative artifact: every assistant turn's `content` + `reasoning_content`, every `tool_calls` entry with full arguments, every tool result message the model actually received. Read these when tool_trace.jsonl isn't enough — i.e. whenever you need to understand *why* the agent did what it did, not just what tools fired. Available only when `DP_HARNESS_DUMP_SESSION` was on at run time. See below for record structure + parsing. |
| `analysis/proposed_fix.md` | Operator-facing recipe written when `rebuild_ok=true`. |
| `analysis/manual_handoff.md` | Operator-facing escalation summary written when the job escalates (MANUAL tier, retry cap, budget exhaustion, gave-up). Either this or `proposed_fix.md` is present, not both. |
| `analysis/convert_result.json` | Convert phase typed result (when a convert job ran). Carries `status`, `reapply_ok`, `deferred_patches`. |
| `analysis/triage_result.json` / `patch_result.json` | Typed phase results (Step 36) — root cause + evidence lifted into structured fields, consumed by downstream phases. |
| `port/Makefile`, `port/distinfo`, `port/pkg-plist` | Snapshot of the port at failure time. |

### LLM session dumps — structure and parsing

Each `analysis/sessions/<timestamp>-<target>-<origin>-<pid>[-<role>].job.attempt<N>.jsonl.gz` is the complete `messages` array as it stood at the end of one tool_loop attempt. The filename embeds:

- the role suffix: `-convert`, `-patch`, or no suffix for triage
- attempt number: `attempt1`, `attempt2` (patch can retry, convert/triage are single-attempt)

Decompressed: one JSON object per line, each a message in the LLM-API shape:

```json
{"role": "system", "content": "<system prompt verbatim>"}
{"role": "user", "content": "<initial task payload, plus any failure-context message inserted at attempt N>1>"}
{"role": "assistant", "content": "<text>", "reasoning_content": "<deepseek thinking>", "tool_calls": [...]}
{"role": "tool", "content": "<JSON tool result>", "tool_call_id": "..."}
```

What each role tells you:

- **system (record 0):** the exact prompt the agent saw. Verify any prompt-rule reference (e.g. "the agent went to /work/DPorts despite the prompt saying not to") by grepping the system content.
- **user (record 1):** the assembled payload. **Always check `len(content)`** — anything >50KB warrants a section-by-section breakdown by `## heading`. Common offenders: `## Build Errors` (dsynth log tail), `## Port Files` (every file under port/ inlined), `## Agent Playbooks` (matched playbook content).
- **user (record 2+, attempt 2+ only):** the failure-context message from `attempt_loop._failure_context_message`. After Step 42 this is just `Previous attempt #N did not succeed.\nTail of your prior response:\n<last 2KB>\n` — no structured intent-log summary. If the agent re-submits the exact same wrong edit in attempt 2, the lesson is in those last 2KB of attempt-1 prose.
- **assistant:** the agent's per-turn output. Three fields you care about:
  - `content` (text the model wrote) — usually small except in the final "## Patch Log / ## Rebuild Proof" turn.
  - `reasoning_content` (deepseek thinking-mode output) — often the largest single field. **Carried on every subsequent turn** per deepseek's contract, so accumulates quadratically. A 13KB single-turn reasoning blob is ~3K tokens × every remaining turn.
  - `tool_calls` — array of `{id, function: {name, arguments}}`. The arguments are JSON-stringified; parse before inspecting.
- **tool:** the JSON result the model received. Contents match what `worker.py` returned: `{ok, ...}`. For `materialize_dports`: `stdout_tail` is what the agent reads to decide the apply landed. For `validate_dops`: `{ok, stderr_tail}` with E_* error codes on failure. For `get_effective_overlay`: `{target, effective_ops, filtered_out}` — what compose WILL apply vs. ops scoped to other build lines. For `put_file`: `{ok, sha256, ...}`.

Fetch + decompress in the same shell pipeline as the rest:

```sh
BID=<bundle-id>
"$DPORTSV3" tracker get-bundle "$BID" --jobs --json | python3 -c "
import json,sys
d=json.load(sys.stdin)
for a in d.get('artifacts',[]):
    if 'sessions/' in a['relpath']:
        print(a['relpath'])
" | while read rel; do
    out="$(basename "$rel")"
    "$DPORTSV3" tracker fetch-artifact "$BID" "$rel" > "$out" 2>/dev/null
    gunzip -k "$out"   # keep both .gz and decompressed
done
```

Walking a session (Python, one-shot):

```python
import json
recs = [json.loads(l) for l in open('<file>.jsonl') if l.strip()]
print(f"records={len(recs)} system={len(recs[0]['content'])}B user={len(recs[1]['content'])}B")
for i, r in enumerate(recs):
    if r['role'] in ('system','user'): continue
    if r['role'] == 'assistant':
        tcs = [t['function']['name'] for t in (r.get('tool_calls') or [])]
        rc = r.get('reasoning_content') or ''
        print(f"#{i} ASST text={len(r.get('content') or '')}B reason={len(rc)}B tools={tcs}")
        # show big reasoning blobs
        if len(rc) > 1000:
            print(f"  R: {rc[:400].replace(chr(10),' ')!r}...")
    elif r['role'] == 'tool':
        try:
            d = json.loads(r['content']); ok = d.get('ok')
        except Exception:
            ok = None
        print(f"#{i} TOOL ok={ok} sz={len(r['content'])}B")
```

When tool_trace.jsonl is enough vs. when you need session dumps:

- **tool_trace alone suffices for:** "which tools fired in what order", "did the agent call dsynth_log after build failure", "how many `put_file` writes hit `overlay.dops`", basic efficiency scan.
- **Need session dumps for:** "why did the agent go down path X" (reasoning_content), "what did the agent actually observe in the materialize summary" (full tool result content vs. trace's truncated keys), "did the agent see the failure-context message between attempts" (user-record-2 in attempt 2), "what was the static prompt's exact text" (system record). Anything that requires understanding the agent's mental model needs the session dump.

A session dump may not exist for older bundles or bundles run without `DP_HARNESS_DUMP_SESSION=1`. The text format of `analysis/tool_trace.jsonl` is always present and is the fallback.

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
            analysis/proposed_fix.md analysis/manual_handoff.md \
            analysis/convert_result.json; do
     echo "===== $f ====="
     "$DPORTSV3" tracker fetch-artifact "$BID" "$f" 2>/dev/null \
       || echo "(absent)"
   done
   ```

3. **Activity log if needed** (1 call): `"$DPORTSV3" tracker get-activity --job <job-id> --limit 200`

That's it. Three Bash calls covers ~95% of analyses. Do NOT make one call per artifact, one call per job ID, one call per stage filter — the shell loop above is a single Bash invocation from the permission system's perspective. Reach for more calls only when the first dump leaves a specific gap (e.g. an unexpected stage you want to grep for).

Note: `proposed_fix.md` and `manual_handoff.md` are mutually exclusive (one per terminal outcome); `convert_result.json` is absent on patch-only bundles. The `|| echo "(absent)"` handles all three.

## Expected behavior (current code contract)

This section is what the agent *should* be doing if the code is working. Update it when the code evolves.

### Lifecycle
- A failure bundle is written by the dsynth hook. The runner enqueues a triage job.
- Triage job runs `dportsv3.agent.triage.run` — single LLM call (no tools), may do up to `DP_HARNESS_MAX_SNIPPET_ROUNDS` (default 5) snippet-extractor rounds.
- Triage emits `classification`, `confidence`, root-cause text. `config/agentic-policy.json` resolves a tier (AUTO / ASSIST / MANUAL).
- Routing is driven by `assess_dops` (the substrate gate), **not** by triage classification. At a failure with no `overlay.dops`, the runner (`_ensure_overlay_or_abort` → `overlay_state.bootstrap_decision`) deterministically writes a header overlay (`type port`/`dport` per STATUS) so patch can author the body, or aborts to MANUAL when non-dport compat artifacts are present. (Step 48 deleted the runtime *convert agent* — there is no longer a convert job; older bundles may still show one.)
- AUTO/ASSIST → auto-enqueue patch (after the deterministic bootstrap, if substrate wasn't already dops). MANUAL → stop.
- Patch job runs `dportsv3.agent.patch.run` → `attempt_loop.run` up to `tier.max_iterations` attempts, each driving a `tool_loop` until the LLM stops requesting tools.
- Success gate: `rebuild_proof.json` parsed from the LLM's `## Rebuild Proof (JSON)` block with `rebuild_ok=true`.

### Tool surface

There is **one** patch-agent tool surface (Step 42 deleted the edit-intent layer). The patch agent edits `ports/<origin>/overlay.dops` directly in dops DSL (`put_file` + `validate_dops` + `dops_reference`, reading with `grep` / `get_file`), plus the build-loop tools (`extract`, `dupe`, `genpatch`, `install_patches`, `dsynth_build`, `dsynth_log`, `materialize_dports`) and the read-only views `emit_diff` and `get_effective_overlay`. System prompt: `prompts.PATCH_SYSTEM`. Tool list: `tools.patch_tool_names()` (returns the full registry).

Triage agent has no tools — single-turn LLM call with snippet rounds. System prompt: `prompts.TRIAGE_SYSTEM`.

(Historical bundles only: a separate *convert agent* existed pre-Step-48 with its own tool surface + `prompts.CONVERT_SYSTEM`; both are deleted. When analyzing an archived convert bundle, judge it against that retired contract, not current code.)

Check `dportsv3/agent/tools.py` and `dportsv3/agent/prompts.py` directly if uncertain.

### Substrate contract
- All chroot-bound operations route through `dportsv3 dev-env exec <env> -- ...`.
- Host filesystem ops only on `env_dir/writable/...` (resolved via `dportsv3 dev-env path <env> --writable`).
- No git commits, branches, push, or PRs from the loop. The writable overlay is the workspace; `analysis/changes.diff` is the audit trail. Delivery to upstream (PR open / patch outbox) is a separate operator-triggered phase via `dportsv3.delivery.orchestrator`.
- Guards collapse to three generic gates: `validate_dops` / `check_dsl` (DSL syntax + semantics), `assess_dops` (substrate-state gate that decides whether patch can proceed), and the `_resolve_path` path-escape backstop. `assert_port_clean` (the pre-job clean preflight) runs unconditionally.

### Substrate / mode handling

Patch operates ONLY on dops substrate (Step C, commit `0b7ed09fc26` onward, reinforced by Step 42). For a port with no `overlay.dops`, the runner deterministically bootstraps a header overlay (clean / `newport` dport) so patch can author the body, or aborts to MANUAL when non-dport compat artifacts (`Makefile.DragonFly`/`diffs/`/`dragonfly/`) are present — full absorption of those is the offline tooling's job, not the loop's. (Pre-Step-48 this was a runtime convert job; it's gone.)

The `put_file` boundary refuses any write to a `Makefile.DragonFly` (unconditional since the Step 48 authoring lock). A port that still has compat artifacts and no overlay is aborted to MANUAL, not "routed to convert."

### dops grammar (what `overlay.dops` looks like)

The dops DSL is the source of truth for substrate. **The canonical reference is `scripts/generator/dportsv3/agent/dops_quickref.md`** — read it before judging dops edits. Real examples live at `ports/devel/readline/overlay.dops`, `ports/editors/vim/overlay.dops`, `ports/ports-mgmt/pkg/overlay.dops`.

Quick orientation (full grammar in the quickref):

- `file.materialize { source = "src/path"; dest = "dest/path" }` — copy a file from the dragonfly source tree into the port at compose time. Used for porting in DragonFly-specific replacements of upstream files.
- `file.copy { source = "..."; dest = "..." }` — copy a file from port-local resources (no dragonfly source tree).
- `patch.apply { target = "path/in/wrksrc"; diff = """..."""  }` — apply an inline unified diff at patch phase. The `diff` payload is the actual patch content — when context drifts, the *diff string inside overlay.dops* is what needs editing, not a `dragonfly/patch-*` file.
- `mk set` / `mk add` / `mk remove` / `mk replace-if` — Makefile.DragonFly variable directives. Sequential `mk set` on the same key REPLACES (the engine collapses duplicates); the agent's mental model should not produce duplicate `mk set` lines.
- `mk target` set/append — recipe lines for a make target.
- Per-target scoping via `target @main` / `target @2026Q[1-4]` / `target @any`. `@any` is the right default for fresh overlays; `@main` filters every op away on quarterly builds (silent-skip bug class — see "Target-mismatch ghost" below).

**Bright-line rule:** if the upstream-source file lives in the dragonfly source tree, prefer `file.materialize`; if it's port-local, use `file.copy`. The convert agent's system prompt teaches this — when judging a convert run, check it followed the rule.

### Mode-correctness checks for the analyzer

For any patch or convert bundle, verify in the trace:

- Did the agent read `overlay.dops` early? (It should — that's the source of truth.) Prefer `get_effective_overlay` reads over raw `get_file overlay.dops` reads — on multi-target overlays the effective view filters out ops scoped to other build lines, eliminating manual filtering errors.
- If a patch hunk drifted: did the agent edit the `diff = """..."""` block inside `overlay.dops` (correct), or did it `put_file` to `dragonfly/patch-*` (wrong — silently clobbered on next reapply, the put_file boundary should refuse this on dops ports but verify it did)?
- If a patch became obsolete: did the agent remove the corresponding `patch.apply` / `file.materialize` block from `overlay.dops`, or did it `put_file` an empty `dragonfly/patch-*`? Only the first is durable.
- Did `validate_dops` run after every `put_file overlay.dops`? It should — that's the dops equivalent of "does this even parse." Skipping it and going straight to `materialize_dports` is a discipline regression.
- Did `materialize_dports` re-run between the dops edit and `dsynth_build`? Compose needs to re-render the port tree from the edited overlay before dsynth sees the change.
- For a fresh overlay: does the header carry `target @any` (correct) or `target @main` (silent-skip regression — see failure modes)?

### Output contract for operators
- `changes.diff` must contain the operator-applyable diff. **Empty diff with `rebuild_ok=true` is a contract violation.** Since Step 42 there's no intent-log fallback — this single artifact is the canonical record.
- On success, `proposed_fix.md` is written by `dportsv3.agent.proposed_fix` and must reference a non-zero diff.
- On escalation (MANUAL tier, retry cap, budget exhaustion, gave-up), `manual_handoff.md` is written by `dportsv3.agent.manual_handoff` instead.

### Playbook library (Step 27 + Step 42 reframe)

The legacy KEDB (`docs/known-errors/`) and prompt-embedded pattern content were replaced by a single playbook library at `docs/agent-playbooks/` (markdown entries with YAML-subset frontmatter triggers). The selector is `dportsv3/agent/playbooks.py::load_playbooks`. After Step 42 there are three live filename prefixes:

- `error-*.md` — triggered by triage classification (`triggers.classifications:`); attached to triage + patch payloads.
- `convert-*.md` — attached to convert payloads when `triggers.flows` contains `convert`.
- `toolchain-*.md` — triggered by mechanical toolchain detection on the port Makefile (`playbooks.py::detect_toolchains` — parses `USES=`, `GNU_CONFIGURE=`, file-presence signals like `CMakeLists.txt`, `Cargo.toml`).
- `flow-patch.md` — the single consolidated patch playbook (Step 42 collapsed the 12 deleted `intent-*.md` entries into one). Triggered by `flows: [patch]`. Carries the durable knowledge previously split per-intent: mk-directive traps, scoping judgment (`@any` vs `@main`), the static-patch workflow, broken-patch recovery, `PORTREVISION` handling.

The `intents` trigger axis and the per-call `intent_reference` on-demand attachment path are both gone. Every playbook that fires is attached at payload-build time.

**Telemetry signal — `playbooks_selected` activity row.** Every payload build emits one. The row carries `role` (triage / patch / convert), `included` (list of entry filenames that fired), `skipped_count`, and a `skipped_sample` of up to 8 `{file, reason}` pairs. Fetch via `dportsv3 tracker get-activity --job <id>` and filter for `event=playbooks_selected`. Reasons take shapes like `flow:patch-not-in-['convert']`, `classification:'patch-error'-not-in-['compile-error']`, `toolchains:no-overlap-with-['autoconf']`, or `budget:N+M>BUDGET`.

**What to check in an analysis:**
- For a triage/patch bundle: did `playbooks_selected` fire, and does `included` include the entries you'd expect given the classification and the port's Makefile? E.g. a `patch-error` triage on a `USES=cmake` port should pull `error-*` entries gated on `patch-error` plus `toolchain-cmake.md`. Empty `included` on a port with a recognized toolchain is a red flag — likely a missing trigger or a `find_playbooks_dir()` failure.
- Every patch bundle should include `flow-patch.md`. Its absence on a patch role row points at the `flows: [patch]` trigger or the selector wiring.
- Convert bundles: did `playbooks_selected` with `role=convert` fire and include `convert-target-directive.md` and `convert-classify-patch-domain.md`?
- Does the `skipped_sample` reveal a likely-buggy entry (e.g. a `toolchain-cmake.md` skipped with `toolchains:no-overlap-with-['cmake']` when the port's Makefile clearly has `USES=cmake`)? That points at `detect_toolchains()` not seeing the Makefile.

**Known parser quirks** (smoke-testing surface, mention if you spot symptoms):
- `_parse_inline_list` only parses inline-form YAML (`[a, b]`). Block-form list (`- a\n  - b`) silently yields `()`, which means *wildcard* in the selector. A new entry that "fires on everything" probably has block-form triggers.

## Analysis checklist

For each bundle, work through these questions and write the report against them. Skip ones that don't apply, but say so.

### 1. Pipeline shape

Before drilling into per-job correctness, sketch the pipeline. The canonical flows are:

- Patch only (substrate already dops): triage → patch. One triage session.
- Convert-then-patch: triage → convert → triage (re-classifies against the converted substrate) → patch. **Two triage sessions** is expected here, not a bug.
- Convert only (substrate-only fix): triage → convert. No patch session.
- Convert failed: triage → convert (gave up / budget). No patch session.

List every job that ran with `dportsv3 tracker get-bundle <id> --jobs --json`. The `jobs` array carries each job's type, state, retire_reason. Two triages with identical classifications is normal (convert didn't change the failure shape); two triages with *different* classifications is a substrate change worth narrating.

### 2. Triage correctness
- Does the classification match what `logs/errors.txt` actually shows? (e.g. a fetch failure misclassified as compile-error is a triage bug.)
- Is the confidence appropriate?
- Did snippet rounds happen, and were they useful? (Look for `snippets/round_N/` artifacts.)
- If two triage rounds ran (pre-convert + post-convert), did classifications change between them? If yes, note why — typically convert promoted compat→dops which changes the substrate's failure expression.
- Record each triage's token usage; the second triage often duplicates the first when convert didn't change failure shape (cheap re-run, expected).

### 3. Convert correctness (if a convert job ran)
- Status from `analysis/convert_result.json`: `verified` / `failed` / `no_conversion_proof_block`. `reapply_ok=true` means convert produced a valid overlay that compose accepts.
- **Verify `target @any` in the produced overlay.** Read `analysis/changes.diff` or grep the env's `overlay.dops` for the header. Anything other than `target @any` is a regression of the post-2026-05-26 fix (commits `d71f605c206` + `47846e7a392`). `@main` in a fresh overlay means every op will silently skip at compose against `@2026Q2` (per `engine/apply.py:296-313`).
- Deferred patches: list `deferred_patches` from convert_result. Each entry says what the dropped framework patch was DOING (intent, not authority). The downstream patch agent should address each one — verify it does.
- Tokens, attempts. Convert's budget is tighter than patch's; a convert that hit `budget-exhausted` after one validate_dops parse error is a known weak spot.

### 4. Patch correctness
- Did the agent reach `rebuild_ok=true`?
- Does the **fix actually fix the root cause**, or did it bypass the problem? (E.g. removing a patch the agent declared obsolete vs. actually adapting it — both may produce `rebuild_ok=true`, but only one is right. Cross-check `patch.md`'s reasoning against the upstream code it read.)
- Did the agent edit `overlay.dops` (correct) or `dragonfly/*` files directly (wrong, the `put_file` boundary should refuse — verify it did)?
- For each `put_file overlay.dops`, was `validate_dops` called before the next `materialize_dports`?
- Did the agent escalate cleanly when blocked by `assess_dops` substrate gates (e.g. half-migration), or did it keep retrying?
- **Turn-to-first-meaningful-edit.** Count tool turns from session start to the first `put_file` that targets `overlay.dops` (or `dragonfly/*` on the rare valid case). Floor on a clean success appears to be ~10-15 turns (opening + investigation + dops_reference if writing fresh). Values >20 suggest over-exploration; 0 means the agent never committed to a hypothesis (paralysis — flag).
- **Self-correction.** Count edits that were later reversed within the same attempt (e.g. an `overlay.dops` write replaced by a corrected version after a failed `materialize_dports`). One self-correction per run is healthy (the agent learned from compose/build feedback). Many self-corrections suggest the agent is thrashing; zero self-corrections paired with `budget-exhausted` may mean the agent never tried anything concrete enough to fail informatively.

### 5. Path discipline (scan tool calls)
- Reads of `/work/DPorts/<origin>/...`? Per `prompts.PATCH_SYSTEM` the agent may NOT read from this path — it's the LOCK ROOT, last-known-good, will disagree with extract output. Note every occurrence.
- Reads of `/xports/...` or any other chroot-internal path that isn't under `/work/`? Tools fail with `ValueError: path must be under /work` (worker.py `_resolve_path`). One occurrence = honest mistake; multiple = the agent didn't read the build log's path notation correctly.
- Hand-constructed `/work/obj/<origin>/...` paths that didn't come from `extract`'s `wrksrc` field? The prompt explicitly forbids constructing these. Compare against `extract`'s `wrksrc` return value.
- Host-side path leaks: tools returning host paths (e.g. `/root/.cache/dports-dev/...`) that the agent then passes to chroot-path-expecting tools. `genpatch`'s `output_dir` return was a known case.

### 6. Materialize cycle signal (P0a/P0b regression check)
For every `materialize_dports` call (each attempt typically has 1-3), check the `summary:` line in `stdout_tail`:

- `applied=N>0` — ops actually applied to the compose tree. Healthy.
- `applied=0` with `skipped>0` — ops were filtered by target mismatch. **Expect the `I_COMPOSE_DOPS_ALL_OPS_SKIPPED` warning in the same stage line** (per commit `663a8eae819`). If the warning is present, surface it; the agent should see it too. If `applied=0 skipped>0` but the warning is absent, that's a P0b regression.
- `applied=0` with `errors>0` — at least one op failed (parser, executor error). Distinct from skipping; check `dops_failed_op_results` in compose report.
- `applied=N>0` and `skipped=0` on a freshly-created overlay confirms P0a is functioning (the convert / patch agents emit `target @any` headers per commits `d71f605c206` / `47846e7a392`).

If you see `target @main` in any agent-emitted fresh overlay, that's a P0a regression — flag immediately.

### 7. Build verification (after dsynth_build)
- After `dsynth_build` returns `rebuild_ok=true`, did the agent verify by grepping the extracted source for the original error symptom, or rely on the tool exit code alone? For deterministic failures (linker duplicate symbol, missing-include compile error), trusting exit code is fine. For symptom classes where multiple bug sites can produce the same error (e.g. `__result` undefined in N headers), grep-the-symptom catches incomplete fixes.
- After `dsynth_build` failed, did `dsynth_log` immediately follow? The prompt says to call it immediately on build failure. If the agent went back to exploring instead, that's a discipline regression.
- Did the agent run `dsynth_build` at least once? `budget-exhausted` with zero build calls is the worst possible signal — the agent never tested anything, never learned from substrate feedback, just analyzed.

### 8. Output contract
- Is `analysis/changes.diff` non-empty when `rebuild_ok=true`?
- On success: is `proposed_fix.md` present and does it reference the diff?
- On escalation: is `manual_handoff.md` present? `rebuild_proof.json` should also exist with `rebuild_ok=false` and a reason — its absence on a terminal-failure bundle is a regression (see failure modes).
- Does the diff actually match what `patch.md` says was changed?

### 9. Efficiency — quantitative breakdown
Don't say "the loop was expensive". Break it down:

- **Static prompt cost.** Sizes of `messages[0]` (system) + `messages[1]` (initial user). Multiply by turn count for the per-turn ceiling. On a clean ASSIST run this is typically 35-50% of total tokens; on bloated cases (>50KB user prompt) it dominates.
- **User prompt composition.** When the user prompt is >50KB, break it by `## section` heading and flag oversized sections (>10KB) the agent never read via subsequent tool calls. Common offender: `## Port Files` which inlines every file under port/; the agent has `get_file` and can pull on demand.
- **Reasoning_content accumulation.** Sum `reasoning_content` byte sizes across all assistant turns. Note single-turn outliers (>5KB is a "thinking hard" turn — fine on hard ports, suspicious on simple ones). Deepseek thinking-mode requires reasoning_content carry on every subsequent turn, so this accumulates quadratically.
- **Tool result carry.** Identify the top-3 biggest tool returns by byte size. Each `dsynth_log` is ~10-16KB. A 16KB result carried across 10 subsequent turns is ~40K tokens. Also watch `get_effective_overlay` on large multi-target overlays.
- **Completion.** Usually small unless the agent wrote a long Patch Log.
- **Sum the per-turn prompt sizes** (from the `llm_turn` activity events if present, or estimate as static + cumulative reasoning + cumulative tool results). Compare to `patch_result.tokens_total` — a big discrepancy may indicate the trace is missing entries.

Other efficiency checks:
- Redundant tool calls — e.g. multiple `emit_diff` calls in a row, or `materialize_dports` called twice when once would do.
- Did the agent re-read files it had already read?
- Did it call tools with the wrong args (e.g. passing origin where relpath was expected)?
- Did the agent use raw `get_file overlay.dops` on a multi-target port instead of `get_effective_overlay`? On wide overlays this can double the per-turn carry.

### 10. Playbook coverage (Step 27)
- Did `playbooks_selected` fire for each role this bundle ran? (triage always; patch if it reached patch; convert if it's a convert bundle.)
- Does `included` look right for the bundle's classification + detected toolchains? Empty `included` on a port with recognized USES= is a red flag.
- For every patch bundle: is `flow-patch.md` in `included`? Its absence is a Step-27/42 wiring regression.
- Does the `skipped_sample` reveal a likely-buggy entry (e.g. a `toolchain-cmake.md` skipped with `toolchains:no-overlap-with-['cmake']` when the port's Makefile clearly has `USES=cmake`)? That points at `detect_toolchains()` not seeing the Makefile.
- Caveat: the text-format `playbooks_selected` activity row only shows counts (`included=N skipped=M`), not filenames — which means the "does `included` look right" check above is unverifiable from the default `get-activity` output. To actually verify which entries fired, pull raw JSON: `dportsv3 tracker get-activity --job <id> --json` and inspect the row's `payload` field for the filename list. If `--json` doesn't expose it either, fall back to `curl http://<tracker>/api/jobs/<id>/activity` and grep for `playbooks_selected`. If no surface exposes the filenames, flag as a tracker feature gap and treat the count as a black box.

### 11. Lifecycle hygiene
- **Always run `list-bundles --origin <category/port> --limit 10` for the port** even if the current bundle looks like a clean one-shot. It's one cheap CLI call and gives ground truth about prior agentic activity on this port. The `port/STATUS` file (when present in the bundle snapshot) is a compat-era artifact that convert deletes — it can hint at upstream version history but is not a substitute for the tracker query and disappears entirely on already-dops ports.
- Was this port previously bundled? If so, did the loop converge (older bundles `accepted` / `verified`) or thrash (multiple `dead` / `budget-exhausted` over time)?
- If MANUAL tier: was the classification one that should have been AUTO/ASSIST?
- `assert_port_clean` is now unconditional. If the patch job aborted with a clean-check failure, that's an env-state problem (uncommitted edits from a prior run, chroot in unknown state), not a patch agent bug.

## Known failure modes (extend this list)

Patterns seen in the wild. When you see a new one, append it here and flag it in your report's "Skill update suggestions" section so the operator folds it in.

- **Empty `changes.diff` with `rebuild_ok=true`.** Agent edited files inside the writable overlay but the runner's diff capture didn't pick it up. Suspected causes: overlay isn't a git working tree, or the diff scope path is wrong. Operator gets "agent fixed" with nothing to land. **Always flag this as a bug.** Confirmed historically for **dops-mode `put_file` writes to `overlay.dops`** (`devel_gperf-20260523-094119Z`) and for **freshly-created `overlay.dops` files** (`multimedia_v4l_compat-20260523-101601Z`) — the bug fires on any `put_file` write the overlay's baseline misses. Verify on every current bundle that the diff is non-empty whenever a `put_file` against `overlay.dops` succeeded.
- **Agent passes origin where `emit_diff` wants a relpath.** Tool signature is `emit_diff(env, origin, relpath)`; the LLM sometimes passes only `origin` (e.g. `"devel/gperf"`). Either the prompt is unclear or the schema is.
- **Agent proceeds against a substrate `assess_dops` flagged as not-yet-converted.** The runner should refuse `state:<state>` and escalate; if the trace shows the patch agent continuing past such a block it's a runner/policy bug.
- **Agent declares a patch "obsolete" based on shallow upstream inspection.** Removing a patch and getting a green dsynth is not proof the patch was actually obsolete — it may have addressed a runtime or platform-specific issue dsynth doesn't catch. Flag when the agent's justification is thin.
- **Wasted `get_file` turn from a mis-guessed offset.** When inspecting a C source file for include directives, the agent sometimes first reads from a non-zero offset and then re-reads from the top, burning a turn. Steer the agent toward `grep` for include-presence checks instead of speculative offset reads.
- **`rebuild_proof.json` missing on budget-exhaustion / give-up bundles.** Expected per the success-gate contract: even on terminal failure the runner should emit a proof JSON with `rebuild_ok=false` and a reason. Operator skimming the artifact list can't distinguish a clean "agent gave up" from "agent crashed mid-attempt." Flag whenever `patch_audit.json` reports a terminal status but `rebuild_proof.json` is absent.
- **Knowledge gap: `.for`-parsed Makefile list variables and value-with-spaces.** Variables like `BINARY_ALIAS`, `MAKE_ENV`, `PLIST_SUB` are iterated by `.for var1 var2 in ${VAR}` which tokenizes on whitespace and expects N words per row. A value with an embedded space (e.g. `BINARY_ALIAS=gmd5sum=md5 -r`) produces the compose error `Wrong number of words (N) in .for substitution list with M variables`. The agent typically misdiagnoses by toggling flags rather than escaping the value or switching to a wrapper script. Worth a dedicated `error-for-substitution-list.md` playbook.
- **Attempt-boundary amnesia.** When attempt 1 fails on a specific value and attempt 2 receives a fresh context, the agent sometimes re-emits the exact same edit that already failed in attempt 1. After Step 42 the failure-context message is just the last 2KB of attempt-1 prose, so if attempt 1's prose didn't surface the specific edit that broke, attempt 2 has no signal. Flag whenever attempt N writes the same `overlay.dops` content as attempt N-1.
- **Premature `materialize_dports` on the consumer origin before the provider overlay is activated.** When a port uses `MASTERDIR` (or otherwise shares compose artifacts with a sibling origin), the agent sometimes materializes the *consumer* origin immediately after writing the dops overlay for the *provider*, before materializing the provider itself. Compose runs against the wrong origin, shows `modes: compat=1`, and the wasted call is only caught because the agent then self-corrects with a second call to the right origin. Seen in `multimedia_v4l_compat-20260523-101601Z`. Prompt should steer the agent to always `materialize_dports` the origin that owns `overlay.dops` first.
- **Target-mismatch ghost (`target @main` in fresh overlay → all ops silently skipped).** When an agent emits a fresh `overlay.dops`, the header MUST be `target @any`. Compose runs against `@2026Q2` (or whatever the env's build target is); per `engine/apply.py:296-313` every op with `target=@main` is filtered with `status="skipped"` and an `info`-level `I_APPLY_TARGET_MISMATCH` diagnostic that didn't bubble to stage output. The `summary applied=0` reads as "patch didn't take" and the agent typically diagnoses it as a compose bug, burning hundreds of K tokens chasing the ghost. **Mitigated by commit `663a8eae819` (compose stage warning `I_COMPOSE_DOPS_ALL_OPS_SKIPPED`).** When analyzing a bundle: verify the fresh overlay has `target @any` and that the warning fires on any dead overlay. Confirmed historically in skalibs / libfyaml / gnome_subr 20260601 bundles.
- **Analysis paralysis — 0 meaningful edits, 0 dsynth_build calls, full ASSIST budget consumed.** Agent investigates indefinitely without committing a hypothesis. Often correlated with: (a) a complex `## Deferred from Convert` section that invites verdict-first investigation, (b) a port class where the agent can't easily map the bug to a dops edit shape, (c) the prompt's "4+ tool calls without an edit = drifting" rule failing to fire (it's soft, no enforcement). The agent never gets concrete substrate feedback because it never tested anything. Reasoning_content can hit 50K+ chars total across the session. Confirmed historically in `lang_python311-20260601-222113Z`. Flag: count of `put_file overlay.dops` and count of `dsynth_build` calls; both zero with `budget-exhausted` is the signature.
- **Static-prompt bloat from `## Port Files` section.** The runner inlines every file under `port/<origin>/` into the user prompt regardless of whether the agent will read it. On ports with many `files/patch-*` + a giant pkg-plist (python311's was 533KB; the inlined section was 48KB of a 96KB user prompt), this section can dominate the static prompt and re-ship 10-12K tokens per turn for files the agent never queries. The agent has `get_file` and can pull on demand; pre-emptive inlining pays a quadratic cost. Confirmed historically in `lang_python311-20260601-222113Z`.
- **Raw `get_file overlay.dops` on multi-target overlays instead of `get_effective_overlay`.** The raw read returns ops for every target; the agent then has to manually filter by the env's compose target. On wide overlays this is both error-prone (silent inclusion of out-of-scope ops in the agent's mental model) and expensive (per-turn carry of unscoped ops). Patch-flow tool `get_effective_overlay` returns the filtered view as structured data. Flag any patch trace that reads raw and never reads effective.
- **Proof-block orphan — successful build but budget cut off before the proof JSON was written.** Distinct from plain `budget-exhausted`: `dsynth_build` returned `rebuild_ok=true` and the agent attempted one more tool call (typically `emit_diff` or a final `get_file`), but the budget check after the LLM turn refused dispatch. The runner then synthesizes a `rebuild_ok=false` proof, writes `manual_handoff.md` instead of `proposed_fix.md`, and classifies the run as `budget-exhausted` — misrepresenting a correct fix as a failure. **Detection signature: `rebuild_proof.json.synthetic=true` AND `changes.diff` is non-empty AND `manual_handoff.md` exists AND the tool_trace shows a successful `dsynth_build` followed by one more tool call.** When you see this, surface it as `[high]` and tell the operator the fix is actually applicable; the handoff document will be misleading because its "Last Failing Build" section reflects the pre-success state, not the actual final outcome. Confirmed in `devel_nspr-20260606-001249Z` (budget overrun ~59K tokens, ~5% over a 1.2M ceiling). Calibration signal: patch-error bundles with N stale patches appear to need ~N × headroom; the current ASSIST budget may be undersized for multi-patch decay.
- **`genpatch` → `install_patches` always fails.** `genpatch` returns an `output_dir` value that is a chroot-internal path (`/root/.cache/dports-dev/envs/<env>/writable/work/genpatch-out`). `install_patches` then looks for files at that path host-side, where it does not exist, and raises `FileNotFoundError`. **Every** trace that calls `install_patches` after `genpatch` will hit this. The correct post-`genpatch` sequence is `get_file <patch_path under wrksrc>` → `put_file dragonfly/patch-<...>` to write the patch directly into the repo. Agents that find this on their own typically burn ~2 turns + ~50K tokens recovering; agents that don't may loop. Flag any trace containing `install_patches` and note whether the agent self-corrected. Real fix is either changing `genpatch`'s return to the host-side sidecar path, or removing `install_patches` from the tool surface entirely. Confirmed in `devel_nspr-20260606-001249Z` turns 8-10.

## Historical (pre-Step-42) failure modes

These were intent-flow-specific (the deleted edit-intent layer: `apply_intent`/`intent_reference` tools, `intent_log.json` artifact, per-directive intent renderers and schemas, `intent-*.md` playbooks). They no longer apply to current bundles, but if you're analyzing an archived bundle from before commit `3788ed20b58` (2026-06-06) you may still see these patterns. The list is also preserved as forensic context for *why* the layer was deleted.

- **Intent-flow: agent retries past `intent_log_full=True`.** Should have escalated to MANUAL — the log either hit count or byte caps.
- **Intent-flow: agent ignores `blocked_by: substrate_invariant`.** Half-migrated substrate had to be resolved by an operator or convert agent before patch intents could land; ignoring the block kept getting refused.
- **Intent-flow: agent ignores `blocked_by: transaction_mode_drift`.** Once the first `apply_intent` pinned `mode_at_apply`, subsequent calls in a different mode were refused.
- **Intent-flow: substrate_diff disagrees with the rendered changes.diff.** Concat of ok=true `substrate_diff` values should have equalled `changes.diff`; drift meant either the diff accumulator was buggy or the canonical-log path was bypassed (`canonical_log_broken=true`).
- **Intent-flow: patch job aborted with `patch_preflight_dirty` or `patch_preflight_error`.** Now subsumed under the unconditional `assert_port_clean` preflight — same diagnosis, the env had uncommitted edits or was in an unknown state.
- **Intent-flow: `change_makefile op=set` appended duplicate `mk set` lines.** Two sequential `set` intents on the same key produced additive substrate_diffs and a final overlay with duplicate `mk set KEY` lines; the agent rationalized "last set wins" in `patch.md`. Confirmed in `databases_redis-20260526-205826Z`. One of the structural reasons the layer was deleted.
- **Intent-flow: `add_patch` for a wrksrc-only target shipped the wrong overlay shape.** Should have been `add_file kind=materialize`; agent typically self-corrected after 2-3 turns. Confirmed in `devel_libuv-20260601-222117Z`.
- **Intent-flow: `drop_patch` left the patch file orphaned on disk.** Removed the overlay reference but not the file under `dragonfly/`; a subsequent `add_patch` with the same target failed with "patch already exists." Confirmed in `devel_libuv-20260601-222117Z`.
- **Intent-flow: pre-emptive `intent_reference` batching.** Agent fetched references for 3-5 intent types upfront before committing to any. Each unused reference was 3-6KB of context carry. Confirmed in `devel_libuv-20260601-222117Z`.
- **Step-27 telemetry: `playbooks_selected` activity rows missing.** Confirmed absent in `databases_redis-20260526-205826Z`. Cause traced to `queue_root` handling in `_log_playbook_selection`. Worth a spot-check on current bundles too — if `playbooks_selected` is still missing post-Step-42 the same regression survived.

## Report shape

Produce something like this (markdown, no fluff):

```
# Agentic analysis — <origin> (<bundle-id>)

## Summary
<2-3 sentences: what the agent tried to do, did it land, is the result trustworthy>

## Pipeline
<which jobs ran, in order, with state>
- triage-1: <classification> (<tokens>)
- convert: <status> (<tokens>)        ← if convert ran
- triage-2: <classification> (<tokens>) ← if convert ran; same/different from triage-1?
- patch: <status>, attempts=N (<tokens>)

## Triage
- Classification: <X> (confidence: <Y>) — <assessment: correct / questionable / wrong>
- Root cause as stated: <quote>
- Actual root cause from logs: <if different>
- Round changes: <if 2 triages, same/different conclusion, why>

## Convert (if convert ran)
- Status: <verified / failed / no-op>
- reapply_ok: <true / false>
- Overlay target directive: <@any (correct) / @main (REGRESSION) / other>
- Deferred patches: <count> [<paths>]
- Tokens: <prompt / completion / total>

## Patch
- Status: <success / needs-help / budget-exhausted / blocked-by-substrate>
- Attempts: <N> / tier max
- Tokens: <prompt / completion / total>
- Tool sequence: <one-line summary of the trace>
- overlay.dops edits: <N put_file writes, each followed by validate_dops? Y/N>
- Fix narrative: <what patch.md claims>
- Fix verdict: <is the fix real?>

## Per-bundle metrics
- Turn-to-first-meaningful-edit: <N> (floor ~10-15 on clean success; 0 = paralysis)
- overlay.dops edits emitted: <N> / reversed mid-attempt: <M> (self-correction count)
- dsynth_build calls: <N> (must be ≥1 for any meaningful run)
- After dsynth_build success/fail, verification approach: <tool exit code only / grep extracted source for symptom / no build run>

## Materialize signal (P0a/P0b regression check)
- materialize_dports calls: <N>
- Each call's summary: <applied=N skipped=M errors=K, warnings present>
- I_COMPOSE_DOPS_ALL_OPS_SKIPPED: <absent on every materialize (healthy) / present on N calls (overlay target mismatch)>
- Any fresh overlay with `target @main`? <yes/no> (yes = P0a regression — flag immediately)

## Path discipline
- /work/DPorts/<origin> reads: <count> (forbidden; lock root)
- /xports/ or other non-/work paths passed to chroot tools: <count>
- Hand-constructed /work/obj paths not derived from extract.wrksrc: <count>
- Host-side path leaks (e.g. genpatch output_dir confusion): <count>
- Raw `get_file overlay.dops` on a multi-target port (should use get_effective_overlay): <count>

## Output contract
- changes.diff: <bytes> — <ok / empty-bug / mismatched>
- proposed_fix.md: <present and references diff / present but stale / absent on success (bug)>
- manual_handoff.md: <present on escalation / absent on escalation (bug)>
- rebuild_proof.json: <present / absent on terminal failure (bug)>

## Playbooks (Step 27 + Step 42)
- Triage: included=<list or "—">, skipped=<count> — <looks right / suspicious / missing event>
- Patch: included=<list or "—">, skipped=<count>, flow-patch.md present? <yes/no>
- Convert (if convert bundle): included=<list>, skipped=<count> — <…>

## Token shape
- Static prompt (system + user): <bytes> ≈ <tokens>/turn × <N> turns = <subtotal>
- User prompt composition (if >50KB): break down by `## section`, flag sections >10KB the agent never read
- Reasoning_content cumulative: <chars>; single-turn outliers >5KB: <list>
- Top-3 biggest tool returns: <name, bytes>
- Estimated breakdown by source vs. actual total from `patch_result`: <static/reasoning/tool_carry/completion percentages>

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

Keep it terse. The operator skims this. Per-bundle metrics + materialize signal + path discipline are the *minimum* data set — every bundle gets these even when nothing surprising shows up, so we can spot drift over time.

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
