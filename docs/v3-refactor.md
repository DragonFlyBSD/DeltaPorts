# DeltaPorts v3 Refactor Plan

## Purpose

This document defines a staged refactor for `scripts/generator/dportsv3` to reduce
module bloat, remove duplicated logic, improve model consistency, and preserve
current runtime behavior.

The plan is intentionally incremental: each phase is scoped to be releasable,
testable, and reversible.

## Goals

- Keep compose/apply/migration behavior stable while refactoring internals.
- Centralize shared logic (IO, validation, counting, serialization helpers).
- Decompose large modules into cohesive units with clear responsibilities.
- Normalize internal data contracts and stabilize external JSON shapes.
- Make future feature work lower-risk by reducing change coupling.

## Non-Goals

- No DSL language changes.
- No policy changes to compatibility behavior unless explicitly called out.
- No forced output schema breakage during intermediate phases.

## Refactor Principles

- Behavior first: refactors are mechanical by default.
- Small PR slices: each phase should be split into multiple reviewable commits.
- Add tests before moving logic where practical.
- Keep temporary compatibility shims until downstream usage is migrated.
- Use type hints and dataclasses to clarify contracts at module boundaries.

## Original Pain Points (Summary)

- `compose.py` and `engine/apply.py` are still too large and multi-purpose.
- Validation is duplicated across parser/semantic/apply/compose.
- CLI command modules duplicate file and JSON handling logic.
- Migration records use mixed field conventions (`target`, `targets`,
  `available_targets`) and dict-heavy flows.
- Pilot rollout code bundles orchestration, gate logic, artifact persistence,
  and reporting in a single module.

## Execution Strategy

- Order phases from lowest risk and highest reuse to highest structural impact.
- Keep `main` green after every commit.
- Run full suite (`tests/test_dportsv3_*.py`) after each phase.
- Validate compose parity after phases 4 and 5.

## Implementation Status (Completed)

This plan has been executed end-to-end.

- Overall status: completed (7/7 phases)
- Validation: `uvx pytest tests/test_dportsv3_*.py -q` -> 149 passed
- Production note: this refactor intentionally preferred internal cleanup over
  migration/backward-compat constraints.

### Phase Completion Snapshot

- Phase 1: completed
- Phase 2: completed
- Phase 3: completed
- Phase 4: completed
- Phase 5: completed
- Phase 6: completed
- Phase 7: completed

### Final Module Map (Implemented)

- Common:
  - `scripts/generator/dportsv3/common/io.py`
  - `scripts/generator/dportsv3/common/validation.py`
  - `scripts/generator/dportsv3/common/metrics.py`
  - `scripts/generator/dportsv3/common/text.py`
- Compose:
  - `scripts/generator/dportsv3/compose.py` (orchestrator/facade)
  - `scripts/generator/dportsv3/compose_models.py`
  - `scripts/generator/dportsv3/compose_discovery.py`
  - `scripts/generator/dportsv3/compose_patching.py`
  - `scripts/generator/dportsv3/compose_stages.py`
  - `scripts/generator/dportsv3/compose_reporting.py`
- Apply engine:
  - `scripts/generator/dportsv3/engine/apply.py` (pipeline + registry)
  - `scripts/generator/dportsv3/engine/apply_common.py`
  - `scripts/generator/dportsv3/engine/executors/mk_ops.py`
  - `scripts/generator/dportsv3/engine/executors/file_text_patch.py`
- Migration:
  - `scripts/generator/dportsv3/migration/models.py`
  - `scripts/generator/dportsv3/migration/inventory.py`
  - `scripts/generator/dportsv3/migration/classify.py`
  - `scripts/generator/dportsv3/migration/convert.py`
  - `scripts/generator/dportsv3/migration/waves.py`
  - `scripts/generator/dportsv3/migration/batch.py`
  - `scripts/generator/dportsv3/migration/policy.py`
  - `scripts/generator/dportsv3/migration/progress.py`
  - `scripts/generator/dportsv3/migration/dashboard.py`
- Contracts:
  - `scripts/generator/tests/test_dportsv3_schema_contracts.py`

### Post-Refactor Code Overview

The codebase now follows a layered layout with thinner orchestrators and focused
modules.

#### 1) CLI and Command Routing

- `scripts/generator/dportsv3/cli.py` defines top-level command surfaces:
  `compose`, `compose-report`, `dsl`, and `migrate` subcommands.
- `scripts/generator/dportsv3/commands/compose.py` is a thin adapter from CLI
  args to `run_compose(...)`.
- `scripts/generator/dportsv3/commands/compose_report.py` turns compose JSON
  artifacts into compact summaries.
- `scripts/generator/dportsv3/commands/dsl.py` exposes parse/check/plan/apply
  lifecycle for one overlay DSL input.
- `scripts/generator/dportsv3/commands/migrate.py` dispatches migration program
  actions (inventory/classify/convert/batch/policy/progress/dashboard/wave).

#### 2) Compose Runtime (Primary Production Path)

- `scripts/generator/dportsv3/compose.py` is the compose orchestrator only:
  validates target/profile, runs stages in order, and applies strict
  short-circuit rules.
- `scripts/generator/dportsv3/compose_models.py` defines runtime/report models:
  - `ComposeResult` (run-level aggregate)
  - `ComposeStageResult` (stage diagnostics + metadata)
  - `ComposePortReport` (per-origin accounting)
  - `ComposePortContext` (discovery + planning context)
- `scripts/generator/dportsv3/compose_discovery.py` discovers overlays,
  determines mode (`dops` vs `compat`), resolves compat assets, and validates
  target-scoped payload lanes.
- `scripts/generator/dportsv3/compose_patching.py` isolates patch execution and
  patch artifact detection, including treetop identity seeding for `GIDs/UIDs`.
- `scripts/generator/dportsv3/compose_stages.py` contains stage implementations:
  - `seed_stage`
  - `apply_special_stage`
  - `preflight_stage`
  - `prune_stale_overlays_stage`
  - `semantic_stage`
  - `fallback_stage`
  - `system_replacements_stage`
  - `finalize_stage`
- `scripts/generator/dportsv3/compose_reporting.py` centralizes human/tool
  summary generation from compose JSON payloads.

#### 3) Compat and Shared Compose Primitives

- `scripts/generator/dportsv3/compat.py` handles legacy compatibility merges per
  origin and keeps script-parity semantics.
- `scripts/generator/dportsv3/plan_types.py` provides shared plan-type
  filesystem materialization (`port`, `mask`, `dport`, `lock`) used by semantic
  and compat execution paths.
- `scripts/generator/dportsv3/policy.py` centralizes compose policy constants
  (excluded roots, special components, patch timeout, MOVED/UPDATING behavior,
  treetop identity injection rules).
- `scripts/generator/dportsv3/fsutils.py` provides shared filesystem tree-copy
  helper used across compose/compat modules.
- `scripts/generator/dportsv3/system_replacements.py` owns deterministic
  Makefile/text replacement rules applied during compose finalization.

#### 4) DSL Engine Pipeline

- `scripts/generator/dportsv3/engine/api.py` is the facade that chains
  lexer/parser/semantic/planner/apply phases.
- Core compiler pipeline modules:
  - `scripts/generator/dportsv3/engine/lexer.py`
  - `scripts/generator/dportsv3/engine/parser.py`
  - `scripts/generator/dportsv3/engine/semantic.py`
  - `scripts/generator/dportsv3/engine/planner.py`
  - `scripts/generator/dportsv3/engine/ast.py`
- `scripts/generator/dportsv3/engine/models.py` defines stable data contracts
  for diagnostics, plan ops, and apply reports.
- Apply execution stack:
  - `scripts/generator/dportsv3/engine/apply.py` (orchestrator + executor
    registry + strict/oracle behavior)
  - `scripts/generator/dportsv3/engine/apply_common.py` (shared apply helpers)
  - `scripts/generator/dportsv3/engine/executors/mk_ops.py`
  - `scripts/generator/dportsv3/engine/executors/file_text_patch.py`
  - `scripts/generator/dportsv3/engine/fsops.py` (transactional staged writes)
  - `scripts/generator/dportsv3/engine/oracle.py` (post-rewrite bmake checks)

#### 5) Migration Program Tooling

- Inventory and classification:
  - `scripts/generator/dportsv3/migration/inventory.py`
  - `scripts/generator/dportsv3/migration/classify.py`
- Conversion and wave mechanics:
  - `scripts/generator/dportsv3/migration/convert.py`
  - `scripts/generator/dportsv3/migration/waves.py`
  - `scripts/generator/dportsv3/migration/batch.py`
- Policy/progress/dashboard views:
  - `scripts/generator/dportsv3/migration/policy.py`
  - `scripts/generator/dportsv3/migration/progress.py`
  - `scripts/generator/dportsv3/migration/dashboard.py`
- Normalized migration record adapters:
  - `scripts/generator/dportsv3/migration/models.py`
- Pilot rollout modules were removed in a follow-up cleanup to reduce surface
  area and keep migration tooling focused on inventory/classification/conversion
  and wave reporting primitives.

#### 6) Cross-Cutting Shared Helpers

- `scripts/generator/dportsv3/common/io.py` centralizes JSON/text read/write and
  standardized command-facing error messages.
- `scripts/generator/dportsv3/common/validation.py` centralizes target selector
  and `on-missing` validation/normalization.
- `scripts/generator/dportsv3/common/metrics.py` centralizes counting
  aggregations.
- `scripts/generator/dportsv3/common/text.py` centralizes safe text reads for
  migration scans/classification.

#### 7) Schema Contract Safety

- `scripts/generator/tests/test_dportsv3_schema_contracts.py` locks report field
  shapes for compose/apply/migration outputs to reduce accidental schema drift
  during future refactors.

---

## Phase 1: Common IO and Command Utilities

Status: completed.

### Goal

Remove duplicated file/JSON/text helpers from command handlers.

### Scope

- In:
  - Shared read/write/emit helpers for JSON and line files.
  - Shared command error formatting helpers.
- Out:
  - Any behavior changes in compose/apply/migration logic.

### Deliverables

- New module(s), for example:
  - `scripts/generator/dportsv3/common/io.py`
  - `scripts/generator/dportsv3/common/cli_output.py`
- Migrate helpers currently duplicated in:
  - `scripts/generator/dportsv3/commands/dsl.py`
  - `scripts/generator/dportsv3/commands/migrate.py`
  - `scripts/generator/dportsv3/commands/compose_report.py`

### Tasks

1. Implement shared helpers:
   - `read_text_file(path)`
   - `read_json_file(path)`
   - `read_json_list(path, key_candidates=...)`
   - `read_lines_file(path)`
   - `emit_json(payload, pretty)`
   - `write_json_file(path, payload, trailing_newline=True)`
2. Replace per-command local helper functions with common imports.
3. Keep existing error message text stable where possible.

### Tests

- Add focused unit tests for helper behavior (missing file, invalid JSON, not-a-file).
- Re-run existing CLI command tests to confirm no output/exit regressions.

### Acceptance Criteria

- No duplicate JSON/text helper implementations remain in command modules.
- CLI tests pass with unchanged exit behavior.

Implemented in:

- `scripts/generator/dportsv3/common/io.py`
- `scripts/generator/dportsv3/commands/dsl.py`
- `scripts/generator/dportsv3/commands/migrate.py`
- `scripts/generator/dportsv3/commands/compose_report.py`
- `scripts/generator/dportsv3/commands/compose.py`

---

## Phase 2: Common Validation and Small Shared Utilities

Status: completed.

### Goal

Centralize repeated low-level validation and aggregation logic.

### Scope

- In:
  - Target selector validation utilities.
  - `on-missing` policy normalization utilities.
  - Generic counting helpers.
  - Safe text read helper for migration modules.
- Out:
  - Structural decomposition of compose/apply.

### Deliverables

- New module(s), for example:
  - `scripts/generator/dportsv3/common/validation.py`
  - `scripts/generator/dportsv3/common/metrics.py`
  - `scripts/generator/dportsv3/common/text.py`

### Tasks

1. Consolidate target regex and parsing helpers used by:
   - `engine/parser.py`
   - `engine/semantic.py`
   - `engine/apply.py`
   - `compose.py`
2. Consolidate `on-missing` value checks and normalization.
3. Replace duplicated `_count_by` implementations in migration code.
4. Replace duplicated migration `_read_text` helpers.

### Tests

- Add unit tests for target and `on-missing` validators.
- Add unit tests for shared counting helper.
- Run parser/semantic/apply tests for regression safety.

### Acceptance Criteria

- Validation primitives are defined once and reused.
- Duplicate helper functions are removed from source modules.

Implemented in:

- `scripts/generator/dportsv3/common/validation.py`
- `scripts/generator/dportsv3/common/metrics.py`
- `scripts/generator/dportsv3/common/text.py`
- `scripts/generator/dportsv3/engine/parser.py`
- `scripts/generator/dportsv3/engine/semantic.py`
- `scripts/generator/dportsv3/engine/apply.py`
- `scripts/generator/dportsv3/compose.py`
- `scripts/generator/dportsv3/migration/inventory.py`
- `scripts/generator/dportsv3/migration/classify.py`
- `scripts/generator/dportsv3/migration/dashboard.py`
- `scripts/generator/dportsv3/migration/waves.py`

---

## Phase 3: Model Normalization Layer

Status: completed.

### Goal

Standardize internal contracts for migration and report payload generation.

### Scope

- In:
  - Typed adapters for migration records.
  - Canonical field mapping for target-related fields.
  - Consistent status vocabulary.
- Out:
  - Breaking external schema changes.

### Deliverables

- New module(s), for example:
  - `scripts/generator/dportsv3/migration/models.py`
  - `scripts/generator/dportsv3/common/serialization.py`

### Tasks

1. Introduce typed migration record model with constructor normalization:
   - unify `target`, `targets`, `available_targets`.
2. Add compatibility adapter to preserve existing output keys.
3. Fix status mismatch in batch artifact reporting (`stale_count` alignment).
4. Avoid repeated plan recomputation in deterministic checks (`convert.py`).

### Tests

- Add model normalization tests for mixed input records.
- Add regression test for batch status counters.
- Add regression test for convert deterministic branch behavior.

### Acceptance Criteria

- Internal migration logic consumes normalized typed records.
- Existing output payload keys remain available.

Implemented in:

- `scripts/generator/dportsv3/migration/models.py`
- `scripts/generator/dportsv3/migration/waves.py`
- `scripts/generator/dportsv3/migration/dashboard.py`
- `scripts/generator/dportsv3/migration/convert.py`

---

## Phase 4: Compose Decomposition

Status: completed.

### Goal

Turn `compose.py` into a thin pipeline orchestrator with stage modules.

### Scope

- In:
  - Split stage logic and discovery logic into dedicated modules.
  - Keep public `run_compose` behavior stable.
- Out:
  - Changes to compose CLI surface.

### Deliverables

- Proposed layout:
  - `scripts/generator/dportsv3/compose/pipeline.py`
  - `scripts/generator/dportsv3/compose/models.py`
  - `scripts/generator/dportsv3/compose/discovery.py`
  - `scripts/generator/dportsv3/compose/patching.py`
  - `scripts/generator/dportsv3/compose/stages/seed.py`
  - `scripts/generator/dportsv3/compose/stages/special.py`
  - `scripts/generator/dportsv3/compose/stages/preflight.py`
  - `scripts/generator/dportsv3/compose/stages/semantic.py`
  - `scripts/generator/dportsv3/compose/stages/compat.py`
  - `scripts/generator/dportsv3/compose/stages/finalize.py`

### Tasks

1. Move compose dataclasses from monolith into compose models module.
2. Move overlay discovery and compat-layer selection out of pipeline.
3. Move patch execution and artifact scan helpers into patching module.
4. Move each stage implementation into dedicated stage module.
5. Keep `run_compose(...)` as orchestrator that invokes stage functions.
6. Keep `dportsv3.compose` as compatibility facade exports during transition.

### Tests

- Snapshot tests for stage order and stage metadata shape.
- Existing compose integration tests.
- Parity diff check against known baseline tree.

### Acceptance Criteria

- `compose.py` becomes a minimal facade/orchestrator.
- Stage logic is isolated and independently testable.
- Compose output and diagnostics remain stable.

Implemented in:

- `scripts/generator/dportsv3/compose.py`
- `scripts/generator/dportsv3/compose_models.py`
- `scripts/generator/dportsv3/compose_discovery.py`
- `scripts/generator/dportsv3/compose_patching.py`
- `scripts/generator/dportsv3/compose_stages.py`
- `scripts/generator/dportsv3/compose_reporting.py`

---

## Phase 5: Apply Executor Decomposition

Status: completed.

### Goal

Reduce `engine/apply.py` complexity by splitting operation handlers by domain.

### Scope

- In:
  - Extract operation executors into domain modules.
  - Keep apply pipeline behavior and diagnostics unchanged.
- Out:
  - New operation types.

### Deliverables

- Proposed layout:
  - `scripts/generator/dportsv3/engine/executors/common.py`
  - `scripts/generator/dportsv3/engine/executors/mk_ops.py`
  - `scripts/generator/dportsv3/engine/executors/file_ops.py`
  - `scripts/generator/dportsv3/engine/executors/text_ops.py`
  - `scripts/generator/dportsv3/engine/executors/patch_ops.py`
  - `scripts/generator/dportsv3/engine/apply_pipeline.py`

### Tasks

1. Extract shared helper routines (missing policy, path resolution, diagnostics).
2. Move mk operations into dedicated executor module.
3. Move file/text/patch operations into dedicated modules.
4. Keep a central executor registry mapping op kind -> handler.
5. Keep oracle integration in pipeline module with unchanged profile semantics.

### Tests

- Per-executor unit tests with fixtures.
- Existing apply end-to-end tests.
- Dry-run and diff emission regression checks.

### Acceptance Criteria

- `engine/apply.py` is reduced to orchestration and registry glue.
- No apply result field changes.

Implemented in:

- `scripts/generator/dportsv3/engine/apply.py`
- `scripts/generator/dportsv3/engine/apply_common.py`
- `scripts/generator/dportsv3/engine/executors/mk_ops.py`
- `scripts/generator/dportsv3/engine/executors/file_text_patch.py`

---

## Phase 6: Migration Pilot Decomposition

Status: completed.

### Goal

Split pilot rollout logic into cohesive modules and reduce hidden coupling.

### Scope

- In:
  - Separate manifest creation, gate evaluation, persistence, and report summarization.
  - Keep command behavior and artifact structure stable.
- Out:
  - Policy rule changes for gates.

### Deliverables

- Proposed layout:
  - `scripts/generator/dportsv3/migration/pilot/manifest.py`
  - `scripts/generator/dportsv3/migration/pilot/gates.py`
  - `scripts/generator/dportsv3/migration/pilot/compare.py`
  - `scripts/generator/dportsv3/migration/pilot/artifacts.py`
  - `scripts/generator/dportsv3/migration/pilot/ledger.py`
  - `scripts/generator/dportsv3/migration/pilot/report.py`
  - `scripts/generator/dportsv3/migration/pilot/__init__.py`

### Tasks

1. Extract tree compare logic and failure signature aggregation.
2. Extract gate computation into pure function module.
3. Extract JSON artifact write and ledger append/load functions.
4. Keep existing public pilot function imports as wrappers for compatibility.

### Tests

- Unit tests per pilot submodule.
- Existing migrate pilot command tests.
- Gate regression fixtures for pass/fail edge cases.

### Acceptance Criteria

- `migration/pilot.py` reduced to compatibility wrapper or removed.
- Pilot artifacts and ledger schema remain stable.

Implemented in:

- `scripts/generator/dportsv3/migration/pilot.py`
- `scripts/generator/dportsv3/migration/pilot_manifest.py`
- `scripts/generator/dportsv3/migration/pilot_compare.py`
- `scripts/generator/dportsv3/migration/pilot_gates.py`
- `scripts/generator/dportsv3/migration/pilot_artifacts.py`
- `scripts/generator/dportsv3/migration/pilot_report.py`

---

## Phase 7: Schema Stabilization and Cleanup

Status: completed.

### Goal

Formalize output contracts, remove temporary shims, and lock refactor results.

### Scope

- In:
  - Schema version policy and contract tests.
  - Removal of transitional wrappers no longer needed.
  - Documentation updates for module boundaries.
- Out:
  - Functional behavior changes.

### Deliverables

- Contract tests for JSON payloads:
  - compose result
  - compose-report overview
  - apply result
  - migration batch/wave/pilot artifacts
- Changelog notes for internal module moves.

### Tasks

1. Add schema fixtures and approval tests.
2. Remove deprecated compatibility helper aliases.
3. Update docs (`implementation-plan-v3.md` and runbooks) to reflect new module layout.

### Tests

- Full `tests/test_dportsv3_*.py` suite.
- Contract fixture tests must be deterministic and stable.

### Acceptance Criteria

- No duplicate helper classes/functions remain in known hotspots.
- JSON contracts are versioned and verified by tests.
- Refactor is complete with no behavior regressions.

Implemented in:

- `scripts/generator/tests/test_dportsv3_schema_contracts.py`

---

## Suggested PR Breakdown (Original)

- PR 1: Phase 1 (shared IO helpers + command migration)
- PR 2: Phase 2 (validation + shared metrics/text helpers)
- PR 3: Phase 3 (model normalization + stale counter fix + deterministic caching)
- PR 4: Phase 4A (compose models/discovery extraction)
- PR 5: Phase 4B (compose stage extraction)
- PR 6: Phase 5A (apply shared helpers + file/text executors)
- PR 7: Phase 5B (mk/patch executors + registry cleanup)
- PR 8: Phase 6 (pilot modularization)
- PR 9: Phase 7 (schema contracts + shim removal + docs)

## Risk Management

- Highest risk phases: 4, 5, and 6.
- Mitigations:
  - preserve facade imports and function signatures during transitions,
  - move code first, then simplify,
  - run parity checks after compose/apply structural moves,
  - add contract tests before removing shims.

## Exit Criteria

Refactor is considered complete when:

- The 7 phases are merged.
- `dportsv3` test suite is green with stable compose/apply/migration outputs.
- No known duplicated helpers remain for IO, validation, metrics, and target parsing.
- Module boundaries are documented and enforced by tests and imports.

Status: satisfied.
