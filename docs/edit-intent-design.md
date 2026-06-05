# Edit-intent DSL — design (Step 25a)

> **Status: shipped — dops-only (updated 2026-06-05).** This started
> as the normative spec for Step 25b and was written dual-mode (one
> intent rendering to *either* a compat file edit *or* a dops
> statement). **It shipped narrowed to dops-only.** A later
> consolidation ("Step C") removed compat-mode rendering and the
> `convert_to_dops` intent entirely: convert now runs as a hard
> prerequisite (the patch agent only ever sees dops-converted
> substrate), so the translator has a single mode. This document has
> been reconciled to that reality — the dual-mode framing, the
> "Compat render:" lines, and `convert_to_dops` are gone. Git history
> carries the original dual-mode draft if needed.
>
> The canonical, machine-readable intent catalog is the per-intent
> JSON schemas under
> `dportsv3/agent/edit_intent/schemas/` and the coverage matrix in
> `docs/intent-surface-gaps.md`. This doc is the *architecture* record
> (state model, transactions, workspace lifecycle, replay); it does
> not re-spec every intent field.
>
> Cross-reference: `docs/agentic-architecture-backlog.md` Step 25
> (shipped) + Steps 39/40 (intent catalog growth),
> `docs/intent-surface-gaps.md` (catalog matrix), `docs/dsl-v0.md`
> (the substrate dops grammar).

## 1. Purpose

The agent stack has eight years of accumulated bandages around one
hole: the patch agent has no framework for *what change it is
making*. It writes files; the runner observes via `git diff`; both
sides have blind spots that produce production bugs (empty diffs,
half-migrations, verify drift, partial-staging leaks).

This document specifies a small DSL the patch agent emits instead
of file writes, a translator that renders each intent into the
dops substrate (an `overlay.dops` statement and any companion
patch file), a transactional execution model that bounds workspace
state, and an intent log that becomes the canonical artifact
replacing `analysis/changes.diff` as the authoritative record of a
patch attempt.

> **Why dops-only.** The original design rendered to *either* compat
> (`Makefile.DragonFly` + `dragonfly/patch-*`) *or* dops, so the
> patch agent could stay substrate-agnostic. In practice the loop
> made convert a hard prerequisite — every port is converted to dops
> before the patch agent touches it — so the compat render path was
> dead weight and was removed (Step C). The patch agent's edit
> surface is uniformly dops; `worker.apply_intent` refuses any
> non-converted substrate before the translator is even constructed.

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

### 3.2 Intent types

The v0 design specced six patch-agent types plus a convert-only
`convert_to_dops`. The shipped catalog dropped `convert_to_dops`
(convert no longer uses an intent — see §6) and grew to **ten**
patch-agent types: the original six below, plus `replace_in_dops_block`
(heredoc-body edits) and the three Step-39 symmetric deletes
(`drop_mk_directive`, `drop_file`, `drop_target_block`). The six
specs below are kept as the architectural illustration; for the
full, current, field-exact catalog see the JSON schemas under
`dportsv3/agent/edit_intent/schemas/` and the intent→dops reference
table in `docs/intent-surface-gaps.md`.

Each spec below: required fields, optional fields, dops rendering,
expected diff shape.

#### 3.2.1 `replace_in_patch`

Edit a single hunk inside an existing patch file. The most common
shape (a context line drifted, a function name changed upstream).

**Required:** `target` (relpath under `ports/<origin>/`),
`find` (string), `replace` (string).
**Optional:** `occurrence` (int, default 1; which match to
replace if `find` is non-unique).

**Dops render:** emit
`text.replace_once { file=<target>, from=<find>, to=<replace> }`
into `overlay.dops`. Refuse (intent error) if `find` not found, or
if `occurrence` exceeds match count.

**Expected diff shape:** single-file modification, two-line
hunk (find/replace) at minimum.

#### 3.2.2 `drop_patch`

Declare an existing patch obsolete and remove it.

**Required:** `target` (relpath, `dragonfly/patch-*` or a dops
`patch apply` statement reference), `reason` (string, will land
in the intent log + commit message).

**Dops render:** remove the corresponding
`patch apply <target>` statement from `overlay.dops` and delete the
on-disk patch file.

**Expected diff:** single-line `overlay.dops` modification plus the
patch-file deletion.

#### 3.2.3 `add_patch`

Introduce a new patch for a file the port doesn't currently
touch.

**Required:** `target` (relpath, where the new patch lives),
`diff` (full unified diff bytes against the upstream source).
**Optional:** `from_dupe` (boolean — `True` means this patch was
captured via `dupe` + `genpatch`; the diff field will be
populated by the runner, agent supplies `target` + `from_dupe`
only).

**Dops render:** write the diff to
`ports/<origin>/<target>` AND emit `patch apply <target>` into
`overlay.dops`.

**Validator:** the diff must apply cleanly against the
current upstream source (the translator runs `patch --dry-run`
during application).

**Expected diff:** new patch-file creation plus a single-line
`overlay.dops` addition.

#### 3.2.4 `add_file`

Add a port-local file (resource) or stage a file from the
DragonFly source tree.

**Required:** `dest` (relpath under `ports/<origin>/`),
`kind` (`"resource"` | `"materialize"`).
**Required iff kind=resource:** `content` (string).
**Required iff kind=materialize:** `source` (relpath in
DragonFly source tree).

**Dops render:** for `resource`, write content to
`ports/<origin>/<dest>` and emit
`file.copy { from=<dest>, to=<dest> }` into `overlay.dops`; for
`materialize`, emit `file.materialize { from=<source>, to=<dest> }`
(dops references the source, no content written directly).

**Expected diff:** single-line `overlay.dops` addition (plus the
resource file for `kind=resource`).

#### 3.2.5 `change_makefile`

Edit a Makefile variable.

**Required:** `path` (relpath, e.g. `Makefile`), `key` (var name),
`op` (`"set"` | `"append"` | `"remove"` | `"unset"`). `value` is
required for set/append/remove and optional (ignored) for unset.

**Dops render:** emit
`mk set <KEY> "<value>"` (or `mk add` / `mk remove` / `mk unset`)
into `overlay.dops`. `mk unset <KEY>` deletes the variable's
assignment line from the composed Makefile at compose time —
symmetric inverse of `mk set`, used to drop an upstream
assignment that's wrong for our target. Re-emitting `set` for the
same key accumulates lines (last-wins at compose, no implicit
strip post-38e); delete a prior line explicitly with
`drop_mk_directive`.

**Expected diff:** single-line dops addition.

#### 3.2.6 `bump_portrevision`

Operator-flag intent: increment PORTREVISION. Metadata, no file
content change beyond the Makefile bump.

**Required:** none beyond intent presence.

**Dops render:** emit
`mk.var.set { var=PORTREVISION, value=<n+1> }`.

**Expected diff:** single-line dops addition.

#### 3.2.7 Later additions (shipped, not in the v0 spec)

Four intents joined the catalog after v0; they are dops-only like
the rest. Field shapes live in the schemas + gap matrix (§3.2
intro):

- `replace_in_dops_block` — edit text inside an `mk target
  set/append NAME <<TAG ... TAG` heredoc body.
- `drop_mk_directive` — remove one `mk set/unset/add/remove VAR`
  line (symmetric inverse of `change_makefile`; Step 39a).
- `drop_file` — remove a non-patch `file copy`/`file materialize`
  line and delete the on-disk file (inverse of `add_file`;
  Step 39b).
- `drop_target_block` — remove a whole `mk target` heredoc block
  (Step 39c).

The convert-only `convert_to_dops` intent that v0 declared here was
**removed**. Convert does not use the intent layer — it authors
`overlay.dops` directly via `put_file` (see §6).

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
BEGIN  → translator constructed for (env, origin), mode="dops".
       │   worker.apply_intent gates on assess_dops first: a
       │   non-converted or half-migrated substrate is refused
       │   before the translator exists. Workspace expected clean
       │   (assertion).
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
2. Construct the translator (mode="dops").
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

## 6. Convert is outside the intent layer

Convert's output (`overlay.dops`) is meant to **persist** across
jobs: triage immediately depends on the converted state. The patch
agent's workspace-reset discipline (§5) does not apply to convert.

The v0 design proposed expressing convert as a `convert_to_dops`
intent transaction (so cleanup semantics would be uniform). **That
is not what shipped.** Convert authors `overlay.dops` directly via
substrate-level tools (`put_file`, the deterministic
`migration.convert` machinery), not through `apply_intent`. The
intent layer is the *patch* agent's surface only. Consequences:

- There is no `convert_to_dops` intent (removed; see §3.2.7).
- Convert's output persists in the env checkout; the patch agent's
  post-job reset (§5.2) is scoped to patch/verify jobs and never
  runs against a convert job.
- The patch agent never sees a compat port: `worker.apply_intent`
  refuses any substrate that isn't already dops-converted (§9.3),
  so convert running first is a hard precondition, not a mode the
  translator selects between.

Operator promotion of converted state happens through the normal
convert-job delivery path, independent of the intent log.

## 7. Intent log format

`analysis/intent_log.json` lives in the bundle:

```json
{
  "schema_version": 1,
  "origin": "devel/gperf",
  "target": "@2026Q2",
  "mode_at_apply": "dops",
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
- `mode_at_apply` is always `"dops"` post-Step-C; the field is
  retained for forensics and log-format stability (older logs may
  carry other values).
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

    # 3. Construct translator (mode is always dops post-Step-C).
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

### 8.1 Replay semantic constraints (added 2026-05-25 after 25e review)

The replayer in production (`_replay_intent_log` in
`scripts/tools/dev-env/dports_dev_env/cli.py`) makes two
deliberate choices that operators should be aware of when
interpreting verify outcomes.

**(a) `ok=False` entries are skipped on replay.** Failed intents
were no-ops at original apply time; replaying them just produces
phantom failures in the verify run. The skip means *replay ≠
exact reproduction of the agent run* whenever the agent
persisted past a failure. For verify-fix's purpose ("can the
substrate state the agent left behind rebuild?"), the skip is
right — what we want to verify is the success-state, not the
process. If the agent run included ok=False entries that the
follow-up intents depended on, the replay's end state will
differ from the original. Worth flagging in operator-facing
output ("N of M intents skipped on replay").

**(b) An intent log with zero `ok=True` entries replays as "no
work."** rc=0, applied=0, no substrate changes. `reapply` and
`dbuild` then run against the unmodified baseline. If the build
passes, verify reports verified — but the bundle's intent log
says everything failed at apply time. The verify endpoint
should surface "nothing to verify; intent log has no successful
entries" rather than letting the operator interpret an
unrelated baseline-build success as a verdict on the fix. The
orchestrator should refuse to POST `verified` when
`applied_count == 0`; today this is a known gap, deferred to
25g (workspace lifecycle) where the orchestrator gains the
context to decide.

**(c) Baseline drift refused at replay.** Before walking the
intent list, the replayer compares the log's `baseline_commit`
against the env's current git HEAD. Mismatch returns rc=1 with
a "refusing replay; baseline mismatch" error pointing the
operator at `dportsv3 dev-env update`. Empty baseline (older
logs, or git resolution failure at apply time) is allowed
through with a stderr warning — operator opted in by triggering
verify.

## 9. Validator rules

Validation runs at BOTH intent-emit time (the agent calls
`apply_intent`) AND at translator.render time (defense in depth).

### 9.1 Universal (mode-independent)

- `target` paths must be relative, no `..`, no leading `/`, must
  start with `ports/<origin>/` for any path-bearing intent.
- `find` strings must be present in the target file for
  `replace_in_patch`; `occurrence` must not exceed match count.
- `add_patch.diff` must be syntactically valid unified diff.

### 9.2 Intent-specific (dops)

All intents render to `overlay.dops` substrate ops:

- `drop_patch` target must be referenced by a `patch apply`
  statement in current `overlay.dops`.
- `add_patch` target must NOT already be referenced.
- `change_makefile.path` must equal `Makefile.DragonFly` or
  `Makefile`; the resulting `mk.*` op must be parseable.
- `drop_mk_directive` / `drop_file` / `drop_target_block` must
  match exactly one scoped overlay line; zero or ambiguous
  matches are a hard refusal (Step 39 scope discipline).

### 9.3 The half-migration invariant (load-bearing)

The substrate a port presents must be fully dops, not half. A
port MUST NOT carry a legacy `Makefile.DragonFly` *and* an
`overlay.dops` at the same time — the agent would be editing a
substrate that compose only partly honors.

This is enforced at the worker boundary, not in the log: before
constructing the Translator, `worker.apply_intent` calls
`assess_dops`, and a half-migrated port returns
`action='surface_invariant'` — the patch agent is held off until
convert finishes authoring the overlay. Because convert is a hard
prerequisite (the patch agent only ever sees dops-converted
substrate), the intent log itself is always single-mode by
construction; there is no `convert_to_dops` intent to mix in.

## 10. Tool surface boundary

After Step 25 (recap from `docs/agentic-architecture-backlog.md`):

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

## 11. Intent → dops rendering (canonical reference)

For each intent type, the canonical rendering. The translator is
deterministic: same intent + same baseline ⇒ same substrate ops.
All intents render to `overlay.dops`; there is no compat column
post-Step-C. This table is illustrative — the per-intent JSON
schemas under `dportsv3/agent/edit_intent/schemas/` and the
coverage matrix in `docs/intent-surface-gaps.md` are canonical.

| Intent | Dops rendering |
|---|---|
| `replace_in_patch` | append `text.replace_once { file, from, to }` to `overlay.dops` |
| `drop_patch` | remove `patch apply <target>` line from `overlay.dops` |
| `add_patch` | write diff to `ports/<origin>/<target>` + append `patch apply <target>` to `overlay.dops` |
| `add_file{resource}` | append `file.copy { from=<staged>, to=<dest> }` to `overlay.dops`; content goes in `ports/<origin>/<dest>` |
| `add_file{materialize}` | append `file.materialize { from=<source>, to=<dest> }` to `overlay.dops` |
| `change_makefile` | append `mk.var.{op} { var, value }` to `overlay.dops` (re-emit accumulates, last-wins at compose) |
| `bump_portrevision` | append `mk.var.set { var=PORTREVISION, value=<n+1> }` to `overlay.dops` |
| `replace_in_dops_block` | rewrite a heredoc block body in `overlay.dops` |
| `drop_mk_directive` | remove a scoped `mk.*` line from `overlay.dops` |
| `drop_file` | remove a scoped `file.copy`/`file.materialize` line from `overlay.dops` |
| `drop_target_block` | remove a scoped target heredoc from `overlay.dops` |

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

Convert keeps its substrate-level tools (per §10) and authors
`overlay.dops` directly via `put_file` / `validate_dops` /
`emit_diff`. It is outside the intent layer (§6); there is no
`convert_to_dops` intent. The deterministic
`migration.convert.convert_record` machinery remains available to
convert as a substrate-level helper, not as an intent.

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
replace_in_dops_block,drop_mk_directive,drop_file,
drop_target_block}.json`. (`convert_to_dops` was dropped at
Step C — convert authors `overlay.dops` directly.)

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
content is written to `ports/<origin>/<target>` and referenced
from `overlay.dops` via a `patch apply` line.

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

From `agentic-architecture-backlog.md` Step 25's "Bandages this
step retires" table. The design above structurally eliminates
each:

| Bandage | Where in this design |
|---|---|
| `_git_diff_with_untracked` | §7 (intent log replaces post-hoc diff capture) |
| `convert_record.mk_path.unlink()` + circuit breaker | convert-side, outside the intent layer (§6) |
| `overlay_state` unification | §5 (workspace assertions remove dual-substrate ambiguity) |
| `surface_invariant` runtime check | §9.3 (enforced at the worker boundary via `assess_dops`) |
| `_lookup_bundle_target` | unrelated — stays |
| Verify-fix subprocess gymnastics | §8 (verify replays intents, no subprocess to dev-env apply) |
| `git apply --3way` staging leak | §8 (no `git apply`) |
| Env state accumulation | §5 (post-job reset) |
| `Makefile.DragonFly + overlay.dops` half-migration | §9.3 (worker-boundary `assess_dops` gate) |
| `process_verify_requests` reconciler | unrelated — stays |
