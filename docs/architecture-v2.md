# DeltaPorts Architecture v2: Simplified Overlay System

## Overview

This document describes a new architecture for DeltaPorts that addresses pain points
with the current system while maintaining the merge-time patching workflow.

**Status:** Design complete, pending implementation

## Goals

1. **Eliminate STATUS file bloat** - Replace 32K STATUS files with centralized tracking
2. **Explicit over implicit** - Every overlay declares its purpose via `overlay.toml`
3. **Strong validation** - Catch errors before merge, not during builds
4. **First-class quarterly support** - `@QUARTER` overrides are a core concept
5. **Simplified mental model** - Clear rules for what goes where

## Design Decisions

| Aspect | Decision |
|--------|----------|
| `overlay.toml` | **Required** for all customized ports |
| Build state tracking | **Configurable** (local, git branch, external) |
| Directory names | **Keep current** (`Makefile.DragonFly`, `dragonfly/`) |
| Validation | **Strong** - fail on errors |
| Quarterly support | **Explicit** `--target` required |

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
| No manifest | `overlay.toml` required | Explicit documentation |
| Implicit customization detection | Declared in `overlay.toml` | Strong validation |
| `@QUARTER` as future feature | First-class support | Multi-quarterly workflow |

---

## Configuration Files

### `overlay.toml` (Port Manifest)

Required for every customized port. Declares what the overlay does and how.

```toml
# ports/ports-mgmt/pkg/overlay.toml

[overlay]
# Required: Why does this overlay exist?
reason = "DragonFly-specific pkg(8) repository configuration"

# Optional metadata
maintainer = "marino@dragonflybsd.org"
upstream_bug = "https://github.com/freebsd/pkg/issues/XXX"
dragonfly_bug = "https://bugs.dragonflybsd.org/issue/1234"

[quarterly]
# Which FreeBSD quarterlies this overlay supports
# Empty/omitted = universal (works with any)
supported = ["2025Q2", "2025Q3"]

# If diffs/ has @QUARTER subdirs, this MUST be true
quarterly_diffs = true

[diffs]
# Files to remove from FreeBSD port
remove = ["files/patch-linux.c", "pkg-message.freebsd"]

[dragonfly]
# Explicitly list non-patch files (auto-detected for patch-* files)
files = ["df-latest.conf.sample"]
```

#### Minimal Examples

**Simple port (just patches):**
```toml
[overlay]
reason = "Fix for DragonFly signal handling"
```

**Port marked IGNORE:**
```toml
[overlay]
reason = "Linux emulation not available on DragonFly"

[status]
ignore = "Requires Linux compatibility layer"
```

**Port with quarterly-specific diffs:**
```toml
[overlay]
reason = "Rust bootstrap version differs between quarterlies"

[quarterly]
supported = ["2025Q2", "2025Q3"]
quarterly_diffs = true
```

---

### `dports.toml` (Repository Config)

Repository-wide configuration.

```toml
# DeltaPorts/dports.toml

[repository]
name = "DeltaPorts"
version = "2.0.0"

[quarterly]
# Supported quarterlies (for validation)
supported = ["2024Q4", "2025Q1", "2025Q2", "2025Q3"]

# Current recommended quarterly
current = "2025Q3"

# Require explicit --target flag
require_target = true

[paths]
# Defaults, can be overridden by environment or CLI
freebsd_ports = "/usr/fports"
merged_output = "/usr/dports-merged"
dports_tree = "/usr/dports"

[state]
# Where to store build state
# Options: "local", "branch:NAME", "file:PATH", "none"
storage = "local"

[validation]
# Strictness: "strict", "warn", "permissive"
level = "strict"

# Fail merge if any patch fails
fail_on_patch_error = true

# Require overlay.toml in all port directories
require_manifest = true
```

---

### `state/builds.json` (Build Tracking)

Centralized build state, replacing individual STATUS files.

```json
{
  "$schema": "https://deltaports.dragonflybsd.org/schemas/builds-v1.json",
  "meta": {
    "updated": "2025-01-17T12:00:00Z",
    "generator": "dports 2.0.0"
  },
  "ports": {
    "ports-mgmt/pkg": {
      "current": {
        "quarterly": "2025Q3",
        "version": "1.21.3",
        "status": "success",
        "built": "2025-01-15T10:30:00Z"
      },
      "history": [
        {
          "quarterly": "2025Q2",
          "version": "1.20.9",
          "status": "success",
          "built": "2024-10-01T08:00:00Z"
        }
      ]
    },
    "lang/rust": {
      "current": {
        "quarterly": "2025Q3",
        "version": "1.75.0",
        "status": "failed",
        "error": "LLVM version mismatch",
        "built": "2025-01-10T14:22:00Z"
      }
    }
  }
}
```

#### Storage Options

| Option | Config | Description |
|--------|--------|-------------|
| Local | `storage = "local"` | `state/builds.json`, gitignored |
| Git branch | `storage = "branch:state"` | Separate branch, not in working tree |
| Custom file | `storage = "file:/path/to/builds.json"` | Any path |
| External | `storage = "none"` | Managed by CI/poudriere |

---

## Quarterly Resolution Rules

When merging with `--target 2025Q3`:

### For `diffs/` directory:

1. If `diffs/@2025Q3/` exists → use **only** its contents
2. If other `@QUARTER/` dirs exist but not `@2025Q3/` → **skip port**
3. If no `@QUARTER/` dirs exist → use top-level files (universal)
4. If both top-level and `@2025Q3/` exist → **warn**, use `@2025Q3/`

### For `dragonfly/` directory:

Same rules apply. `@QUARTER` subdirs completely replace base content.

### For `Makefile.DragonFly`:

Check for `Makefile.DragonFly.@2025Q3` first, fall back to `Makefile.DragonFly`.

---

## Validation Rules

### Errors (Block Merge)

| Rule | Description |
|------|-------------|
| Missing manifest | `overlay.toml` missing in customized port directory |
| Invalid TOML | `overlay.toml` has syntax errors |
| Missing reason | `[overlay].reason` field required |
| Unsupported quarterly | `quarterly.supported` doesn't include `--target` |
| Inconsistent quarterly_diffs | `quarterly_diffs=true` but no `@QUARTER` subdirs |
| Missing quarterly dir | `@QUARTER` required but doesn't exist for target |
| Patch failure | Diff file fails to apply cleanly |
| Missing remove target | File in `diffs.remove` doesn't exist in FreeBSD port |
| Missing referenced file | File referenced in `overlay.toml` doesn't exist |

### Warnings (Logged, Don't Block)

| Rule | Description |
|------|-------------|
| Orphan quarterly | `@QUARTER` subdir not in `quarterly.supported` |
| Mixed state | Both top-level diff and `@QUARTER` diff for same file |
| Undeclared files | `dragonfly/` has non-patch files without `[dragonfly].files` |
| Unknown fields | `overlay.toml` has unrecognized fields (typo detection) |
| Empty overlay | Port directory exists but has no actual customizations |

---

## CLI Commands

```bash
# Merge (--target required)
dports merge --target 2025Q3
dports merge --target 2025Q3 ports-mgmt/pkg
dports merge --target 2025Q3 --strict

# Validation
dports check --target 2025Q3
dports check --target 2025Q3 ports-mgmt/pkg
dports check --target 2025Q3 --all

# List/query
dports list --has-overlay
dports list --quarterly 2025Q3
dports list --status failed

# State management
dports state show ports-mgmt/pkg
dports state update ports-mgmt/pkg --version 1.21.3 --success
dports state import /path/to/poudriere/logs

# Migration (from v1)
dports migrate generate-manifests [--dry-run]
dports migrate extract-status [--dry-run]
dports migrate remove-status [--dry-run]
```

---

## Migration from v1

### Automated Migration

```bash
# Preview changes
dports migrate generate-manifests --dry-run

# Step 1: Generate overlay.toml for existing ports
dports migrate generate-manifests

# Step 2: Extract STATUS files into builds.json
dports migrate extract-status

# Step 3: Remove STATUS files
dports migrate remove-status

# Step 4: Validate
dports check --target 2025Q3 --all
```

### Migration Logic

| Existing Structure | Generated overlay.toml |
|-------------------|----------------------|
| Only STATUS file (no customizations) | Port removed from DeltaPorts |
| Has `Makefile.DragonFly` with IGNORE | `reason` + `[status] ignore` |
| Has `diffs/` | `reason = "TODO: add description"` |
| Has `dragonfly/` | `reason = "TODO: add description"` |
| Has `@QUARTER` subdirs | `[quarterly] supported` inferred |
| Has `diffs/REMOVE` | `[diffs] remove` populated |

### Manual Review Required

After migration, ports with `reason = "TODO"` need human review to add
meaningful descriptions.

---

## Implementation Phases

### Phase 1: Core Tooling
- [ ] Add `overlay.toml` parser (TOML library)
- [ ] Implement validation engine
- [ ] Add `--target` requirement to merge
- [ ] Implement quarterly resolution logic
- [ ] Add `dports check` command

### Phase 2: Migration Tooling
- [ ] `dports migrate generate-manifests`
- [ ] `dports migrate extract-status`
- [ ] `dports migrate remove-status`
- [ ] Dry-run mode for all migration commands

### Phase 3: State Management
- [ ] Implement configurable state storage
- [ ] `dports state show/update/import` commands
- [ ] State branch workflow (if using git branch storage)

### Phase 4: Execute Migration
- [ ] Run migration on repository
- [ ] Review and fix generated manifests
- [ ] Remove STATUS files
- [ ] Update documentation
- [ ] Tag v2.0.0

---

## Infrastructure Patches (`special/`)

The `special/` directory contains patches and replacements for the ports
infrastructure (Mk framework, Templates, top-level files). Unlike port
overlays, these **do not use `overlay.toml`** - they follow a simpler
file-based convention.

### Structure

```
special/
├── Mk/
│   ├── diffs/                      # Patches to FreeBSD Mk/ files
│   │   ├── bsd.port.mk.diff        # Patch: Mk/bsd.port.mk
│   │   ├── bsd.df.gcc.mk.diff      # NEW FILE: creates Mk/bsd.df.gcc.mk
│   │   ├── Uses_compiler.mk.diff   # Patch: Mk/Uses/compiler.mk
│   │   ├── Scripts_qa.sh.diff      # Patch: Mk/Scripts/qa.sh
│   │   ├── Features_lto.mk.diff    # Patch: Mk/Features/lto.mk
│   │   ├── @2025Q2/                # Quarterly overrides
│   │   │   └── bsd.port.mk.diff
│   │   └── @2025Q3/
│   │       └── bsd.port.mk.diff
│   └── replacements/               # Complete file replacements
│       └── Uses/
│           └── linux.mk            # Replaces Mk/Uses/linux.mk entirely
├── Templates/
│   └── diffs/
│       ├── config.site.diff
│       └── @2025Q3/
│           └── config.site.diff
└── treetop/
    └── diffs/
        ├── Makefile.diff           # Root ports Makefile
        ├── UIDs.diff               # User IDs file
        └── GIDs.diff               # Group IDs file
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

### Types of Changes

1. **Patches** - Standard unified diffs applied to existing FreeBSD files
2. **New Files** - Diffs that create files from scratch (diff against `/dev/null` or empty `.orig`)
3. **Replacements** - Complete file replacements stored in `replacements/` subdirectory

#### Example: New File via Diff

`bsd.df.gcc.mk.diff` creates a new DragonFly-specific file:

```diff
--- bsd.df.gcc.mk.orig	2021-09-19 13:40:43 UTC
+++ bsd.df.gcc.mk
@@ -0,0 +1,85 @@
+# bsd.gcc.df.mk  - Reaction to USE_GCC on DragonFly
+#
+# The primary base compiler is used if possible...
+.if !defined(_INCLUDE_BSD_DF_GCC_MK)
+_INCLUDE_BSD_DF_GCC_MK=	yes
...
```

#### Example: Replacement File

`special/Mk/replacements/Uses/linux.mk` completely replaces FreeBSD's Linux
compatibility framework with a stub that sets `IGNORE`:

```make
# Ports Linux compatibility framework
.ifndef _INCLUDE_USES_LINUX_MK
_INCLUDE_USES_LINUX_MK=	yes
IGNORE=	Linux emulation is not supported on DragonFly
.endif
```

### Quarterly Support

Like ports, `special/` supports `@QUARTER` subdirectories for quarterly-specific patches:

```
special/Mk/diffs/
├── bsd.port.mk.diff          # Universal (fallback)
├── @2025Q2/
│   └── bsd.port.mk.diff      # Q2-specific override
└── @2025Q3/
    └── bsd.port.mk.diff      # Q3-specific override
```

**Resolution rules** (same as ports):
- If `@{target}/` exists → use only its contents
- If other `@QUARTER/` dirs exist but not target → **error**
- If no `@QUARTER/` dirs exist → use top-level (universal)

### Processing Order

1. Copy FreeBSD `Mk/` (or `Templates/`) to working directory
2. Remove files that don't apply to DragonFly (e.g., `bsd.gcc.mk`)
3. Apply diffs from `special/{dir}/diffs/` (respecting `@QUARTER`)
4. Copy files from `special/{dir}/replacements/` (overwrites patched files)
5. Copy result to output tree

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

## Appendix: Comparison with v1

### What's Better

1. **No more 32K STATUS files** - Single `builds.json` file
2. **Explicit documentation** - Every overlay explains why it exists
3. **Validation catches errors early** - Before merge, not during build
4. **Quarterly support built-in** - Not an afterthought
5. **Queryable** - Easy to list ports by status, quarterly support, etc.

### What's the Same

1. **Merge-time patching** - Same workflow, diffs applied during merge
2. **Directory names** - `Makefile.DragonFly`, `dragonfly/`, `diffs/`
3. **REMOVE mechanism** - Still works, now declared in `overlay.toml`
4. **special/ structure** - Mk, Templates, treetop unchanged

### Breaking Changes

1. **`--target` required** - No implicit default quarterly
2. **`overlay.toml` required** - Ports without manifest won't merge
3. **STATUS files removed** - Build tracking is separate
4. **Stricter validation** - Invalid structures cause errors
