# DPorts Generator Tool (`dports.py`)

## Overview

`dports.py` is a unified Python tool that merges the FreeBSD Ports Collection with
DeltaPorts overlays to produce DPorts (DragonFly BSD's ports tree).

It replaces the legacy shell scripts (`merge.sh`, `sync1.sh`, `prune.sh`, etc.) with
a single tool offering better error handling, logging, and maintainability.

**Location:** `scripts/generator/dports.py`

## Commands

| Command | Description |
|---------|-------------|
| `merge` | Full merge of all ports (or specific ports) |
| `sync` | Sync a single port to the potential tree |
| `prune` | Remove obsolete ports from DPorts/DeltaPorts |
| `makefiles` | Generate category Makefiles |
| `index` | Generate INDEX file |
| `daemon` | Run the background commit daemon |
| `bulk-list` | Generate list for poudriere bulk builds |
| `stinkers` | Find unbuilt ports with most dependents |
| `check` | Validate patches against a target quarterly |

## Global Options

```
-c, --config PATH    Config file (default: /usr/local/etc/dports.conf)
-v, --verbose        Verbose output
-n, --dry-run        Show what would be done
-q, --quiet          Minimal output
--strict             Fail on warnings (patch errors, mixed state, etc.)
-h, --help           Show help
```

## Configuration

Config file: `/usr/local/etc/dports.conf` (see `scripts/generator/dports-default.conf`)

Environment variables override config:
- `DPORTS_FPORTS` - FreeBSD ports tree
- `DPORTS_MERGED` - Output merged tree
- `DPORTS_DPORTS` - DPorts tree
- `DPORTS_DELTA` - DeltaPorts repository
- `DPORTS_POTENTIAL` - Potential tree for sync
- `DPORTS_INDEX` - INDEX file location

## Logging

Logs are written to `~/.dports/logs/` with timestamps.

---

# Multi-Quarterly FreeBSD Support

## Problem Statement

DeltaPorts currently targets a single FreeBSD quarterly branch (e.g., 2025Q2).
This creates pain points:

1. **Quarterly transitions** - When FreeBSD releases a new quarterly, all patches
   may need updating simultaneously
2. **Testing new quarterlies** - No way to test against upcoming quarterly while
   maintaining stable builds
3. **Branch maintenance** - Historically required separate DeltaPorts branches

## Solution: `@QUARTER` Overrides

Version-specific patches using `@QUARTER` subdirectories within `diffs/` and
`dragonfly/` directories.

## Design Decisions

| Aspect | Decision |
|--------|----------|
| Universal fallback | **No** - files must be explicit per-quarterly or universal |
| `@QUARTER` override | **Replaces entirely** - no merging with parent |
| Per-port targeting | **No** - single `--target` flag for whole merge |
| Mixed state handling | **Warn** - if `@QUARTER/` exists, top-level is ignored |
| Missing target | **Skip port** - excluded from merge with log message |
| special/ support | **Yes** - Mk/, Templates/, treetop/ support `@QUARTER` |

## Directory Structure

### Quarterly-Specific Port

When a port needs different patches for different FreeBSD quarterlies:

```
ports/ports-mgmt/pkg/
├── STATUS                          # Status: NORM/MASK/LOCK/DPORT
├── Makefile.DragonFly              # Universal (or use .@QUARTER suffix)
├── diffs/
│   ├── @2025Q2/                    # Complete set for Q2
│   │   ├── Makefile.diff
│   │   ├── pkg-plist.diff
│   │   └── REMOVE
│   └── @2025Q3/                    # Complete set for Q3
│       ├── Makefile.diff
│       └── pkg-plist.diff
└── dragonfly/
    ├── @2025Q2/                    # Patches for Q2
    │   ├── patch-foo.c
    │   └── patch-bar.c
    └── @2025Q3/                    # Patches for Q3
        └── patch-foo.c             # May differ from Q2 version
```

### Universal Port

When a port's patches work across all FreeBSD quarterlies:

```
ports/devel/someport/
├── STATUS
├── Makefile.DragonFly
├── diffs/
│   ├── Makefile.diff               # Works for all quarterlies
│   └── REMOVE
└── dragonfly/
    └── patch-bsd.c                 # Universal patch
```

### Special Directory

Infrastructure patches (Mk/, Templates/, treetop/) also support `@QUARTER`:

```
special/
├── Mk/
│   ├── diffs/
│   │   ├── bsd.port.mk.diff        # Universal
│   │   └── @2025Q3/
│   │       └── bsd.port.mk.diff    # Q3-specific override
│   └── replacements/
│       └── Uses/
│           └── linux.mk            # Universal replacement
├── Templates/
│   └── diffs/
│       └── @2025Q3/
│           └── ...
└── treetop/
    └── diffs/
        └── Makefile.diff           # Universal
```

## Resolution Rules

For `--target 2025Q3`:

| Scenario | Resolution |
|----------|------------|
| `component/@2025Q3/` exists | Use **only** its contents |
| Other `@QUARTER/` dirs exist, but not target | **Skip port** entirely |
| No `@QUARTER/` dirs exist | Use top-level files (universal) |
| Both top-level AND `@QUARTER/` exist | **Warn**, use `@QUARTER/` only |

### Algorithm

```
1. Check if target-specific subdir exists (e.g., diffs/@2025Q3/)
   → YES: Use only that subdir's contents
   
2. Check if ANY @QUARTER subdirs exist
   → YES (but not our target): SKIP this port
   
3. No @QUARTER subdirs
   → Use top-level files (universal port)
   
4. If both top-level files AND matching @QUARTER exist
   → WARN and use @QUARTER only
```

## CLI Usage

```bash
# Merge entire tree for a target quarterly (--target required)
dports merge --target 2025Q3

# Merge specific port
dports merge --target 2025Q3 ports-mgmt/pkg

# Strict mode - fail on any warnings
dports merge --target 2025Q3 --strict

# Validate patches without merging
dports check --target 2025Q3
dports check --target 2025Q3 ports-mgmt/pkg

# Dry run - show what would happen
dports merge --target 2025Q3 --dry-run
```

## Behavioral Summary

| Scenario | Behavior |
|----------|----------|
| No `--target` specified | **Error** - target is required |
| Port has `@2025Q2/` only, merging `--target 2025Q3` | **Skip port** |
| Port has top-level + `@2025Q2/`, merging `--target 2025Q2` | **Warn**, use `@2025Q2/` |
| Port has only top-level (no `@*/`) | **Merge** as universal |
| Port has `@2025Q3/`, merging `--target 2025Q3` | **Merge** using `@2025Q3/` |

## Migration Path

### Phase 1: No Changes Needed

Existing ports with no `@QUARTER/` structure continue working:
- Top-level files are treated as universal (work for all targets)
- Tool accepts `--target` but universal ports merge normally

### Phase 2: Add Quarterly Overrides As Needed

When a port needs quarterly-specific changes:

1. Create `@QUARTER/` subdirectory in `diffs/` or `dragonfly/`
2. Move/copy relevant files into the subdir
3. If patches differ between quarterlies, create multiple `@QUARTER/` subdirs

### Example Migration

**Before (universal):**
```
ports/lang/rust/
├── STATUS
├── diffs/
│   └── Makefile.diff
└── dragonfly/
    └── patch-build.rs
```

**After (quarterly-specific):**
```
ports/lang/rust/
├── STATUS
├── diffs/
│   ├── @2025Q2/
│   │   └── Makefile.diff    # Q2 version
│   └── @2025Q3/
│       └── Makefile.diff    # Q3 version (different)
└── dragonfly/
    └── patch-build.rs       # Still universal (works for both)
```

## Workflow Examples

### Normal Quarterly Sync

```bash
# 1. Update FreeBSD ports to new quarterly
cd /usr/fports && git fetch && git checkout 2025Q3

# 2. Merge with target
dports merge --target 2025Q3

# 3. Review any skipped ports (missing @2025Q3 overrides)
# 4. Update patches as needed
# 5. Re-run merge
```

### Testing Upcoming Quarterly

```bash
# While 2025Q2 is stable, test against 2025Q3
dports check --target 2025Q3

# See which ports need @2025Q3 overrides
# Prepare patches in advance of quarterly transition
```

### Supporting Multiple Quarterlies Simultaneously

```bash
# Build stable packages against Q2
dports merge --target 2025Q2
# ... build with poudriere ...

# Build bleeding-edge against Q3
dports merge --target 2025Q3
# ... build with poudriere ...
```

---

## Implementation Status

- [ ] Add `--target` argument to CLI
- [ ] Implement `resolve_quarterly_path()` function
- [ ] Update `Merger._full_merge()` with resolution logic
- [ ] Update `merge_mk_templates()` for special/ support
- [ ] Add `dports check` command
- [ ] Update logging to include target
- [ ] Documentation and migration guide
