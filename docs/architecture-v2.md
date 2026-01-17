# DeltaPorts Architecture v2: Branch-Scoped Overlay System

## Overview

DeltaPorts v2 is branch-native. Every overlay input is scoped to a FreeBSD ports
target branch. The tool no longer treats root component files as defaults.

**Status:** Design locked for implementation

---

## Core Decisions

1. **Target model is branch-based**
   - `--target` identifies the FreeBSD ports branch.
   - Allowed targets are:
     - `main`
     - `YYYYQ[1-4]` (example: `2025Q2`)

2. **Component-local target layout is mandatory**
   - `diffs/@<target>/...`
   - `dragonfly/@<target>/...`
   - `Makefile.DragonFly.@<target>`

3. **Root component files are invalid**
   - No root fallback.
   - Root component paths are hard validation errors.

4. **Single FreeBSD checkout**
   - One working tree is used for all targets.
   - `sync --target` switches that checkout to the requested branch.
   - Branch switch fails if the FreeBSD tree is dirty.

5. **No v1 migration focus in v2 design**
   - v2 behavior is defined independently.

---

## Directory Structure

```text
DeltaPorts/
├── dports.toml
├── ports/
│   └── {category}/{port}/
│       ├── overlay.toml
│       ├── Makefile.DragonFly.@main
│       ├── Makefile.DragonFly.@2025Q2
│       ├── diffs/
│       │   ├── @main/
│       │   │   └── Makefile.diff
│       │   └── @2025Q2/
│       │       └── Makefile.diff
│       └── dragonfly/
│           ├── @main/
│           │   └── patch-configure
│           └── @2025Q2/
│               └── patch-configure
└── special/
    ├── Mk/
    │   └── diffs/
    │       ├── @main/
    │       │   └── bsd.port.mk.diff
    │       └── @2025Q2/
    │           └── bsd.port.mk.diff
    ├── Templates/
    │   └── diffs/
    │       ├── @main/
    │       └── @2025Q2/
    └── treetop/
        └── diffs/
            ├── @main/
            └── @2025Q2/
```

### Forbidden root component paths

The following are invalid in v2 and must fail `check`:

- `ports/*/*/Makefile.DragonFly`
- `ports/*/*/diffs/*.diff` and `ports/*/*/diffs/*.patch`
- `ports/*/*/dragonfly/*` files outside `dragonfly/@<target>/`
- `special/*/diffs/*.diff` outside `@<target>/`

---

## Overlay Manifest

`overlay.toml` declares component intent and port type.

```toml
[overlay]
reason = "DragonFly-specific behavior"
type = "port" # port | mask | dport | lock

[components]
makefile_dragonfly = true
diffs = true
dragonfly_dir = true

[status]
ignore = "Optional reason for mask behavior"
```

Notes:

- Supported targets are discovered from filesystem `@<target>` entries.
- No `quarterly_overrides` list is required.

---

## Resolution Rules

Given target `T`:

1. Validate target format (`main` or `YYYYQ[1-4]`).
2. Confirm FreeBSD checkout is on branch `T`.
3. Resolve per component:
   - Makefile fragment: `Makefile.DragonFly.@T`
   - Diffs directory: `diffs/@T/`
   - Dragonfly overlay directory: `dragonfly/@T/`
4. If component is declared enabled but target path is missing, fail.
5. If forbidden root component files exist, fail.

There is no fallback from `@T` to non-target paths.

---

## FreeBSD Checkout Model

DeltaPorts uses one FreeBSD ports checkout path.

### `sync --target T`

1. Verify checkout is clean (`git status --porcelain` must be empty).
2. `git fetch` from remote.
3. Checkout branch `T` (`main` or `YYYYQn`).
4. Fast-forward only.

If the tree is dirty, sync fails immediately.

### Merge/Check guard

`merge`, `check`, and `special` must fail if current branch does not match
`--target`.

---

## Validation Rules

### Hard errors

- Invalid target value.
- Unknown `@<target>` name in overlay content.
- Declared component missing for requested target.
- Any forbidden root component path.
- FreeBSD branch mismatch for requested target.
- Dirty FreeBSD tree when target switch is required.

### Warnings

- Extra target content present but unused for the current run.
- Empty target component directories.

---

## `special/` Policy

`special/` follows the same target-scoped policy as port overlays.

- Use only `special/**/diffs/@<target>/...` for target `T`.
- Root `special/**/diffs/*.diff` is invalid.
- No target fallback behavior.

---

## CLI Semantics (v2)

```bash
# Sync single shared FreeBSD checkout to target branch
dports sync --target main
dports sync --target 2025Q2

# Validate branch-scoped overlay inputs
dports check --target main all
dports check --target 2025Q2 category/port

# Merge using only @<target> inputs
dports merge --target main category/port
dports merge --target 2025Q2 all

# Apply branch-scoped special infrastructure diffs
dports special --target main
dports special --target 2025Q2
```

---

## State Model

Build state is keyed by target branch.

- Per-port records include `target`.
- `main` and quarterly targets are tracked independently.

---

## Non-Goals for v2 Architecture

- Root-path fallback behavior for overlays.
- Arbitrary branch-name targets.
- v1 migration workflow as a design dependency.
