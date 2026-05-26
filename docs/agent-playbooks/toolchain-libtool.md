---
triggers:
  toolchains: [libtool]
  flows: [triage, patch]
tags: [libtool, la-file, relink]
priority: 100
---

# libtool — usual suspects on DragonFly

The port uses GNU libtool to manage library linking. libtool
generates `.la` files alongside `.so` libraries; failures cluster
around `.la` file paths, the install-time relink, and version
mismatches between libtool and the system.

## Usual suspects (likelihood-ordered)

1. **`.la` files referencing absolute build-time paths.**
   Symptom: link-against-installed-port fails with "cannot find
   `/wrkdir/.../lib<name>.la`" — paths embedded at build time
   don't survive the move into the staging dir. Fix:
   `USES=libtool` (the framework patches `.la` files post-build);
   if already used, the port's libtool may be stale — bump or
   regenerate.

2. **Install-time relink loops** — libtool re-runs `ld` against
   `lib<name>.la` references to other newly-installed `.la`
   files, and one of them isn't yet visible. Symptom: install
   phase hangs or fails after build succeeded. Fix:
   `INSTALL_TARGET=install-strip` (skip the relink) when the
   build doesn't actually need it, or override
   `MAKE_ENV+=lt_cv_deplibs_check_method=pass_all`.

3. **`libtool` mismatch** between port's vendored `libtool` and
   the system `devel/libtool` port. Symptom: `libtool: command
   not found` or behavior changes between FreeBSD ports tree and
   DragonFly. Fix: `USES=libtool` forces use of the system
   libtool from `devel/libtool`.

4. **Hardcoded `gcc -shared` instead of `${LIBTOOL} --mode=link`.**
   Symptom: shared-library build works on FreeBSD (where libtool
   isn't invoked) but fails on DragonFly. Cause: upstream
   Makefile bypasses libtool. Fix: patch the Makefile to use
   libtool.

5. **`.la` files left in staging that shouldn't be installed.**
   Symptom: pkg-plist mismatch (Orphaned `.la` files). See
   [error-plist-mismatch](error-plist-mismatch.md). Fix: usually
   `INSTALL_TARGET=install-strip` strips `.la` files; otherwise
   list them in `PLIST_FILES` or remove from staging.

## Quick wins

- `MAKE_ENV+=LIBTOOL_FORCE_STATIC=yes` to force static linking
  if shared-lib generation is broken and the port doesn't ship
  shared libs as a primary deliverable.
- `gnu-cfg.update_libtool=yes` (in older Makefiles) bumps the
  vendored libtool to the system one.

## Logs to read first

- The `libtool:` prefix lines in the build output — libtool
  prefixes its own messages, distinguishing them from compiler
  output.
- The first `relink` line during install; the path it's trying
  to relink is usually the diagnostic anchor.
