# DeltaPorts v2 Implementation Plan

## Summary

| Aspect | Current State |
|--------|---------------|
| Approach | v2 Python package exists under `scripts/generator/dports/` |
| Structure | Modular package with core modules + command modules |
| Python | 3.11+ target (`tomllib` with `tomli` fallback) |
| Migration | Implemented as `dports migrate [port|all]` |
| Features | Core pieces implemented; strictness and parity items still pending |
| Commands | 16 wired commands (some are placeholders/partial) |
| Testing | Manual/real-tree oriented; no full automated suite yet |

---

## Package Structure (Current)

```
scripts/generator/
├── dports/                    # v2 package
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py
│   ├── config.py
│   ├── models.py
│   ├── overlay.py
│   ├── quarterly.py
│   ├── merge.py
│   ├── special.py
│   ├── state.py
│   ├── migrate.py
│   ├── validate.py
│   ├── transform.py
│   ├── utils.py
│   └── commands/
│       ├── __init__.py
│       ├── add.py
│       ├── check.py
│       ├── diff.py
│       ├── list.py
│       ├── logs.py
│       ├── makefiles.py
│       ├── merge.py
│       ├── migrate.py
│       ├── prune.py
│       ├── save.py
│       ├── special.py
│       ├── state.py
│       ├── status.py
│       ├── sync.py
│       ├── update.py
│       └── verify.py
├── dports_v1.py              # v1 retained for reference
└── dports-default.conf       # legacy reference config
```

---

## Implementation Status

Legend:
- `[x]` implemented
- `[~]` partially implemented
- `[ ]` planned

### Phase 0: Preparation
- [x] Package directory structure created
- [x] v1 script renamed to `dports_v1.py`

### Phase 1: Core Infrastructure

#### 1.1 Configuration System (`config.py`)
- [x] TOML config loading with search paths
- [x] Dataclass-based config objects
- [~] Quarterly config support (basic default only)
- [ ] Environment variable override system (`DPORTS_*`)
- [ ] Rich path/policy validation

#### 1.2 Data Models (`models.py`)
- [x] Core dataclasses (`PortOrigin`, `OverlayManifest`, `MergeResult`, `ValidationResult`, `PortState`)
- [x] Enums for build state and port type
- [~] Final model shape alignment with architecture docs

#### 1.3 Logging & Utils (`utils.py`)
- [x] Logging setup and logger factory
- [x] `cpdup` wrapper with shutil fallback
- [x] `apply_patch` wrapper and patch artifact cleanup
- [x] Port-list helpers

### Phase 2: Overlay & Quarterly System

#### 2.1 Overlay Parser (`overlay.py`)
- [x] Parse `overlay.toml` via `tomllib`
- [x] Manifest loading and lazy access
- [x] Basic validation checks against declared customization flags
- [~] Strict schema enforcement and unknown-field policy

#### 2.2 Quarterly Resolution (`quarterly.py`)
- [x] Quarterly type/parser (`YYYYQn`)
- [x] `diffs/@QUARTER` discovery helper support
- [~] Merge logic uses quarterly resolution for diffs only
- [ ] Full quarterly parity (`dragonfly/`, `Makefile.DragonFly.@QUARTER`, `special/`)

#### 2.3 Validation Engine (`validate.py`)
- [x] `validate_port` and `validate_all_ports`
- [x] Diff format lint checks and orphan detection
- [~] Full architecture-level validation matrix
- [ ] Merge-time hard gate on validation failures

### Phase 3: Merge Logic

#### 3.1 Transform Functions (`transform.py`)
- [x] Architecture transforms (`amd64 -> x86_64`)
- [x] libomp dependency stripping
- [x] UIDs/GIDs/MOVED/Tools transformations

#### 3.2 Port Merger (`merge.py`)
- [x] `PortMerger` class and merge dispatch
- [x] PORT/MASK/DPORT/LOCK handling
- [x] Diff apply + dragonfly file overlay + transforms
- [~] Strict error policy and validation-gated execution

#### 3.3 Special Handling (`special.py`)
- [x] Lightweight `special/` apply flow used by command path
- [~] Extended infrastructure merge helpers exist but are not command-integrated
- [ ] Full quarterly + replacements parity in command path

### Phase 4: State Management

#### 4.1 State System (`state.py`)
- [x] `BuildState` manager
- [x] Local JSON load/save backend
- [x] Import from legacy STATUS files
- [ ] `git-branch` backend
- [ ] `external` backend

### Phase 5: Commands

#### 5.1 Core Commands
- [x] `merge`
- [~] `sync` (CLI wired, implementation TODO)
- [x] `prune`
- [x] `makefiles`

#### 5.2 v2 Management Commands
- [x] `check`
- [x] `migrate`
- [x] `state`
- [x] `list`

#### 5.3 Utility / Ported Command Set
- [x] `status`, `verify`, `add`, `save`, `diff`, `special`, `logs`, `update`

#### 5.4 CLI Entry Point
- [x] argparse subcommand wiring
- [x] global flags (`--config`, `--verbose`, `--quiet`, `--version`)
- [x] command dispatch via registry

### Phase 6: Migration Tooling
- [x] Unified migration command (`dports migrate [port|all]`)
- [x] Dry-run support
- [x] STATUS parsing and overlay generation
- [~] Output/state format alignment with runtime state backend

### Phase 7: Repository Config Files
- [~] Config loader supports repo config file
- [ ] Create and commit root `dports.toml`
- [ ] Update `.gitignore` for `state/` and migration outputs

---

## Known Gaps

1. `sync` command is not implemented yet.
2. State backends `git-branch` and `external` are not implemented.
3. `merge` does not currently enforce a strict pre-merge `check` gate.
4. Quarterly behavior is complete for `diffs/` only; parity elsewhere is pending.
5. Migration-generated state JSON and runtime state JSON should be unified.
6. `special` command path does not yet use the full infrastructure merge helper flow.

---

## Dependency Flow (Current)

```
Config/Models/Utils
      |
      +--> Overlay + Quarterly + Validate
      |          |
      |          +--> Merge
      |
      +--> State
      |
      +--> Special
      |
      +--> Commands/* --> CLI
```

---

## Next Execution Order

1. Finish `sync` implementation.
2. Add validation gate policy to `merge` (with override flag semantics).
3. Implement quarterly parity for `dragonfly/`, `Makefile.DragonFly`, and `special/`.
4. Unify state JSON schemas (migration output and runtime backend).
5. Implement state backends (`git-branch`, `external`).
6. Add repo-level `dports.toml` and finalize docs/examples around it.

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Behavior drift between docs and code | Keep this plan as status-tracked (implemented/partial/planned) |
| Migration confusion | Preserve dry-run usage and document output paths clearly |
| Quarterly edge cases | Add explicit quarterly parity checklist before release |
| State backend complexity | Keep local backend as stable default until alternates are production-ready |
