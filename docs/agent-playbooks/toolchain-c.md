---
triggers:
  toolchains: [c]
  flows: [triage, patch]
tags: [c, clang, compiler]
priority: 110
---

# C / C++ compiler — usual suspects on DragonFly

This catch-all fires for any port whose Makefile declares
`USES=compiler:<std>` (e.g. `c11`, `c++17-lang`) or otherwise
flags itself as a native C/C++ port. DragonFly's default C
compiler is clang; most pattern matches against the broader
clang-versus-gcc landscape apply.

## Usual suspects (likelihood-ordered)

1. **`-Werror` turning warnings into errors** on DragonFly's
   clang that don't fire on the port's reference compiler.
   Symptom: build fails on a warning the project doesn't even
   acknowledge. Fix: `CFLAGS+=-Wno-error=<warning>` (preferred —
   keeps the warning visible) or `CFLAGS+=-Wno-<warning>`
   (silences entirely).

2. **C/C++ standard mismatch.** Symptom: "no matching function"
   or "<type> is not a member of std" on code that compiled on
   the reference platform. Cause: project assumes a newer
   standard than the Makefile declares. Fix:
   `USES=compiler:c++17-lang` (or appropriate level); the
   framework sets `CXXFLAGS=-std=c++17` consistently.

3. **`__attribute__((...))` not recognized.** Most GCC attributes
   work on clang but a few don't (`returns_twice`, `error`,
   some target-specific ones). Symptom: warning escalates to
   error via `-Werror`. Same fix as #1.

4. **`__builtin_*` functions** with different signatures. Rare
   but spectacular when it happens. Fix: patch the call site
   with an `#ifdef __clang__` branch.

5. **Linker script differences.** DragonFly's `ld` (lld via
   binutils alternative) handles some GNU ld extensions
   differently. Symptom: link succeeds but produces a broken
   binary, or fails on `--whole-archive` placement. Patch the
   link line via `LDFLAGS` overrides.

6. **BSD type names invisible under strict POSIX flags.**
   Symptom: `error: unknown type name 'u_short' | 'u_int' |
   'caddr_t'` from inside a DragonFly system header, with build
   command carrying `-D_POSIX_C_SOURCE=...` and/or
   `-D_XOPEN_SOURCE=...`. See `error-bsd-types-visibility.md`
   for the diagnosis chain and ranked fixes. **Never** reach for
   `-D__BSD_VISIBLE` (or any `__`-prefixed feature-test macro) —
   those are libc internals and live in the C-standard reserved
   namespace. Use the user-facing `_BSD_SOURCE` or, better,
   remove the restricting POSIX flag from upstream's Makefile.

## Quick wins

- `CFLAGS+=-fcommon` to restore pre-clang-15 behavior for
  duplicate-symbol issues from un-extern'd globals.
- `STRICT_DEPENDS=yes` is the framework default; flipping it
  isn't usually the right answer (it hides real bugs).

## Logs to read first

- The first `error:` line and the 5 lines above it — clang's
  diagnostic is dense; the underline indicates the problem
  location, the surrounding lines carry context.
- `cc -v` output (from a manual reproduction in WRKSRC) if the
  compiler invocation itself looks suspicious.
