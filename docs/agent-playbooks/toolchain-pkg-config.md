---
triggers:
  toolchains: [pkg-config]
  flows: [triage, patch]
tags: [pkg-config, pkgconf]
priority: 100
---

# pkg-config — usual suspects on DragonFly

The port uses `pkg-config` (or `pkgconf`) to discover library
paths and compile flags. Failures cluster around `.pc` file
presence, search-path configuration, and version-string parsing.

## Usual suspects (likelihood-ordered)

1. **Missing `.pc` file for a library that exists.** Symptom:
   `Package <name> was not found in the pkg-config search path`.
   Cause: the dependency port didn't install a `.pc` file (some
   minimal builds skip them). Fix: depend on a different variant
   (`-dev` / `-devel` subpackage if available), or write a stub
   `.pc` file in `files/` and install it via the port's
   `pre-configure`.

2. **`PKG_CONFIG_PATH` not including the port's search path.**
   Symptom: `.pc` files exist but pkg-config can't find them.
   Fix: `CONFIGURE_ENV+=PKG_CONFIG_PATH=${LOCALBASE}/libdata/pkgconfig`
   (the framework usually sets this; check whether the upstream
   build overrides it).

3. **Version comparison failures** — `pkg-config --atleast-version`
   rejects a version it should accept. Cause: non-standard
   version strings in the `.pc` file (e.g. `1.0~rc3`). Fix:
   patch the `.pc` file's `Version:` line to a parseable form.

4. **`Requires:` chain reaching a missing package.** Symptom:
   `Package <transitive> was not found` even though the top-level
   package's `.pc` exists. Each `.pc` file lists its own
   transitive deps; one missing in the chain fails the whole
   lookup. Fix: locate the broken intermediate and either install
   the missing dep or patch the `.pc` to drop the unused
   transitive.

5. **`pkgconf` vs `pkg-config` behavior differences.** DragonFly
   ships `pkgconf` as the default pkg-config implementation; it's
   ~99% compatible but a few edge cases differ (variable
   expansion, recursive flag dedup). Fix: pin the implementation
   via `USES=pkgconfig:build,run` (build-time only) or
   `USES=pkgconfig:run` (run-time too).

## Quick wins

- `pkg-config --debug <name>` (run manually in WRKSRC) shows the
  full search path and which `.pc` files were considered.
- `pkg-config --cflags --libs <name>` reproduces what the
  upstream build is trying to compute.

## Logs to read first

- The `configure` output line right before "no" — autoconf
  embeds pkg-config calls; the failure message names the package
  but not the failure mode.
- Any `Requires:` chain reachable via `pkg-config
  --print-requires <name>` in WRKSRC.
