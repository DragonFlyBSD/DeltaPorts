# Edit-intent DSL — design (Step 25a)

> **Status: draft for review (2026-05-25).** Normative for the
> Step 25b implementation. Sections marked **DECISION** below have
> default answers; the operator can override before 25b starts.
>
> Cross-reference: `docs/agentic-consolidation-plan.md` Step 25,
> `docs/agentic-loop-brittleness-brief.md`, `docs/dsl-v0.md`
> (the substrate dops grammar).

## 1. Purpose

The agent stack has eight years of accumulated bandages around one
hole: the patch agent has no framework for *what change it is
making*. It writes files; the runner observes via `git diff`; both
sides have blind spots that produce production bugs (empty diffs,
half-migrations, verify drift, partial-staging leaks).

This document specifies a small DSL the patch agent emits instead
of file writes, a translator that renders each intent into the
right substrate edit (compat or dops), a transactional execution
model that bounds workspace state, and an intent log that becomes
the canonical artifact replacing `analysis/changes.diff` as the
authoritative record of a patch attempt.

The design is normative for Step 25b implementation. If 25b finds
a case the design doesn't cover, the design is wrong; revise here
first.

## 2. Two-tier state model

The env's writable overlay is bifurcated:

```
┌─ Baseline ─────────────────────────────────────────────────────┐
│  git HEAD of <env>/writable/work/DeltaPorts                    │
│  Operator-controlled. Changes only on explicit operator action │
│  (or convert; see §6.2).                                       │
└─────────────────────────────────────────────────────────────────┘
            │
            │ applied on top at job-start
            ▼
┌─ Ephemeral ────────────────────────────────────────────────────┐
│  Intent log for the current job                                │
│  Captured at job-end (pass or fail). Then DISCARDED:           │
│    git -C work/DeltaPorts checkout HEAD -- ports/<origin>/     │
│    git -C work/DeltaPorts clean -fd ports/<origin>/            │
└─────────────────────────────────────────────────────────────────┘
```

Properties:

- The bundle's `analysis/intent_log.json` is the canonical record.
- Verify replays the intent log against any env at the same git
  HEAD; the env's prior ephemeral state is irrelevant because
  cleanup happens at the previous job's end.
- The patch agent cannot accumulate state across jobs. Today's
  drift class is structurally impossible.

## 3. Intent grammar

### 3.1 Format

Each intent is a single-line JSON object. The intent log is a
JSON array; one element per intent in execution order:

```json
[
  {"type": "drop_patch",
   "target": "dragonfly/patch-lib_getopt.c",
   "reason": "obsolete: upstream gperf-3.3 unconditionally includes <string.h>"},
  {"type": "add_patch",
   "target": "dragonfly/patch-src_main.c",
   "diff": "--- src/main.c.orig\n+++ src/main.c\n@@ ..."}
]
```

JSON, not a custom grammar, on purpose: the LLM emits JSON
natively; one round of validation rather than a parser; trivial
to round-trip through the bundle and the tracker UI.

### 3.2 Intent types (v0)

Seven types. Each spec: required fields, optional fields, compat
rendering, dops rendering, expected diff shape.

#### 3.2.1 `replace_in_patch`

Edit a single hunk inside an existing patch file. The most common
shape (a context line drifted, a function name changed upstream).

**Required:** `target` (relpath under `ports/<origin>/`),
`find` (string), `replace` (string).
**Optional:** `occurrence` (int, default 1; which match to
replace if `find` is non-unique).

**Compat render:** read `ports/<origin>/<target>`, replace
the Nth occurrence of `find` with `replace`, write back. Refuse
(intent error) if `find` not found, or if `occurrence` exceeds
match count.

**Dops render:** emit
`text.replace_once { file=<target>, from=<find>, to=<replace> }`
into `overlay.dops`.

**Expected diff shape:** single-file modification, two-line
hunk (find/replace) at minimum.

#### 3.2.2 `drop_patch`

Declare an existing patch obsolete and remove it.

**Required:** `target` (relpath, `dragonfly/patch-*` or a dops
`patch apply` statement reference), `reason` (string, will land
in the intent log + commit message).

**Compat render:** delete `ports/<origin>/<target>`.
**Dops render:** remove the corresponding
`patch apply <target>` statement from `overlay.dops`.

**Expected diff:** single-file deletion (compat) or single-line
`overlay.dops` modification (dops).

#### 3.2.3 `add_patch`

Introduce a new patch for a file the port doesn't currently
touch.

**Required:** `target` (relpath, where the new patch lives),
`diff` (full unified diff bytes against the upstream source).
**Optional:** `from_dupe` (boolean — `True` means this patch was
captured via `dupe` + `genpatch`; the diff field will be
populated by the runner, agent supplies `target` + `from_dupe`
only).

**Compat render:** write the diff to
`ports/<origin>/<target>`.
**Dops render:** write the diff to
`ports/<origin>/<target>` AND emit `patch apply <target>` into
`overlay.dops`.

**Validator:** the diff must apply cleanly against the
current upstream source (the translator runs `patch --dry-run`
during application).

**Expected diff:** new file creation (compat) plus possibly a
single-line `overlay.dops` addition (dops).

#### 3.2.4 `add_file`

Add a port-local file (resource) or stage a file from the
DragonFly source tree.

**Required:** `dest` (relpath under `ports/<origin>/`),
`kind` (`"resource"` | `"materialize"`).
**Required iff kind=resource:** `content` (string).
**Required iff kind=materialize:** `source` (relpath in
DragonFly source tree).

**Compat render:** write content directly to `dest`; for
`materialize`, copy from the source tree into the dragonfly/
subdir.

**Dops render:** emit
`file.copy { from=<source>, to=<dest> }` or
`file.materialize { from=<source>, to=<dest> }` into
`overlay.dops`. Don't write file contents directly — dops
references them.

**Expected diff:** new file creation (compat); single-line
`overlay.dops` addition (dops).

#### 3.2.5 `change_makefile`

Edit a Makefile variable.

**Required:** `path` (relpath, e.g. `Makefile.DragonFly` or
`Makefile`), `key` (var name), `value` (string),
`op` (`"set"` | `"append"` | `"remove"`).

**Compat render:** parse the Makefile, apply the op, write back.
**Dops render:** emit
`mk.var.set { var=<key>, value=<value> }` (or `.append` / `.remove`)
into `overlay.dops`.

**Expected diff:** single-file Makefile modification (compat);
single-line dops addition (dops).

#### 3.2.6 `bump_portrevision`

Operator-flag intent: increment PORTREVISION. Metadata, no file
content change beyond the Makefile bump.

**Required:** none beyond intent presence.

**Compat render:** edit the Makefile's `PORTREVISION` line
(insert if missing).
**Dops render:** emit
`mk.var.set { var=PORTREVISION, value=<n+1> }`.

**Expected diff:** single-line Makefile modification.

#### 3.2.7 `convert_to_dops` (convert-only; see §6.2)

Lift a compat port to dops. Restricted to the convert agent; the
patch agent cannot emit this intent. Declared here so the grammar
is closed.

**Required:** none beyond intent presence (the convert agent
runs against one origin at a time).

**Compat render:** N/A (mode is "this intent migrates compat to
dops").
**Dops render:** generate `overlay.dops` from the existing
`Makefile.DragonFly` (deterministic translation via the existing
`migration.convert` machinery), then delete the source files.

**Expected diff:** new `overlay.dops` creation + deletion of
`Makefile.DragonFly` and any other legacy artifacts in one atomic
intent.

### 3.3 Reserved for v1+

These come up in design discussion but are deferred:

- `apply_upstream_commit` — apply a single upstream commit as a
  patch by SHA. Useful but requires an upstream-checkout helper
  that doesn't exist.
- `bisect` — operator-driven, not agent-driven.
- `rename_patch` — currently expressible as
  `drop_patch` + `add_patch`.

## 4. Transaction semantics

Each agent run is one transaction. The lifecycle:

```
BEGIN  → translator constructed for (env, origin); mode resolved
       │   from classify_dops. Workspace expected clean (assertion).
       ▼
EMIT   → agent calls apply_intent(intent_json) N times. Each call:
       │     1. validator.check(intent, current_state)
       │     2. translator.render(intent, mode) → substrate ops
       │     3. apply substrate ops to env
       │     4. append intent to in-memory log
       │   Failure at validator OR substrate apply rolls back THIS
       │   intent's effects; the log records the attempt as failed
       │   but applied intents stay applied (per-intent atomicity,
       │   not whole-job atomicity).
       ▼
COMMIT → on PATCH_OK (rebuild_ok=true): write intent_log.json to
       │   the bundle; the workspace reset (§5) discards the
       │   ephemeral state.
ABORT  → on PATCH_GAVE_UP / PATCH_BUDGET_OUT / runner crash: write
           the intent log to the bundle anyway (operator forensics);
           workspace reset runs.
```

### 4.1 Atomicity unit

**Per-intent.** Not per-job. The agent emits intent A, it
applies, the agent emits intent B, B fails validation: A stays,
B is rolled back, the agent can either emit a different B or
give up. This matches today's `put_file → put_file → put_file`
mental model.

**Whole-job atomicity is reserved for v1+** — it would require
the translator to defer substrate writes until COMMIT, which
forces the agent to fly blind on intermediate state. Not worth
the complexity for v0.

### 4.2 Per-intent rollback

For each substrate operation a translator emits, it records an
inverse op. On apply failure (e.g. the validator passed but the
actual file write threw), the inverse runs to revert. For most
intents this is trivial (delete the file we just wrote); for
`replace_in_patch` it's an idempotent re-write of the original
content.

Inverse ops are NOT recorded in the intent log (the operator
doesn't need to see them). They live in the in-memory transaction
state until the intent's substrate apply completes successfully.

## 5. Workspace lifecycle

### 5.1 Pre-job assertion

At BEGIN, the translator asserts the workspace is clean:

```python
git -C work/DeltaPorts status --porcelain ports/<origin>/
# must be empty
```

If not clean, BEGIN aborts with an error. The runner logs and
moves on; the operator must explicitly reset (see §5.4) or
investigate.

This is a hard rule. It catches "the previous job didn't run
cleanup" and "the operator was poking around" before they pollute
the new job's intent log.

### 5.2 Post-job reset

At COMMIT or ABORT, after the intent log is written to the
bundle:

```python
git -C work/DeltaPorts checkout HEAD -- ports/<origin>/
git -C work/DeltaPorts clean -fd ports/<origin>/
```

Scope is `ports/<origin>/` only; sibling ports are not touched.
This is non-recursive in terms of port topology (the agent isn't
allowed to edit sibling ports anyway — see §10).

### 5.3 Verify replay

Verify-fix's `apply_and_build` primitive is reshaped:

```
dportsv3 dev-env apply-and-build ENV ORIGIN [--intent-log PATH] [--json]
```

`--intent-log` replaces today's `--diff`. The flow:

1. Assert workspace clean (§5.1).
2. Resolve translator mode from `classify_dops`.
3. Replay every intent in the log via `translator.apply()`.
4. Run `reapply` + `dbuild`.
5. Reset workspace.

Drift is structurally impossible: replay produces the same
substrate state regardless of env identity, as long as git HEAD
matches.

### 5.4 Operator escape hatch

```
dportsv3 dev-env reset-port ENV ORIGIN
```

Equivalent to the post-job reset, manually invoked. Useful when
the operator was experimenting in the env and wants to drop
their work without rebuilding the env.

## 6. The convert exception

Convert's output (`overlay.dops`) is meant to **persist** across
jobs: triage immediately depends on the converted state. Two
candidate designs were on the table; this section makes the call.

### 6.1 Design (a) — commit to local branch

Convert runs as a normal intent transaction (the
`convert_to_dops` intent of §3.2.7) but its post-job behavior is
different:

1. Apply the intent (write `overlay.dops`, delete legacy files).
2. Capture the intent log to the bundle.
3. `git commit -m "convert: <origin>"` on a local branch
   `agent/convert/<origin>` in the env's checkout.
4. Reset back to that branch (so the convert artifacts persist).
5. Subsequent jobs see git HEAD = the post-convert state.

**Pros:** convert is just another intent transaction. Cleanup
semantics are uniform. The operator promotes by merging
`agent/convert/<origin>` into their main clone (or a PR
branch).

**Cons:** auto-commits inside the env's checkout. If the
operator was on a different branch, this surprises them.
Requires the env's checkout to be on a writable branch; some
operators may have it on a detached HEAD pointing at a tag.

### 6.2 Design (b) — convert is special-cased

Convert applies its intent but the post-job reset is **skipped**
for `convert_to_dops` intent transactions specifically. The
ephemeral state persists in the workspace until a future job's
cleanup or operator reset.

**Pros:** no auto-commits. Minimal change to convert behavior.
**Cons:** one operation is exempted from the clean-workspace
invariant, which weakens the guarantee. Subsequent triage runs
see ephemeral state from convert as "baseline-ish" — same
ambiguity the current system has.

### 6.3 **DECISION (default):** Design (a)

Auto-commits to a dedicated `agent/convert/<origin>` branch are
operationally honest (the operator can `git log
agent/convert/*` to see what landed) and preserve the
clean-workspace invariant. Detached-HEAD envs get an explicit
error at convert time pointing the operator to run
`dportsv3 dev-env update` to attach to a branch.

If the operator overrides this decision before 25b, switch to
(b). The translator code surface is similar; the difference is
in the convert job's COMMIT path.

## 7. Intent log format

`analysis/intent_log.json` lives in the bundle:

```json
{
  "schema_version": 1,
  "origin": "devel/gperf",
  "target": "@2026Q2",
  "mode_at_apply": "compat",
  "baseline_commit": "abc123...",
  "intents": [
    {"seq": 0, "intent": {"type": "drop_patch", ...},
     "applied_at": "2026-05-25T...", "ok": true,
     "substrate_diff": "diff --git a/...\ndeleted file mode ..."},
    {"seq": 1, "intent": {"type": "add_patch", ...},
     "applied_at": "2026-05-25T...", "ok": true,
     "substrate_diff": "diff --git a/...\nnew file mode ..."}
  ]
}
```

Notes:

- `schema_version` lets future log readers handle grammar
  evolution.
- `baseline_commit` is the git commit the intents were applied
  against. Verify cross-checks before replay.
- `mode_at_apply` is recorded for forensics (was the port compat
  or dops at intent time?).
- `substrate_diff` per intent is the actual diff that intent
  produced. This is what `analysis/changes.diff` is computed
  from at bundle-view time (concatenation in order).

`analysis/changes.diff` becomes a **derived view** — generated on
demand from the intent log. The tracker UI shows both: intent
list (operator-friendly) and concatenated diff (review-friendly).

## 8. Verify replay semantics

```python
def verify_replay(env_name: str, intent_log: dict) -> VerifyResult:
    # 1. Assert workspace clean.
    assert_clean(env_name, intent_log["origin"])

    # 2. Assert baseline.
    head = git_head(env_name, "/work/DeltaPorts")
    if head != intent_log["baseline_commit"]:
        return VerifyResult(ok=False, reason="baseline_mismatch", ...)

    # 3. Construct translator (mode may differ from mode_at_apply
    #    if the port has been converted since — that's the agent's
    #    intent, replay it).
    translator = Translator(env_name, intent_log["origin"])

    # 4. Replay every intent.
    for entry in intent_log["intents"]:
        result = translator.apply(entry["intent"])
        if not result.ok:
            return VerifyResult(ok=False,
                                failed_intent=entry["seq"],
                                reason=result.error)

    # 5. Build.
    reapply(env_name, intent_log["origin"])
    return dsynth_build(env_name, intent_log["origin"])
```

No `git apply`. No diff replay. No `--3way`. The intent grammar
is the protocol; the substrate is the implementation. The two
classes of bug we hit today (new-file diff on populated env,
3-way merge failure on new files) cannot occur.

## 9. Validator rules

Validation runs at BOTH intent-emit time (the agent calls
`apply_intent`) AND at translator.render time (defense in depth).

### 9.1 Universal (mode-independent)

- `target` paths must be relative, no `..`, no leading `/`, must
  start with `ports/<origin>/` for any path-bearing intent.
- `find` strings must be present in the target file for
  `replace_in_patch`; `occurrence` must not exceed match count.
- `add_patch.diff` must be syntactically valid unified diff.

### 9.2 Mode-sensitive

Compat mode:

- `drop_patch` target must exist.
- `add_patch` target must NOT exist.
- `add_file` dest must NOT exist (for kind=resource).
- `change_makefile` path must exist; `op=remove` key must be
  present in the file.

Dops mode:

- `drop_patch` target must be referenced by a `patch apply`
  statement in current `overlay.dops`.
- `add_patch` target must NOT already be referenced.
- `change_makefile.path` must equal `Makefile.DragonFly` or
  `Makefile`; the resulting `mk.*` op must be parseable.

### 9.3 The half-migration invariant (load-bearing)

A single intent log MUST NOT contain intents that imply *both*
modes. Specifically:

- No log can write to `overlay.dops` (any dops-mode intent) AND
  also write a `Makefile.DragonFly` (compat-mode intent on a
  Makefile path).
- No log can contain a `convert_to_dops` intent AND any
  compat-mode intent.

Today's `multimedia/v4l_compat` incident — agent emitted both —
fails this check at the second intent's `apply_intent` call. The
log is rejected; the agent gets an explicit error and can
re-think.

This replaces today's `surface_invariant` runtime check at
*next-triage* time. Validation moves to write time.

## 10. Tool surface boundary

After Step 25 (recap from `docs/agentic-consolidation-plan.md`):

| Tool | Patch agent | Convert agent |
|---|---|---|
| `env_verify` | ✓ | ✓ |
| `get_file` | ✓ | ✓ |
| `grep` | ✓ | ✓ |
| `materialize_dports` | ✓ | ✓ |
| `extract` | ✓ | ✓ |
| `dupe` | ✓ | ✓ |
| `genpatch` | ✓ | ✓ |
| `dsynth_build` | ✓ | ✓ |
| `put_file` (port subtree) | ✗ — use `apply_intent` | ✓ (convert authors `overlay.dops`) |
| `put_file` (non-port paths) | ✓ (WRKSRC for `dupe`/`genpatch` flow) | ✓ |
| `install_patches` | ✗ — use `apply_intent{type=add_patch, from_dupe=true}` | ✗ (convert doesn't install patches) |
| `validate_dops` | ✗ (translator validates) | ✓ |
| `emit_diff` | ✗ (intent log IS the diff) | ✓ (convert still emits for now) |
| `apply_intent` | ✓ (new) | ✗ (convert uses substrate-level tools) |
| `intent_reference` | ✓ (read-only docs lookup) | n/a |

`intent_reference` is a tiny new tool that returns the intent
grammar spec on demand — the patch prompt cites it instead of
inlining the full grammar.

## 11. Compat ↔ dops translation table (canonical reference)

For each intent type, the canonical rendering. The translator is
deterministic: same intent + same baseline ⇒ same substrate ops.

| Intent | Compat | Dops |
|---|---|---|
| `replace_in_patch` | sed/Python in-place replace on `ports/<origin>/<target>` | append `text.replace_once { file, from, to }` to `overlay.dops` |
| `drop_patch` | `unlink ports/<origin>/<target>` | remove `patch apply <target>` line from `overlay.dops` |
| `add_patch` | write diff to `ports/<origin>/<target>` | write diff to `ports/<origin>/<target>` + append `patch apply <target>` to `overlay.dops` |
| `add_file{resource}` | write content to `ports/<origin>/<dest>` | append `file.copy { from=<staged>, to=<dest> }` to `overlay.dops`; content goes in `ports/<origin>/<dest>` |
| `add_file{materialize}` | `cp <source>` into `ports/<origin>/<dest>` | append `file.materialize { from=<source>, to=<dest> }` to `overlay.dops` |
| `change_makefile` | parse + rewrite `<path>` | append `mk.var.{op} { var, value }` to `overlay.dops` |
| `bump_portrevision` | edit `PORTREVISION` line in Makefile | append `mk.var.set { var=PORTREVISION, value=<n+1> }` to `overlay.dops` |
| `convert_to_dops` | n/a (mode would already be dops; reject as invalid) | run the deterministic Makefile.DragonFly → overlay.dops translator (existing `migration.convert.convert_record`), then delete legacy files |

## 12. Rollout

### 12.1 Slice-by-slice (matches plan §25b–g)

- **25b:** Translator + transaction engine as a pure library.
  Tests against canned intent inputs and assertable output diffs.
  No agent integration. No production impact.
- **25c:** `apply_intent` tool wired into the runner. Old tools
  (`put_file`, `install_patches`) stay in the registry but are
  not exposed in the patch agent's prompt yet. The patch agent
  can be opted-in per-bundle via a runner env var
  (`DP_HARNESS_PATCH_USE_INTENT=1`).
- **25e:** Intent log written to the bundle as
  `analysis/intent_log.json`. `analysis/changes.diff` becomes a
  derived view (still written, for backward compat).
- **25g:** Workspace reset policy. The pre-job clean assertion
  and post-job reset go in. Convert exception per §6.3.
- **25d:** Patch prompt swap. The old tools come out of the
  registry for the patch agent (still available to convert).
- **25f:** Telemetry — intent_applied events.

### 12.2 Compatibility with existing bundles

Bundles pre-Step-25 have `analysis/changes.diff` but no
`analysis/intent_log.json`. The verify-fix orchestrator falls
back to the legacy diff path when no intent log is present.
After enough port runs to flush old bundles out of operator
review, the legacy path retires (call it 25h, post-shipment).

### 12.3 Convert agent migration

Convert keeps its substrate-level tools (per §10). The
`convert_to_dops` intent in §3.2.7 is *also* available for
convert; its translator branch calls the same
`migration.convert.convert_record` machinery. This lets the
convert agent later be rewritten on intents without changing
behavior.

## 13. Resolved questions (decisions for 25b)

These were open in the first draft; resolved 2026-05-25 with the
operator.

### 13.1 Schema enforcement — `jsonschema` library

Use the `jsonschema` library, not hand-rolled validators. The
extra dep is worth it for two reasons: (1) LLM-friendly error
messages (`jsonschema.ValidationError` carries path + expected +
actual, which feeds back into a good `apply_intent` error
response the agent can react to), and (2) the per-intent JSON
schemas double as machine-readable spec — the `intent_reference`
tool can return them verbatim.

The schemas live next to the grammar dataclasses:
`dportsv3/agent/edit_intent/schemas/{replace_in_patch,drop_patch,
add_patch,add_file,change_makefile,bump_portrevision,
convert_to_dops}.json`.

### 13.2 Intent log size cap — 100 intents, 1 MB total

Two caps on the same intent log:

- **Count cap: 100 intents per log.** A realistic patch fix is
  1–5 intents; complex ports might hit 10–20. 100 leaves
  headroom for unusual cases and catches runaway agent loops
  early. Refuse the 101st with a clear error: *"intent log
  exceeds 100 entries — almost certainly an agent loop; the
  patch agent should split into smaller bundles or escalate to
  the operator."*
- **Size cap: 1 MB total.** Per-intent size varies from ~100
  bytes (`drop_patch`) to many KB (`add_patch` with a big diff).
  The 1 MB ceiling catches a single intent with a pathological
  diff *and* a swarm of medium intents. Refuse with: *"intent
  log size exceeds 1 MB — split, simplify, or escalate."*

Both caps are operator-overridable via env var
(`DP_HARNESS_INTENT_MAX_COUNT`, `DP_HARNESS_INTENT_MAX_BYTES`)
for the genuinely-complex-port edge case, but defaults are
strict.

### 13.3 `add_patch.from_dupe` round-trip — most-recent + basename match

When the agent emits `add_patch{target, from_dupe: true}`,
the translator looks in `<env>/writable/work/<wrksrc>/.genpatch-out/`
(or wherever genpatch deposits patches in the env) for the
file whose basename matches `target`'s basename, picking the
most recently modified one if multiple match. The captured
content goes into `target` (compat) or gets referenced from
`overlay.dops` (dops).

Refuses with explicit error if no matching file exists in the
genpatch output dir.

### 13.4 Convert exception detached-HEAD — error, don't auto-attach

§6.3 stays as-drafted: explicit error pointing the operator to
`dportsv3 dev-env update`. Auto-attaching to `main` could
silently clobber operator intent — they might be on a tag for
a reason (testing against a specific FPORTS revision, comparing
behavior across branches, etc.). The error message names the
attachment command so the operator-fix is one paste.

### 13.5 Telemetry payload — inline diff if ≤ 4 KB, else sha256

Per-intent telemetry event shape:

```json
{
  "type": "intent_applied",
  "ts": "...",
  "bundle_id": "...",
  "intent_seq": 0,
  "intent_type": "drop_patch",
  "intent_target": "dragonfly/patch-...",
  "ok": true,
  "substrate_diff_sha256": "abc...",
  "substrate_diff_bytes": 234,
  "substrate_diff": "diff --git a/...\n..."  // present iff bytes <= 4096
}
```

If `substrate_diff_bytes <= 4096` the full diff is inline. If
larger, only the sha256 + byte count flow through telemetry; the
full diff lives in `analysis/intent_log.json[intents[N]].substrate_diff`
and the operator clicks through. Keeps event throughput modest
while making the common case (small diffs from
`replace_in_patch`, `drop_patch`, `change_makefile`,
`bump_portrevision`) maximally readable in the UI.

## 14. Bandages retired (cross-reference)

## 14. Bandages retired (cross-reference)

From `agentic-consolidation-plan.md` Step 25's "Bandages this
step retires" table. The design above structurally eliminates
each:

| Bandage | Where in this design |
|---|---|
| `_git_diff_with_untracked` | §7 (intent log replaces post-hoc diff capture) |
| `convert_record.mk_path.unlink()` + circuit breaker | §3.2.7 (`convert_to_dops` intent is atomic) |
| `overlay_state` unification | §5 (workspace assertions remove dual-substrate ambiguity) |
| `surface_invariant` runtime check | §9.3 (validator at write time) |
| `_lookup_bundle_target` | unrelated — stays |
| Verify-fix subprocess gymnastics | §8 (verify replays intents, no subprocess to dev-env apply) |
| `git apply --3way` staging leak | §8 (no `git apply`) |
| Env state accumulation | §5 (post-job reset) |
| `Makefile.DragonFly + overlay.dops` half-migration | §9.3 (validator rejects mixed-mode logs) |
| `process_verify_requests` reconciler | unrelated — stays |
