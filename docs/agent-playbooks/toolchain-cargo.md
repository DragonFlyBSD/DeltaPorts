---
triggers:
  toolchains: [cargo]
  flows: [triage, patch]
tags: [cargo, rust, build-rs]
priority: 100
---

# Cargo (Rust) — usual suspects on DragonFly

The port uses `cargo build --release` (or `cargo install`), often
with vendored dependencies in `cargo-crates/`. Failures cluster
around `build.rs` scripts, `cfg(target_os = ...)` gates, and
linker issues.

## Usual suspects (likelihood-ordered)

1. **`#[cfg(target_os = "freebsd")]` excluding DragonFly.**
   Symptom: function/struct/feature missing at compile time on
   DragonFly. Fix: patch the cfg to
   `#[cfg(any(target_os = "freebsd", target_os = "dragonfly"))]`
   or add a parallel `#[cfg(target_os = "dragonfly")]` block.

2. **`build.rs` scripts hardcoding FreeBSD paths or commands.**
   Symptom: build.rs panic, often about a missing system header
   or library path. The build script runs at build time and
   produces compile flags; if it doesn't know DragonFly, the
   downstream compile fails. Fix: patch `build.rs` to query
   `env::var("CARGO_CFG_TARGET_OS")` and branch on dragonfly.

3. **`libc` crate version pin too old** for DragonFly's syscall
   surface. Symptom: missing constants or fn signatures from
   `libc::*`. Fix: bump the libc dep (often in vendored
   `Cargo.lock`); upstream usually has DragonFly support in
   current versions.

4. **Linker errors against system libraries.** Symptom:
   `cannot find -l<name>`. Cause: `cargo:rustc-link-lib=<name>`
   in build.rs without DragonFly path discovery. Fix:
   `MAKE_ENV=RUSTFLAGS='-L /usr/local/lib'` or similar; or patch
   build.rs.

5. **Network access during build** — `cargo fetch` against
   crates.io. Ports framework should preempt this via
   `CARGO_CRATES`, but a misconfigured port may still try.
   Symptom: timeout or network error early in build. Fix:
   `cargo-crates` listing in the Makefile must cover every
   transitive.

## Quick wins

- `CARGO_BUILD_JOBS=1` to serialize when parallel builds OOM the
  builder. Rustc is memory-hungry.
- `cargo tree` (in WRKSRC, manually) to map missing crates back
  to their declaration.

## Logs to read first

- The `error[E####]:` line — rustc errors are precise; the
  diagnostic + spans usually point at the exact line.
- `build.rs` output is captured under
  `target/release/build/<crate-hash>/output`; if a build script
  fails, the panic message lives there.
