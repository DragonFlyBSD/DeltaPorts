# DeltaPorts v2 Implementation Plan (Branch-Scoped Redesign)

## Design Inputs (Locked)

1. Targets are restricted to:
   - `main`
   - `YYYYQ[1-4]`
2. Overlays are component-local and target-scoped:
   - `Makefile.DragonFly.@<target>`
   - `diffs/@<target>/...`
   - `dragonfly/@<target>/...`
3. Root component paths are hard errors.
4. FreeBSD ports source uses one checkout (branch switches in place).
5. Target switch/sync fails if FreeBSD tree is dirty.
6. v2 work is independent from v1 migration concerns.

---

## Scope

This plan refactors existing v2 code to the branch-scoped design.

In scope:

- Target parsing/validation model
- Branch guard and sync workflow
- Strict target resolution for all components
- Validation hardening
- `special/` parity
- State target keying
- Docs and CLI wording alignment

Out of scope:

- Arbitrary branch names
- Root fallback compatibility mode
- Migration tooling work as a v2 blocker

---

## Workstreams

Legend:

- `[ ]` pending
- `[~]` in progress/partial
- `[x]` completed

### WS1: Target Model Foundation

- [ ] Replace quarterly-centric parsing with target parser (`main` + `YYYYQ[1-4]`)
- [ ] Update helper APIs to accept `target` terminology
- [ ] Add shared target validation utility used by CLI/commands/overlay checks

Primary files:

- `scripts/generator/dports/quarterly.py`
- `scripts/generator/dports/config.py`
- `scripts/generator/dports/cli.py`

### WS2: Single Checkout Branch Workflow

- [ ] Implement repository cleanliness guard before branch switch
- [ ] Implement `sync --target` branch switch + fast-forward workflow
- [ ] Add branch-match guard to `merge`, `check`, and `special`

Primary files:

- `scripts/generator/dports/commands/sync.py`
- `scripts/generator/dports/commands/merge.py`
- `scripts/generator/dports/commands/check.py`
- `scripts/generator/dports/commands/special.py`
- `scripts/generator/dports/config.py`

### WS3: Strict Component Resolution

- [ ] Resolve `Makefile.DragonFly.@<target>` only
- [ ] Resolve `diffs/@<target>/` only
- [ ] Resolve `dragonfly/@<target>/` only
- [ ] Remove all fallback logic from target paths to root paths

Primary files:

- `scripts/generator/dports/overlay.py`
- `scripts/generator/dports/merge.py`

### WS4: Validation Hardening

- [ ] Root component files become hard errors
- [ ] Invalid target names in `@...` paths become hard errors
- [ ] Missing target component path for declared component becomes hard error
- [ ] Clear error messages with per-component remediation hints

Primary files:

- `scripts/generator/dports/validate.py`
- `scripts/generator/dports/overlay.py`

### WS5: `special/` Target Parity

- [ ] Enforce `special/**/diffs/@<target>/...` policy
- [ ] Reject root `special/**/diffs/*.diff`
- [ ] Align `special` command behavior with merge/check target guards

Primary files:

- `scripts/generator/dports/special.py`
- `scripts/generator/dports/commands/special.py`

### WS6: State and Query Alignment

- [ ] Track build state with `target` field semantics
- [ ] Ensure list/status output reflects target-scoped records

Primary files:

- `scripts/generator/dports/state.py`
- `scripts/generator/dports/commands/state.py`
- `scripts/generator/dports/commands/list.py`

### WS7: CLI and Docs Alignment

- [ ] Update CLI help text from "quarterly" to "target branch" where applicable
- [ ] Keep `--target` flag name stable
- [ ] Update docs to branch-scoped examples (`@main`, `@2025Q2`)

Primary files:

- `scripts/generator/dports/cli.py`
- `docs/architecture-v2.md`
- `docs/implementation-plan-v2.md`

---

## Execution Order

1. WS1 (target model)
2. WS2 (branch workflow/guards)
3. WS3 (component resolution)
4. WS4 (validation hardening)
5. WS5 (`special/` parity)
6. WS6 (state alignment)
7. WS7 (CLI/docs cleanup)

---

## Acceptance Criteria

1. `dports check --target main` and `dports merge --target main` only consume
   `@main` component paths.
2. `dports check --target 2025Q2` and `dports merge --target 2025Q2` only consume
   `@2025Q2` component paths.
3. Any root component file causes check failure.
4. Any unknown/invalid `@<target>` naming causes check failure.
5. `sync --target T` fails on dirty FreeBSD tree.
6. `merge/check/special --target T` fail if FreeBSD checkout branch is not `T`.
7. `special/` follows the same strict target policy.

---

## Test Matrix

- `main` target, complete component set
- `2025Q2` target, complete component set
- missing `Makefile.DragonFly.@<target>` with component enabled
- missing `diffs/@<target>/` with component enabled
- missing `dragonfly/@<target>/` with component enabled
- root component files present
- invalid target directories (`@foo`, `@2025Q5`, `@2025q2`)
- dirty FreeBSD tree during `sync --target`
- branch mismatch during `merge/check/special`

---

## Risks and Mitigations

| Risk | Mitigation |
|------|------------|
| Existing overlay trees still use root paths | Add clear `check` errors with exact replacement path examples |
| Branch mismatch confusion | Add explicit preflight output: current branch vs requested target |
| Strict mode rollout friction | Provide one-time remediation doc snippet for converting root files to `@main` |
| Partial code-path drift | Route merge/check/special through shared target resolver utilities |

---

## Immediate Next Step

Implement WS1 and WS2 first, then run `check` in strict mode to identify all
overlay trees that must be moved to `@main`/`@YYYYQn` layout.
