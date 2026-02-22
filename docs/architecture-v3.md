# DeltaPorts Architecture v3: Hybrid DSL + Ops IR + CST-Lite

## Status

Design proposal for v3.

This document consolidates the semantic-ops direction, hybrid DSL authoring
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

v2 fixed target scoping and validation, but still relies heavily on raw text
patching for many routine edits. This creates recurring drift and rebasing work
when upstream files move or reformat.

v3 keeps patching where needed, but introduces a structured overlay layer for
common repetitive port-level modifications.

---

## Locked Constraints (Inherited)

1. Targets are restricted to `main` and `YYYYQ[1-4]`.
2. Inputs are target-scoped (`@target`); no implicit root fallback.
3. Single FreeBSD checkout; branch switch in place.
4. Dirty FreeBSD tree blocks sync/switch.
5. `special/` changes are sensitive and remain patch/replacement-first.
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

- Authoring: compact text DSL (`.dops`)
- Canonical execution format: normalized ops IR (`[[ops]]` in `overlay.toml`)

The engine executes only ops IR. DSL is compiled to IR.

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

## DSL v0 Reference (Normative Authoring Syntax)

The DSL is the contributor-facing authoring layer. The runtime applies compiled
ops IR only.

### DSL goals

- concise for contributors
- deterministic and compilable
- explicit target scoping
- strict error behavior by default

### File and scope model

- File name: `overlay.dops`
- One overlay origin per file (`port <category/name>` required exactly once)
- Multiple `target @...` blocks in one file are allowed and recommended for
  shared logic across targets
- Supported targets: `@main`, `@YYYYQ1`, `@YYYYQ2`, `@YYYYQ3`, `@YYYYQ4`

### Lexical conventions

- `#` starts a comment (outside heredoc bodies)
- Strings are double-quoted (`"..."`) and support escapes: `\\`, `\"`, `\n`,
  `\t`
- Bare identifiers are space-free tokens; use quoted strings for values with
  spaces
- Paths are relative to the overlay port root unless absolute
- Recipe bodies must use heredoc and preserve literal tabs/spaces

### Top-level directives

```text
target @2025Q2
port security/dsniff
type port
reason "DragonFly-specific adjustments"
maintainer "delta@dragonflybsd.org"
```

- `target` sets active target scope for following ops until next `target`
- `port` declares origin
- `type` maps to `overlay.type` (`port|mask|dport|lock`)
- `reason` maps to `overlay.reason`
- `maintainer` maps to top-level maintainer metadata

### Operation syntax

#### Makefile var ops

```text
mk set <VAR> "<value>" [on-missing error|warn|noop]
mk unset <VAR> [on-missing error|warn|noop]
mk add <VAR> <token> [on-missing error|warn|noop]
mk remove <VAR> <token> [on-missing error|warn|noop]
```

#### Makefile conditional/block ops

```text
mk disable-if condition "<expr>" [contains "<anchor>"] [on-missing ...]
mk replace-if from "<expr>" to "<expr>" [contains "<anchor>"] [on-missing ...]
```

#### Makefile target/recipe ops

```text
mk target set <name> <<'MK'
	<recipe line 1>
	<recipe line 2>
MK

mk target append <name> <<'MK'
	<recipe lines appended>
MK

mk target remove <name> [on-missing ...]
mk target rename <old> -> <new> [on-missing ...]
```

#### File/text ops

```text
file copy <src> -> <dst>
file remove <path> [on-missing ...]

text line-remove file <path> exact "<line>" [on-missing ...]
text line-insert-after file <path> anchor "<line>" line "<line>" [on-missing ...]
text replace-once file <path> from "<needle>" to "<replacement>" [on-missing ...]
```

#### Patch fallback op

```text
patch apply <path>
```

### Full example

```text
# ports/security/dsniff/overlay.dops

target @2025Q2
port security/dsniff
type port
reason "DragonFly-specific adjustments"

mk remove USES linux on-missing warn
mk add USES ssl
mk set BROKEN_DragonFly "fails with old SSL API"

mk disable-if condition "${OPSYS} == FreeBSD || ${SSL_DEFAULT} == openssl" \
  contains "Requires LibreSSL for old SSL interface" \
  on-missing warn

mk target set dfly-patch <<'MK'
	${REINPLACE_CMD} -e 's/^.*\- name: FreeBSD/&\n    - name: DragonFly/g' \
	${WRKSRC}/metainfo.yaml
MK

file remove files/patch-linux-only.c on-missing warn
patch apply dragonfly/@2025Q2/patch-src_main.c
```

### Makefile.DragonFly target migration pattern

`Makefile.DragonFly` target recipes (for example `dfly-patch:`) are represented
directly as `mk target ...` ops rather than line patches.

Before (legacy target snippet):

```make
dfly-patch:
	${REINPLACE_CMD} -e 's/foo/bar/' ${WRKSRC}/file
```

After (DSL):

```text
mk target set dfly-patch <<'MK'
	${REINPLACE_CMD} -e 's/foo/bar/' ${WRKSRC}/file
MK
```

This covers the target/recipe-heavy segment observed in samples while keeping
tab-sensitive recipe formatting deterministic.

### Determinism and failure behavior

- default `on-missing`: `error`
- ambiguous match: `error`
- parse failure in targeted file: `error`
- operations are applied in source order after target scoping is resolved
- re-applying same op set must be idempotent

`on-missing` override values:

- `error`: fail application
- `warn`: emit warning and continue
- `noop`: skip silently (discouraged outside migration)

### Compile mapping to IR

- Each DSL operation compiles to one normalized `[[ops]]` record
- `target @...` compiles to `ops.target`
- `mk set` -> `kind = "mk.var.set"`
- `mk unset` -> `kind = "mk.var.unset"`
- `mk add` -> `kind = "mk.var.token_add"`
- `mk remove` -> `kind = "mk.var.token_remove"`
- `mk disable-if` -> `kind = "mk.block.disable"`
- `mk replace-if` -> `kind = "mk.block.replace_condition"`
- `mk target set|append|remove|rename` -> `kind = "mk.target.*"`
- `file copy|remove` -> `kind = "file.copy"|"file.remove"`
- `text ...` ops -> `kind = "text.*"`
- `patch apply` -> `kind = "patch.apply"`
- Directive metadata compiles to `overlay.toml` fields:
  - `type` -> `[overlay].type`
  - `reason` -> `[overlay].reason`
  - `maintainer` -> top-level `maintainer`

---

## Ops IR (Canonical Execution Format)

DSL compiles to normalized ops in `overlay.toml`.

```toml
[overlay]
reason = "DragonFly-specific adjustments"
type = "port" # port | mask | dport | lock

[components]
dragonfly_dir = true

[[ops]]
id = "uses-remove-linux"
target = "@2025Q2"
kind = "mk.var.token_remove"
file = "Makefile"
name = "USES"
value = "linux"
on_missing = "warn"

[[ops]]
id = "dfly-patch-target"
target = "@2025Q2"
kind = "mk.target.set"
file = "Makefile"
name = "dfly-patch"
recipe = [
  "\t${REINPLACE_CMD} -e 's/^.*\\- name: FreeBSD/&\\n    - name: DragonFly/g' \\",
  "\t${WRKSRC}/metainfo.yaml"
]
```

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

If `overlay.type = "port"` and origin is missing in FreeBSD target branch,
overlay is stale.

Handling:

- `check`: hard error
- `compose`: blocked by preflight validation
- `migrate` (temporary):
  - out-of-place: auto-prune stale `port` overlays
  - in-place: stale prune requires explicit flag

---

## Compose Pipeline in v3

For target `T`:

1. Seed output tree from FreeBSD `T`.
2. Apply `special/` infrastructure patches/replacements.
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

## Implementation Plan (v3)

### Phase 0: foundation

- ops IR schema and validator
- DSL parser and compiler to ops IR
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
   - Mitigation: ops IR remains canonical, DSL is one-way authoring layer.
3. Recipe/tab corruption in target edits.
   - Mitigation: heredoc recipe literals and tab-preserving tests.
4. Contributor overhead.
   - Mitigation: helper tooling and templates after MVP stabilizes.

---

## Open Questions

1. Default `on_missing` for non-critical block ops: `warn` vs `error`.
2. Whether to include `mk.target.rename` in MVP or v0.1.
3. Whether to separate ops per target file later (`ops/@target.*`) vs single
   `overlay.toml`.
4. Exact default bmake oracle checks for CI vs local modes.

---

## Summary

v3 adopts a practical hybrid model:

- concise DSL for contributors
- strict ops IR as canonical execution plan
- CST-lite structural rewrites for BSDMakefiles
- patch fallback preserved for complexity and safety
- `special/` remains patch/replacement-only

This direction captures the observed real-world change patterns while avoiding
both pure patch fragility and full AST/evaluator complexity.
