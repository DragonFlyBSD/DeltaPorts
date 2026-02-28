# DeltaPorts v3 Implementation Plan: DSL Engine First

## DSL Engine Plan (Phase 1 of dportsv3)

- Build a standalone package at `scripts/generator/dportsv3` with zero runtime dependency on legacy `dports` modules.
- Make `overlay.dops` the only source format; compile to an in-memory normalized plan (no persisted ops file).
- Deliver parser + semantic validator + planner first; no compose/apply integration yet.

## Locked Defaults For This Plan

- One `overlay.dops` file per origin (`port category/name` required once).
- Multiple `target @...` blocks per file are allowed.
- `type` is file-global (`port|mask|dport|lock`), not target-scoped.
- No implicit default target; ops must be under an explicit `target @...`.
- Strict-by-default behavior (`on-missing error`, ambiguity is error).

## Implementation Steps

1. **Bootstrap `dportsv3` package**

### Step 1 Goal

- Stand up an isolated, installable `dportsv3` CLI scaffold with `dsl parse`, `dsl check`, and `dsl plan` command surfaces.
- No legacy runtime dependency.
- No apply/compose logic yet.

### Scope (Step 1 only)

- In: package skeleton, CLI wiring, command stubs, packaging entrypoint, smoke tests.
- Out: real lexer/parser/analyzer/planner behavior (that starts in later steps).

### Work Plan

1. **Create package skeleton**
- Add `scripts/generator/dportsv3/` with:
  - `__init__.py`
  - `__main__.py`
  - `cli.py`
  - `commands/__init__.py`
  - `commands/dsl.py`
  - `engine/__init__.py`
  - `engine/api.py` (facade interfaces only)
  - `engine/models.py` (diagnostic/result dataclasses only)

2. **Add isolated CLI entrypoint**
- Update `scripts/generator/pyproject.toml`:
  - add script: `dportsv3 = "dportsv3.cli:main"`
  - include `dportsv3` package in wheel build config.
- Keep existing `dports` untouched.

3. **Implement command surface (stubbed)**
- Root command: `dportsv3`
- Subcommands:
  - `dportsv3 dsl parse <overlay.dops>`
  - `dportsv3 dsl check <overlay.dops>`
  - `dportsv3 dsl plan <overlay.dops> [--json]`
- Behavior for now:
  - validate input path/readability
  - call engine facade
  - return deterministic "not implemented yet" diagnostic for parser/analyzer/planner internals
  - consistent exit codes and error formatting.

4. **Define engine interfaces now (no logic yet)**
- `parse_dsl(text, source_path) -> ParseResult`
- `check_dsl(text, source_path) -> CheckResult`
- `build_plan(text, source_path) -> PlanResult`
- Shared diagnostic model with code/severity/message/location to lock contract early.

5. **Add bootstrap tests**
- CLI help smoke test (`dportsv3 --help`, `dportsv3 dsl --help`).
- Command routing tests for `parse/check/plan`.
- Input file validation tests (missing file, unreadable file).
- "No legacy dependency" guard test: fail if any `dportsv3` module imports `dports.` namespace.

6. **Docs touchpoint for Step 1 completion**
- Update `docs/implementation-plan-v3.md` with Step 1 status + what was intentionally stubbed.
- Keep `docs/architecture-v3.md` unchanged for this step (already aligned with in-memory plan).

### Acceptance Criteria

- `dportsv3` is installable/runnable via script entrypoint and `python -m dportsv3`.
- `dsl parse/check/plan` commands exist and behave predictably.
- `dportsv3` imports do not depend on legacy `dports` modules.
- Bootstrap tests pass.
- No `overlay.toml` assumption appears in `dportsv3` code.

### Step 1 Status (Implemented)

- Implemented package scaffold under `scripts/generator/dportsv3`.
- Added isolated CLI entrypoint `dportsv3` in `scripts/generator/pyproject.toml`.
- Added stubbed command surfaces: `dsl parse`, `dsl check`, `dsl plan`.
- Added engine facade contracts and deterministic `E_NOT_IMPLEMENTED` diagnostics.
- Added bootstrap tests under `scripts/generator/tests` including no-legacy-import guard.
- Intentionally left parser/analyzer/planner internals unimplemented for Step 1.

2. **Freeze grammar/spec in docs**

### Step 2 Goal

- Make the DSL spec normative and standalone.
- Keep `docs/architecture-v3.md` architectural (not language-lawyer heavy).

### Scope

- In: documentation restructuring + normalization.
- Out: parser/analyzer/planner code changes.

### Implementation Plan

1. **Create canonical spec doc**
- Add `docs/dsl-v0.md` as the single normative DSL reference.
- Move all grammar/syntax/semantics content there from `docs/architecture-v3.md`.

2. **Define strict spec structure in `docs/dsl-v0.md`**
- Purpose and status (`v0`, normative).
- Lexical rules (comments, strings, escapes, heredoc behavior, continuation lines).
- Grammar (EBNF-style).
- Directives (`target`, `port`, `type`, `reason`, `maintainer`).
- Operation forms (`mk`, `file`, `text`, `patch`).
- Determinism and failure policy (`on-missing`, ambiguity, parse failure).
- Compile mapping to normalized in-memory plan (field-level mapping).
- Diagnostics contract (error classes/codes expected by `dsl parse/check/plan`).
- Conformance examples (valid + invalid snippets).

3. **Trim `docs/architecture-v3.md`**
- Replace the long DSL section with a short “DSL Spec” section linking to `docs/dsl-v0.md`.
- Keep only architectural points in architecture doc:
  - why DSL exists,
  - normalized in-memory plan model,
  - compose stages, boundaries, policy.
- Remove duplicated grammar-level details to avoid drift.

4. **Cross-document consistency pass**
- Ensure terminology matches everywhere:
  - “normalized in-memory plan” (not persisted ops file),
  - no `overlay.toml` in v3 runtime model.
- Align operation naming between docs (`mk.var.*`, `mk.target.*`, etc.).
- Ensure examples in architecture still match DSL spec.

5. **Update implementation tracker**
- In `docs/implementation-plan-v3.md`, mark Step 2 completed once docs are merged.
- Record what changed and what remains (Step 3 lexer).

### Acceptance Criteria

- `docs/dsl-v0.md` is the sole normative language spec.
- `docs/architecture-v3.md` links to DSL spec and no longer duplicates grammar internals.
- No contradictions between the two docs.
- No references implying persisted transition ops files.
- Reader can implement lexer/parser from `docs/dsl-v0.md` alone.

### Suggested execution order

1. Draft `docs/dsl-v0.md`
2. Refactor `docs/architecture-v3.md` to reference it
3. Consistency sweep
4. Mark Step 2 status in `docs/implementation-plan-v3.md`

### Step 2 Status (Implemented)

- Added canonical normative DSL spec at `docs/dsl-v0.md`.
- Refactored `docs/architecture-v3.md` to reference `docs/dsl-v0.md` and removed duplicated grammar internals.
- Kept architecture-level content focused on normalized in-memory plan, boundaries, pipeline, and policy.
- Completed terminology consistency pass for operation kinds and runtime model wording.
- Next remaining implementation step is Step 3 (Lexer).

3. **Lexer**

### Step 3 Goal

- Implement a standalone DSL lexer with source spans (`line`, `column`) for all emitted tokens.
- Cover lexical features defined in `docs/dsl-v0.md` (comments, strings/escapes, `->`, heredoc, continuations).
- Preserve heredoc body exactly (including leading tabs and raw content).

### Scope

- In: lexer module, token/span models, lexer diagnostics, lexer tests, minimal API integration.
- Out: AST parsing, semantic checks, plan building logic.

### Implementation Plan

1. **Add lexer data model**
- Create token/span types in `scripts/generator/dportsv3/engine/models.py` (or a dedicated `engine/tokens.py`):
  - `SourceSpan` (`line_start`, `column_start`, `line_end`, `column_end`)
  - `Token` (`kind`, `value`, `span`)
- Token kinds to support:
  - `WORD`, `STRING`, `ARROW`, `HEREDOC_START`, `HEREDOC_BODY`, `NEWLINE`, `EOF`
  - Optional: `COMMENT` (or comments skipped entirely)

2. **Implement lexer module**
- Add `scripts/generator/dportsv3/engine/lexer.py` with `lex_dsl(text, source_path) -> LexResult`.
- Lexical behavior:
  - Skip comments outside heredoc (`# ...`)
  - Parse double-quoted strings with escapes: `\\`, `\"`, `\n`, `\t`
  - Parse bare words as space-delimited tokens
  - Emit `ARROW` for `->`
  - Recognize heredoc start `<<'TAG'` and capture raw body until exact terminator line
  - Support trailing `\` as continuation marker by joining logical lines in token stream
  - Track precise source spans for diagnostics and tokens

3. **Define lexer diagnostics**
- Add stable parse-lex error codes (under `E_PARSE_*`), e.g.:
  - `E_PARSE_UNTERMINATED_STRING`
  - `E_PARSE_INVALID_ESCAPE`
  - `E_PARSE_UNTERMINATED_HEREDOC`
  - `E_PARSE_INVALID_HEREDOC_START`
  - `E_PARSE_UNEXPECTED_CHAR`
- Include source path + line/column in each diagnostic.

4. **Integrate into API (minimal)**
- Update `scripts/generator/dportsv3/engine/api.py`:
  - `parse_dsl()` runs lexer first.
  - If lexical errors exist: return `ParseResult(ok=False, diagnostics=...)`.
  - If lexer succeeds: keep parser-not-implemented behavior for now (`E_NOT_IMPLEMENTED`) until Step 4.
- Keep `check_dsl()` and `build_plan()` behavior unchanged for Step 3.

5. **Add lexer-focused tests**
- New test file: `scripts/generator/tests/test_dportsv3_lexer.py`.
- Required coverage:
  - basic directives/ops tokenization
  - string escapes
  - comments ignored outside heredoc
  - `->` token recognition
  - continuation-line handling
  - heredoc start/body/end (exact body preservation, including tabs)
  - lexical error paths (unterminated string/heredoc, invalid escape)
  - line/column span correctness on representative cases
- Update existing CLI tests only if output changes due to lexer diagnostics.

6. **Step 3 tracker update**
- Update `docs/implementation-plan-v3.md` with Step 3 status once merged.
- Note that Step 4 parser remains pending.

### Acceptance Criteria

- Lexer exists and is callable independently from parser.
- `parse_dsl()` surfaces lexical errors with `E_PARSE_*` and source spans.
- Heredoc body is preserved byte-for-byte as intended by spec (including leading tabs).
- Tests for lexer behavior and error cases pass.
- No dependency introduced on legacy (`dports.*`) modules.

### Suggested execution order

1. Add token/span/lex result models
2. Implement `lexer.py` core scanner + heredoc state
3. Wire `parse_dsl()` to run lexer
4. Add tests and adjust CLI expectations if needed
5. Mark Step 3 status in implementation doc

### Step 3 Status (Implemented)

- Added token/span/lexer result models in `scripts/generator/dportsv3/engine/models.py`.
- Implemented lexer in `scripts/generator/dportsv3/engine/lexer.py`.
- Added lexer diagnostics with `E_PARSE_*` codes and source location metadata.
- Integrated lexer into `parse_dsl()` in `scripts/generator/dportsv3/engine/api.py`.
- Added lexer-focused test coverage in `scripts/generator/tests/test_dportsv3_lexer.py`.
- Kept `check_dsl()` and `build_plan()` as Step 1 stubs for parser/planner follow-up work.
- Next remaining implementation step is Step 4 (Parser AST).

4. **Parser (AST)**

### Step 4 Goal

- Implement a recursive-descent parser that consumes lexer tokens and builds a typed AST.
- Parse all directive and operation forms defined in `docs/dsl-v0.md`.
- Replace `parse_dsl()` stub behavior with real parse success/failure + rich parse diagnostics.

### Scope

- In: AST models, parser module, parser diagnostics, `parse_dsl()` integration, parser tests, CLI parse behavior updates.
- Out: semantic validation rules (Step 5), normalized plan compilation (Step 6), apply engine.

### Implementation Plan

1. **Add AST data model**
- Extend `scripts/generator/dportsv3/engine/models.py` (or add `engine/ast.py`) with:
  - `AstDocument` (top-level statements)
  - Directive nodes: `TargetDirective`, `PortDirective`, `TypeDirective`, `ReasonDirective`, `MaintainerDirective`
  - Operation family nodes:
    - `MkOpNode` (with variants/actions for `set/unset/add/remove/disable-if/replace-if/target-*`)
    - `FileOpNode`
    - `TextOpNode`
    - `PatchOpNode`
  - Shared per-node `span` for diagnostics
- Keep AST syntactic; do not enforce semantic constraints yet (single `port`, target ordering, etc.).

2. **Implement parser module**
- Add `scripts/generator/dportsv3/engine/parser.py` with:
  - `parse_tokens(tokens, source_path) -> ParseResult`
  - Internal token stream helper (`peek`, `advance`, `match`, `expect`, newline handling)
- Parsing behavior:
  - statement-oriented parsing with newline boundaries
  - directives: `target`, `port`, `type`, `reason`, `maintainer`
  - operations: full `mk`, `file`, `text`, `patch` forms from `docs/dsl-v0.md`
  - optional tails:
    - `contains "..."` for block ops
    - `on-missing error|warn|noop`
  - heredoc ops:
    - `mk target set|append ... <<'TAG'`
    - consume associated `HEREDOC_BODY` token exactly as lexed

3. **Define parser diagnostics**
- Add parse syntax error codes under `E_PARSE_*`, e.g.:
  - `E_PARSE_UNEXPECTED_TOKEN`
  - `E_PARSE_EXPECTED_TOKEN`
  - `E_PARSE_EXPECTED_STATEMENT`
  - `E_PARSE_EXPECTED_NEWLINE`
  - `E_PARSE_INVALID_ON_MISSING`
- Diagnostics must include source path + line/column from token spans.
- Error messages must include expected token hints (e.g. “expected STRING after `reason`”).

4. **Integrate parser into API**
- Update `scripts/generator/dportsv3/engine/api.py`:
  - `parse_dsl()` flow: lex -> if lexical errors return -> parse tokens -> return AST on success.
  - On successful parse: `ParseResult(ok=True, ast=<AstDocument>, diagnostics=[])`.
  - Remove `E_NOT_IMPLEMENTED` path from `parse_dsl()` only.
- Keep `check_dsl()` and `build_plan()` as stubs for now.

5. **Update tests**
- Add `scripts/generator/tests/test_dportsv3_parser.py`:
  - valid parse coverage for directives + each op family
  - heredoc parse coverage (`mk target set/append`)
  - optional clause parsing (`contains`, `on-missing`)
  - error coverage for malformed syntax and expected-token failures
  - span assertions on representative nodes/diagnostics
- Update `scripts/generator/tests/test_dportsv3_cli.py`:
  - `dsl parse` should now return success (`0`) for syntactically valid files
  - invalid syntax should return `2` with `E_PARSE_*` diagnostic

6. **Step tracker update**
- Add “Step 4 Status (Implemented)” section in `docs/implementation-plan-v3.md`.
- Note that Step 5 semantic analyzer is next.

### Acceptance Criteria

- `parse_dsl()` returns `ok=True` with AST for syntactically valid DSL input.
- Invalid syntax returns deterministic `E_PARSE_*` diagnostics with source spans.
- All directive/op forms in `docs/dsl-v0.md` parse into AST nodes.
- Heredoc recipe bodies are preserved exactly from lexer to AST.
- Parser tests and existing v3 tests pass.
- No dependency introduced on legacy `dports` modules.

### Suggested execution order

1. Add AST models  
2. Implement parser core + statement dispatch  
3. Implement op-family parsers and optional clauses  
4. Wire `parse_dsl()` to lexer+parser pipeline  
5. Add/adjust tests  
6. Mark Step 4 status in implementation doc

### Step 4 Status (Implemented)

- Added typed AST node models in `scripts/generator/dportsv3/engine/ast.py`.
- Implemented recursive-descent parser in `scripts/generator/dportsv3/engine/parser.py`.
- Added parser diagnostics with deterministic `E_PARSE_*` error codes and source spans.
- Integrated parser into `parse_dsl()` in `scripts/generator/dportsv3/engine/api.py`.
- Updated CLI parse expectations and added parser coverage in `scripts/generator/tests/test_dportsv3_cli.py` and `scripts/generator/tests/test_dportsv3_parser.py`.
- Kept `check_dsl()` and `build_plan()` as stubs for Step 5/Step 6 work.
- Next remaining implementation step is Step 5 (Semantic analyzer).

5. **Semantic analyzer**

### Step 5 Goal

- Implement a semantic analyzer over parsed AST that enforces DSL document rules beyond syntax.
- Enforce directive constraints (`port` cardinality, `type` validity/cadinality, target scoping).
- Make `check_dsl()` run real syntax+semantic validation with deterministic semantic diagnostics.

### Scope

- In: semantic models/pass, semantic diagnostics (`E_SEM_*`), `check_dsl()` integration, semantic tests, CLI check behavior updates.
- Out: plan compilation (Step 6), apply engine, filesystem/upstream existence checks.

### Implementation Plan

1. **Add semantic data model**
- Add `scripts/generator/dportsv3/engine/semantic.py` and supporting types (or add minimal types in models):
  - `SemanticResult` (`ok`, `diagnostics`, `document`/`scoped_ops`)
  - optional analyzed node wrapper for resolved target scope per op
- Keep this layer AST-based and side-effect free.

2. **Implement semantic pass**
- Input: `AstDocument` from parser.
- Passes:
  - **Directive cardinality/shape**
    - exactly one `port` required
    - `type` optional but at most one
    - `reason` optional but at most one
    - `maintainer` optional but at most one
  - **Target scope resolution**
    - maintain current active target from `TargetDirective`
    - each operation must have an active target in scope
    - attach resolved target to operation in analyzed output
  - **Cross-node validation**
    - detect duplicate singleton directives
    - detect conflicting/empty document states (e.g., no statements, no operations if policy requires at least one op)
  - **Op semantic sanity guards** (defensive checks even if parser already constrained)
    - ensure action-specific fields are present (`mk target rename` needs old/new, heredoc ops need recipe/tag, etc.)
    - ensure `on_missing` appears only on supported ops and values are `error|warn|noop`

3. **Define semantic diagnostics**
- Add deterministic `E_SEM_*` codes, e.g.:
  - `E_SEM_MISSING_PORT`
  - `E_SEM_DUPLICATE_PORT`
  - `E_SEM_DUPLICATE_TYPE`
  - `E_SEM_DUPLICATE_REASON`
  - `E_SEM_DUPLICATE_MAINTAINER`
  - `E_SEM_MISSING_TARGET_SCOPE`
  - `E_SEM_INVALID_TARGET_SCOPE`
  - `E_SEM_INVALID_OPERATION_STATE`
- Diagnostics include source path + node/token line/column spans.

4. **Integrate into API**
- Update `scripts/generator/dportsv3/engine/api.py`:
  - `check_dsl()` flow: `lex -> parse -> semantic`
  - lexical or parse failures return immediately as `CheckResult(ok=False, diagnostics=...)`
  - semantic success returns `CheckResult(ok=True, diagnostics=[])`
  - semantic failures return `CheckResult(ok=False, diagnostics=E_SEM_...)`
- Keep `build_plan()` stubbed for Step 6.
- Keep `parse_dsl()` behavior unchanged from Step 4.

5. **Update tests**
- Add `scripts/generator/tests/test_dportsv3_semantic.py`:
  - valid document passes check
  - missing `port`
  - duplicate singleton directives
  - operation before first `target`
  - mixed target blocks resolving correctly
  - invalid semantic op state cases
  - source location assertions on semantic diagnostics
- Update `scripts/generator/tests/test_dportsv3_cli.py`:
  - `dsl check` valid file returns `0`
  - semantic-invalid file returns `2` with `E_SEM_*`
  - keep parse tests unchanged

6. **Step tracker update**
- Update `docs/implementation-plan-v3.md`:
  - replace short Step 5 bullets with this detailed plan
  - add `Step 5 Status (Implemented)` after merge
  - note Step 6 planner as next

### Acceptance Criteria

- `check_dsl()` performs real syntax+semantic validation (no `E_NOT_IMPLEMENTED`).
- Semantic diagnostics are deterministic and use stable `E_SEM_*` codes with source spans.
- Target scoping is resolved for every operation or fails with semantic error.
- Required cardinality rules (`port` exactly once, singleton directives) are enforced.
- Semantic tests + existing v3 tests pass.
- No dependency introduced on legacy `dports.*` modules.

### Suggested execution order

1. Add semantic result/types  
2. Implement semantic analyzer core rules  
3. Wire `check_dsl()` to `lex -> parse -> semantic`  
4. Add semantic tests + CLI check updates  
5. Run full v3 test suite  
6. Mark Step 5 status in implementation doc

### Step 5 Status (Implemented)

- Added semantic analyzer in `scripts/generator/dportsv3/engine/semantic.py`.
- Implemented directive cardinality checks and target scope resolution.
- Added deterministic `E_SEM_*` diagnostics with source spans.
- Integrated `check_dsl()` flow to `lex -> parse -> semantic` in `scripts/generator/dportsv3/engine/api.py`.
- Updated CLI check expectations and added semantic coverage in `scripts/generator/tests/test_dportsv3_cli.py` and `scripts/generator/tests/test_dportsv3_semantic.py`.
- Kept `build_plan()` stubbed for Step 6 planner implementation.
- Next remaining implementation step is Step 6 (Planner).

6. **Planner (ephemeral IR)**

### Step 6 Goal
- Compile parsed + semantically validated DSL into the normalized in-memory plan (`Plan`, `PlanOp`) used by runtime and `dsl plan --json`.
- Replace `build_plan()` stub with real `lex -> parse -> semantic -> planner` flow.
- Guarantee deterministic plan output (stable order + stable IDs) with `E_PLAN_*` diagnostics for planner-stage failures.

### Scope
- In: planner module, op-to-kind mapping, metadata extraction, plan diagnostics, API integration, planner/CLI tests.
- Out: apply engine execution, filesystem/upstream checks, persisted plan files.

### Implementation Plan

1. **Planner module**
- Add `scripts/generator/dportsv3/engine/planner.py`.
- Add core function:
  - `compile_plan(document, scoped_ops, source_path) -> PlanResult`
- Keep planner pure (no filesystem side effects).

2. **Metadata extraction**
- From AST directives:
  - `port` -> `plan.port` (required; semantic already enforces)
  - `type` -> `plan.type` (default `port`)
  - `reason` -> `plan.reason` (default empty)
  - `maintainer` -> `plan.maintainer` (default empty)

3. **Operation mapping (AST -> normalized plan ops)**
- Map actions to canonical `kind` values and payloads:
  - `mk set` -> `mk.var.set`
  - `mk unset` -> `mk.var.unset`
  - `mk add` -> `mk.var.token_add`
  - `mk remove` -> `mk.var.token_remove`
  - `mk disable-if` -> `mk.block.disable`
  - `mk replace-if` -> `mk.block.replace_condition`
  - `mk target set|append|remove|rename` -> `mk.target.set|append|remove|rename`
  - `file copy|remove` -> `file.copy|file.remove`
  - `text line-remove|line-insert-after|replace-once` -> `text.line_remove|text.line_insert_after|text.replace_once`
  - `patch apply` -> `patch.apply`
- Include resolved `target` per op from semantic scoped ops.
- Preserve source order from scoped ops exactly.
- Normalize recipe payload for target ops (recommended: `recipe` as list of lines preserving leading tabs).

4. **Deterministic ID generation**
- Generate stable IDs by source order, e.g. `op-0001-mk-var-set`.
- Ensure identical input yields identical IDs/JSON.

5. **Planner diagnostics**
- Add `E_PLAN_*` codes for planner-stage issues, e.g.:
  - `E_PLAN_UNSUPPORTED_ACTION`
  - `E_PLAN_INVALID_OPERATION`
  - `E_PLAN_METADATA_MISSING`
- Include source location from node spans.

6. **API integration**
- Update `scripts/generator/dportsv3/engine/api.py`:
  - `build_plan()` becomes:
    - `lex -> parse -> semantic -> compile_plan`
  - Return early on lexical/parse/semantic errors with existing diagnostics.
  - On success: `PlanResult(ok=True, plan=<Plan>, diagnostics=[])`.
- Keep `parse_dsl()` and `check_dsl()` behavior as-is.

7. **Tests**
- Add `scripts/generator/tests/test_dportsv3_planner.py`:
  - valid full document -> expected `Plan` metadata + op kinds
  - target scoping carried into each op
  - deterministic IDs across repeated runs
  - recipe preservation for `mk target set/append`
  - planner error path coverage (`E_PLAN_*`)
- Update `scripts/generator/tests/test_dportsv3_cli.py`:
  - `dsl plan --json` valid file returns `0` and emits JSON
  - invalid file returns `2` with parse/sem/plan diagnostics
  - replace current `plan not implemented` expectation

8. **Step tracker update**
- In `docs/implementation-plan-v3.md`:
  - replace short Step 6 bullets with detailed Step 6 plan
  - add `Step 6 Status (Implemented)` once merged
  - mark remaining work as test hardening/golden fixtures (if that’s next)

### Acceptance Criteria
- `build_plan()` no longer emits `E_NOT_IMPLEMENTED`.
- Valid DSL returns `PlanResult(ok=True)` with canonical plan.
- `dsl plan --json` emits deterministic output.
- Planner emits stable `E_PLAN_*` diagnostics when needed.
- Full v3 test suite passes.

### Suggested execution order
1. Implement `planner.py` with mapping + ID generation  
2. Wire `build_plan()` pipeline in API  
3. Add planner tests  
4. Update CLI plan tests  
5. Run full test suite  
6. Update Step 6 status in implementation doc

### Step 6 Status (Implemented)

- Added planner compiler in `scripts/generator/dportsv3/engine/planner.py`.
- Implemented deterministic AST-to-plan mapping for all current DSL operation families.
- Added stable `op-XXXX-...` ID generation and preserved operation source order.
- Integrated `build_plan()` flow to `lex -> parse -> semantic -> compile_plan` in `scripts/generator/dportsv3/engine/api.py`.
- Updated CLI plan expectations and added planner coverage in `scripts/generator/tests/test_dportsv3_cli.py` and `scripts/generator/tests/test_dportsv3_planner.py`.
- Added `E_PLAN_*` diagnostic coverage for unsupported actions, invalid operations, and missing metadata.
- Next remaining implementation area is test hardening and golden fixture expansion under Step 7.

7. **Tests (must-have before apply engine)**

### Step 7 Goal
- Harden the v3 test suite so lexer/parser/semantic/planner behavior is regression-safe.
- Add golden fixture coverage for full `.dops -> plan JSON` flows.
- Lock diagnostic contract stability (`E_PARSE_*`, `E_SEM_*`, `E_PLAN_*`) and deterministic output guarantees.

### Scope
- In: test refactor/expansion, fixture layout, golden assertions, CLI contract tests, deterministic/stability checks.
- Out: apply engine/runtime mutation logic.

### Implementation Plan

1. **Create fixture-based test structure**
- Add fixture tree under `scripts/generator/tests/fixtures/dportsv3/`:
  - `valid/*.dops`
  - `invalid/parse/*.dops`
  - `invalid/semantic/*.dops`
  - `invalid/planner/*.dops`
  - `golden/*.dops` + matching `*.plan.json`
- Add a small test helper module to load fixtures and expected outputs consistently.

2. **Add golden plan tests**
- Expand planner tests to run `.dops` fixtures through `build_plan()`.
- Compare produced `plan.to_dict()` against golden JSON files.
- Include representative coverage:
  - all op families (`mk`, `file`, `text`, `patch`)
  - multi-target scoped ops
  - target recipe heredoc (`set`/`append`) with tab preservation
  - metadata (`port`, `type`, `reason`, `maintainer`)

3. **Add diagnostic contract tests**
- Add fixture-driven invalid cases that assert:
  - exact diagnostic code family (`E_PARSE_*`, `E_SEM_*`, `E_PLAN_*`)
  - source line/column presence
  - stable exit behavior (`check`/`plan` return `2`)
- Cover known edge set explicitly:
  - escaped strings
  - duplicate directives
  - missing target scope
  - invalid `on-missing`
  - malformed heredoc forms

4. **Add determinism/stability tests**
- Assert repeated `build_plan()` calls return byte-equivalent JSON.
- Assert deterministic op ordering and ID generation for identical input.
- Add comments/whitespace/continuation normalization tests to ensure semantic equivalence does not alter deterministic output unexpectedly.

5. **Expand CLI end-to-end tests**
- Strengthen `test_dportsv3_cli.py` for:
  - `dsl parse`, `dsl check`, `dsl plan --json` across valid/invalid fixtures
  - stdout/stderr separation guarantees
  - non-zero exit codes for parse/semantic/planner failures
- Verify JSON output is parseable and sorted/deterministic as currently emitted.

6. **Add a test matrix entrypoint**
- Add one command path in docs (and optionally Make target/script) for full v3 suite:
  - `python -m pytest tests/test_dportsv3_*.py`
- Keep this as the canonical pre-apply gate.

7. **Step tracker update**
- In `docs/implementation-plan-v3.md`:
  - replace Step 7 short bullets with this detailed Step 7 plan
  - add `Step 7 Status (Implemented)` after merge
  - mark DSL-engine phase done if all acceptance criteria pass

### Acceptance Criteria
- Fixture-based golden tests exist and pass.
- Diagnostics contract is covered for parse/semantic/planner failures with stable codes.
- Deterministic plan JSON + op IDs are enforced by tests.
- CLI behavior (exit codes + output channels) is regression-tested.
- Full v3 test suite passes and is documented as the required gate before apply-phase work.

### Suggested Execution Order
1. Add fixture layout + test helpers  
2. Implement golden planner tests  
3. Implement diagnostic contract tests  
4. Expand CLI end-to-end tests  
5. Run full suite and stabilize failures  
6. Update Step 7 status in implementation doc

### Step 7 Status (Implemented)

- Added fixture tree under `scripts/generator/tests/fixtures/dportsv3/` for valid, invalid, and golden cases.
- Added fixture helper module in `scripts/generator/tests/dportsv3_testutils.py`.
- Expanded planner tests with golden fixture comparisons and determinism checks in `scripts/generator/tests/test_dportsv3_planner.py`.
- Added diagnostic contract coverage in `scripts/generator/tests/test_dportsv3_diagnostics.py`.
- Expanded CLI end-to-end contract tests in `scripts/generator/tests/test_dportsv3_cli.py`.
- Established canonical v3 test matrix gate command:
  - `python -m pytest tests/test_dportsv3_*.py`
- DSL engine phase (Steps 1-7) is complete under current scope.

8. **Migration Program (Portfolio Scale)**

### Step 8 Goal
- Define and execute a scalable migration program from legacy overlays to `overlay.dops` for the ports that actually carry DragonFly delta logic.
- Prioritize safety and throughput: auto-convert what is safe, preserve patch fallback for the rest.
- Make migration measurable and continuously enforceable in CI.

### Scope
- In: inventory, classification, batch conversion tooling, review workflow, CI gates, progress tracking, migration policy.
- Out: forcing full-tree conversion of untouched ports; removing patch fallback.

### Implementation Plan

1. **Lock migration scope policy**
- Default scope: migrate ports with existing DragonFly overlay deltas plus any newly touched ports.
- Explicitly exclude untouched upstream-only ports from mandatory conversion.
- Keep patch fallback as a permanent supported lane.

2. **Build migration inventory**
- Scan all current overlay artifacts and produce a machine-readable inventory snapshot.
- Record per-port facts: current overlay type, target coverage, patch presence, `Makefile.DragonFly` presence, stale state, and complexity signals.
- Version this snapshot so progress can be tracked over time.

3. **Add classifier pass**
- Classify each port into `auto-safe`, `review-needed`, `fallback-only`, or `stale`.
- Base classification on deterministic heuristics from parser/planner support surface.
- Emit reasons per classification so maintainers can triage quickly.

4. **Implement converter MVP**
- Build a converter for high-confidence patterns first: var ops, simple file/text ops, straightforward target recipes.
- Generate `overlay.dops` candidates and run `parse/check/plan` automatically as post-generation validation.
- Refuse conversion when confidence is low; label as `review-needed` or `fallback-only`.

5. **Add batch runner and reports**
- Add batch command to process conversion sets by classifier bucket.
- Always support `--dry-run` and deterministic report output.
- Produce per-run artifacts: converted count, blocked count, fallback count, stale count, and failure diagnostics.

6. **Introduce golden migration fixtures**
- Add migration fixture corpus from real overlays representing each bucket.
- Assert expected converter outcome category and expected planned output shape for auto-safe cases.
- Lock regression tests so converter logic changes are intentional.

7. **Wave rollout strategy**

- **Goal**
  - Migrate in controlled waves (not big-bang), starting with high-impact/high-confidence overlay sets.

- **Prerequisites (must be ready before Wave 1)**
  - Inventory + classifier outputs exist (`auto-safe`, `review-needed`, `fallback-only`, `stale`).
  - Converter MVP works for `auto-safe`.
  - Batch runner supports `--dry-run` and deterministic reports.
  - CI gate command is locked: `python -m pytest tests/test_dportsv3_*.py`.

- **Wave Design**
  - Define wave unit as: `target branch x category group x confidence bucket`.
  - Start with:
    1. `auto-safe` only
    2. active targets first (`main`, current quarter)
    3. high-churn categories first
  - Keep batch size small (e.g., 50–150 ports/wave) for reviewability.

- **Per-Wave Execution Loop**
  1. Select wave candidate set from classifier report.
  2. Run converter in `--dry-run`; review blockers.
  3. Run real conversion for auto-safe subset only.
  4. Validate converted outputs with `parse/check/plan` and test matrix.
  5. Publish wave report (converted/blocked/fallback/stale + reason codes).
  6. Open review PR(s) scoped to that wave only.

- **Quality Gates (per wave)**
  - No parse/check/plan failures in converted set.
  - Deterministic output confirmed (repeat run produces same plan JSON/IDs).
  - No new unclassified legacy overlays introduced.
  - Block merge if gate fails.

- **Review & Sign-off**
  - Require maintainer sign-off by category owner (or designated reviewer).
  - Any risky conversion is moved from `auto-safe` to `review-needed`.
  - Keep patch fallback where conversion confidence is low.

- **Abort / Pause Conditions**
  - Unexpected spike in diagnostics.
  - Determinism regressions.
  - Reviewer rejection trend above threshold.
  - If triggered: pause wave progression, fix tooling, rerun last wave.

- **Tracking**
  - Maintain a wave ledger:
    - wave id, scope, converted count, blocked count, fallback count, stale count, pass/fail.
  - Publish cumulative coverage and remaining backlog after each wave.

- **Definition of completion for Step 7**
  - At least one successful rollout wave completed end-to-end.
  - Wave playbook proven repeatable.
  - Next waves queued with approved scope and owners.

### Step 8.7 Status (Implemented)

- Added wave rollout helpers in `scripts/generator/dportsv3/migration/waves.py`.
- Added migration CLI subcommands in `scripts/generator/dportsv3/commands/migrate.py` and wired `dportsv3 migrate wave-plan|wave-report` in `scripts/generator/dportsv3/cli.py`.
- Added deterministic wave-selection and gate-report tests in `scripts/generator/tests/test_dportsv3_migration.py`.
- Added migration fixtures under `scripts/generator/tests/fixtures/dportsv3/migration/` for inventory and wave result scenarios.
- Confirmed quality-gate behavior through strict wave-report CLI exit codes.
- Remaining Step 8 work is still required for inventory/classifier/converter and actual conversion waves.

8. **Enforce forward policy**
- New or modified overlay work must be dops-first unless explicitly marked fallback-only with reason.
- CI blocks new unclassified legacy overlays.
- CI publishes migration scorecard metrics on every run.

9. **Define completion thresholds**
- Migration is “operationally complete” when all in-scope ports are classified, all auto-safe ports converted, and remaining non-converted ports are explicitly tagged fallback-only or stale with rationale.
- Keep a standing backlog for review-needed ports and burn it down continuously.

### Acceptance Criteria
- 100% of in-scope ports are inventoried and classified with reason codes.
- Auto-safe bucket converts with passing `parse/check/plan` and deterministic output.
- CI enforces dops-first policy for newly touched overlays.
- Migration dashboard/report is generated on demand and in CI.
- Remaining non-converted ports are explicitly accounted for (`review-needed`, `fallback-only`, or `stale`) with no unknowns.

### Suggested execution order
1. Scope lock + inventory schema  
2. Classifier implementation  
3. Converter MVP for safe patterns  
4. Batch runner + reports  
5. Fixture/golden migration tests  
6. Wave 1 rollout + CI policy gates  
7. Iterate waves until in-scope completion

### Step 8 Status (Implemented)

- Added migration inventory scanner in `scripts/generator/dportsv3/migration/inventory.py`.
- Added deterministic classifier in `scripts/generator/dportsv3/migration/classify.py`.
- Added converter MVP in `scripts/generator/dportsv3/migration/convert.py`.
- Added batch runner and reporting in `scripts/generator/dportsv3/migration/batch.py`.
- Added forward policy evaluation in `scripts/generator/dportsv3/migration/policy.py`.
- Added completion threshold evaluator in `scripts/generator/dportsv3/migration/progress.py`.
- Added migration CLI workflows in `scripts/generator/dportsv3/commands/migrate.py` and `scripts/generator/dportsv3/cli.py`.
- Added migration fixtures and tests in `scripts/generator/tests/test_dportsv3_migration.py`, `scripts/generator/tests/test_dportsv3_migration_program.py`, and `scripts/generator/tests/fixtures/dportsv3/migration/`.
- Canonical v3 test matrix gate remains:
  - `python -m pytest tests/test_dportsv3_*.py`

9. **Remaining Runtime Execution Work (Apply + Compose)**

### What is still missing

- `overlay.dops -> actual file mutations` (apply engine)
- compose wiring to build a real output tree using v3 execution
- BSD Makefile CST-lite rewrite runtime
- oracle validation (`bmake`) after rewrites
- end-to-end command/reporting for real tree mutation and diff preview
- CI enforcement for migration forward-policy gates

### Step 9: Apply Engine Core

### Step 9 Goal
- Implement the first real runtime executor: `overlay.dops -> apply pipeline entrypoint`.
- Execute compiled plan ops against a single port root with deterministic order and strict failure behavior.
- Provide safe write mechanics and dry-run support, even before full op rewrite logic lands in Steps 10/11.

### Scope
- In: apply command surface, execution context, dispatcher, transaction-safe file write utilities, apply diagnostics/reporting, core tests.
- Out: full Makefile CST rewrite semantics (Step 10), full op executor logic (Step 11), compose integration (Step 13).

### Implementation Plan

1. **Add apply API contract**
- Add `apply_plan(...)` in a new module (e.g. `scripts/generator/dportsv3/engine/apply.py`).
- Inputs:
  - `plan` (`Plan`)
  - `port_root` (`Path`)
  - `target` (`@main`/`@YYYYQ[1-4]`)
  - options: `dry_run`, `strict`
- Output model:
  - `ApplyResult` with per-op results + aggregate counts + diagnostics.

2. **Add apply models and diagnostics**
- Extend/add models for:
  - `ApplyContext`
  - `ApplyOpResult` (applied/skipped/failed, reason, timing optional)
  - `ApplyResult` (totals + errors/warnings + op rows)
- Introduce stable error families:
  - `E_APPLY_INVALID_TARGET`
  - `E_APPLY_INVALID_PORT_ROOT`
  - `E_APPLY_UNKNOWN_KIND`
  - `E_APPLY_EXECUTOR_NOT_IMPLEMENTED`
  - `E_APPLY_WRITE_FAILED`

3. **Implement execution context + preflight**
- Validate:
  - `port_root` exists and is directory
  - `target` is valid
  - each op target matches requested target (or is skipped deterministically with reason)
- Build deterministic op list in source/plan order.

4. **Implement dispatcher skeleton**
- Add `kind -> executor` registry.
- For Step 9, register all known kinds but allow placeholder executor behavior.
- Unknown kind returns `E_APPLY_UNKNOWN_KIND` and fails under `strict`.

5. **Add transaction-safe write utilities**
- Add helper utilities (e.g. `engine/fsops.py`) for:
  - read file
  - write temp file + atomic replace
  - no-write mode for dry-run
- Ensure no partial writes on failure in strict mode.

6. **Add initial executor placeholders**
- Implement executor stubs for all current kinds:
  - `mk.*`, `file.*`, `text.*`, `patch.apply`
- In Step 9, each stub returns deterministic `not implemented` apply diagnostic (or dry-run preview row), without mutating content logic yet.
- This gives complete runtime plumbing while keeping semantic rewrite work in Step 10/11.

7. **Expose CLI command**
- Add `dportsv3 dsl apply <overlay.dops> --port-root <path> --target <@...> [--dry-run] [--json] [--strict]`.
- Pipeline:
  - `parse -> check -> plan -> apply`
- Return codes:
  - `0` success
  - `2` apply or validation failure
  - `1` IO/usage errors.

8. **Add apply reports**
- Emit per-op rows and aggregate summary:
  - total ops
  - applied
  - skipped
  - failed
  - warnings/errors
- Add JSON output mode for CI and future compose integration.

9. **Tests for Step 9**
- New tests:
  - `test_dportsv3_apply.py` (API-level)
  - CLI apply tests in `test_dportsv3_cli.py`
- Cover:
  - valid wiring (plan reaches apply stage)
  - dry-run behavior
  - target mismatch skip behavior
  - unknown kind handling
  - strict vs non-strict failure handling
  - deterministic per-op ordering/report output.

### Acceptance Criteria
- `dportsv3 dsl apply` exists and runs end-to-end through parse/check/plan/apply.
- Apply preflight validation and target filtering are deterministic.
- Dispatcher and write-transaction scaffolding are in place.
- Apply diagnostics use stable `E_APPLY_*` codes.
- Full `tests/test_dportsv3_*.py` suite passes with new apply tests included.

### Suggested execution order
1. Add apply models + diagnostics  
2. Implement apply context + preflight  
3. Implement dispatcher + executor stubs  
4. Add fs transaction utilities  
5. Wire CLI `dsl apply`  
6. Add tests + stabilize outputs

### Step 9 Status (Implemented)

- Added apply-stage models in `scripts/generator/dportsv3/engine/models.py` (`ApplyContext`, `ApplyOpResult`, `ApplyResult`).
- Added transaction-safe filesystem utilities in `scripts/generator/dportsv3/engine/fsops.py`.
- Added apply dispatcher and placeholder executor registry in `scripts/generator/dportsv3/engine/apply.py`.
- Added end-to-end apply API pipeline in `scripts/generator/dportsv3/engine/api.py` (`apply_dsl`).
- Added CLI command surface `dportsv3 dsl apply` in `scripts/generator/dportsv3/cli.py` and `scripts/generator/dportsv3/commands/dsl.py`.
- Added API/CLI test coverage in `scripts/generator/tests/test_dportsv3_apply.py` and updated `scripts/generator/tests/test_dportsv3_cli.py`.
- Remaining execution work is Step 10+ (runtime Makefile CST and real op mutation executors).

### Step 10: BSD Makefile CST-lite Runtime Parser

### Step 10 Goal
- Implement a runtime BSD Makefile CST-lite parser used by apply executors.
- Preserve formatting fidelity (including tabs/continuations) and source spans.
- Provide rewrite/query primitives needed by Step 11 executors.

### Scope
- In: Makefile CST parser, CST node model, parse diagnostics, rewrite/query primitives, fixtures/tests.
- Out: full op execution wiring (Step 11), transitive include/master rewrites, bmake oracle (Step 14).

### Implementation Plan

1. **Add CST runtime models**
- New module(s), e.g.:
  - `scripts/generator/dportsv3/engine/makefile_cst.py`
  - optional `scripts/generator/dportsv3/engine/makefile_rewrite.py`
- Define nodes with spans and raw text preservation:
  - `AssignmentNode`
  - `DirectiveIfNode` / `DirectiveElifNode` / `DirectiveElseNode` / `DirectiveEndifNode`
  - `TargetNode`
  - `RecipeLineNode`
  - `IncludeNode`
  - `RawLineNode`
- Add `MakefileDocument` container with ordered nodes + original line table.

2. **Parser entrypoint + diagnostics**
- Implement:
  - `parse_makefile_cst(text: str, source_path: Path | None) -> MakefileParseResult`
- Add stable parser diagnostic family (new namespace), e.g.:
  - `E_MKPARSE_UNBALANCED_DIRECTIVE`
  - `E_MKPARSE_INVALID_ASSIGNMENT`
  - `E_MKPARSE_INVALID_TARGET`
  - `E_MKPARSE_CONTINUATION_EOF`
- Keep behavior strict and deterministic.

3. **Lex/scan rules for BSD Makefile lines**
- Recognize assignment operators: `=`, `+=`, `?=`, `:=`, `!=`
- Handle continuation `\` logically while preserving original physical lines.
- Detect directives `.if/.elif/.else/.endif`.
- Detect targets `name:` and associated tab-prefixed recipe lines.
- Detect `.include ...`.
- Classify everything else as `RawLineNode`.
- Preserve untouched content exactly.

4. **Build CST indexes for fast rewrites**
- Build helper indexes on `MakefileDocument`:
  - assignments by var name
  - targets by name
  - directive blocks / conditional regions
- Ensure lookup ordering is stable and source-order-based.

5. **Add rewrite/query primitives (used by Step 11)**
- Start with non-executing primitives that return edit intents:
  - `find_var_assignments(name)`
  - `set_var(name, value)`
  - `unset_var(name)`
  - `token_add(name, token)`
  - `token_remove(name, token)`
  - `find_target(name)`
  - `target_set(name, recipe_lines)`
  - `target_append(name, recipe_lines)`
  - `target_remove(name)`
  - `target_rename(old, new)`
  - `find_condition(expr, contains?)`
- Rewriter outputs deterministic edit plan over CST nodes/line ranges.

6. **Serializer / render**
- Add renderer:
  - `render_makefile(document) -> str`
- Guarantee byte-for-byte preservation for untouched regions.
- Deterministic newline handling and tab preservation for recipe lines.

7. **Tests + fixtures**
- Add fixture set under `scripts/generator/tests/fixtures/dportsv3/makefile/`:
  - simple var-only
  - continuation-heavy
  - target+recipe
  - nested conditionals
  - include lines
- Add tests:
  - `test_dportsv3_makefile_cst.py`
  - `test_dportsv3_makefile_rewrite_primitives.py`
- Verify:
  - node typing + spans
  - directive balance detection
  - preservation of tabs/continuations
  - deterministic render
  - primitive behavior + ambiguity handling

8. **Step-9 integration hook (minimal)**
- Keep Step 9 apply executors as placeholders.
- Add import-ready integration seam so Step 11 can call CST primitives directly.
- Do not change user-visible mutation behavior yet.

### Acceptance Criteria
- Parser produces CST-lite nodes and source spans for representative BSD Makefiles.
- Parse diagnostics are stable and actionable (`E_MKPARSE_*`).
- Rewriter primitives exist and are deterministic.
- Render preserves untouched formatting exactly.
- New CST test suite passes, and full `tests/test_dportsv3_*.py` remains green.

### Suggested Execution Order
1. CST node/result models  
2. parser scanner/state machine  
3. indexing layer  
4. primitive rewrite API  
5. renderer  
6. fixtures/tests  
7. integration seam for Step 11

### Step 10 Status (Implemented)

- Added runtime CST-lite parser in `scripts/generator/dportsv3/engine/makefile_cst.py`.
- Added node model and parse result types for assignments, directives, targets, recipes, includes, and raw lines.
- Added deterministic parse diagnostics (`E_MKPARSE_*`) for directive balance, invalid assignments/targets, and continuation EOF.
- Added document indexes (`assignment_index`, `target_index`, `directive_regions`) and stable renderer `render_makefile(...)`.
- Added rewrite/query primitives in `scripts/generator/dportsv3/engine/makefile_rewrite.py` with deterministic edit intents.
- Added fixture corpus under `scripts/generator/tests/fixtures/dportsv3/makefile/` and tests in `scripts/generator/tests/test_dportsv3_makefile_cst.py` and `scripts/generator/tests/test_dportsv3_makefile_rewrite_primitives.py`.
- Added import-ready integration seam via `scripts/generator/dportsv3/engine/__init__.py` exports.
- Remaining runtime work continues in Step 11 (real op mutation executors using these primitives).

### Step 11: Implement All Op Executors

### Step 11 Goal
- Replace Step 9 placeholder executors with real mutation executors for all supported op kinds.
- Apply deterministic, idempotent changes to real files under `port_root`.
- Enforce strict `on_missing`/ambiguity/failure behavior exactly as defined in DSL + architecture docs.

### Scope
- In: real executors for `mk.*`, `file.*`, `text.*`, `patch.apply`, apply diagnostics, mutation tests.
- Out: compose orchestration (Step 13), bmake oracle checks (Step 14), broad rollout/policy gates (Step 15+).

### Implementation Plan

1. **Executor framework hardening (`apply.py`)**
- Replace `_placeholder_executor` path with real executor registry.
- Add reusable execution helpers:
  - resolve target file path from op payload (default `Makefile` where relevant)
  - normalize `on_missing` behavior (`error|warn|noop`)
  - ambiguity guard (`exactly one match` unless op allows multiple)
- Add/extend apply diagnostics:
  - `E_APPLY_AMBIGUOUS_MATCH`
  - `E_APPLY_MISSING_SUBJECT`
  - `E_APPLY_PARSE_FAILED`
  - `E_APPLY_PATCH_FAILED`
  - `W_APPLY_ON_MISSING_WARN`

2. **Makefile document load/cache layer**
- Add apply-local cache per file path:
  - parse once via `parse_makefile_cst(...)`
  - mutate in memory
  - render once at commit stage
- Cache key: absolute file path in `port_root`.
- On parse error:
  - honor `on_missing` policy where applicable
  - otherwise fail with `E_APPLY_PARSE_FAILED`.

3. **Implement `file.*` executors first**
- `file.copy`: copy source -> destination inside `port_root`; fail on missing source.
- `file.remove`: remove path with `on_missing` handling.
- Use `FileTransaction` for staged writes/removes and strict rollback behavior.

4. **Implement `text.*` executors**
- `text.line_remove`: strict exact line match (single or deterministic policy).
- `text.line_insert_after`: strict single anchor match.
- `text.replace_once`: strict single replacement occurrence.
- Respect `on_missing` and ambiguity policy.
- Preserve newline style where possible.

5. **Implement `mk.var.*` executors**
- `mk.var.set`
- `mk.var.unset`
- `mk.var.token_add`
- `mk.var.token_remove`
- Use CST indexes + rewrite primitives; convert intents into actual node/line edits.
- Preserve untouched formatting; only mutate targeted assignment blocks.

6. **Implement `mk.target.*` executors**
- `mk.target.set`, `mk.target.append`, `mk.target.remove`, `mk.target.rename`
- Preserve recipe tab prefixes and existing block boundaries.
- Enforce single-target deterministic behavior unless op semantics explicitly permit more.

7. **Implement `mk.block.*` executors**
- `mk.block.disable`
- `mk.block.replace_condition`
- Match condition by expression + optional `contains`.
- Apply only when uniquely matched; else ambiguity error.
- Preserve block body/formatting except intended condition edit/disable operation.

8. **Implement `patch.apply` executor**
- Apply patch payload relative to `port_root` using deterministic subprocess strategy.
- Capture stderr/stdout into diagnostics.
- Fail with `E_APPLY_PATCH_FAILED` on non-zero result (strict + rollback semantics preserved).

9. **Mutation serialization + commit behavior**
- Ensure mutated `MakefileDocument` instances are rendered and staged via `FileTransaction`.
- Commit all staged changes only after op loop passes strict conditions.
- Keep dry-run mode mutation-capable in memory, but no filesystem writes.

10. **Tests + fixtures**
- Add/extend fixtures under `scripts/generator/tests/fixtures/dportsv3/apply/`:
  - var ops
  - target recipe ops
  - block ops
  - text/file ops
  - patch apply
  - ambiguous/missing cases
- Add tests:
  - `test_dportsv3_apply_executors.py`
  - expand `test_dportsv3_apply.py` and CLI tests
- Validate:
  - strict vs non-strict behavior
  - `on_missing` semantics
  - determinism/idempotency (reapply gives same output)
  - rollback on strict failure.

### Acceptance Criteria
- No executor returns `E_APPLY_EXECUTOR_NOT_IMPLEMENTED` for supported kinds.
- All listed op kinds perform real mutations in non-dry-run mode.
- Ambiguity and missing-subject behavior is deterministic and policy-driven.
- Dry-run executes full mutation logic in memory and reports accurate results.
- Full `python -m pytest tests/test_dportsv3_*.py` passes with new executor tests.

### Suggested Execution Order
1. `file.*` + `text.*` executors  
2. Makefile parse/cache + `mk.var.*`  
3. `mk.target.*`  
4. `mk.block.*`  
5. `patch.apply`  
6. full strict/non-strict + idempotency test hardening

### Step 11 Status (current)
- Replaced Step 9 placeholder executor registry in `scripts/generator/dportsv3/engine/apply.py` with concrete executors for all supported op kinds (`mk.*`, `file.*`, `text.*`, `patch.apply`).
- Added apply-stage on-missing policy handling (`error|warn|noop`) with deterministic diagnostics (`E_APPLY_MISSING_SUBJECT`, `E_APPLY_AMBIGUOUS_MATCH`, `W_APPLY_ON_MISSING_WARN`, `E_APPLY_PATCH_FAILED`).
- Added concrete mutation behavior for text and Makefile operation families using CST parsing and deterministic subject matching.
- Updated file transaction reads in `scripts/generator/dportsv3/engine/fsops.py` so staged writes/removes are visible to later ops within the same apply run.
- Expanded apply/CLI tests in `scripts/generator/tests/test_dportsv3_apply.py` and `scripts/generator/tests/test_dportsv3_cli.py` to assert real executor outcomes (applied/skipped/failed) instead of placeholder behavior.
- Full dportsv3 test matrix passes in the project venv: `.venv/bin/python -m pytest tests/test_dportsv3_*.py` (87 passed).

### Step 12: Dry-Run Diff + Per-Port Reporting

### Step 12 Goal
- Add `--dry-run --diff` so `dportsv3 dsl apply` reports planned file changes without writing.
- Add stable per-port reporting fields for CI: total/applied/skipped/warnings/errors/fallback patch count.
- Lock diagnostic family boundaries so parse/check/plan/apply remain clearly separated and contract-stable.

### Scope
- In: apply diff preview, report schema hardening, CLI output modes, diagnostic family contract tests.
- Out: compose orchestration (Step 13), bmake oracle checks (Step 14), policy gates (Step 15+).

### Implementation Plan

1. **CLI surface + mode rules**
- Add `--diff` flag to `dsl apply` in `scripts/generator/dportsv3/cli.py`.
- Enforce mode: `--diff` requires `--dry-run` (explicit error if missing).
- Keep `--json` behavior, but include diff/report payloads in JSON when requested.

2. **Apply result/report schema**
- Extend `ApplyResult` in `scripts/generator/dportsv3/engine/models.py` with stable report fields:
  - `total_ops`, `applied_ops`, `skipped_ops`, `warning_count`, `error_count`, `fallback_patch_count`.
- Add report version marker (`report_version`) for CI contract stability.
- Define `fallback_patch_count` as non-skipped `patch.apply` op results.

3. **Diff capture infrastructure**
- Extend `FileTransaction` (`scripts/generator/dportsv3/engine/fsops.py`) with read-only staged change introspection (writes/removes, deterministic path order).
- In apply engine (`scripts/generator/dportsv3/engine/apply.py`), build per-file before/after snapshots for staged changes.
- Render unified diffs with stdlib `difflib.unified_diff`, using repo-relative paths and stable ordering.

4. **Patch fallback preview handling**
- For `patch.apply` during dry-run+diff, include patch payload preview as diff artifact (or classified fallback diff entry) even if no staged file write occurs.
- Keep `E_APPLY_PATCH_FAILED` behavior unchanged.

5. **Output contract (human + JSON)**
- In `scripts/generator/dportsv3/commands/dsl.py`:
  - Non-JSON + `--diff`: print unified diff text to stdout after diagnostics.
  - JSON + `--diff`: include structured `diffs` array (path, change type, unified diff text).
  - Always include stable `report` object for CI parsing.

6. **Diagnostic family stabilization**
- Add stage/family validation tests to ensure:
  - parse => `E_PARSE_*`
  - check => `E_PARSE_*|E_SEM_*`
  - plan => `E_PARSE_*|E_SEM_*|E_PLAN_*`
  - apply => prior families plus `E_APPLY_*|W_APPLY_*|I_APPLY_*`
- Keep current code prefixes unchanged; enforce via tests, not ad-hoc string checks in command handlers.

### Test Plan
- Update `scripts/generator/tests/test_dportsv3_cli.py`:
  - `dsl apply --dry-run --diff` emits diff and does not modify files.
  - `--diff` without `--dry-run` returns deterministic apply-mode error.
  - `--json --diff` contains `report` and `diffs` with stable keys.
- Update/add apply tests in `scripts/generator/tests/test_dportsv3_apply.py`:
  - Deterministic diff ordering and content for text/makefile/file ops.
  - `fallback_patch_count` correctness.
  - Dry-run parity: same op statuses as normal apply, no writes.
- Add diagnostic contract tests (new or extend `scripts/generator/tests/test_dportsv3_diagnostics.py`) for family boundaries.
- Run full matrix in venv: `.venv/bin/python -m pytest tests/test_dportsv3_*.py`.

### Acceptance Criteria
- `dportsv3 dsl apply ... --dry-run --diff` shows deterministic planned file deltas and writes nothing.
- JSON output includes stable per-port report fields + diff artifacts suitable for CI ingestion.
- `fallback_patch_count` present and correct.
- Diagnostic family contracts are tested and stable.
- Full v3 test matrix passes in `scripts/generator/.venv`.

### Suggested Execution Order
1. CLI flags + mode validation  
2. `ApplyResult`/report schema  
3. transaction diff capture + unified diff rendering  
4. patch fallback preview path  
5. CLI output wiring (text/json)  
6. diagnostics contract tests + full matrix

### Step 12 Status (current)
- Added `--diff` to `dportsv3 dsl apply` in `scripts/generator/dportsv3/cli.py` and enforced mode validation in `scripts/generator/dportsv3/commands/dsl.py` (`--diff` requires `--dry-run`).
- Extended apply reporting in `scripts/generator/dportsv3/engine/models.py` with stable `report` payload (`report_version`, totals, warnings/errors, `fallback_patch_count`) while keeping existing `summary` fields.
- Added per-file diff artifacts (`diffs`) to apply results and JSON output.
- Added staged change introspection in `scripts/generator/dportsv3/engine/fsops.py` and unified diff generation in `scripts/generator/dportsv3/engine/apply.py` using `difflib.unified_diff`.
- Added dry-run patch fallback preview artifacts for `patch.apply` ops in diff mode.
- Added/updated tests in `scripts/generator/tests/test_dportsv3_apply.py`, `scripts/generator/tests/test_dportsv3_cli.py`, and `scripts/generator/tests/test_dportsv3_diagnostics.py` for diff output, report contract, mode validation, fallback count, and stage diagnostic family stability.
- Full dportsv3 matrix passes in venv: `.venv/bin/python -m pytest tests/test_dportsv3_*.py` (93 passed).

### Step 13: Compose Pipeline Integration

### Step 13 Goal
- Add `dportsv3 compose --target @...` as the end-to-end tree builder for v3.
- Integrate existing v3 DSL apply engine into a full compose flow over all selected origins.
- Preserve architecture constraints: target-scoped inputs, stale-overlay preflight blocking, framework (`special/`) patch-first behavior, deterministic stage ordering.

### Scope
- In: compose CLI command, stage orchestration, preflight checks, per-port apply integration, fallback patch payload application, implicit payload copy, finalization, compose reporting.
- Out: bmake oracle enforcement (Step 14), CI policy gates (Step 15), rollout automation (Step 16).

### Implementation Plan

1. **Compose CLI surface**
- Add top-level command: `dportsv3 compose`.
- Add flags:
  - required: `--target @main|@YYYYQ[1-4]`, `--output <path>`
  - optional: `--delta-root <path>` (default repo root), `--freebsd-root <path>`, `--dry-run`, `--strict`, `--replace-output`, `--json`
- Exit behavior:
  - `0` success
  - `2` compose/preflight failure
  - `1` usage/input file errors

2. **Compose data model + report contract**
- Add v3 compose models (stage + run result), separate from legacy result types.
- Keep stage-level counters and aggregate summary:
  - per-stage: changed/skipped/warnings/errors/duration
  - per-port: total/applied/skipped/warnings/errors/fallback patch count
- JSON contract includes:
  - `target`, `output_path`, `stages`, `ports`, `summary`, `ok`
  - stable schema marker (`report_version`) for CI parsing

3. **Pipeline skeleton (ordered stages)**
- Implement stage orchestration in this exact order:
  1) seed output tree from upstream target  
  2) apply `special/` infrastructure  
  3) validate overlays and plans (preflight)  
  4) apply per-port semantic ops (`overlay.dops`)  
  5) apply fallback patch payloads  
  6) copy implicit payload files (`dragonfly/@target/...`)  
  7) finalize tree (prune/category Makefiles/UPDATING)
- Fail-fast on stage failure when `--strict`; otherwise accumulate failures and finish report.

4. **Preflight checks (hard blockers)**
- Validate target syntax and branch constraints:
  - FreeBSD tree is git repo
  - current branch matches requested target branch (normalized without `@`)
- Validate stale overlays:
  - for `type port`, upstream origin must exist in selected FreeBSD target tree
- Validate overlay target-scope correctness:
  - reject invalid `@...` directories in payload layouts
  - `overlay.dops` must parse/check/plan cleanly
- Emit deterministic compose diagnostics with a new compose code family (e.g. `E_COMPOSE_*`).

5. **Origin discovery + selection**
- Discover candidate origins from `delta-root/ports/*/*`.
- For each origin detect:
  - `overlay.dops` presence
  - fallback payloads in `diffs/@target`
  - implicit payload files in `dragonfly/@target`
  - optional `newport/` source for `dport`
- Default mode: compose all discovered overlays (overlay candidates).

6. **Per-port execution policy by plan type**
- Build plan from `overlay.dops`; dispatch by `plan.type`:
  - `port`: apply DSL ops to seeded upstream origin
  - `mask`: remove origin from output tree (or skip materialization if absent)
  - `dport`: copy from `newport/` into output, then apply DSL ops if present
  - `lock`: copy from configured lock source tree, then apply DSL ops if present
- Reuse `apply_plan` / `apply_dsl` for op execution and reporting.

7. **Fallback patch stage**
- Apply legacy fallback patch payloads after semantic ops:
  - scan `ports/<origin>/diffs/@target/*.diff|*.patch`
  - apply against output origin path
- Record fallback application counts/errors in per-port and compose summary.
- Keep behavior deterministic (sorted patch order).

8. **Implicit payload copy stage**
- Copy files from `ports/<origin>/dragonfly/@target/**` into output origin tree.
- Preserve relative paths and create parents as needed.
- Include copied file counts in stage/per-port metadata.

9. **Finalize stage**
- Prune removed ports not in target upstream tree.
- Regenerate category `Makefile`s.
- Merge/update `UPDATING` output artifact.
- Keep this stage deterministic and dry-run aware.

10. **Diagnostics + observability hardening**
- Introduce compose diagnostic family (`E_COMPOSE_*`, optionally `W_COMPOSE_*`/`I_COMPOSE_*`).
- Keep parse/check/plan/apply families unchanged; compose wraps but does not rewrite underlying codes.
- Ensure compose JSON report contains stage breakdown + per-port rollups suitable for CI consumption.

### Test Plan
- Add fixtures under `scripts/generator/tests/fixtures/dportsv3/compose/`:
  - mini FreeBSD tree
  - mini Delta overlay tree with `port/mask/dport/lock` examples
  - fallback diffs + implicit payload files
  - stale/invalid-target preflight failures
- Add tests:
  - `test_dportsv3_compose.py` (pipeline order, policy, reporting, dry-run parity)
  - extend `test_dportsv3_cli.py` for `compose` CLI success/failure/json behavior
- Validate:
  - strict vs non-strict stage failure behavior
  - deterministic stage/port ordering
  - no writes in dry-run
  - per-port report fields match architecture requirements
  - fallback patch counts are accurate

### Acceptance Criteria
- `dportsv3 compose --target @... --output ...` executes full stage order and returns stable report output.
- Preflight blocks stale `type port` overlays and invalid target-scope payload layouts.
- Per-port report includes: total/applied/skipped/warnings/errors/fallback patch count.
- Compose can process `port`, `mask`, `dport`, and `lock` policy behavior deterministically.
- Full v3 test matrix passes with compose tests included.

### Suggested Execution Order
1. Compose models + CLI command plumbing  
2. Pipeline skeleton + stage result wiring  
3. Preflight validators (branch/stale/target-scope/plan validity)  
4. Per-port `overlay.dops` execution policy by `plan.type`  
5. Fallback patch stage  
6. Implicit payload copy stage  
7. Finalize stage  
8. Reporting + diagnostics contracts + full matrix

### Step 13 Status (current)
- Added `dportsv3 compose` CLI surface in `scripts/generator/dportsv3/cli.py` with target/output/root/dry-run/strict/json flags and optional `--lock-root`.
- Implemented v3 compose models and pipeline in `scripts/generator/dportsv3/compose.py` with deterministic stage order: seed, special, preflight, semantic ops, fallback patches, implicit payload copy, finalize.
- Added hard preflight blockers for invalid target, FreeBSD git branch mismatch, stale `type port` overlays, invalid target-scope payload layouts, and invalid `overlay.dops` plans.
- Added per-port reporting and aggregate summary contract (including fallback patch count and schema marker `report_version`).
- Implemented plan-type dispatch behavior for `port`, `mask`, `dport`, and `lock` during semantic stage.
- Added fallback patch application stage (`diffs/@target`) and implicit payload copy stage (`dragonfly/@target`).
- Added compose tests in `scripts/generator/tests/test_dportsv3_compose.py` and validated full matrix in venv: `.venv/bin/python -m pytest tests/test_dportsv3_*.py` (96 passed).

### Step 14: bmake Oracle Integration

### Step 14 Goal
- Add a constrained bmake oracle pass after rewrite/apply to validate Makefile correctness.
- Surface oracle failures as apply-stage diagnostics and fail in strict mode.
- Support profile-based behavior (`local` vs `ci`) so local runs stay fast while CI is stricter.

### Scope
- In: oracle runner module, apply integration, compose propagation, CLI profile flags, reporting fields, tests.
- Out: full build/package execution, dependency graph evaluation, transitive include rewriting.

### Implementation Plan

1. **Oracle profile contract**
- Define oracle profiles: `off`, `local`, `ci`.
- Default profile: `local` for `dsl apply` and `compose`.
- Behavior:
  - `off`: skip oracle completely.
  - `local`: syntax/parse checks only; missing `bmake` is warning.
  - `ci`: syntax/parse + variable sanity probes; missing `bmake` is error.

2. **CLI surface**
- Add `--oracle-profile {off,local,ci}` to:
  - `dportsv3 dsl apply`
  - `dportsv3 compose`
- Include selected profile in JSON context/report output for traceability.

3. **Oracle runner module**
- Add `scripts/generator/dportsv3/engine/oracle.py`.
- Implement constrained checks:
  - parse/syntax check invocation (no command execution side effects).
  - selected variable probes for CI sanity (e.g. `PORTNAME`, `CATEGORIES`, `MAINTAINER`).
- Return structured result (ok, checks_run, failures, warnings, command stderr/stdout snippets).

4. **Apply integration**
- Extend apply pipeline to run oracle after op execution and before commit finalization.
- Run oracle against the effective post-op view:
  - non-dry-run: staged transaction view before commit.
  - dry-run: temp materialized view from staged snapshot.
- Emit stable apply-family diagnostics:
  - `E_APPLY_ORACLE_FAILED`
  - `E_APPLY_ORACLE_UNAVAILABLE`
  - `W_APPLY_ORACLE_SKIPPED`
- Strict mode: oracle error fails run and rolls back transaction.

5. **Compose integration**
- Pass oracle profile from compose into per-port apply execution.
- Aggregate oracle outcomes in per-port and stage summaries (checked/failed/skipped counts).
- Keep compose stage ordering unchanged; oracle is part of semantic apply validation behavior.

6. **Reporting contract updates**
- `ApplyResult` report fields:
  - `oracle_profile`
  - `oracle_checks`
  - `oracle_failures`
  - `oracle_skipped`
- Compose summary/report includes aggregated oracle metrics and affected origins.

7. **Failure policy**
- `strict=true`: any oracle error -> run fails.
- `strict=false`:
  - `local`: oracle failures recorded; run marked failed only for hard errors.
  - `ci`: oracle failures always treated as hard apply failure.
- Keep parse/check/plan/apply family boundaries stable; oracle diagnostics remain in `E_APPLY_*` / `W_APPLY_*`.

### Test Plan
- Add `scripts/generator/tests/test_dportsv3_oracle.py`:
  - profile behavior (`off/local/ci`)
  - missing `bmake` handling
  - command failure mapping to diagnostics.
- Extend `test_dportsv3_apply.py`:
  - strict rollback on oracle failure
  - dry-run oracle on staged view
  - report counters populated correctly.
- Extend `test_dportsv3_compose.py`:
  - compose forwards profile and aggregates oracle failures.
- Extend `test_dportsv3_cli.py`:
  - new flag parsing and JSON profile visibility.
- Run full matrix:
  - `.venv/bin/python -m pytest tests/test_dportsv3_*.py`

### Acceptance Criteria
- `dsl apply` and `compose` support `--oracle-profile`.
- Oracle runs post-rewrite in apply path and reports deterministic diagnostics.
- Strict mode fails on oracle errors with rollback semantics preserved.
- CI profile is stricter than local and enforceable in tests.
- Full dportsv3 test matrix passes with oracle coverage included.

### Suggested Execution Order
1. Add oracle profile flags to CLI and command handlers  
2. Implement `engine/oracle.py` runner and result model  
3. Integrate oracle into apply transaction flow  
4. Propagate profile through compose per-port execution  
5. Add reporting fields + diagnostics mapping  
6. Add tests and run full matrix

### Step 14 Status (current)
- Added `--oracle-profile {off,local,ci}` to `dportsv3 dsl apply` and `dportsv3 compose` CLI surfaces.
- Added constrained oracle runner in `scripts/generator/dportsv3/engine/oracle.py` with profile-based behavior, missing-`bmake` handling, and deterministic check/failure reporting.
- Integrated oracle execution in `scripts/generator/dportsv3/engine/apply.py` after op staging and before commit, with staged-view materialization and diagnostics mapping (`E_APPLY_ORACLE_FAILED`, `E_APPLY_ORACLE_UNAVAILABLE`, `W_APPLY_ORACLE_SKIPPED`).
- Extended apply report/context contracts in `scripts/generator/dportsv3/engine/models.py` to include `oracle_profile`, `oracle_checks`, `oracle_failures`, and `oracle_skipped`.
- Propagated oracle profile through `scripts/generator/dportsv3/engine/api.py` and compose semantic apply integration in `scripts/generator/dportsv3/compose.py`, including compose-level oracle aggregation fields.
- Added/updated tests in `scripts/generator/tests/test_dportsv3_oracle.py`, `scripts/generator/tests/test_dportsv3_apply.py`, `scripts/generator/tests/test_dportsv3_cli.py`, and `scripts/generator/tests/test_dportsv3_compose.py`.
- Full dportsv3 test matrix passes in venv: `.venv/bin/python -m pytest tests/test_dportsv3_*.py` (106 passed).

### Step 15: CI + Policy Enforcement

### Step 15 Goal
- Enforce migration-forward policy in CI for changed overlays.
- Block policy regressions on PRs:
  - new unclassified legacy overlays
  - touched legacy overlays that are not dops-first (unless `fallback-only`)
- Publish a machine-readable migration dashboard artifact (classification + completion thresholds).

### Scope
- In: CI workflow wiring, migration policy/progress gate plumbing, dashboard artifact command/output, tests.
- Out: rollout wave execution (Step 16), bmake/oracle behavior changes (already Step 14), broad repo policy beyond overlay migration.

### Implementation Plan

1. **Define CI gate contract**
- Keep two gate classes:
  - **policy gates (hard fail)**:
    - touched legacy overlay must be dops-first or explicitly `fallback-only`
    - touched origin must exist in inventory/classification inputs
  - **progress gates (configurable fail/warn)**:
    - completion thresholds emitted always
    - failing thresholds can be hard fail only when CI mode is set to strict completion
- Keep deterministic JSON outputs for all checks.

2. **Add migration dashboard aggregator**
- Add `scripts/generator/dportsv3/migration/dashboard.py` to combine:
  - classified inventory summary (bucket/target/category counts)
  - forward policy output
  - progress output (thresholds + operational completeness)
  - gate verdict block (`policy_pass`, `progress_pass`, `ci_pass`)
- Include schema/version marker (e.g. `dashboard_version: v1`) and timestamp/target metadata.

3. **Expose dashboard in CLI**
- Extend `scripts/generator/dportsv3/commands/migrate.py` with new action:
  - `migrate dashboard`
- Inputs:
  - classified JSON
  - optional conversion results JSON
  - optional touched origins file
  - mode flags (`--strict-policy`, `--strict-progress` or unified `--strict`)
- Output:
  - stable JSON for artifact publishing and CI parsing.

4. **Harden policy/progress evaluators for CI usage**
- Update `scripts/generator/dportsv3/migration/policy.py` to cleanly separate:
  - global policy diagnostics
  - touched-origin policy diagnostics
- Ensure touched-origin enforcement exactly matches Step 15 requirement.
- Update `scripts/generator/dportsv3/migration/progress.py` to expose:
  - threshold booleans
  - ratio/percentage-style counters for dashboard visibility.

5. **Add CI workflow job**
- Add a dedicated lightweight workflow (recommended) in `.github/workflows/` for migration policy checks, or add a new job in existing PR workflow.
- Job steps:
  1. checkout repo
  2. set up Python env for `scripts/generator`
  3. build inventory + classification
  4. derive touched origins from PR diff (`ports/<cat>/<port>/...`)
  5. run `migrate policy-check --strict`
  6. run `migrate progress` (strict/non-strict per mode)
  7. run `migrate dashboard` and upload artifact (always)
- Ensure artifact upload runs with `if: always()` so failed PRs still get diagnostics.

6. **Touched-origin extraction utility**
- Add a small deterministic helper (module or workflow script) to map changed files to unique `category/name` origins.
- Ignore non-port paths.
- Sort output for reproducibility and easier diffing.

7. **Document CI policy behavior**
- Update implementation doc Step 15 status section after implementation.
- Add short operator notes (where to find artifact, what each gate means, what blocks merges).

### Test Plan
- Extend `scripts/generator/tests/test_dportsv3_migration_program.py` with:
  - dashboard payload contract tests
  - policy gate behavior on touched origins
  - progress threshold visibility fields
- Add focused tests (new file if cleaner) for touched-origin extraction edge cases.
- Add CLI tests for `migrate dashboard` success/failure and strict exit behavior.
- Validate full suite:
  - `.venv/bin/python -m pytest tests/test_dportsv3_*.py`

### Acceptance Criteria
- CI blocks PRs when touched legacy overlays violate dops-first policy (except `fallback-only`).
- CI blocks PRs for new unclassified legacy overlay violations under configured gate mode.
- Dashboard artifact is always uploaded and contains classification + completion + gate verdicts.
- Migration gate outputs are deterministic and schema-stable.
- Full dportsv3 test matrix passes.

### Suggested Execution Order
1. Add dashboard module + CLI action  
2. Add touched-origin extraction helper  
3. Tighten policy/progress outputs for CI  
4. Wire CI workflow gate job + artifact upload  
5. Add/expand tests  
6. Run full matrix and finalize Step 15 status notes

### Step 15 Status (current)
- Added migration dashboard aggregation in `scripts/generator/dportsv3/migration/dashboard.py` with stable payload contract (`dashboard_version`, classification summary, policy/progress blocks, and gate verdicts).
- Added touched-origin extraction helper in `scripts/generator/dportsv3/migration/touched.py` to deterministically derive `category/name` origins from changed file paths.
- Added `migrate dashboard` command wiring in `scripts/generator/dportsv3/cli.py` and `scripts/generator/dportsv3/commands/migrate.py`, including strict-policy/strict-progress gate modes and optional changed-file derivation input.
- Hardened policy/progress outputs in `scripts/generator/dportsv3/migration/policy.py` and `scripts/generator/dportsv3/migration/progress.py` with versioned payloads, gate-friendly summaries, and ratio visibility for dashboards.
- Added CI workflow `.github/workflows/dportsv3-migration-policy.yml` that runs inventory/classify/policy/progress/dashboard checks and always uploads artifact `dportsv3-migration-dashboard`.
- Added/updated tests in `scripts/generator/tests/test_dportsv3_migration_program.py` and validated migration CLI behavior; full v3 matrix passes in venv: `.venv/bin/python -m pytest tests/test_dportsv3_*.py` (107 passed).
- Operator notes: use artifact `dportsv3-migration-dashboard` from workflow run; `gates.policy_pass` blocks policy regressions, while `gates.progress_pass` is emitted for monitoring and can be made blocking via strict-progress mode.

### Step 16: Rollout Simplification

### Step 16 Goal
- Keep rollout workflow focused on compose-first execution and deterministic migration reporting.
- Remove non-primary rollout surfaces from docs and runtime guidance.

### Scope
- In: compose-first operator runbook, migration wave reporting, dashboard/policy/progress gates.
- Out: dedicated pilot lifecycle commands and pilot-specific artifacts.

### Step 16 Status (implemented)
- Removed pilot command surfaces from runtime and documentation.
- Kept migration primitives (`inventory`, `classify`, `convert`, `batch`, `policy-check`, `progress`, `dashboard`, `wave-plan`, `wave-report`) as the only supported rollout utilities.
- Updated rollout notes to keep quarter bring-up centered on `dportsv3 compose` and deterministic report triage.

### Step 17: Compose Parity + Target Layering Consolidation

### Step 17 Goal
- Align v3 compose runtime with current scripts-based operational process for quarter bring-up.
- Make compatibility behavior explicit and deterministic: if `overlay.dops` exists, run dops path only; if it does not, run compatibility path by default.
- Introduce target layering that avoids redoing inventory-to-port migration each quarter.

### Scope
- In: compose parity with legacy merge behavior, compatibility fallback policy, target layering (`@any` + explicit target overrides), CLI/docs consolidation toward compose-first guidance.
- Out: regex/negative target selectors, non-deterministic target resolution.

### Consolidated Rules (Locked)
- Per-origin execution mode:
  - `overlay.dops` present => `dops` mode only.
  - `overlay.dops` absent => compatibility mode only (default).
- Compatibility mode must follow existing scripts process for ports (`Makefile.DragonFly`, `diffs`, `dragonfly`, transforms).
- `special/` framework flow must remain patch-first and quarter-scoped as in current scripts workflow.
- Target layering model:
  - allow `@any` baseline scope
  - allow multiple target selectors in one directive (`target @2025Q4,@2026Q1`)
  - if no target is declared before operations, implicit active scope is `@any`
  - execution for target `T`: apply `@any` first, then `T`.

### Implementation Plan
1. **Compose mode dispatcher parity**
- Refactor per-origin compose dispatch to explicit mode selection (`dops` vs `compat`) before any mutation stage runs.
- Mode decision rule (single source of truth):
  - `overlay.dops` exists -> `dops` mode
  - `overlay.dops` absent -> `compat` mode
- Enforce no-mix behavior:
  - in `dops` mode, skip `Makefile.DragonFly`/`diffs`/`dragonfly` compatibility handlers entirely
  - in `compat` mode, skip dops parser/planner/apply path entirely
- Add structured per-origin reporting fields:
  - `mode`, `mode_reason`, `compat_stages_executed`, `dops_ops_executed`
- Add deterministic diagnostics when a mode is blocked or invalid (`E_COMPOSE_MODE_CONFLICT`, `E_COMPOSE_MODE_DISPATCH_FAILED`).

2. **Compatibility executor parity module**
- Port and stabilize compatibility logic from `scripts/generator/dports/merge.py` and related helpers into v3 runtime path.
- Keep behavior-equivalent handling for all manifest types in compatibility mode:
  - `port`: seed upstream port, then apply Makefile/diffs/dragonfly/transform flow
  - `mask`: skip/remove materialization
  - `dport`: source from `newport/`
  - `lock`: source from configured lock tree
- Match existing scripts ordering exactly for compatibility mutations:
  1) base copy
  2) Makefile.DragonFly apply/append
  3) patch apply
  4) dragonfly file copy
  5) transforms + cleanup
- Reuse current target validation and patch apply behavior so quarter migration behavior does not regress.
- Add parity fixtures that compare v3 compatibility outputs with known-good legacy outputs.

3. **`special/` process parity guardrails**
- Match current scripts flow for framework copy+patch sequence and error surfacing.
- Stage order and behavior must stay explicit:
  1) copy framework roots from FreeBSD (`Mk`, `Templates`, `Tools`, `Keywords`, treetop)
  2) apply target-scoped `special/*/diffs/@<target>` patches
  3) apply `replacements/` overlays
- Preserve quarter bring-up ergonomics:
  - patch failures are reported with file-level context and do not hide failing component
  - rerun must be deterministic after manual patch fixes
- Add concise machine-readable stage report payload:
  - `component`, `copied`, `patched`, `failed_patches`, `missing_target_dir`
- Document manual fix loop in compose docs as first-class, expected workflow.

4. **Target layering in parser/semantic/planner**
- Extend target grammar to support:
  - baseline selector `@any`
  - multi-target selector directives (`target @2025Q4,@2026Q1`)
- Semantic scoping rules:
  - active scope defaults to `@any` before first explicit target directive
  - operations inherit current active selector set
  - optional guard: reject combining `@any` with explicit selectors in one directive if ambiguity is introduced
- Planner expansion rules:
  - represent selectors deterministically and expand to per-target operation records as needed
  - preserve source order within selector groups
- Apply resolution rule for requested target `T`:
  - include `@any` operations first
  - include `T` operations second
  - keep deterministic idempotent ordering for repeated runs.

5. **Inventory/classifier/wave alignment**
- Replace hard `@main` default semantics for unscoped legacy records with baseline-capable semantics aligned to `@any`.
- Extend inventory schema with explicit targeting metadata:
  - `target_mode`: `explicit` | `baseline`
  - `available_targets`: explicit targets plus `@any` when baseline-capable
- Keep classifier bucket logic intact, but include targeting metadata in reports and dashboard summaries.
- Update wave/selection behavior:
  - for requested `@Q`, include explicit `@Q` + baseline-capable entries
  - keep deterministic sort and emit selection reason per origin (`explicit_target_match` vs `baseline_match`).
- Add migration report counters for visibility:
  - baseline-selected count
  - explicit-selected count
  - excluded-by-target count.

6. **Compose payload lookup precedence**
- For compatibility payload trees, define deterministic lookup precedence:
  1) explicit target dir (`@Q`)
  2) baseline dir (`@any`)
- Explicit target entries win on path conflicts for identical relative paths.
- Apply this precedence consistently across:
  - `diffs`
  - `dragonfly`
  - Makefile compatibility sources (where target-scoped variants exist)
- Add conflict diagnostics and report rows when both layers provide same subject:
  - info/warn for override visibility
  - strict failure only for ambiguous or invalid cases.

7. **Operational consolidation**
- Shift docs/CLI guidance to compose-first quarter workflow.
- Establish canonical operator command sequence:
  1) compose framework (`special/` + patch fixes)
  2) compose ports (mode-dispatch behavior)
  3) review deterministic report and CI gate outputs
  4) rerun into arbitrary output roots as needed
- Update runbook sections (`docs/rollout-v3.md` and implementation notes) to reference compose-first workflow.
- Add migration-safe transition notes for maintainers converting existing `@main`-only overlays to baseline/explicit target layering.

### Test Plan
- Add compose parity tests asserting exact mode dispatch and no-mix behavior.
- Add compatibility parity fixtures against known scripts outcomes.
- Add target-layering DSL tests for `@any`, multi-target selector parsing, implicit baseline scope, and deterministic expansion.
- Add quarter bring-up regression tests for framework patch failures and rerun flow.
- Run full matrix:
  - `.venv/bin/python -m pytest tests/test_dportsv3_*.py`

### Acceptance Criteria
- v3 compose mirrors current scripts behavior for framework and compatibility port handling.
- dops-present origins never execute compatibility fallback stages.
- dops-missing origins execute compatibility mode by default without manual flags.
- Quarterly compose can reuse baseline overlays through `@any` semantics without full re-migration each quarter.
- Full dportsv3 test matrix passes.

### Step 17 Progress (current)
- **17.1 Compose mode dispatcher parity**: Implemented. Added explicit per-origin mode dispatch (`dops` vs `compat`) in `scripts/generator/dportsv3/compose.py`, enforced no-mix execution, and added per-origin reporting fields (`mode`, `mode_reason`, `compat_stages_executed`, `dops_ops_executed`). Added compose tests for dops-suppresses-compat and default compat behavior when dops is absent.
- **17.2 Compatibility executor parity module**: Implemented. Added `scripts/generator/dportsv3/compat.py` with compatibility type inference (`port|mask|dport|lock`), Makefile override resolution, ordered merge execution (base copy -> Makefile -> patches -> payload -> transforms), and dry-run-safe patch validation roots. Integrated compose compatibility mode to call the shared executor through `apply_compat_ops`, derive compat type from `overlay.toml`/`newport`, and report executed compatibility stages per origin. Added compose coverage for compatibility mode across all manifest types.
- **17.3 `special/` process parity guardrails**: Implemented. Reordered `special/` mutation flow in compose to copy -> patch -> replacements per component (including treetop), added missing target diff directory visibility via `I_COMPOSE_SPECIAL_TARGET_DIR_MISSING`, and emitted machine-readable component rows in stage metadata (`component`, `copied`, `patched`, `failed_patches`, `missing_target_dir`). Added compose test coverage that asserts patch-before-replacement ordering and metadata shape.
- **17.4 Target layering in parser/semantic/planner**: Implemented. Extended DSL target handling to accept `@any` and comma-separated selectors in one `target` directive, enabled implicit `@any` scope before first explicit target, and expanded multi-target operations deterministically in semantic/planner output. Updated apply execution ordering to evaluate `@any` operations before requested target-specific operations while preserving deterministic order within each group. Added parser/semantic/planner/apply coverage for `@any`, multi-target expansion, implicit baseline scope, and `@any`/explicit mixed-selector rejection.
- **17.5 Inventory/classifier/wave alignment**: Implemented. Updated migration inventory records to emit baseline-aware target metadata (`target_mode`, `available_targets`) and switched unscoped legacy overlays to baseline semantics (`@any`) instead of hard `@main` defaults. Extended wave selection to include baseline-capable records for requested quarter targets, emit per-origin selection reasons (`explicit_target_match`/`baseline_match`), and publish visibility counters (`baseline_selected_count`, `explicit_selected_count`, `excluded_by_target_count`). Added migration tests for metadata emission and baseline-inclusive wave selection.
- **17.6 Compose payload lookup precedence**: Implemented. Added deterministic compat payload layering in compose for `diffs`, `dragonfly`, and compatibility Makefile sources with explicit-target-over-`@any` precedence and stable merged ordering. Implemented `@any` fallback when explicit target payloads are absent and override visibility diagnostics (`I_COMPOSE_COMPAT_LAYER_OVERRIDE`) when both layers provide the same subject. Added compose tests covering baseline fallback and explicit-over-baseline override behavior.
- **17.7 Operational consolidation**: Implemented. Reworked rollout runbook to a compose-first operational sequence (inventory/classify visibility, compose execution, fix+rerun loop, CI gate pass) and added migration transition notes for moving `@main`-only overlays toward baseline/explicit layering.

### Acceptance criteria for v3 executable state

- `dportsv3 apply` can mutate a real port tree correctly with `--dry-run --diff`.
- `dportsv3 compose` builds a target output tree end-to-end.
- All op kinds execute with deterministic/idempotent behavior.
- `bmake` oracle failures are surfaced and actionable.
- CI enforces migration forward policy and publishes migration progress.

### Suggested execution order

1. Step 9 apply engine core  
2. Step 10 runtime Makefile CST-lite  
3. Step 11 op executors  
4. Step 12 dry-run diff/reporting  
5. Step 13 compose integration  
6. Step 14 oracle checks  
7. Step 15 CI policy gates  
8. Step 16 rollout simplification  
9. Step 17 compose parity + target layering

## Definition of Done (DSL engine)

- `dportsv3 dsl check <overlay.dops>` reliably validates syntax + semantics.
- `dportsv3 dsl plan <overlay.dops>` emits deterministic normalized plan.
- Test suite covers happy path + critical failure paths.
- No `overlay.toml` required anywhere in this new engine.

## Out of Scope For This Phase

- No full Makefile mutation executors yet.
- No compose wiring yet.
- No full-tree wave execution over all in-scope ports yet.
