# DeltaPorts v3 Implementation Plan: DSL Engine First

## DSL Engine Plan (Phase 1 of dportsv3)

- Build a standalone package at `scripts/generator/dportsv3` with zero runtime dependency on `dports` v2 modules.
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
- Create module skeleton: `scripts/generator/dportsv3`.
- Add isolated CLI entrypoint (`dportsv3`) in `scripts/generator/pyproject.toml`.
- Add minimal commands: `dsl parse`, `dsl check`, `dsl plan` (plan = normalized JSON output for debugging only).

2. **Freeze grammar/spec in docs**
- Split/author a dedicated DSL spec doc (normative grammar + semantics).
- Keep `docs/architecture-v3.md` architectural; move language lawyering to DSL spec page.

3. **Lexer**
- Implement tokenization with source spans (line/col).
- Support comments, escaped strings, symbols (`->`, heredoc markers), continuation lines, and heredoc blocks.
- Preserve heredoc body exactly (especially leading tabs).

4. **Parser (AST)**
- Recursive-descent parser (no external parser dependency).
- AST nodes for directives and each op family (`mk`, `file`, `text`, `patch`).
- Produce rich parse diagnostics with location and expected token hints.

5. **Semantic analyzer**
- Enforce directive constraints (single `port`, valid targets, legal `type`).
- Resolve target scoping onto each op.
- Validate op argument shapes and `on-missing` usage.
- Emit deterministic, actionable errors/warnings.

6. **Planner (ephemeral IR)**
- Compile AST -> normalized in-memory `Plan` dataclasses.
- Stable operation order and generated IDs.
- JSON serializer only for inspection (`dsl plan --json`), not as source-of-truth artifact.

7. **Tests (must-have before apply engine)**
- Unit tests: lexer, parser, semantic checks, planner mapping.
- Golden tests: full DSL files -> expected normalized plan JSON.
- Edge tests: heredoc tab fidelity, escaped strings, duplicate directives, missing target scope, ambiguous constructs.

## Definition of Done (DSL engine)

- `dportsv3 dsl check <overlay.dops>` reliably validates syntax + semantics.
- `dportsv3 dsl plan <overlay.dops>` emits deterministic normalized plan.
- Test suite covers happy path + critical failure paths.
- No `overlay.toml` required anywhere in this new engine.

## Out of Scope For This Phase

- No Makefile mutation/apply execution yet.
- No compose wiring yet.
- No migration tooling from old overlays yet.
