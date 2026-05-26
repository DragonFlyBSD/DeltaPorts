---
triggers:
  toolchains: [cmake]
  flows: [triage, patch]
tags: [cmake, find_package]
priority: 100
---

# CMake — usual suspects on DragonFly

The port runs `cmake` to generate build files (Makefile or
ninja.build), then builds. Failures cluster around configure-time
`find_package` calls and OS-specific code paths.

## Usual suspects (likelihood-ordered)

1. **`find_package` failing to locate a library that's installed.**
   Symptom: `Could NOT find <Pkg>` despite the port being a
   declared dependency. Common cause: the `.cmake` config module
   uses FreeBSD-only paths or assumes `pkg-config` paths that
   differ on DragonFly. Fix: pass `-D<Pkg>_DIR=<path>` via
   `CMAKE_ARGS`, or set `<Pkg>_ROOT` env var.

2. **`if(CMAKE_SYSTEM_NAME STREQUAL "FreeBSD")` blocks that don't
   include DragonFly.** Symptom: features disabled or wrong code
   path taken. Fix: patch `CMakeLists.txt` to also match
   `"DragonFly"`, or override via `CMAKE_ARGS`
   `-DCMAKE_SYSTEM_NAME=FreeBSD` (only safe when DragonFly behaves
   identically to FreeBSD for the gated logic).

3. **`-Werror` or strict warning flags treated as errors** that
   the project ships but DragonFly's clang treats more aggressively.
   Fix: `CFLAGS=-Wno-<warning>` or `-Wno-error=<warning>` in
   `CONFIGURE_ENV`.

4. **`check_function_exists` / `check_symbol_exists` thinking a
   FreeBSD-only function is available.** Same shape as the
   autoconf version. Override via `CMAKE_ARGS`
   `-DHAVE_<FUNC>=0`.

5. **Out-of-tree build assumption mismatches.** CMake builds in a
   `_build` subdir; some projects assume in-tree (resources at
   `${CMAKE_SOURCE_DIR}`). Symptom: file-not-found errors at
   install/runtime referring to generated files. Fix usually
   patches the CMakeLists.txt `install()` calls.

## Quick wins

- `CMAKE_ARGS=-Wno-dev` to silence developer warnings; useful when
  triaging real errors hidden in noise.
- `CMAKE_BUILD_TYPE=Release` / `Debug` if the failure depends on
  optimization-driven warnings.

## Logs to read first

- `cmake` output up to "Configuring incomplete, errors occurred!"
- `CMakeFiles/CMakeError.log` for failed compile checks.
- `CMakeFiles/CMakeOutput.log` for successful checks (helps verify
  which detection paths fired).
