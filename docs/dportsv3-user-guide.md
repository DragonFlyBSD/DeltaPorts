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

## Command Entrypoints

From `scripts/generator`:

```bash
python -m dportsv3 --help
```

If installed from package entrypoints:

```bash
dportsv3 --help
```

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

## Targets and Scope

Compose and apply CLI target argument accepts:

- `@main`
- `@YYYYQ1`, `@YYYYQ2`, `@YYYYQ3`, `@YYYYQ4`

Inside DSL, scoped operations also support `@any` baseline scope and
comma-separated selectors on one `target` directive.

Apply execution order for a requested target `T` is deterministic:

1. `@any` operations,
2. `T` operations.

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
.venv/bin/python -m dportsv3 migrate inventory --root . --json > artifacts/inventory.json
.venv/bin/python -m dportsv3 migrate classify artifacts/inventory.json --json > artifacts/classified.json
.venv/bin/python -m dportsv3 migrate wave-plan artifacts/classified.json --target @2026Q1 --json > artifacts/wave-plan.json
```

2) Compose target output tree:

```bash
.venv/bin/python -m dportsv3 compose \
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
.venv/bin/python -m dportsv3 compose-report artifacts/compose-2026Q1.json
```

4) Fix issues and rerun same command until clean.

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
- `--prune-stale-overlays`: remove stale `type=port` overlays from delta/output.
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
6. `apply_compat_ops`
7. `apply_system_replacements`
8. `finalize_tree`

Per-origin mode selection is automatic:

- `overlay.dops` exists -> semantic (dops) mode.
- `overlay.dops` missing -> compatibility mode.

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

## Troubleshooting

### `E_COMPOSE_INVALID_TARGET`

- Use `@main` or `@YYYYQ[1-4]`.

### `E_COMPOSE_TARGET_BRANCH_MISMATCH`

- Your FreeBSD checkout branch does not match `--target`.
- Switch branch in `--freebsd-root` and rerun.

### `E_COMPOSE_OUTPUT_NOT_EMPTY`

- Output path is non-empty and `--replace-output` was not provided.

### Stale overlay errors

- If overlay is stale and should be removed, rerun with
  `--prune-stale-overlays`.

### Patch failures in `apply_special` or compat stage

- Inspect stage errors and file names in compose report.
- Fix source patch/payload content and rerun.

### Oracle failures

- Use `--oracle-profile off` for local exploratory loops.
- Keep `--oracle-profile ci` for strict CI enforcement.

## Recommended Operator Loop

1. Generate inventory/classification visibility artifacts.
2. Run compose into a clean output root.
3. Run compose-report for compact triage.
4. Fix failures in overlay sources.
5. Rerun compose until clean.
6. Enforce strict/oracle CI mode for final validation.

## Command Help Pointers

For exact runtime flags, use:

```bash
dportsv3 --help
dportsv3 compose --help
dportsv3 compose-report --help
dportsv3 dsl --help
dportsv3 migrate --help
```
