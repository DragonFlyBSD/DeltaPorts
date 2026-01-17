# DeltaPorts v2 Implementation Plan

## Summary

| Aspect | Decision |
|--------|----------|
| Approach | **Clean rewrite** as Python package |
| Structure | `scripts/generator/dports/` package |
| Python | **3.11+** (uses built-in `tomllib`) |
| Migration | **One-time** - run migration, then v2 only |
| Features | **Core together** - @QUARTER + overlay.toml + validation |
| Commands | **All 12** existing + 4 new (check, migrate, state, list) |
| Testing | **Manual** verification on real data |

---

## Package Structure

```
scripts/generator/
├── dports/                    # New v2 package
│   ├── __init__.py           # Version, exports
│   ├── __main__.py           # CLI entry point
│   ├── cli.py                # Argument parsing, command dispatch
│   ├── config.py             # Config loading (dports.toml, env, CLI)
│   ├── models.py             # Data classes (PortOverlay, MergeResult, etc.)
│   ├── overlay.py            # overlay.toml parsing and validation
│   ├── quarterly.py          # @QUARTER resolution logic
│   ├── merge.py              # Port merging logic
│   ├── special.py            # Mk/Templates/treetop merging
│   ├── state.py              # builds.json state management
│   ├── migrate.py            # Migration from v1
│   ├── validate.py           # Validation engine
│   ├── transform.py          # Arch transforms (amd64→x86_64)
│   ├── utils.py              # Helpers (cpdup, patch, logging)
│   └── commands/             # Command implementations
│       ├── __init__.py
│       ├── merge.py
│       ├── sync.py
│       ├── prune.py
│       ├── check.py          # NEW
│       ├── migrate_cmd.py    # NEW
│       ├── state_cmd.py      # NEW
│       ├── list_cmd.py       # NEW
│       ├── makefiles.py
│       ├── bulk_list.py
│       ├── daemon.py
│       ├── stinkers.py
│       ├── index.py
│       ├── updating.py
│       ├── quicksync.py
│       ├── identify_nobody.py
│       └── deps.py
├── dports_v1.py              # OLD v1 (renamed for reference)
└── dports-default.conf       # Keep as reference
```

---

## Implementation Phases

### Phase 0: Preparation
- [ ] Create package directory structure
- [ ] Create empty `__init__.py` files
- [ ] Rename old `dports.py` to `dports_v1.py`

### Phase 1: Core Infrastructure

#### 1.1 Configuration System (`config.py`)
- [ ] Parse `dports.toml` (repo-level config)
- [ ] Environment variable overrides (`DPORTS_*`)
- [ ] CLI argument overrides
- [ ] Path validation
- [ ] Quarterly configuration (supported list, current, require_target)

#### 1.2 Data Models (`models.py`)
- [ ] `Config` dataclass
- [ ] `PortOverlay` - parsed overlay.toml
- [ ] `MergeResult` - merge outcome
- [ ] `QuarterlyTarget` - quarterly identifier with validation
- [ ] `ValidationError`, `ValidationWarning`
- [ ] Enums: `PortStatus`, `OverlayType`

#### 1.3 Logging & Utils (`utils.py`)
- [ ] Logger class (file + console)
- [ ] `cpdup()` - copy preserving timestamps
- [ ] `apply_patch()` - patch with error capture
- [ ] `cleanup_orig_files()`
- [ ] Path helpers

### Phase 2: Overlay & Quarterly System

#### 2.1 Overlay Parser (`overlay.py`)
- [ ] Parse `overlay.toml` using `tomllib`
- [ ] Validate required fields (`[overlay].reason`)
- [ ] Validate `[quarterly]` section
- [ ] Validate `[diffs]`, `[dragonfly]` sections
- [ ] Return `PortOverlay` model

#### 2.2 Quarterly Resolution (`quarterly.py`)
- [ ] `resolve_quarterly_path(base_dir, component, target)` → Path | None
- [ ] Detect `@QUARTER` subdirectories
- [ ] Apply resolution rules
- [ ] Return resolved path + warnings

#### 2.3 Validation Engine (`validate.py`)
- [ ] `validate_overlay(port_path, target)` → List[Error|Warning]
- [ ] Check overlay.toml exists and valid
- [ ] Check quarterly support
- [ ] Check file references exist
- [ ] Configurable strictness

### Phase 3: Merge Logic

#### 3.1 Transform Functions (`transform.py`)
- [ ] `needs_transform(directory)` → List[str]
- [ ] `transform_content(content)` → str
- [ ] `transform_file(path)`
- [ ] `transform_dir(directory, files)`
- [ ] Architecture transforms (amd64→x86_64)
- [ ] libomp removal

#### 3.2 Port Merger (`merge.py`)
- [ ] `Merger` class with context manager
- [ ] `merge_port(origin, target)` → MergeResult
- [ ] Handle: MASK, LOCK, DPORT, PORT statuses
- [ ] Integrate overlay.toml validation
- [ ] Integrate @QUARTER resolution
- [ ] Apply transforms
- [ ] Track patch errors

#### 3.3 Special Merger (`special.py`)
- [ ] `merge_mk(cfg, target, workdir)` - Mk/ with @QUARTER
- [ ] `merge_templates(cfg, target, workdir)` - Templates/ with @QUARTER
- [ ] `merge_treetop(cfg, target)` - UIDs, GIDs, MOVED
- [ ] `merge_tools(cfg)` - Tools/ with perl fix
- [ ] `merge_keywords(cfg)` - Keywords/
- [ ] Handle all `replacements/` files

### Phase 4: State Management

#### 4.1 State System (`state.py`)
- [ ] `StateManager` class
- [ ] Load/save `builds.json`
- [ ] Storage backends: local file, git branch, external
- [ ] `get_port_state(origin)` → BuildState
- [ ] `update_port_state(origin, version, status)`
- [ ] `import_from_status_files(delta_path)`

### Phase 5: Commands

#### 5.1 Core Commands (Rewrite)
- [ ] `merge` - Full merge with --target
- [ ] `sync` - Single port merge
- [ ] `prune` - Remove obsolete ports
- [ ] `makefiles` - Generate Makefiles

#### 5.2 New v2 Commands
- [ ] `check` - Validation without merge
- [ ] `migrate` - STATUS→overlay.toml, builds.json
- [ ] `state` - Show/update/import state
- [ ] `list` - Query ports by criteria

#### 5.3 Existing Commands (Port)
- [ ] `bulk-list`
- [ ] `daemon`
- [ ] `stinkers`
- [ ] `index`
- [ ] `updating`
- [ ] `quicksync`
- [ ] `identify-nobody`
- [ ] `deps`

#### 5.4 CLI Entry Point
- [ ] Argument parser with subcommands
- [ ] Global flags: `--target`, `--config`, `--verbose`, `--dry-run`, `--quiet`, `--strict`
- [ ] Command dispatch
- [ ] Error handling and exit codes

### Phase 6: Migration Tooling

- [ ] `dports migrate generate-manifests` - Create overlay.toml files
- [ ] `dports migrate extract-status` - Build builds.json from STATUS
- [ ] `dports migrate remove-status` - Remove STATUS files
- [ ] `dports migrate validate` - Validate migrated structure

### Phase 7: Repository Config Files

- [ ] Create `dports.toml` in repo root
- [ ] Update `.gitignore` for state/

---

## Dependency Graph

```
Phase 0 (Structure)
    │
    ▼
Phase 1 (Infrastructure)
    ├── 1.1 config.py
    ├── 1.2 models.py
    └── 1.3 utils.py
          │
          ▼
Phase 2 (Overlay/Quarterly)
    ├── 2.1 overlay.py ──────┐
    ├── 2.2 quarterly.py ────┼──► 2.3 validate.py
    │                        │
    ▼                        ▼
Phase 3 (Merge)
    ├── 3.1 transform.py
    ├── 3.2 merge.py (depends on 2.1, 2.2, 2.3, 3.1)
    └── 3.3 special.py (depends on 2.2, 3.1)
          │
          ▼
Phase 4 (State)
    └── 4.1 state.py
          │
          ▼
Phase 5 (Commands)
    ├── 5.1 Core (merge, sync, prune, makefiles)
    ├── 5.2 New (check, migrate, state, list)
    ├── 5.3 Ported (bulk-list, daemon, stinkers, etc.)
    └── 5.4 CLI
          │
          ▼
Phase 6 (Migration)
    └── 6.1-6.4 Migration commands
          │
          ▼
Phase 7 (Config Files)
    └── dports.toml, .gitignore
```

---

## Estimated Effort

| Phase | Files | Est. Lines | Complexity | Est. Time |
|-------|-------|-----------|------------|-----------|
| 0. Structure | 15+ | ~100 | Low | 0.5 day |
| 1. Infrastructure | 3 | ~300 | Medium | 1 day |
| 2. Overlay/Quarterly | 3 | ~400 | Medium | 1.5 days |
| 3. Merge Logic | 3 | ~500 | High | 2 days |
| 4. State Management | 1 | ~200 | Medium | 0.5 day |
| 5. Commands | 15 | ~800 | Medium | 2 days |
| 6. Migration | 1 | ~300 | High | 1 day |
| 7. Config Files | 2 | ~50 | Low | 0.5 day |
| **Total** | **~43** | **~2650** | | **~9 days** |

---

## Implementation Order

1. **Phase 0** - Create package structure
2. **Phase 1** - Core infrastructure
3. **Phase 3.1** - Transform functions (port from v1)
4. **Phase 2** - Overlay and quarterly system
5. **Phase 3.2-3.3** - Merge logic
6. **Phase 4** - State management
7. **Phase 5.4** - CLI scaffolding
8. **Phase 5.1** - Core commands
9. **Phase 5.3** - Port existing commands
10. **Phase 5.2** - New v2 commands
11. **Phase 6** - Migration tooling
12. **Phase 7** - Config files
13. **Run migration** on actual repository
14. **Verify** everything works

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Breaking existing workflow | Keep v1 as `dports_v1.py` until v2 proven |
| Migration data loss | Migration is read-only until `remove-status` |
| Quarterly resolution edge cases | Extensive manual testing with real ports |
| Large port count (~32K) | Test with subset first, then full |
