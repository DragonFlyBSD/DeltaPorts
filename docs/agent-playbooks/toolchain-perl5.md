---
triggers:
  toolchains: [perl5]
  flows: [triage, patch]
tags: [perl, xs, makemaker]
priority: 100
---

# Perl 5 — usual suspects on DragonFly

The port uses `Makefile.PL` (ExtUtils::MakeMaker) or `Build.PL`
(Module::Build), generates a Makefile, then runs `make`. Failures
cluster around XS modules and perl version mismatches.

## Usual suspects (likelihood-ordered)

1. **XS module compile failures referencing FreeBSD-only
   functions.** Symptom: `.c` files generated from `.xs` fail to
   compile, referencing `setproctitle`, `kevent` extensions, etc.
   Fix: patch the `.xs` source to gate the affected blocks with
   `#ifdef __FreeBSD__` (no DragonFly equivalent) or extend to
   `defined(__FreeBSD__) || defined(__DragonFly__)` when the
   function exists on both.

2. **Perl version mismatch** — port pins a perl version
   (`PERL_VERSION=5.36+`) that doesn't match the installed perl.
   Symptom: `Perl v5.x.x required` at Makefile.PL time. Fix:
   bump `USES=perl5:run` to the right requirement, or update the
   dependency.

3. **`Cwd::abs_path` failing on missing path** during install.
   Symptom: post-install rename or symlink fails. Usually an
   upstream bug; patch the install script.

4. **DESTDIR / PREFIX handling** — perl Makefile.PL ports often
   need explicit `MAKE_ENV=PERL5LIB=${WRKSRC}/blib/lib` or
   similar.

5. **Test failures on `make test`** that don't reflect runtime
   problems (date/time, locale, networking-dependent tests).
   Fix: skip the failing test files via patch (`t/<name>.t`) or
   override `TEST_ENV` to disable network tests.

## Quick wins

- `USES=perl5` defaults to runtime-only; if the port needs
  build-time perl too, use `USES=perl5:build,run`.
- `BROKEN_DRAGONFLY=` is the heavy-hammer; only use after
  upstream patches don't apply.

## Logs to read first

- The first `cc` invocation in the failure block — XS errors
  point at line numbers in the generated `.c`, which the LLM
  can map back to the original `.xs` via the line tags.
- `Makefile.PL` output before "Generating Makefile" — version
  mismatches show up here, often misleadingly far from the
  build-time symptom.
