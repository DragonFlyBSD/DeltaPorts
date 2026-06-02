# Patch agent — deep cross-bundle analysis

Analysis of patch-agent failures on the 2026Q2 build line, performed by
walking session-dump JSONLs from five bundles against the runner code.

## TL;DR

The agent is mostly behaving correctly given its prompt. **Three substrate/runner bugs account for most of the budget burn**, with one *dominant* bug — `target @main` — silently breaking every fresh patch agent run:

1. **`target @main` in fresh overlays makes every op a no-op** (`scripts/generator/dportsv3/agent/edit_intent/_dops.py:672`). Build target is `@2026Q2`; overlay header says `@main`; compose at `scripts/generator/dportsv3/engine/apply.py:296-313` marks all ops `status="skipped"` with `I_APPLY_TARGET_MISMATCH`. The agent's edits never reach the build.
2. **`applied_ops` summary metric is misleading**. `summary: ops=N applied=0` reads as failure but actually means "N ops registered, 0 *compat-mode* ops applied". Dops-mode applied ops show only in per-stage `changed=N`. The agent reads `applied=0`, concludes its patch didn't take, and chases a non-existent bug.
3. **`from_dupe=true` / dupe+genpatch escape hatch is broken**. `worker.py:163-178` (`_resolve_chroot_path`) rejects the host path that `genpatch` (`worker.py:2389`) returned (`output_dir=str(genpatch_out)` is a HOST path, but the tool surface elsewhere uses chroot paths). Crashes with `ValueError: path must be under /work`.

Behavioural problems (visibility-ghost misdiagnosis, schema-rejection learning gap) are real but **secondary** — they're symptoms of bug #1 + #2.

## Methodology

Five bundles with full LLM session dumps (`analysis/sessions/*.jsonl.gz`) walked turn-by-turn:

- `devel_skalibs-20260601-073525Z` (1 attempt, 30 turns, ghost-chase + broken from_dupe)
- `textproc_libfyaml-20260601-092914Z` (2 attempts, 64+37 turns)
- `audio_alsa-plugins-20260601-092926Z` (1 attempt, 57 turns, substrate damage)
- `sysutils_gnome_subr-20260601-100820Z` (2 attempts, 34+24 turns)
- `net-mgmt_net-snmp-20260601-100812Z` (convert agent only, never reached patch)

Each session was read at the level of every assistant turn's `reasoning_content`, every tool call's arguments, and every tool result's payload — then cross-referenced against the implementing code paths (`attempt_loop.py`, `prompts.py PATCH_INTENT_SYSTEM`, `tools.py`, `worker.py`, `edit_intent/_dops.py`, `engine/apply.py`, `compose_reporting.py`).

## Per-bundle ledger

| bundle | tokens | turns | first apply_intent | failure root cause |
|---|---|---|---|---|
| skalibs 20260601-073 | 1,236,533 | 30 (1 attempt) | T14 | **target @main → patch skipped silently** → 25-turn ghost chase → broken from_dupe |
| libfyaml 20260601 att1 | 868,821 | 64 | T23 | **target @main → patch skipped** → ghost chase via wrong verification path |
| libfyaml 20260601 att2 | 332,568 | 37 | n/a (verified instead) | inherits broken overlay from att1; diagnoses incomplete fix (multi-file bug) but runs out of budget |
| alsa-plugins 20260601 | 1,249,656 | 57 (1 attempt) | T14 (after extract recovery) | Substrate damage from prior convert agent (recursive `OPTIONS_DEFAULT`) burned ~10 turns; large-patch porting (16KB FreeBSD patch) burned the rest |
| gnome_subr 20260601 att1 | 696,221 | 34 | T18 | **target @main → all 3 ops skipped** → reads working `pkg` overlay for reference (15 turns spent) |
| gnome_subr 20260601 att2 | 543,706 | 24 | T29 (after 28 turns of analysis) | Discovers `DISTFILES=# none` — but no intent type can text-edit a port Makefile when there's no WRKSRC. ~280K of reasoning_content accumulation. |
| net-snmp 20260601 | 174,913 | ~10 (convert, not patch) | n/a | Convert agent stopped mid-loop after one `validate_dops` parse error |

## The four codepath findings

### Finding 1 — `target @main` in fresh overlay → all ops silently skipped (THE root cause)

**File:** `scripts/generator/dportsv3/agent/edit_intent/_dops.py:666-677`

```python
def _initial_overlay_header(t) -> str:
    """Matches the convention emitted by ``migration.convert``."""
    return (
        f"target @main\n"      # <-- THIS
        f"port {t.origin}\n"
        ...
    )
```

**File:** `scripts/generator/dportsv3/engine/apply.py:296-313`

```python
if op.target not in {"@any", target}:
    row = ApplyOpResult(
        ...
        status="skipped",
        message="target-mismatch",
        diagnostics=[_diag(
            severity="info",
            code="I_APPLY_TARGET_MISMATCH",
            message=f"op target {op.target} does not match requested target {target}",
            ...
        )]
    )
```

Compose runs against `target="@2026Q2"`. Header says `@main`. `@main not in {"@any","@2026Q2"}` → every op silently skipped. **`info`-level diagnostic, not surfaced in the agent-visible summary line.**

Verified: every production `overlay.dops` under `ports/*/overlay.dops` uses `target @any`. None use `@main`. The intent translator's default is the outlier.

This single bug explains the patch failure of **skalibs, libfyaml att1, and gnome_subr (both attempts)** — at least 4 of 7 walked sessions.

### Finding 2 — `summary: applied=0` is misleading

**File:** `scripts/generator/dportsv3/compose_reporting.py:330-339`

```python
lines.append(
    "summary: "
    f"ports={result.summary['port_total']} "
    f"ops={result.summary['total_ops']} "
    f"applied={result.summary['applied_ops']} "   # <-- counts ONLY non-skipped
    f"fallback={result.summary['fallback_patch_count']} "
    f"errors={result.summary['errors']}"
)
```

When every op gets `status="skipped"` (Finding 1), `applied_ops=0`. The per-stage line `[ok] apply_semantic_ops: changed=N skipped=M` shows `changed` (which counts non-skipped applications), but `summary applied=` is the headline number the agent reads first.

The agent in all three visibility-ghost runs (skalibs T39, libfyaml-1 T39, gnome_subr-1 T30) explicitly cited "applied=0" as proof the patch didn't apply. Quoting libfyaml-1 turn 39 reasoning: *`"summary: ports=1 ops=1 applied=0 fallback=0 errors=0" — So it says ops=1 but applied=0. The op was recognized but not applied!`*

This is **technically correct** given Finding 1, but the agent reasonably interpreted "skipped" as "tried-and-failed" rather than "never-attempted-due-to-target-mismatch". The `I_APPLY_TARGET_MISMATCH` diagnostic that would have told it the real reason is suppressed in the human-formatter rollup.

### Finding 3 — `from_dupe`/genpatch escape hatch is broken

**File:** `scripts/generator/dportsv3/agent/worker.py:163-178`

```python
def _resolve_chroot_path(paths: EnvPaths, chroot_path: str) -> Path:
    if not (chroot_path == "/work" or chroot_path.startswith("/work/")):
        raise ValueError(f"path must be under /work (got {chroot_path!r})")
    ...
```

**File:** `scripts/generator/dportsv3/agent/worker.py:2336, 2389`

```python
genpatch_out = paths.writable / "work" / "genpatch-out"  # HOST path
...
return _exec_result(
    ...
    output_dir=str(genpatch_out),   # <-- host path returned to LLM
    ...
)
```

`genpatch` returns `output_dir` as the **host path** (`/root/.cache/dports-dev/envs/2026Q2/writable/work/genpatch-out`). The tool surface elsewhere expects in-chroot paths (`/work/...`). When the agent tries to inspect/list the output dir (skalibs turn 60, libfyaml-1 turn 63 truncated), the path validator rejects it.

This blocks the documented "intent didn't take effect → fall back to dupe/genpatch" recovery. Combined with Finding 1, the agent has no working recovery path.

### Finding 4 — gnome_subr-class ports have no usable intent surface

`sysutils/gnome_subr` has `DISTFILES=# none` (subroutine port, no source). The patch fix requires modifying its `Makefile` directly. Available intents:

- `change_makefile` → emits `mk set` (Makefile variable override evaluated at build time, **after** the license check that's failing)
- `add_patch` → applies a patch to WRKSRC files (no WRKSRC exists)
- `add_file` (kind=materialize) → copies a file into `files/` (doesn't help if you need to edit the *port Makefile*)

The intent grammar has no way to text-edit a port Makefile. The agent in gnome_subr att2 discovered this at turn 27 (15,851-char reasoning) but had no path forward — burned 280K tokens of reasoning carried across remaining turns.

## What I retract from earlier diagnoses

- **"Apply without verify"** — wrong metric. The agent rebuilds promptly when there's a logical fix-attempt boundary. The 5:1 ratio counted intra-recovery edits as missed verifications.
- **"Exploration bloat"** — the new prompt's "4+ tool calls is drifting" rule is being respected on all 4 sessions I walked (skalibs T14, libfyaml-1 T23, alsa-plugins T14, gnome_subr-1 T18, gnome_subr-2 T29 — the last is the only outlier and was doing genuinely-necessary diagnosis).
- **"Visibility ghost is a behavioural problem"** — NO. The visibility ghost is the *symptom*. The cause is the `applied=0` summary metric (Finding 2) + `target @main` default (Finding 1). The "ghost" is real — patches genuinely aren't applying.
- **"Static prompt is the dominant cost"** — still significant (~40-50%) but not actionable until the substrate bugs are fixed, since the agent is currently burning 30+ extra turns chasing the ghost. Fix Findings 1-2 first, then ~half the runs would finish in ~15 turns instead of 30-60.

## Token-cost composition (revised across all 5 patch sessions)

| source | typical share | mechanism |
|---|---|---|
| Static system+user prompt | 30–48% | re-shipped on every turn (12.7K + 22-66K bytes) |
| **Ghost-chase turns** | **20–50%** | turns 24-60+ on skalibs/libfyaml-1/gnome_subr-1: pure waste caused by Findings 1+2 |
| reasoning_content accumulation | 5–25% | deepseek thinking carry; dominant on gnome_subr-2 (~280K) |
| Large-patch I/O | <10% (only alsa-plugins) | 16KB patch read in chunks + intent return |
| Tool-result payload carry | 5–10% | dsynth_log ~16KB, intent_reference ~4-5KB |
| Schema-rejection recovery | 2–5% | drop_patch missing `target`/`reason`, change_makefile missing `path` |

## Code-grounded fix proposals (prioritized by leverage)

### P0 — Change one line; unblocks ~half the patch runs

**Fix `_initial_overlay_header` to emit `target @any`** in `scripts/generator/dportsv3/agent/edit_intent/_dops.py:672`:

```python
return (
    f"target @any\n"   # was @main; @any matches all build targets
    f"port {t.origin}\n"
    ...
)
```

Verification: all production overlays use `@any`. The comment "Matches the convention emitted by migration.convert" suggests the convert agent already emits `@any` (consistent with alsa-plugins's `target @any` left by convert at attempt 1 turn 22). The intent flow's header drifted from this convention.

Predicted leverage: skalibs / libfyaml-1 / gnome_subr (both attempts) would have their patches actually apply at compose time. Whether each then succeeds depends on whether the patch is otherwise correct, but at minimum the agent's dsynth_build would either pass or fail with a *different* error — letting the agent iterate productively instead of ghost-chasing.

### P0 — Make the summary line honest

**File:** `scripts/generator/dportsv3/compose_reporting.py:330-339`

Either:
- Surface `skipped_due_to_target_mismatch` separately in the summary line, OR
- Promote `I_APPLY_TARGET_MISMATCH` to a `warning` severity (currently `info`) so it appears in `[warning] apply_semantic_ops: ...warnings=N` and the agent sees a hint

Even better: in the summary line when `applied=0 AND skipped>0`, append `note: N ops skipped (likely target mismatch — check 'target @any' in overlay.dops)`.

### P1 — Fix the genpatch host-path leak

**File:** `scripts/generator/dportsv3/agent/worker.py:2389`

Return the in-chroot path, not the host path:

```python
output_dir="/work/genpatch-out",   # chroot path
# or remove output_dir entirely if the agent shouldn't need to inspect it
```

This is the documented escape hatch from the system prompt. It being broken means agents that correctly notice "the dops apply didn't work" have no recovery and burn the rest of the budget thrashing.

### P1 — Intent surface for no-WRKSRC ports

Add `text_replace_in_port_makefile` intent (or extend `change_makefile` with an `op: "replace_text"` mode that does literal text replacement on the port's Makefile, not a `mk set` override). Gates only when the port has `DISTFILES=# none` or similar (`text.replace_once` already exists as an executor; just expose it as an intent type).

Predicted leverage: enables gnome_subr-class fixes. Probably ~5% of compile/configure-error bundles.

### P2 — Tighten the "verify by symptom" guidance in the prompt

In `prompts.py PATCH_INTENT_SYSTEM`, after the "MANDATORY OPENING PROCEDURE" section, add:

> **When `dsynth_build` fails after an `apply_intent`:** before assuming your edit didn't land, **grep the extracted source for the original error symptom** (e.g. the symbol the compiler complained about). If the symptom is gone, the edit worked and the failure is from *another* site — read `dsynth_log` for the new error. If the symptom is still present, look at `materialize_dports` output for `I_APPLY_TARGET_MISMATCH` warnings (your overlay's `target` must be `@any` to apply).

libfyaml attempt 2 turns 15-17 demonstrate this technique working correctly. Once the prompt makes it explicit, the runner-side fixes #1 and #2 will compound.

### P3 — Carry the intent log across attempts more prominently

`agent/patch.py:_format_intent_log_summary` already builds a one-line-per-entry summary. The failure-context message at `attempt_loop.py:79-95` includes it after a 2KB "Tail of your prior response" block. On gnome_subr attempt 2, the agent re-submitted the same `drop_patch` schema rejection. The summary is being shipped but at the bottom of the failure context — easy to skip.

Move the prior-intent-summary to the top of the failure-context message, ahead of the narrative tail.

### P4 — Drop the `dsynth_log` payload size

`worker.py:dsynth_log` returns up to ~16 KB. The agent rarely uses more than the bottom 1-2 KB (the actual error). Default `tail_lines` to 50 (currently appears to default high) and have the tool description explicitly say "Use grep on the log path if you need broader context."

Predicted leverage: 20-30K tokens saved per dsynth_log call.

## What's actually fixable

If P0a (target @main → @any) and P0b (honest summary) both land:

- **skalibs**: patch would apply; build would pass (single-file fix, agent's `__BSD_VISIBLE` diagnosis is correct)
- **libfyaml**: first patch would apply; agent would see the second-file error from dsynth_log; emit a second add_patch; build would pass. Probably in ~25 turns instead of 64+37.
- **gnome_subr**: change_makefile + add_file would actually apply at compose time. Whether the build then passes depends on whether `mk set LICENSE_FILE` overrides at the right phase (license check is in `make patch` phase; `mk set` overrides should take effect). If not, still need P1 (text-replace intent) for this class of port.
- **alsa-plugins**: already wasn't a target-mismatch case; would still need the 16 KB patch read. P4 (smaller dsynth_log) might save enough for the agent to finish before budget exhaustion.

In short: **one-line fix (P0a) likely unblocks 60-70% of compile-error patch failures on the 2026Q2 build line.**

---

# Ongoing findings from later bundle walks

Bundles analyzed after the initial cohort. Each entry: the bundle ID, what failure mode it surfaced, and what would actually help. New entries appended at the bottom; the same fix may show up under multiple bundles when the evidence reinforces it.

## `lang/python311` (`lang_python311-20260601-222113Z`)

Triage class: `plist-error`. Pipeline: triage → convert (succeeded) → triage again (re-classify against converted substrate) → patch. Patch run burned **1.24M tokens, 20 turns, 0 intents emitted, 0 dsynth_build calls** — pure analysis paralysis on the deferred-from-convert pkg-plist diff.

**Not** a target-mismatch case (the recent P0a/P0b fixes don't apply here — the overlay already existed from convert, agent never reached materialize-after-intent).

Highest-leverage observations:

1. **Trim the "Port Files" section of the patch agent's user prompt.** On this run it was **48,700 bytes** out of a 96,707-byte user prompt — half the static cost, ~12K tokens re-shipped per turn × 20 turns ≈ 240K tokens. The agent has `get_file` and pulled files on demand anyway; inlining everything pre-emptively pays for files the agent never reads. Worst on ports with many `files/patch-*` and a giant pkg-plist (python311's pkg-plist alone is 533 KB). The build-time cost scales with the port's file count, not with what the agent actually needs.

2. **Hard turn-budget gate for pure investigation.** Prompt currently says "if you're 4+ tool calls in without an intent you're drifting" (line 471) but there's no enforcement. The agent went to 25+ tool calls / 0 intents. A genuine gate: after N tool calls, the next turn MUST be `apply_intent` or `Rebuild Status: gave-up`. Soft warnings don't fire on the kind of port where analysis is genuinely complex — which is exactly when the rule is most needed.

3. **Require at least one `dsynth_build` before budget exhaustion.** The agent never tested anything. Even a wrong intent → build → log would have given ground truth and broken the analysis loop. Pair with #2: "before you can emit `Rebuild Status: gave-up`, you must have run dsynth_build at least once."

4. **Clarify or enforce the `/work/DPorts` directory rule.** Prompt says "you may NOT read from /work/DPorts" (line 590) but the agent read it 5 times. The "DPorts/DeltaPorts/compose root" naming triad blurs together. Either (a) make the worker refuse `grep`/`get_file` against `/work/DPorts/<origin>/...` with a hint pointing at the compose root, or (b) drop the warning if `DPorts` content actually matches current state in practice (it did in this env — DPorts pkg-plist had the current DragonFly overlay applied). The current warning isn't load-bearing in either direction.

5. **Plist-error / deferred-from-convert verdict-first nudge.** When the payload includes a `## Deferred from Convert` section, prime the agent toward verdict-first action: "Your FIRST intent should be a verdict (`dropped` for obsolete parts, `add_patch`/`replace_in_patch` for regenerated parts). Don't investigate the framework-level cause of why upstream changed — just emit the verdicts and let dsynth tell you what's still broken." The agent in this run knew about the verdicts (the prompt mentions them) but treated the investigation as a prerequisite to the verdict.

None of these are P0 — the P0a/P0b shipped fixes were the right call for the dominant compile-error class. These are a different failure cluster that the prompt's discipline rules and prompt-bloat profile need to handle separately. Worth picking up if more plist-error / deferred-from-convert bundles repeat the pattern.

## `devel/libuv` (`devel_libuv-20260601-222117Z`) — successful counterexample

Triage class: `compile-error`. Pipeline: triage → convert (clean, no deferred patches) → patch. Patch run **succeeded in one attempt**: 5 intents emitted, 1 build, `rebuild_ok=true`, ~956K tokens across 24 turns. First success bundle walked with both P0a and P0b shipped — confirms target-mismatch ghost is closed.

What success looks like, calibrated:

1. **Turn-to-first-apply_intent: 15.** Three opening turns (env_verify / materialize / extract) + ~6 investigation turns (overlay/Makefile.in reads, FREEBSD_TRUE/DRAGONFLY_TRUE greps) + a 5-call `intent_reference` batch + 1 final Makefile.in read for hunk context. Higher than the prompt's "drifting at 4+" hint, but the run worked. Successful runs apparently navigate by domain feedback only — the meta-discipline rules ("you're drifting") do not fire on the cases where they'd be useful, because successful agents don't notice them and stuck agents don't either.

2. **Self-correction is the actual signal of health.** At turn 36 the agent emitted `add_patch(target=dragonfly/patch-Makefile.in)` with an inline diff, which produces the wrong overlay shape — `patch apply` instead of `file materialize`. Materialize at turn 38 failed with `E_COMPOSE_APPLY_FAILED / No file to patch`. Turn 40 reasoning (6,723 chars) recovered: *"the patch install shape (`patch apply`) tries to apply at compose time before the source is extracted. Makefile.in needs `file materialize` instead."* The agent dropped the bad intent, switched to `add_file kind=materialize` reusing the orphaned patch file, materialized cleanly, built clean. **This is the structural difference between this success and python311's failure**: libuv committed an intent → got concrete substrate feedback → had something specific to reason about → recovered. python311's agent never committed anything → never got feedback → never recovered. The hypothesis "agent paralysis = no concrete signal to anchor on" now has a paired comparison.

3. **P0a/P0b confirmed working.** First materialize (turn 5, pre-edit) shows `applied=2, modes: dops=1` — the convert-produced overlay is live and target-matching. No `I_COMPOSE_DOPS_ALL_OPS_SKIPPED` warning anywhere in the session. Convert's overlay header read `target @any`. Intent translator's substrate_diffs at turns 32+ append to the existing overlay rather than creating a new one, so the P0a default isn't exercised on this bundle — but if it ever regressed, P0b would have surfaced it loudly on the post-edit materialize.

4. **Budget headroom is tight.** Used 956K of the 1.2M ASSIST budget — ~250K cushion. A second wrong-shape intent or one more `intent_reference` round would have eaten it. Successful runs on non-trivial ports aren't comfortably under budget; they're barely under budget. Worth knowing when reasoning about which interventions are safe to skip.

5. **Pre-emptive intent_reference batching is real, even on a clean run.** The agent fetched 5 references (`replace_in_dops_block`, `drop_patch`, `add_patch`, `change_makefile`, `replace_in_patch`) before emitting any intent. Used 3 of the 5; the other 3 are ~12-15K bytes of context carry for no gain (cumulative quadratic cost: ~30-50K tokens by session end). The discipline rule in the prompt says "call before each new intent type" — successful agents read this as license to bulk-fetch upfront. Worth a prompt nudge: "fetch per-type immediately before use, not speculatively."

Two distinct intent-flow bugs surfaced and are now in the failure-mode catalog:

- `add_patch` ships `patch apply` overlay shape regardless of target file's location. Wrong when the target lives in wrksrc (Makefile.in, configure, source code) — should ship `file materialize` instead. Costs a 2-intent detour per occurrence.
- `drop_patch` removes the overlay reference but leaves the patch file on disk. Subsequent `add_patch` for the same target fails with "patch already exists". The agent worked around it via `add_file source=<orphan path>` — clever but fragile.

## Failure-mode catalog (running)

A short index of the *classes* of failure observed so far, with which fix landed (or didn't):

| failure mode | first seen | leverage fix | status |
|---|---|---|---|
| Target-mismatch ghost (`target @main` default + misleading `applied=0` summary) | skalibs / libfyaml / gnome_subr 20260601 | P0a (`_dops.py` `@any`), P0b (compose stage warning) | **shipped** |
| Substrate damage from prior agents (e.g. recursive `mk set OPTIONS_DEFAULT`) | alsa-plugins | needs prior-agent dops correctness checks | open |
| Large-patch porting cost (>16 KB inline patch reads + intent returns) | alsa-plugins | likely needs incremental patch construction primitive | open |
| Multi-file fix gap (same bug in N headers, agent only sees first) | libfyaml | possibly "after build fails, grep extracted source for symptom across full wrksrc" | open |
| No-WRKSRC port can't be patched via intents | gnome_subr | needs text-edit-port-Makefile intent | open |
| `dupe`/`genpatch` returns HOST path, validator rejects it | skalibs / libfyaml | fix `worker.py:2389` to return `/work/...` path | open |
| Schema rejection not learned across attempts | gnome_subr (drop_patch `target` field) | move prior-intent-summary to top of failure-context message | open |
| Analysis paralysis (0 intents, 0 builds, full budget burned) | python311 | hard turn-budget gate + mandatory dsynth_build before give-up | open |
| Static-prompt bloat on multi-file ports (Port Files section) | python311 | trim "Port Files" from user prompt; agent has get_file | open |
| Convert agent hits validate_dops parse error and gives up | net-snmp | small budget + no retry on parse fix | open |
| `add_patch` for wrksrc-only target ships `patch apply` (should be `file materialize`) | libuv | distinguish in `intent_reference(add_patch)` and/or in translator | open |
| `drop_patch` removes overlay ref but leaves patch file orphaned | libuv (and old skalibs) | delete the file, or expose `also_remove_file` flag | open |
| Pre-emptive `intent_reference` batching (fetch types never used) | libuv | prompt nudge: fetch per-type immediately before use, not speculatively | open |

## Method note: what to check on each new bundle

After every walked bundle, the questions to answer (added here so future walks don't drift):

1. Did the agent's first `apply_intent` arrive within ~5 tool calls of session start? If not, why — and would a hard gate have helped?
2. After the first `apply_intent`, did `materialize_dports` show `applied=N>0`? If 0, is `I_COMPOSE_DOPS_ALL_OPS_SKIPPED` warning in the stage line? If not, why not (regression of P0a, or a new class of skip)?
3. After the first `dsynth_build`, did the agent verify by grepping the extracted source for the original error symptom? Or did it jump to "compose bug" / "patch didn't apply"?
4. How many intents were emitted in total? How many built? Ratio sane?
5. If a second attempt fired, did its first turn reflect the prior intent log (no re-submitting failed intent types)?
6. Total tokens broken into: static prompt × N turns, reasoning_content carry, tool result carry, completion. Which line item dominates?
7. Any reads of `/work/DPorts/...`? `/work/obj/...` hand-constructed paths? `/xports/...` chroot-internal paths leaking host paths?

If a finding is a one-off (one bundle, plausibly idiosyncratic), note it but don't generalize. If two bundles in different categories repeat it, promote to the failure-mode catalog above with a proposed fix.

