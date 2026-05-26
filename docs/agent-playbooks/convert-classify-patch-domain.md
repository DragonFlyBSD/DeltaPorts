---
triggers:
  flows: [convert]
tags: [framework, upstream-source, file-materialize, patch-apply]
priority: 50
---

# Classifying a patch's domain — framework vs upstream-source

When converting an unsupported item from `Makefile.DragonFly` or
`diffs/` to a dops op, the first decision is: which domain does this
patch live in? The dops grammar treats the two domains very
differently, and getting it wrong produces silently-broken overlays.

## The two domains

### Framework domain — `Makefile.DragonFly` or `diffs/*.diff`

These target the FreeBSD ports framework files compose materializes
into `port_root` — the port's own `Makefile`, `distinfo`,
`pkg-descr`, etc. These files DO exist in `port_root` after
compose's seed stage; `patch apply diffs/X.diff` works against them.

For each unsupported framework item, decide which op:

1. **Framework adjustment expressible as semantic op** (Makefile
   variable set/add, OS-detection `.if` block, recipe target):
   `mk set`, `mk add`, `mk remove`, `mk replace-if`,
   `mk disable-if`, `mk block set`, `mk target set/append`.
2. **Framework adjustment too complex for semantic ops** (multi-line
   restructuring of the port's own Makefile, conditional logic
   that doesn't fit `mk` ops): `patch apply diffs/<file>.diff`.
   The engine applies this against the compose-materialized
   framework files in `port_root`. Works.

### Upstream-source domain — `dragonfly/*`

These target files inside the upstream distfile tarball (e.g.
`Makefile.am`, `src/foo.c`). Those files are NOT in `port_root` at
compose time — they only appear at build time. `patch apply
dragonfly/X` DOES NOT WORK for these — the engine has nothing to
patch against. Stage them with `file materialize` instead so
`bsd.port.mk`'s `do-patch` picks them up at build time.

For each unsupported upstream-source item:

3. **Simple substitution against upstream source** (single-line
   rename, OS-detection one-liner): consider expressing as a
   `mk target set post-extract` (or `post-patch`) with
   `REINPLACE_CMD` — runs at build time inside `${WRKSRC}`, more
   durable than a static patch against a generated file like
   `Makefile.in`.
4. **Complex upstream-source surgery** (multi-hunk, intertwined
   ifdef context, anything you can't describe in one sentence):
   `file materialize dragonfly/<file> -> dragonfly/<file>`. Stages
   the patch from the source overlay into `port_root/dragonfly/`,
   `bsd.port.mk` applies it at build time.

## The bright lines

- Anything under `dragonfly/` → `file materialize`. **Never**
  `patch apply` — compose has no extracted source to patch against
  yet. **Never** `file copy` either — `file copy`'s `src` is
  resolved within `port_root`, not the source overlay, and the
  `dragonfly/` patch doesn't exist in `port_root` at that point.
- Anything under `diffs/` or `Makefile.DragonFly` → semantic op
  preferred (`mk set/add/remove`, `mk replace-if`, etc.); fall
  back to `patch apply diffs/<file>.diff` only when no semantic op
  fits.
- The naming is confusing on purpose: `file materialize` is the
  source-overlay → port-tree direction; `file copy` is the
  within-tree direction. Read each carefully before emitting.

## Common pitfalls

- **`patch apply dragonfly/...`** — silently invalid; compose
  reports `E_COMPOSE_APPLY_FAILED` or worse, succeeds at compose
  time but fails at build with a patch error the agent can't see.
- **`file copy dragonfly/...`** — same shape; the source doesn't
  exist in `port_root` at compose time, so the copy is a no-op or
  an error.
- **Emitting a `mk target set` for a phase `bsd.port.mk` doesn't
  hook** — `dfly-patch:` IS hooked on DragonFly (`bsd.port.mk`
  slot 880), `post-patch:` is also hooked. Arbitrary target names
  are dead code.
