# dportsv3 User Guide

## Purpose

This is the comprehensive operator and maintainer guide for the current
DeltaPorts generator: `dportsv3`.

Use this document as the canonical reference for:

- compose target tree generation,
- `overlay.dops` validation/planning/apply workflows,
- migration inventory and wave reporting,
- diagnostics triage and rerun loops.

## What `dportsv3` Is

`dportsv3` is the active Python generator under `scripts/generator`.

Main command families:

- `compose`: build full output tree for a target.
- `compose-report`: summarize compose JSON artifacts.
- `dsl`: parse/check/plan/apply one `overlay.dops`.
- `migrate`: inventory/classification/conversion and wave/dashboard reporting.
- `tracker`: record and browse build runs/results across targets.

## Command Entrypoints

Preferred from the DeltaPorts repo root:

```bash
./dportsv3 --help
```

- The wrapper bootstraps `scripts/generator/.venv` automatically on first run.
- It also reinstalls the editable package when `scripts/generator/pyproject.toml`
  changes.

Direct entrypoint inside the generator venv:

```bash
scripts/generator/.venv/bin/dportsv3 --help
```

Module fallback from `scripts/generator`:

```bash
python -m dportsv3 --help
```

## Preparation: Required Repositories and Context

`dportsv3` expects distinct sources with explicit roles.

### 1) DeltaPorts repository (overlay source)

This repository provides DragonFly overlay inputs and optional framework deltas.

- Required for all `compose` and `migrate` workflows.
- Passed as:
  - `--delta-root` for `compose`
  - `--root` for `migrate` commands

Minimum expected layout under Delta root:

- `ports/<category>/<port>/...`
- optional `special/...`

### 2) FreeBSD ports repository (base source)

This is the upstream base tree to compose against.

- Required for `compose`.
- Passed as `--freebsd-root`.
- Must be a git checkout.
- Must already be on the branch matching `--target` (`main` or `YYYYQn`).

Important: `dportsv3` does **not** switch FreeBSD branches for you. It validates
that current branch and compose target match.

### 3) Optional lock tree (for `type lock` overlays)

- Only needed if overlays use `type lock`.
- Passed as `--lock-root`.
- If omitted, compose falls back to `<delta-root>/locked`.

## Repository Layout Expectations

At minimum:

- Delta repo root (passed as `--delta-root` or `--root`) contains:
  - `ports/<category>/<port>/...`
  - optional `special/...`
- FreeBSD ports checkout (passed as `--freebsd-root`) contains a full ports tree.

Typical compose inputs per origin under `ports/<category>/<port>/`:

- `overlay.dops` for semantic mode.
- compatibility inputs when `overlay.dops` is absent:
  - `Makefile.DragonFly` and target variants,
  - `diffs/`,
  - `dragonfly/`,
  - optional `newport/`.

## Quarter-to-Quarter Workflow

`compose` is single-target per run. Jumping between quarters means switching the
FreeBSD checkout branch and rerunning compose with a different target.

### Example: `@2026Q1` -> `@2026Q2`

```bash
# 1) switch FreeBSD base checkout
git -C ../freebsd-ports fetch origin
git -C ../freebsd-ports switch 2026Q1

# 2) compose Q1
./dportsv3 compose \
  --target @2026Q1 \
  --delta-root . \
  --freebsd-root ../freebsd-ports \
  --output artifacts/compose/@2026Q1 \
  --replace-output --json > artifacts/compose-2026Q1.json

# 3) switch to next quarter
git -C ../freebsd-ports switch 2026Q2

# 4) compose Q2
./dportsv3 compose \
  --target @2026Q2 \
  --delta-root . \
  --freebsd-root ../freebsd-ports \
  --output artifacts/compose/@2026Q2 \
  --replace-output --json > artifacts/compose-2026Q2.json
```

Recommended practice:

- keep one output root per target (for easier diff/triage),
- run `compose-report` per target artifact,
- when using multi-target DSL refs, validate each active target with its own
  compose/apply run,
- keep source overlays branch-independent and let target scoping drive behavior.

## Targets and Scope

Compose and apply CLI target argument accepts:

- `@main`
- `@YYYYQ1`, `@YYYYQ2`, `@YYYYQ3`, `@YYYYQ4`

Inside DSL, scoped operations also support `@any` baseline scope and
comma-separated selectors on one `target` directive.

Apply execution order for a requested target `T` is deterministic:

1. `@any` operations,
2. `T` operations.

### Multiple Target References in DSL

Runtime commands are still single-target (`--target @main` or one
`@YYYYQx` value per run), but DSL can scope one operation block to multiple
explicit targets.

Valid examples:

```text
target @main
mk set BROKEN "unsupported on main"

target @2026Q1,@2026Q2
mk add LIB_DEPENDS libepoll-shim.so:devel/libepoll-shim

target @any
mk add CFLAGS -Wno-error=deprecated-declarations
```

Notes:

- `mk set` sets an existing Makefile assignment or creates a new top-level
  `VAR= value` assignment before the first target or `.include` when missing.
- `mk add` appends one token to an existing assignment; it does not create a
  missing variable.

Invalid example (rejected by semantic checks):

```text
target @any,@2026Q1
```

Rules:

- `@any` is baseline scope.
- `@any` cannot be combined with explicit selectors in the same `target`
  directive.
- explicit selectors can be comma-separated (`@main,@2026Q1,...`).
- operations for non-requested explicit targets are ignored in a run.

Operational behavior for a run targeting `T`:

1. apply all `@any` operations,
2. apply all `T` operations,
3. skip other explicit target operations.

For formal grammar/semantics, see `docs/dsl-v0.md`.

## Exit Codes

General behavior across commands:

- `0`: success.
- `1`: input/usage/read errors.
- `2`: validation/apply/compose/gate failure.

Examples:

- `dsl parse/check/plan` return `2` on parser/semantic/planner diagnostics.
- `dsl apply` returns `2` on apply/oracle failures.
- `compose` returns `2` when compose result is not `ok`.

## Quickstart (Compose-First)

1) Build migration visibility artifacts (optional but recommended):

```bash
./dportsv3 migrate inventory --root . --json > artifacts/inventory.json
./dportsv3 migrate classify artifacts/inventory.json --json > artifacts/classified.json
./dportsv3 migrate wave-plan artifacts/classified.json --target @2026Q1 --json > artifacts/wave-plan.json
```

2) Compose target output tree:

```bash
./dportsv3 compose \
  --target @2026Q1 \
  --delta-root . \
  --freebsd-root ../freebsd-ports \
  --output artifacts/compose/@2026Q1 \
  --replace-output \
  --oracle-profile local \
  --json > artifacts/compose-2026Q1.json
```

3) Summarize compose report:

```bash
./dportsv3 compose-report artifacts/compose-2026Q1.json
```

4) Fix issues and rerun same command until clean.

## Build Tracker

`tracker` is the v3 build result recorder and dashboard server.

It is a separate concern from compose:

- compose produces target trees,
- external build scripts run test/release builds,
- tracker records results and serves API/dashboard views.

Typical workflow:

1. contributor submits a fix,
2. external automation starts a `test` build for a target,
3. if acceptable, automation starts a `release` build,
4. after success, commit metadata is recorded back to the tracker.

The tracker enforces one active build per `(target, build_type)`.

### Tracker dependencies

Install the optional tracker extras on machines that run the server:

```bash
pip install -e ".[tracker]"
```

The CLI query/record path uses HTTP and does not require FastAPI locally when
talking to an already-running tracker server.

### Tracker commands

```bash
dportsv3 tracker serve [--port 8080] [--db PATH]
dportsv3 tracker start-build --target @2026Q1 --type test --server http://tracker:8080
dportsv3 tracker finish-build --run 12 --commit-sha <sha> --commit-branch 2026Q1 --server http://tracker:8080
dportsv3 tracker record-result --run 12 --origin devel/foo --version 1.2 --result success --log-url https://logs.example/devel/foo.log.gz --server http://tracker:8080
dportsv3 tracker status --target @2026Q1 --server http://tracker:8080
dportsv3 tracker failures --target @2026Q1 --server http://tracker:8080
dportsv3 tracker show-build --run 12 --server http://tracker:8080
dportsv3 tracker compare-builds 10 12 --server http://tracker:8080
```

### Tracker dashboard pages

Once the server is running, the dashboard provides:

- `/`: target overview,
- `/target/{target}`: per-target current status,
- `/target/{target}/{cat}/{port}`: one port's current state and recent history,
- `/builds`: recent build runs,
- `/builds/{id}`: one build run detail (auto-refresh while active),
- `/builds/compare?a=&b=`: build-to-build comparison,
- `/diff?a=&b=`: cross-target current-state diff.

Current log handling is link-only: results can store `log_url`, and the dashboard
renders it as a plain link. Log serving/decompression policy is external.

## Compose Command

Reference:

```bash
dportsv3 compose --target @... --output <dir> --freebsd-root <dir> [options]
```

Key flags:

- `--delta-root`: Delta repo root (default `.`).
- `--lock-root`: optional source tree for `type lock` overlays.
- `--dry-run`: evaluate without filesystem writes.
- `--strict`: stop on first failed stage.
- `--replace-output`: allow replacing non-empty output root.
- `--origin <category/port>`: re-compose only selected origins into an existing
  composed output tree; repeat the flag to select several ports.
- `--prune-stale-overlays`: remove stale `type=port` overlays from output after
  preflight; delta overlays are kept intact.
- `--oracle-profile {off,local,ci}`:
  - `off`: skip oracle checks.
  - `local`: run oracle checks when possible; missing `bmake` is non-fatal.
  - `ci`: missing oracle tool/check failures are fatal.

Stage order:

1. `seed_output`
2. `apply_special`
3. `preflight_validate`
4. `prune_stale_overlays`
5. `apply_semantic_ops`

Incremental selected-origin compose:

- When `--origin` is used, compose works against the existing full output tree.
- `seed_output` and `apply_special` are skipped.
- Only the selected origins are revalidated and recomposed.
- If `--output` does not already exist, compose fails.
6. `apply_compat_ops`
7. `apply_system_replacements`
8. `finalize_tree`

Per-origin mode selection is automatic:

- `overlay.dops` exists -> semantic (dops) mode.
- `overlay.dops` missing -> compatibility mode.

## Compatibility Behavior (No `overlay.dops`)

When `overlay.dops` is missing for an origin, compose runs compatibility mode.

### Compat type inference

Inference order:

1. `overlay.toml` type (`overlay.type` or top-level `type`) if valid
2. `overlay.toml` `status.ignore` -> `mask`
3. `STATUS` first token (`PORT|MASK|DPORT|LOCK`)
4. `newport/` exists -> `dport`
5. default -> `port`

### Compat execution model

- `port`: seed upstream origin then apply compatibility artifacts.
- `mask`: remove/skip origin in output.
- `dport`: materialize from `newport/`.
- `lock`: materialize from `--lock-root` (or `<delta-root>/locked`).

For `port`, compatibility stages run in this order:

1. apply `Makefile.DragonFly*` (if present per precedence)
2. copy `dragonfly/` payload files
3. apply `diffs/REMOVE` deletions
4. apply fallback `diffs/*.diff` patches

### Makefile precedence in compat

Priority order:

1. `Makefile.DragonFly` (legacy root file)
2. `Makefile.DragonFly.@<target>`
3. `Makefile.DragonFly.@any`

Notes:

- `diffs/*.patch` files are intentionally ignored in compat fallback selection.
- Patch failures are reported as compose stage errors (`apply_compat_ops`).

### Compat vs Semantic During Transition

Per origin, compose picks exactly one mode:

- `overlay.dops` exists -> semantic mode.
- `overlay.dops` missing -> compatibility mode.

As soon as `overlay.dops` is present for an origin, compatibility artifacts for
that same origin are not executed by compose.

## Compose Report JSON

`compose --json` emits:

- top-level: `ok`, `target`, `output_path`, `oracle_profile`, `summary`,
  `stages`, `ports`
- summary fields include totals and oracle counters
- each stage includes `name`, `success`, `changed`, `skipped`, `warnings`,
  `errors`, `metadata`, `duration`
- each port row includes mode and accounting fields (`total_ops`,
  `fallback_patch_count`, `compat_stages_executed`, `notes`, etc.)

`compose-report` builds compact triage sections (top error codes/origins/patches,
stale hints, mode counts).

### Checking `special/` in compose JSON

To verify framework (`special/`) handling, inspect the stage whose `name` is
`apply_special`.

- top-level success still matters first: `ok: true`
- then find `stages[]` entry where `name == "apply_special"`
- check `success: true` on that stage
- check `errors: []` on that stage; patch failures appear there as
  `E_COMPOSE_SPECIAL_PATCH_FAILED`
- inspect `metadata.components[]` for per-component rows (`Mk`, `Templates`,
  `Tools`, `Keywords`, `treetop`)

Useful per-component fields under `apply_special.metadata.components[]`:

- `component`: component name
- `patched`: number of selected diffs applied successfully
- `failed_patches`: patch file names that failed
- `selected_patches`: how many diffs were selected for that target
- `removed_legacy_files`: legacy files removed during special handling
- `missing_target_dir`: whether compose had to bootstrap a missing target dir
- `auto_created_from_main`: whether non-`main` target payloads were created from
  unscoped `main` during this run

For a clean `special/` run, the usual checks are:

- `apply_special.success == true`
- `apply_special.errors` is empty
- each relevant component row has `failed_patches == []`
- if bootstrapping a new quarter, `auto_created_from_main: true` is expected and
  appears alongside bootstrap warnings

## DSL Workflow

### Parse and Check

```bash
dportsv3 dsl parse ports/category/name/overlay.dops
dportsv3 dsl check ports/category/name/overlay.dops
```

### Plan

```bash
dportsv3 dsl plan ports/category/name/overlay.dops --json
```

### Apply (safe preview first)

```bash
dportsv3 dsl apply ports/category/name/overlay.dops \
  --port-root artifacts/compose/@2026Q1/category/name \
  --target @2026Q1 \
  --dry-run \
  --diff \
  --oracle-profile local
```

Notes:

- `--diff` requires `--dry-run`.
- `--strict` fails fast on first operation failure.
- `--json` gives machine-readable apply report with op results and diffs.
- Path roots in apply semantics:
  - `file materialize <src> -> <dst>` reads `<src>` from the overlay source
    directory (where `overlay.dops` lives) and writes `<dst>` under
    `--port-root`.
  - `file copy <src> -> <dst>` reads and writes inside `--port-root`.
  - `patch apply <path>` is immediate patching of files under `--port-root`
    (not build-time patch asset registration).
- `file materialize` v1 accepts explicit file paths only (no wildcard/glob
  source expansion).

For ports that carry DragonFly-specific build-time patch assets, prefer
materializing into `dragonfly/` rather than `files/` so the framework can keep
its layered patch lane behavior (`files/` first, then `dragonfly/`).

## Migration Commands

`migrate` is for planning/reporting and incremental conversion support.

Available actions:

- `inventory`: scan candidate overlays.
- `classify`: bucket records.
- `convert`: convert one origin to `overlay.dops` shape.
- `batch`: convert multiple records with filters.
- `policy-check`: evaluate forward policy violations.
- `progress`: evaluate completion thresholds.
- `dashboard`: aggregate policy/progress and CI gates.
- `wave-plan`: deterministic candidate selection.
- `wave-report`: evaluate conversion wave quality.

Examples:

```bash
dportsv3 migrate policy-check artifacts/classified.json --strict --json
dportsv3 migrate dashboard artifacts/classified.json --results artifacts/results.json --strict --json
dportsv3 migrate wave-report artifacts/results.json --strict --json
```

## Port Transition Guide (Compat -> `overlay.dops`)

Use this flow when transitioning legacy overlays to semantic DSL.

### Step 1: inventory and classify

```bash
./dportsv3 migrate inventory --root . --json > artifacts/inventory.json
./dportsv3 migrate classify artifacts/inventory.json --json > artifacts/classified.json
```

Buckets indicate migration path:

- `auto-safe`: candidate for automated conversion
- `review-needed`: manual conversion required
- `fallback-only`: keep compat/patch flow for now
- `stale`: overlay no longer matches upstream origin

### Step 2: convert one port (dry-run first)

```bash
./dportsv3 migrate convert artifacts/classified.json category/port --dry-run --json
```

Then write if acceptable:

```bash
./dportsv3 migrate convert artifacts/classified.json category/port --json
```

### Step 3: validate generated DSL

```bash
./dportsv3 dsl check ports/category/port/overlay.dops
./dportsv3 dsl plan ports/category/port/overlay.dops --json
```

Note: current auto-conversion emits `target @main` in generated `overlay.dops`.
If your operational baseline should be quarter-agnostic, edit target scoping to
fit your policy (`@any` baseline + explicit quarter overrides where needed).

### Step 3b: authoring pattern for multi-quarter maintenance

Recommended pattern:

- place shared behavior in `target @any`,
- add only true divergences in explicit targets (`@main`, `@YYYYQx`),
- keep quarter-specific overrides minimal and local.

Example:

```text
target @any
port net/example
type port
reason "DragonFly baseline + quarter override"

mk add CFLAGS -Wno-error=deprecated-declarations
mk add LIB_DEPENDS libepoll-shim.so:devel/libepoll-shim

target @2026Q2
mk set BROKEN "upstream API break in 2026Q2"
```

Validation sequence for multi-target overlays:

```bash
# structure/semantic check
./dportsv3 dsl check ports/category/port/overlay.dops

# inspect expanded plan
./dportsv3 dsl plan ports/category/port/overlay.dops --json

# preview for each active target
./dportsv3 dsl apply ports/category/port/overlay.dops --port-root artifacts/compose/@main/category/port --target @main --dry-run --diff
./dportsv3 dsl apply ports/category/port/overlay.dops --port-root artifacts/compose/@2026Q1/category/port --target @2026Q1 --dry-run --diff
./dportsv3 dsl apply ports/category/port/overlay.dops --port-root artifacts/compose/@2026Q2/category/port --target @2026Q2 --dry-run --diff
```

### Step 3c: conditional Makefile block authoring (`.if ... .endif`)

Use `mk block set` when a port needs conditional blocks (for example
`defined(LITE)` or `exists(...)`) that are difficult to express with token/line
ops.

Example:

```text
target @any
port editors/vim
type port
reason "manual conversion from Makefile.DragonFly"

mk remove OPTIONS_DEFAULT PTYTHON on-missing warn

mk block set condition "defined(LITE)" <<'BLK'
PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS
BLK

mk block set condition "exists(/usr/lib/priv/libprivate_ncursesw.so)" <<'BLK'
MAKE_ARGS+= EXTRA_DEFS="${CFLAGS:M-I*}"
BLK
```

Current v1 scope:

- block matching is `.if ... .endif` only (not `.elif`-only matching),
- if no matching `.if` block exists, a new block is inserted before the final
  `.include` line (or appended at EOF if absent),
- use `contains "..."` only when duplicate `.if` conditions require
  disambiguation.

### Step 4: preview apply on composed tree

```bash
./dportsv3 dsl apply ports/category/port/overlay.dops \
  --port-root artifacts/compose/@2026Q1/category/port \
  --target @2026Q1 \
  --dry-run --diff --oracle-profile local
```

### Step 5: compose end-to-end and compare outcomes

Run compose for the quarter and inspect per-origin notes and stage diagnostics.

Transition rule of thumb:

- once `overlay.dops` exists, that origin runs semantic mode (compat artifacts for
  the same origin are not executed in compose).

## Troubleshooting

### `E_COMPOSE_INVALID_TARGET`

- Use `@main` or `@YYYYQ[1-4]`.

### `E_COMPOSE_TARGET_BRANCH_MISMATCH`

- Your FreeBSD checkout branch does not match `--target`.
- Switch branch in `--freebsd-root` and rerun.

### `E_COMPOSE_OUTPUT_NOT_EMPTY`

- Output path is non-empty and `--replace-output` was not provided.

### Stale overlay errors

- On first detection, compose auto-writes `removed_in = ["@<target>"]` into the
  overlay's `overlay.toml` when writes are allowed.
- The first run still reports the stale overlay as an error; the next run skips
  that overlay for the same target via `removed_in`.
- If you also want the stale port removed from the composed output tree on that
  same run, rerun with `--prune-stale-overlays`.

### Patch failures in `apply_special` or compat stage

- Inspect stage errors and file names in compose report.
- Fix source patch/payload content and rerun.

### Oracle failures

- Use `--oracle-profile off` for local exploratory loops.
- Keep `--oracle-profile ci` for strict CI enforcement.

### Multi-target DSL scope issues

- `target @any,@2026Q1` fails semantic checks by design.
- If an expected edit did not run, confirm `--target` matches the DSL scope.
- For quarter drift, compare `dsl apply --dry-run --diff` outputs per target.

## Recommended Operator Loop

1. Generate inventory/classification visibility artifacts.
2. Run compose into a clean output root.
3. Run compose-report for compact triage.
4. Fix failures in overlay sources.
5. For multi-target overlays, validate each active target:
   - `dsl check` and `dsl plan --json`
   - `dsl apply --dry-run --diff --target <target>` for each target
   - `compose` and `compose-report` per target artifact
6. Rerun until clean.
7. Enforce strict/oracle CI mode for final validation.

## Command Help Pointers

For exact runtime flags, use:

```bash
dportsv3 --help
dportsv3 compose --help
dportsv3 compose-report --help
dportsv3 dsl --help
dportsv3 migrate --help
dportsv3 tracker --help
```
