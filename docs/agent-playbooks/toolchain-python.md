---
triggers:
  toolchains: [python]
  flows: [triage, patch]
tags: [python, setup-py, pyproject, pep517]
priority: 100
---

# Python — usual suspects on DragonFly

The port uses one of: `setup.py` (legacy), `pyproject.toml` (PEP
517 / 518), or both. Build backends vary: setuptools, flit, poetry,
hatchling, scikit-build. Failures cluster around C extension
modules and PEP 517 build-environment isolation.

## Usual suspects (likelihood-ordered)

1. **C extension module compile failure** referencing FreeBSD-only
   headers/functions. Same shape as the autoconf case — see
   [error-freebsd-only-features](error-freebsd-only-features.md).
   Patch the `.c` source under `dragonfly/` to extend the ifdef
   to DragonFly.

2. **PEP 517 build isolation hiding the real dependency.**
   Symptom: `python -m build` runs in a venv with only declared
   deps; an undeclared transitive (e.g. `Cython`) is missing.
   Fix: `USE_PYTHON=pep517` keeps isolation; pass
   `PYTHON_NO_DEPENDS=yes` and add the missing dep to
   `BUILD_DEPENDS` explicitly.

3. **`setup.py` checking `sys.platform` against `'freebsd*'` and
   not `'dragonfly*'`.** Symptom: features disabled. Fix: patch
   `setup.py` to also match DragonFly, or set the relevant env
   var (often `FORCE_<FEATURE>=1`).

4. **`distutils` removal in Python 3.12+.** Symptom: ports with
   legacy `setup.py` fail with `ModuleNotFoundError: distutils`.
   Fix: add `setuptools` as a build dep, or have the port pin to
   `python:3.11` if upstream hasn't migrated.

5. **`--prefix` vs `--root` confusion.** Symptom: files install
   to wrong directory. The framework sets these; if the port
   overrides via `setup.cfg`, patch it.

6. **Native dependencies missing pkg-config**. See
   [toolchain-pkg-config](toolchain-pkg-config.md). Many Python
   packages link against `libssl`, `libffi`, etc. via
   pkg-config; the lookup logic varies by build backend.

## Quick wins

- `PYTHON_VERSION=` to pin a specific minor (3.11 vs 3.12) when
  the failure looks version-driven.
- `USE_PYTHON=cython` declares a cython build-dep automatically;
  handy when a port's `pyproject.toml` calls cython without
  declaring it.

## Logs to read first

- The full traceback when present — Python errors are usually
  diagnostic; the first frame is often misleading, the last is
  the real cause.
- For C extension fails: search for `gcc -DNDEBUG` or
  `clang -DNDEBUG` invocations; the actual compile command is
  there with the failing line.
