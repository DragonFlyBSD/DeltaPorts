---
triggers:
  flows: [convert]
tags: [target-directive, scope, I_APPLY_TARGET_MISMATCH]
priority: 50
---

# Picking the `target` directive

`overlay.dops` must declare a `target` scope on its first line. The
scope decides which compose targets (e.g. `@main`, `@2026Q2`,
`@2026Q1`) will actually apply the ops. **Picking wrong here makes
the overlay silently dead** — compose runs successfully but every
op is filtered out with `I_APPLY_TARGET_MISMATCH`.

## Rule, based on the legacy artifact's filename

- `Makefile.DragonFly` (no suffix) → **`target @any`**. The legacy
  file applied on every quarterly branch, so the dops translation
  must too. This is the common case.
- `Makefile.DragonFly.@main` → `target @main`.
- `Makefile.DragonFly.@2026Q2` → `target @2026Q2`. Same shape for
  any quarterly suffix.
- Multiple variants with different content → emit one
  `target <selector>` block per variant, or `target @any` for the
  common ops + `target <selector>` for the specifics.

**Default to `@any` unless you have evidence of target-scoping in
the source.** A bare `Makefile.DragonFly` is target-agnostic by
definition; the dops translation must preserve that.

## Failure mode if you pick wrong

If the overlay declares `target @main` but compose runs for
`@2026Q2`, every op is annotated `I_APPLY_TARGET_MISMATCH` and
skipped. The overlay parses, compose succeeds, dsynth eventually
fails with the same error the bundle started with — but the
operator sees the overlay on disk and thinks "convert worked," so
the failure mode is invisible until somebody runs `dportsv3 compose
--target @<env's target> --origin <port>` by hand and reads the
per-op diagnostics. The `_check_overlay_effective_ops` guard in
`_verify_conversion` catches this case AFTER convert claims
success, so the convert job will fail-and-rollback rather than
ship a dead overlay — but only when the env's target doesn't match
your declaration. Don't rely on that guard; pick the right
directive up front.

## Worked example

Source: `ports/devel/foo/Makefile.DragonFly` (unscoped). Translate
to:

```dops
target @any
port devel/foo
type port
reason "..."
```

Source: `ports/devel/foo/Makefile.DragonFly.@2026Q2` (quarterly-
scoped, applies only on @2026Q2):

```dops
target @2026Q2
port devel/foo
type port
reason "..."
```

Source: both unscoped AND `.@2026Q2` files coexist (multi-target
shape):

```dops
target @any
port devel/foo
type port
reason "..."

# common ops here

target @2026Q2

# 2026Q2-specific ops here
```
