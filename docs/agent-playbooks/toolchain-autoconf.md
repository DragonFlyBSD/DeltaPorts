---
triggers:
  toolchains: [autoconf]
  flows: [triage, patch]
tags: [autoconf, automake, configure, libtool]
priority: 100
---

# Autoconf — usual suspects on DragonFly

The port runs `./configure` (generated from `configure.ac`) before
build. Most autoconf failures on DragonFly come from one of a small
set of patterns.

## Usual suspects (likelihood-ordered)

1. **Stale `config.sub` / `config.guess`** that doesn't recognize
   `dragonfly`. Symptom: `configure: error: cannot guess build
   type` or `Invalid configuration ... unrecognized OS`. Fix:
   `USES= autoreconf` (regenerates them from current
   `devel/autoconf` knowledge) or staticly replace via a
   `post-patch` REINPLACE that adds DragonFly to the supported
   list.

2. **OS-detection `#if defined(__FreeBSD__)` blocks that don't
   include `__DragonFly__`**. Symptom: configure succeeds but
   compile fails with undeclared identifiers (functions, types)
   that should be available. Fix: extend the ifdef. A dops
   `mk target set dfly-patch` body with REINPLACE_CMDs is durable
   across upstream regenerations; a static `dragonfly/patch-*.c`
   works but rots on every version bump.

3. **Missing headers FreeBSD has but DragonFly doesn't.** See the
   [error-freebsd-only-features](error-freebsd-only-features.md)
   playbook for the canonical list (`blacklist.h`, `sys/capsicum.h`,
   `sys/audit.h`, `netinet/sctp.h`).

4. **`AC_CHECK_LIB` / `AC_CHECK_HEADER` finding a library/header
   that's not actually linkable on DragonFly.** Symptom:
   `configure` reports yes; build fails with undefined references.
   Fix: patch the configure check or set `ac_cv_lib_<name>=no` via
   `CONFIGURE_ENV`.

5. **`libtool`-driven relink loops** during install. Symptom:
   build phase succeeds, install phase issues relink commands that
   re-run `ld` against `libtool`-rewritten paths and fail. See the
   [toolchain-libtool](toolchain-libtool.md) playbook.

6. **Missing `pkg-config` lookups** for libraries that exist but
   don't ship `.pc` files. See
   [toolchain-pkg-config](toolchain-pkg-config.md).

## Quick wins

- If `configure.ac` mentions `AC_CHECK_FUNCS` for a function not in
  DragonFly libc, add the function to the SKIP list via
  `ac_cv_func_<name>=no` in `CONFIGURE_ENV`.
- `USE_CSTD=` / `USE_CXXSTD=` to pin C/C++ standard when configure
  picks up a flag DragonFly's clang rejects.

## Logs to read first

- `configure` output near the failure line — autoconf is verbose;
  the actual failing test usually appears 5-20 lines above the
  "error:" message.
- `config.log` (`work/<distname>/config.log`) carries the failing
  link/compile command verbatim.
