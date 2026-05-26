---
triggers:
  toolchains: [gmake]
  flows: [triage, patch]
tags: [gmake, make, parallel-build]
priority: 100
---

# gmake — usual suspects on DragonFly

Ports declare `USES=gmake` when the build uses GNU `make` syntax
(pattern rules, conditionals, `$(call ...)`) that DragonFly's BSD
`make` doesn't grok. The framework invokes `/usr/local/bin/gmake`
instead of the system `make`. Failures cluster around the upstream
Makefile's portability assumptions.

## Usual suspects (likelihood-ordered)

1. **Hardcoded `cc` / `gcc` / `g++`.** Symptom: `gcc: not found`
   or wrong-version errors. DragonFly's default `cc` is clang.
   Fix: `MAKE_ENV=CC=${CC} CXX=${CXX}` (the framework already
   sets these for `MAKE_ENV`; check whether the upstream Makefile
   ignores them via direct `CC=gcc` assignment in-file). Patch
   the Makefile if needed.

2. **`uname -s` / `uname -m` switch statements** that don't
   recognize `DragonFly`. Symptom: variable set to a default that
   doesn't match the actual platform. Fix: patch the uname block
   to include DragonFly.

3. **Parallel-build races** — `gmake -j` with bad dependency
   tracking. Symptom: failure that disappears with `-j1`. Fix:
   `MAKE_JOBS_UNSAFE=yes` in the port Makefile (single-threaded
   build only). Upstream patches to fix dep tracking are durable
   but expensive.

4. **`@$(SHELL) -c '...'` blocks with bash-isms.** Symptom:
   syntax error from `/bin/sh`. DragonFly's `/bin/sh` is ash-like;
   no `[[`, no `==`, no arrays. Fix: rewrite the block to
   POSIX sh, or pin `SHELL=/usr/local/bin/bash` via
   `MAKE_ENV` (after declaring `BUILD_DEPENDS=bash:shells/bash`).

5. **`$(shell ...)` calls assuming GNU coreutils flags.**
   Symptom: `--version` works but `tail -n +5` or similar BSD-
   incompatible flag fails. Fix: depend on coreutils
   (`USES=gnu-coreutils`?), or patch the Makefile to use POSIX
   flags.

## Quick wins

- `MAKE_ARGS=V=1` to see the full command for failing targets;
  many gmake-using projects buffer output by default.
- `MAKE_ENV+=SHELL=/bin/sh` first; if that breaks, escalate to
  bash dependency.

## Logs to read first

- The `gmake: ***` line names the failing target. Read 20 lines
  back to see the actual recipe that failed.
- `gmake -d` (debug) is overkill; `gmake -p` (print database) is
  useful when a variable's value is suspicious.
