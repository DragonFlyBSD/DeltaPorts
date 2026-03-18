# DeltaPorts Architecture v3: Hybrid DSL + In-Memory Plan + CST-Lite

## Status

Design proposal for v3.

This document consolidates the semantic-op direction, hybrid DSL authoring
model, CST-lite parser scope for BSDMakefiles, empirical matrix findings, and
an implementation plan that preserves patch fallback and framework safety.

---

## Objective

For target `T` (`main` or `YYYYQ[1-4]`), produce a full DPorts tree as:

- FreeBSD ports branch `T`
- plus DeltaPorts overlay branch `T`

with deterministic behavior and lower maintenance churn than pure line patching.

---

## Why v3 Exists

The previous Python implementation fixed target scoping and validation, but still relies heavily on raw text
patching for many routine edits. This creates recurring drift and rebasing work
when upstream files move or reformat.

v3 keeps patching where needed, but introduces a structured overlay layer for
common repetitive port-level modifications.

---

## Locked Constraints (Inherited)

1. Targets are restricted to `main` and `YYYYQ[1-4]`.
2. Port overlay inputs are target-scoped (`@target`); `special/` is the exception where unscoped payloads are canonical `main` and non-`main` targets live under `@target` subdirectories.
3. Single FreeBSD checkout; branch switch in place.
4. Dirty FreeBSD tree blocks sync/switch.
5. `special/` changes are sensitive, remain patch/replacement-first, and do not use the DSL lane.
6. Stale `port` overlays (missing upstream origin in target) are invalid.

---

## Process Model

For target `T`:

- `U(T)`: origins present in FreeBSD ports branch `T`
- `O(T)`: Delta overlay origins relevant to `T`

Resulting output tree is:

1. Seed full tree from `U(T)`.
2. Apply infrastructure overlays (`special/`).
3. Apply `O(T)` per overlay type:
   - `port`: mutate seeded upstream origin.
   - `mask`: remove/skip output origin.
   - `dport`: add DragonFly-only origin from `newport`.
   - `lock`: replace from locked source tree.

---

## Core v3 Overlay Model

v3 uses a **hybrid authoring and execution model**:

- Authoring source: compact text DSL (`overlay.dops`)
- Execution form: normalized in-memory plan (ephemeral IR)

The engine executes the normalized plan. DSL compiles to this in-memory plan;
no persisted transition ops file is required.

---

## Hard Boundary by Lane

### Lane A: Structured Makefile Ops (preferred)

Use typed operations for common Makefile changes.

### Lane B: Structured File/Text Ops (limited)

Use constrained file operations and anchored text edits where deterministic.

### Lane C: Patch Fallback (required)

Use raw patches for ambiguous, complex, or parser-unsafe changes.

### Framework Boundary (`special/`)

`special/Mk`, `special/Templates`, `special/treetop`, `Tools`, and `Keywords`
remain patch/replacement-first. v3 does not force semantic conversion there.

Framework target semantics are distinct from port overlays:

- unscoped `special/*/diffs/...` and `special/*/replacements/...` mean `main`
- non-`main` targets use complete target-owned trees under `special/*/{diffs,replacements}/@<target>/...`
- compose never layers unscoped `main` payloads together with `@<target>` payloads in one run
- if a non-`main` target tree is missing, compose bootstraps it from unscoped `main` on first compose, then subsequent fixes live only in `@<target>`

---

## Render Model (Important)

v3 operations mutate the actual target files in the compose working/output
tree (for example, `Makefile`, plist, scripts).

v3 does **not** rely on creating synthetic `Makefile.DragonFly` artifacts in
final output. `Makefile.DragonFly` remains an input compatibility source during
transition, not the desired final mutation mechanism.

---

## Empirical Baseline Matrix

This baseline was created from random samples to avoid anecdotal design:

- Sample A: 20 random `Makefile.DragonFly` files (seed `20260222`)
- Sample B: 25 random `ports/*/*/diffs/**/Makefile.diff` files (seed `20260222`)

### Matrix A: `Makefile.DragonFly` sample (20)

- `semantic_var_ops`: 14/20 (70%)
  - Typical: `IGNORE=`, `BROKEN=`, `USES+=`, `CONFIGURE_ARGS+=`, `LDFLAGS=`
- `target_or_recipe_patch`: 6/20 (30%)
  - Typical: `dfly-patch:` targets with tabbed `${REINPLACE_CMD}` recipes

Interpretation:

- A majority is immediately covered by var ops.
- A significant minority requires explicit target/recipe DSL primitives.

### Matrix B: `Makefile.diff` sample (25)

- `var_only_patch`: 10/25 (40%)
- `target_or_recipe_patch`: 11/25 (44%)
- `conditional_patch`: 1/25 (4%)
- `conditional_and_var_patch`: 1/25 (4%)
- `other_text_patch`: 2/25 (8%)

Interpretation:

- v3 MVP must include var ops and target/recipe operations from the start.
- Conditional operations are less frequent in this sample but strategically
  important for robustness.
- Patch fallback remains necessary and expected.

---

## DSL Spec

The normative DSL grammar and semantics are defined in:

- `docs/dsl-v0.md`

This architecture document intentionally stays high-level and does not duplicate
the full language spec.

---

## Normalized Plan (Canonical Runtime Form)

DSL compiles to a normalized in-memory plan that the apply engine executes
directly.

Optional debug/export output may serialize this plan, but persisted plan files
are not part of the steady-state design.

---

## Supported Operation Set (v3 initial)

### Makefile var ops

1. `mk.var.set`
2. `mk.var.unset`
3. `mk.var.token_add`
4. `mk.var.token_remove`

### Makefile structural ops

1. `mk.block.disable`
2. `mk.block.replace_condition`
3. `mk.target.set`
4. `mk.target.append`
5. `mk.target.remove`
6. `mk.target.rename` (v0.1)

### File/Text ops

1. `file.copy`
2. `file.remove`
3. `text.line_remove` (strict exact match)
4. `text.line_insert_after` (strict single-anchor)
5. `text.replace_once` (strict single match)

### Fallback

1. `patch.apply` (explicit form)

Additionally, implicit payload in `dragonfly/@target/...` remains supported.

---

## CST-Lite Specification for BSDMakefiles

v3 uses a **CST-lite parser** (not full semantic AST evaluation).

### Scope

Parse and preserve structure for:

- assignment statements (`=`, `+=`, `?=`, `:=`, `!=`)
- line continuations (`\`)
- directive boundaries (`.if/.elif/.else/.endif`)
- target declarations (`name:`)
- recipe lines (tab-prefixed)
- `.include` statements
- comments/raw lines

### Node model

- `AssignmentNode`
- `DirectiveIfNode` / `DirectiveElifNode` / `DirectiveElseNode` / `DirectiveEndifNode`
- `TargetNode`
- `RecipeLineNode`
- `IncludeNode`
- `RawLineNode`

Each node carries source span metadata (`line_start`, `line_end`) for precise
diagnostics and deterministic rewriting.

### Non-goals

- no full bmake evaluator
- no macro/function execution model
- no transitive include-graph rewrite in v3.0

---

## bmake Oracle Role

Because the language is BSD make, parser rewrites should be checked by a
runtime oracle strategy:

- run constrained `bmake` syntax/parse checks after rewrite
- run selected variable expansion checks for sanity
- report oracle failures as rewrite-stage errors

The oracle validates; it does not replace the rewrite engine.

---

## Master/Slave and Include Policy

v3.0 policy:

1. Rewrite only the explicitly targeted file (`Makefile` unless specified).
2. Do not perform transitive include/master graph rewrites in v3.0.
3. If required behavior is inherited from master/include and cannot be safely
   changed locally, use patch fallback.

This is intentional to keep implementation bounded and reliable.

---

## Determinism, Idempotency, and Failure Policy

### Determinism rules

1. Re-applying the same op set yields the same output.
2. Operation ordering is stable and explicit.
3. Ambiguous matches do not auto-resolve.

### Defaults

- ambiguous match: `error`
- parse failure in targeted file: `error`
- missing target with unset policy: `error`

### Per-op `on_missing`

- `error`: fail port application
- `warn`: emit warning and continue
- `noop`: skip silently (discouraged except transitional use)

---

## Stale Overlay Policy

If file-level `type = "port"` and origin is missing in FreeBSD target branch,
overlay is stale.

Handling:

- `check`: hard error
- `compose`: blocked by preflight validation
- stale overlays are hard failures in validation/compose

---

## Compose Pipeline in v3

For target `T`:

1. Seed output tree from FreeBSD `T`.
2. Apply `special/` infrastructure patches/replacements using unscoped `main` payloads for `@main` and target-owned `@<target>` payloads for non-`main` targets.
3. Validate overlays and op plans.
4. Apply semantic ops.
5. Apply fallback patch payloads.
6. Copy implicit `dragonfly/@T` payload files.
7. Finalize output (prune/makefiles/updating).

---

## Observability Requirements

Per-port report fields:

- total ops
- applied ops
- skipped ops
- warnings
- errors
- fallback patch count

Compose summary aggregates these by stage and target.

---

## Build Tracker Architecture

v3 also includes a separate build tracker subsystem for recording and browsing
build outcomes after compose.

This tracker is intentionally outside the compose pipeline itself:

- compose remains stateless tree generation,
- external build/test automation decides when to start builds,
- tracker records build runs, per-port outcomes, and commit metadata,
- dashboard/API expose current state and historical comparisons.

### Workflow position

The intended operational flow is:

1. PR or local fix preparation,
2. test build for `(target, build_type=test)`,
3. release build for `(target, build_type=release)`,
4. repo commit on the appropriate branch,
5. commit SHA/branch/push time recorded against the build run.

The tracker does not orchestrate these steps; it records them.

### Tracker data model

The tracker stores:

- `build_types`: lookup table (`test`, `release`, extensible later),
- `build_runs`: one row per build run with target, build_type, timestamps, and
  optional commit metadata,
- `build_results`: per-run per-origin results (`success`, `failure`,
  `skipped`, `ignored`) and optional `log_url`,
- `port_status`: current per-target per-origin last-attempt and last-success
  state.

### Concurrency rule

Only one active run is allowed per `(target, build_type)`.

This allows, for example:

- one `@2026Q1` test build,
- one `@2026Q1` release build,

at the same time, while preventing overlapping duplicate runs in the same lane.

### Presentation layer

The tracker uses:

- FastAPI for HTTP API,
- SQLite (WAL mode) for storage,
- server-rendered Jinja2 templates for the dashboard,
- a simple classless CSS presentation layer (Pico CSS + local overrides).

The dashboard is intentionally lightweight: no SPA, no websocket dependency,
and active builds refresh via standard HTML meta refresh.

---

## Implementation Plan (v3)

### Phase 0: foundation

- normalized plan schema and validator
- DSL parser and compiler to normalized plan
- CST-lite parser for Makefile

### Phase 1: high-confidence ops

- var set/unset/add/remove
- target set/append/remove
- file remove/copy
- strict reporting and dry-run parity

### Phase 2: conditional ops and oracle integration

- block disable/condition replace
- bmake oracle checks integrated in apply pipeline

### Phase 3: migration helpers and conversion tooling

- patch-to-op suggestion for simple patterns
- contributor helpers (`dports ops ...`) optional layer

### Phase 4: policy hardening

- stricter op linting
- category-level adoption policies

---

## Decision Gates

1. Proceed with v3 MVP if sampled coverage for safe semantic candidates remains
   >= 60% for repetitive port-level edits.
2. Target/recipe primitives are mandatory in MVP due to observed 30-44% impact
   in samples.
3. Patch fallback remains mandatory; no forced full conversion target.

---

## Risks and Mitigations

1. Parser edge-case risk in BSD make syntax.
   - Mitigation: CST-lite scope, strict failures, patch fallback.
2. DSL/compiler drift from execution behavior.
   - Mitigation: normalized in-memory plan is canonical, DSL is one-way authoring layer.
3. Recipe/tab corruption in target edits.
   - Mitigation: heredoc recipe literals and tab-preserving tests.
4. Contributor overhead.
   - Mitigation: helper tooling and templates after MVP stabilizes.

---

## Open Questions

1. Default `on_missing` for non-critical block ops: `warn` vs `error`.
2. Whether to include `mk.target.rename` in MVP or v0.1.
3. Whether to support optional debug plan export snapshots in CI/local tooling.
4. Exact default bmake oracle checks for CI vs local modes.

---

## Summary

v3 adopts a practical hybrid model:

- concise DSL for contributors
- strict normalized in-memory plan as canonical execution path
- CST-lite structural rewrites for BSDMakefiles
- patch fallback preserved for complexity and safety
- `special/` remains patch/replacement-only with unscoped=`main` and target-owned `@<target>` framework payloads

This direction captures the observed real-world change patterns while avoiding
both pure patch fragility and full AST/evaluator complexity.
