# DeltaPorts Architecture v2: Simplified Overlay System

## Overview

This document describes a new architecture for DeltaPorts that addresses pain points
with the current system while maintaining the merge-time patching workflow.

**Status:** Partially implemented (core v2 package and CLI exist; several features are still in progress)

**Implementation Status Snapshot**

- Implemented: package structure, CLI dispatch, merge/check/list/state/migrate command families
- Partially implemented: validation strictness, quarterly policy enforcement, `special/` workflow
- Planned: non-local state backends, stricter merge-time validation gates, full quarterly parity across all overlay components

## Goals

1. **Eliminate STATUS file bloat** - Replace 32K STATUS files with centralized tracking
2. **Explicit over implicit** - Every overlay declares its purpose via `overlay.toml`
3. **Strong validation** - Catch errors before merge, not during builds
4. **First-class quarterly support** - `@QUARTER` overrides are a core concept
5. **Simplified mental model** - Clear rules for what goes where

## Design Decisions

| Aspect | Decision |
|--------|----------|
| `overlay.toml` | **Target:** required for all customized ports (partially enforced today) |
| Build state tracking | **Configurable** (`local` implemented; others planned) |
| Directory names | **Keep current** (`Makefile.DragonFly`, `dragonfly/`) |
| Validation | **Implemented**, with stricter gates still planned |
| Quarterly support | **Explicit** `--target` required; resolution is partial |

---

## Directory Structure

```
DeltaPorts/
├── dports.toml                     # Repository config
├── state/                          # Build tracking (configurable location)
│   └── builds.json
├── ports/
│   └── {category}/{port}/
│       ├── overlay.toml            # REQUIRED: overlay manifest
│       ├── Makefile.DragonFly      # Optional: included after FreeBSD Makefile
│       ├── diffs/                  # Optional: merge-time patches
│       │   ├── Makefile.diff       # Universal diff
│       │   ├── REMOVE              # Files to remove
│       │   ├── @2025Q2/            # Quarterly-specific
│       │   │   └── Makefile.diff
│       │   └── @2025Q3/
│       │       └── Makefile.diff
│       └── dragonfly/              # Optional: build-time patches & files
│           ├── patch-*.c           # Applied via DFLY_PATCHDIR
│           └── extra-file.sample   # Copied to port
└── special/                        # Infrastructure (no overlay.toml)
    ├── Mk/
    │   ├── diffs/                  # Patches to FreeBSD Mk/ files
    │   └── replacements/           # Complete file replacements
    ├── Templates/
    │   └── diffs/
    └── treetop/
        └── diffs/
```

### Key Changes from v1

| v1 (Current) | v2 (New) | Reason |
|--------------|----------|--------|
| `STATUS` file per port (32K files) | `state/builds.json` centralized | Eliminate bloat |
| No manifest | `overlay.toml` introduced | Explicit documentation |
| Implicit customization detection | Declared in `overlay.toml` | Strong validation |
| `@QUARTER` as future feature | Partial support implemented | Multi-quarterly workflow |

---

## Configuration Files

### `overlay.toml` (Port Manifest)

The current parser accepts a compact, permissive schema. `overlay.toml` is strongly recommended for customized ports and required for full v2 behavior.

```toml
# ports/ports-mgmt/pkg/overlay.toml

[overlay]
reason = "DragonFly-specific pkg(8) repository configuration"
type = "port"          # optional: port | mask | dport | lock

[status]
ignore = "Optional ignore reason"   # mainly for mask-style behavior

# Top-level customization flags
makefile_dragonfly = true
diffs = true
dragonfly_dir = true
extra_patches = false
newport = false

# Informational list used by migration/listing/validation hints
quarterly_overrides = ["2025Q2", "2025Q3"]

# Optional metadata fields are preserved as raw TOML
maintainer = "maintainer@example.org"
```

#### Current Notes

- `[overlay].reason` is the primary required semantic field
- Unknown fields are preserved (useful for forward-compatible metadata)
- Customization booleans (`diffs`, `dragonfly_dir`, etc.) drive merge behavior
- Quarterly-specific diff selection is resolved from filesystem layout (`diffs/@YYYYQn`), not from a required `[quarterly]` table

---

### `dports.toml` (Repository Config)

Repository-wide configuration currently loaded by the v2 tool.

```toml
# DeltaPorts/dports.toml

[paths]
freebsd_ports = "/usr/fports"
merged_output = "/usr/dports-work"
dports_overlay = "/usr/dports"
logs = "/var/log/dports"
delta = "/usr/DeltaPorts"
dports_built_tree = "/usr/dports"

[state]
backend = "local"   # local | git-branch | external
path = "state/builds.json"

[quarterly]
default = "2025Q1"

[merge]
cpdup_path = "/bin/cpdup"
patch_path = "/usr/bin/patch"
```

Note: environment variable override support and richer quarterly policy fields are planned but not fully implemented.

---

### `state/builds.json` (Build Tracking)

Current runtime state format used by `BuildState` local backend:

```json
{
  "version": 1,
  "updated": "2026-02-21T12:00:00.000000",
  "ports": [
    {
      "origin": "ports-mgmt/pkg",
      "status": "success",
      "last_build": "2026-02-20T10:30:00",
      "last_success": "2026-02-20T10:30:00",
      "version": "1.21.3",
      "quarterly": "2025Q3",
      "notes": ""
    },
    {
      "origin": "lang/rust",
      "status": "failed",
      "last_build": "2026-02-19T14:22:00",
      "last_success": null,
      "version": "1.75.0",
      "quarterly": "2025Q3",
      "notes": "LLVM version mismatch"
    }
  ]
}
```

#### Storage Backend Status

| Backend | Config | Status |
|--------|--------|--------|
| Local JSON | `backend = "local"` | **Implemented** |
| Git branch | `backend = "git-branch"` | **Planned** |
| External | `backend = "external"` | **Planned** |

Note: migration tooling currently emits a separate migration-oriented JSON shape; normalization into runtime state is a follow-up task.

---

## Quarterly Resolution Rules

Current implemented behavior during merge with `--target 2025Q3`:

### For `diffs/` directory

1. If `diffs/@2025Q3/` exists, apply only `.diff` and `.patch` files from that directory
2. Otherwise, apply top-level `diffs/*.diff` and `diffs/*.patch`
3. If `diffs/` does not exist, apply no diff patches

### For `dragonfly/` directory

- No quarterly override logic is currently implemented
- Files under `dragonfly/` are copied as-is

### For `Makefile.DragonFly`

- Only `Makefile.DragonFly` is currently supported
- `Makefile.DragonFly.@QUARTER` lookup is planned

---

## Validation Rules

### Errors (reported by `dports check`)

| Rule | Description |
|------|-------------|
| Overlay directory missing | Port overlay path does not exist |
| Missing manifest | `overlay.toml` missing |
| Invalid TOML | `overlay.toml` parse failure |
| Missing declared Makefile | `makefile_dragonfly=true` but `Makefile.DragonFly` missing |
| Missing declared diffs dir | `diffs=true` but `diffs/` missing |
| Missing declared dragonfly dir | `dragonfly_dir=true` but `dragonfly/` missing |

### Warnings

| Rule | Description |
|------|-------------|
| Quarterly override mismatch | Quarterly listed in `quarterly_overrides` but corresponding `diffs/@QUARTER` missing |
| Diff format issues | Missing headers, invalid hunk lines, empty diff files |
| Orphan top-level items | Unexpected top-level files in an overlay directory |

### Current Behavior Note

- `merge` does not currently hard-gate on `check` success; strict pre-merge enforcement is planned.

---

## CLI Commands

```bash
# Core merge/sync workflow
dports merge --target 2025Q3
dports merge --target 2025Q3 ports-mgmt/pkg
dports sync --target 2025Q3
dports prune --target 2025Q3 --dry-run
dports makefiles --target 2025Q3

# Validation
dports check --target 2025Q3
dports check --target 2025Q3 ports-mgmt/pkg

# List/query
dports list --customized
dports list --quarterly 2025Q3
dports list --customized --format table

# State management
dports state show
dports state clear
dports state import --target 2025Q3
dports state export --target 2025Q3

# Migration and utility commands
dports migrate all --dry-run
dports migrate all --output migrated_ports --state-output state/builds.json
dports verify --target 2025Q3 all
dports special --target 2025Q3
dports status ports-mgmt/pkg
dports save ports-mgmt/pkg --target 2025Q3
dports diff ports-mgmt/pkg --target 2025Q3
```

---

## Migration from v1

### Current Migration Command

```bash
# Preview changes
dports migrate all --dry-run

# Migrate all ports to v2 output layout
dports migrate all \
  --output migrated_ports \
  --state-output state/builds.json

# Migrate one port
dports migrate ports-mgmt/pkg --output migrated_ports
```

### Migration Logic

| Existing Structure | Generated overlay.toml |
|-------------------|----------------------|
| Only STATUS file (no customizations) | No migrated overlay dir; tracked in state output only |
| Has `Makefile.DragonFly` with IGNORE | `reason` + `[status] ignore` |
| Has `diffs/` | `diffs = true` |
| Has `dragonfly/` | `dragonfly_dir = true` |
| Has `@QUARTER` subdirs | `quarterly_overrides = [...]` inferred |
| DPORT/LOCK/MASK STATUS type | `[overlay].type` inferred |

### Manual Review Required

After migration, ports with `reason = "TODO"` need human review to add
meaningful descriptions.

---

## Implementation Phases

### Phase 1: Core Tooling
- [x] Added `overlay.toml` parser (TOML library)
- [x] Added `dports check` command
- [x] Added `--target` requirement for merge-related commands
- [~] Quarterly resolution logic implemented for `diffs/` only
- [~] Validation engine implemented with basic/medium strictness

### Phase 2: Migration Tooling
- [x] Added migration command with dry-run support
- [x] Added STATUS parsing and overlay generation
- [x] Added builds/state JSON generation
- [ ] Add dedicated post-migration cleanup/removal workflow

### Phase 3: State Management
- [x] Implemented local JSON state backend
- [x] Added `dports state show|clear|import|export`
- [ ] Implement git-branch backend
- [ ] Implement external backend

### Phase 4: Repository Rollout
- [ ] Run migration on repository
- [ ] Review and fix generated manifests
- [ ] Normalize migration output with runtime state format
- [ ] Finalize stricter validation gates and document final policy
- [ ] Tag v2.0.0

---

## Infrastructure Patches (`special/`)

The `special/` directory contains patches/files for ports infrastructure
(Mk framework, Templates, treetop). These entries do not use `overlay.toml`.

### Current Command Behavior (`dports special`)

`dports special --target ...` currently executes a lightweight workflow:

1. Iterate `special/Mk`, `special/Templates`, `special/treetop`
2. Copy non-`diffs` files/directories into merged output
3. Apply `diffs/*.diff` files only
4. Resolve patch targets from underscore naming convention

Quarterly subdirectory handling in `special/**/diffs/@QUARTER` is not currently
applied by this command.

### Structure (Current Inputs)

```
special/
├── Mk/
│   └── diffs/                      # Patches to merged Mk files
│       ├── bsd.port.mk.diff        # Patch: Mk/bsd.port.mk
│       ├── Uses_compiler.mk.diff   # Patch: Mk/Uses/compiler.mk
│       └── Scripts_qa.sh.diff      # Patch: Mk/Scripts/qa.sh
├── Templates/
│   └── diffs/
│       ├── config.site.diff
└── treetop/
    └── diffs/
        ├── Makefile.diff
        ├── UIDs.diff
        └── GIDs.diff
```

### Naming Convention

Diff files use **underscore (`_`) to represent path separators**:

| Diff File | Target File |
|-----------|-------------|
| `bsd.port.mk.diff` | `Mk/bsd.port.mk` |
| `Uses_compiler.mk.diff` | `Mk/Uses/compiler.mk` |
| `Scripts_qa.sh.diff` | `Mk/Scripts/qa.sh` |
| `Features_lto.mk.diff` | `Mk/Features/lto.mk` |
| `Uses_cargo-extra.mk.diff` | `Mk/Uses/cargo-extra.mk` |

### Quarterly and Replacement Status

- Quarterly-specific `@QUARTER` support for `special/` is **planned**
- Dedicated `replacements/` overwrite semantics are available in helper code but not currently wired into `dports special`
- Full FreeBSD-first infrastructure merge pipeline is available as internal helper logic and planned for command integration

### Key DragonFly Mk Modifications

| File | Lines | Purpose |
|------|-------|---------|
| `bsd.port.mk` | 466 | Core: PORTSDIR=/usr/dports, DFLYVERSION, DFLY_PATCHDIR |
| `bsd.df.gcc.mk` | 85 | NEW: DragonFly GCC/compiler selection |
| `bsd.port.subdir.mk` | 115 | Subdirectory handling adjustments |
| `Scripts/qa.sh` | 147 | QA script modifications |
| `Uses/compiler.mk` | 43 | Compiler detection for DragonFly |
| `Uses/cargo.mk` | 32 | Rust/Cargo build adjustments |
| `Uses/linux.mk` | 20 | REPLACEMENT: Linux compat → IGNORE |

### Why No `overlay.toml` for `special/`?

The `special/` directory is fundamentally different from port overlays:

1. **Infrastructure, not ports** - These files are the build system itself
2. **Always required** - Every merge needs Mk infrastructure
3. **Simpler structure** - Just diffs and replacements, no STATUS tracking
4. **Single purpose** - Make FreeBSD ports framework work on DragonFly

The file-based convention (underscore naming, replacements dir) is sufficient
and adding `overlay.toml` would add complexity without benefit.

---

## Appendix: Current Gaps to Close Before v2 Final

1. Enforce strict pre-merge validation in `merge` path
2. Complete quarterly parity across `diffs/`, `dragonfly/`, `Makefile.DragonFly`
3. Converge migration state JSON and runtime state JSON into one stable schema
4. Wire full infrastructure merge flow (including replacements and quarterly support) into `dports special`
5. Implement `git-branch` and `external` state backends
