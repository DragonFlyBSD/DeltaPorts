---
triggers:
  toolchains: [meson]
  flows: [triage, patch]
tags: [meson, ninja]
priority: 100
---

# Meson — usual suspects on DragonFly

The port runs `meson setup` then `ninja` to build. Meson is
stricter than autoconf/cmake about platform identification; many
failures come from `meson.build` checking `host_machine.system()`
against a hardcoded list that excludes `dragonfly`.

## Usual suspects (likelihood-ordered)

1. **`host_machine.system() == 'freebsd'` (or similar) excluding
   DragonFly.** Symptom: feature disabled or build target skipped.
   Fix: patch `meson.build` to accept `'dragonfly'` alongside
   `'freebsd'`. Pattern:
   ```meson
   if host_machine.system() in ['freebsd', 'dragonfly']
   ```

2. **`dependency()` calls with `required: true` that fail to
   locate a library via pkg-config.** See
   [toolchain-pkg-config](toolchain-pkg-config.md). Workaround:
   pass `-Dfeature_name=disabled` via `MESON_ARGS`.

3. **Option defaults that include FreeBSD-only features.**
   Symptom: setup succeeds but build fails on missing headers.
   Fix: `MESON_ARGS=-D<feature>=disabled` for known-broken
   features on DragonFly (see
   [error-freebsd-only-features](error-freebsd-only-features.md)).

4. **`compiler.has_header` / `has_function` checks against
   headers/functions that exist on FreeBSD but not DragonFly.**
   Override via `meson_options.txt` patch or `CONFIGURE_ENV`.

5. **`subproject()` recursion** pulling in vendored deps that
   don't build on DragonFly. Symptom: setup time blows up; first
   error is from a subdir of `subprojects/`. Fix: use system
   libraries via `--wrap-mode=nofallback` (set in
   `MESON_ARGS`).

## Quick wins

- `MESON_ARGS=--wrap-mode=nofallback` forces system dependencies,
  surfaces pkg-config issues directly rather than via a vendored
  copy.
- `meson setup --reconfigure` skip is automatic via the ports
  framework; manual reruns from `${WRKSRC}` for ad-hoc testing.

## Logs to read first

- `meson-log.txt` in the build dir — meson writes a full setup
  trace there, even when stdout is truncated.
- The first 50 lines of `ninja` output for build-time failures;
  ninja parallelizes so the failing target's output is usually
  interleaved with successes.
