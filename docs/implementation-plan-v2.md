# DeltaPorts v2 Implementation Plan

## Objective

Produce a final DPorts tree as:

- FreeBSD ports at `--target`
- plus DeltaPorts overlays at `--target`

The implementation will be delivered in two steps:

1. Build complete, reusable core building blocks.
2. Redesign the CLI around those building blocks.

---

## Locked Design Constraints

1. Targets are restricted to:
   - `main`
   - `YYYYQ[1-4]`
2. Overlay components are target-scoped:
   - `Makefile.DragonFly.@<target>`
   - `diffs/@<target>/...`
   - `dragonfly/@<target>/...`
3. Root component paths are invalid.
4. FreeBSD ports source uses a single checkout with branch switching in place.
5. Branch switch/sync fails if the FreeBSD tree is dirty.

---

## Step 1: Core Building Blocks

Goal: make all required operations available as code-level primitives before any major CLI redesign.

1. Define the Step 1 architecture contract before implementation starts.
   - Specify pipeline phases, stage inputs/outputs, dry-run guarantees, and error model.
   - Define what each stage may mutate and what it must only report.
   - Primary files: `scripts/generator/dports/models.py`, `scripts/generator/dports/merge.py`, `scripts/generator/dports/special.py`.

2. Introduce a canonical compose pipeline module with explicit stage functions.
   - Required stage functions: `seed_base_tree`, `apply_infrastructure`, `apply_overlay_ports`, `finalize_tree`.
   - Keep orchestration code independent from CLI command handlers.
   - Primary files: `scripts/generator/dports/compose.py` (new), `scripts/generator/dports/merge.py`, `scripts/generator/dports/special.py`.

3. Add structured stage/result dataclasses and aggregate reporting.
   - Return stage-level counts, warnings, errors, durations, and success flags.
   - Ensure results are machine-readable and stable for future CLI/reporting layers.
   - Primary files: `scripts/generator/dports/models.py`, `scripts/generator/dports/compose.py`.

4. Implement full-tree seeding from FreeBSD target into output.
   - Seed from FreeBSD target branch into `merged_output` with explicit overwrite policy.
   - Enforce branch/target preconditions before mutating output.
   - Primary files: `scripts/generator/dports/merge.py`, `scripts/generator/dports/config.py`, `scripts/generator/dports/utils.py`.

5. Wire infrastructure stage to the canonical infrastructure merge path.
   - Use `merge_infrastructure` as the primary mechanism for Mk/Templates/treetop/Tools/Keywords.
   - Do not rely on ad-hoc patch-only flows for final composition.
   - Primary files: `scripts/generator/dports/special.py`, `scripts/generator/dports/compose.py`.

6. Refactor overlay application stage to support explicit selectors.
   - Supported selectors: single origin, overlay candidates, full-tree selection.
   - Keep selector logic separate from stage execution logic.
   - Primary files: `scripts/generator/dports/merge.py`, `scripts/generator/dports/utils.py`, `scripts/generator/dports/overlay.py`.

7. Unify discovery helpers to remove command-specific drift.
   - Provide shared discovery primitives used consistently by check/merge/verify/migrate/compose.
   - Ensure candidate vs full-tree semantics are explicit and non-ambiguous.
   - Primary files: `scripts/generator/dports/utils.py`, `scripts/generator/dports/validate.py`, `scripts/generator/dports/overlay.py`.

8. Make validation fully reusable across all Step 1 stages.
   - Reuse one validation policy for target checks, root-path violations, missing `@<target>` content, and diff format checks.
   - Allow stage preflight and independent validation runs to produce consistent outcomes.
   - Primary files: `scripts/generator/dports/validate.py`, `scripts/generator/dports/overlay.py`, `scripts/generator/dports/quarterly.py`.

9. Complete migration phase APIs for output-tree and in-place workflows.
   - Keep phase APIs explicit: `layout`, `state`, `cleanup`.
   - Ensure collision-safe operations and deterministic cleanup behavior.
   - Primary files: `scripts/generator/dports/migrate.py`, `scripts/generator/dports/state.py`.

10. Add integration verification harness for Step 1 completion.
   - Validate end-to-end compose flow for `main` and one quarterly target.
   - Verify dry-run behavior, branch guards, strict target validation, and migration/compose interoperability.
   - Primary files: `scripts/generator/dports/compose.py`, `scripts/generator/dports/commands/*` (temporary wiring), `docs/implementation-plan-v2.md`.

---

## Step 2: CLI Redesign

Goal: replace ad-hoc command growth with a stable workflow-driven CLI.

### 2.1 CLI Structure

Adopt grouped workflows:

- `dports repo ...`
- `dports overlay ...`
- `dports migrate ...`
- `dports compose ...`
- `dports state ...`
- `dports dev ...`

### 2.2 Canonical User Workflow

Define one top-level command for final tree generation:

- `dports compose run --target <target> --output <path>`

This command orchestrates the compose pipeline and reports phase-level results.

### 2.3 Compatibility Strategy

- Keep existing top-level commands as compatibility aliases for one transition cycle.
- Show deprecation guidance from aliases to new workflow commands.
- Remove deprecated paths after the transition window.

Primary files:

- `scripts/generator/dports/cli.py`
- `scripts/generator/dports/commands/__init__.py`
- `scripts/generator/dports/commands/*.py`

---

## Execution Order

1. Implement compose pipeline API.
2. Integrate strict validation into pipeline stages.
3. Complete migration/state primitives and structured reporting.
4. Add target-aware state query helpers.
5. Redesign CLI command tree around workflows.
6. Add aliases and deprecation path.
7. Validate full end-to-end compose runs on `main` and one quarterly target.

---

## Acceptance Criteria

### Step 1 complete

1. A code-level compose flow can build a full output tree from FreeBSD target + DeltaPorts target.
2. Validation is reusable and consistent across check, migrate, and compose paths.
3. Migration supports both output-tree and in-place operation safely.
4. State consolidation and target-aware querying are available as library primitives.

### Step 2 complete

1. The primary operator flow is `compose run`.
2. Command grouping is workflow-based, not flag-driven.
3. Existing commands map cleanly to compatibility aliases with deprecation guidance.

---

## Verification Matrix

- `main` target full compose run
- `YYYYQn` target full compose run
- dirty FreeBSD tree sync rejection
- branch mismatch rejection for target-bound operations
- root component path validation failures
- missing `@<target>` component failures
- migration collision reporting
- out-of-place migration + compose on migrated tree

---

## Non-Goals

- Arbitrary target names outside `main` and `YYYYQ[1-4]`
- Root fallback behavior for overlay components
- Maintaining ad-hoc CLI growth patterns
