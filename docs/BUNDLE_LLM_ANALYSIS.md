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
