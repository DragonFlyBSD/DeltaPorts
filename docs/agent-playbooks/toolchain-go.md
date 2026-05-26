---
triggers:
  toolchains: [go]
  flows: [triage, patch]
tags: [go, golang, modules]
priority: 100
---

# Go — usual suspects on DragonFly

The port uses `go build` or `go install`, typically with a vendored
dependency tree or `go.mod`-driven module resolution. Failures
cluster around build constraints and CGO interactions.

## Usual suspects (likelihood-ordered)

1. **Build constraints excluding DragonFly.** Symptom: package
   compiles to an empty binary or "undeclared name" errors that
   the import claims to provide. Cause: `//go:build !dragonfly`
   or `// +build linux freebsd darwin` (no dragonfly). Fix:
   patch the file's build constraint to include `dragonfly`.

2. **CGO failures** linking against C libraries. Symptom:
   `ld: cannot find -l<name>` or undefined references during the
   final link. Cause: `#cgo LDFLAGS:` or `#cgo CFLAGS:` lines
   that hardcode FreeBSD paths. Fix: patch the cgo directive to
   use `pkg-config` or a portable path.

3. **`runtime.GOOS` checks** against `freebsd` that should also
   accept `dragonfly`. Symptom: feature panics with "unsupported
   platform" at startup or omits a code path silently. Fix:
   patch the runtime check.

4. **Module resolution failures** — `go mod` can't fetch a
   dependency. Usually network/GOPROXY-related, not DragonFly-
   specific. Fix: pre-populate `vendor/` or use a known-good
   proxy.

5. **`syscall` package gaps** — DragonFly's syscall ABI doesn't
   match FreeBSD exactly in some areas (kqueue extensions, jail
   syscalls). Patches usually need to live in `dragonfly/` and
   route DragonFly-specific calls through.

## Quick wins

- `GOFLAGS="-buildvcs=false"` to skip VCS stamping when the port
  isn't a git checkout.
- `CGO_ENABLED=0` for pure-Go ports that don't actually need
  CGO — sidesteps a whole class of linker failures.
- `GO_TARGET=...` in the port Makefile lists the targets to
  build; trim to what's actually shipped.

## Logs to read first

- The first error past `# <import path>` — go errors come in
  per-package blocks, the failing package is the one named in
  the `#` line.
- `go build -x` (verbose) reproduces the failing command if the
  port's `BUILD_TARGET` is unclear.
