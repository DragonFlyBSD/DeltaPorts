---
triggers:
  intents: [change_makefile]
  flows: [patch]
tags: [mk-var, USES, CFLAGS]
priority: 50
---

# change_makefile — set / append / remove a Makefile variable

## When to use

The port's Makefile needs a variable assignment changed: add a
USES module, append a CFLAGS flag, drop a stray definition. The
intent records the change in `overlay.dops` as an `mk` directive;
compose applies it against the materialized Makefile at
materialize time.

Do **not** use when:
- The change is to a recipe body (multi-line shell inside a target
  like `post-patch:` or `dfly-patch:`) → that's an `mk target set`
  in the overlay; use `replace_in_dops_block` to edit the heredoc
  body.
- The "Makefile" you want to edit is actually a patch file under
  `dragonfly/` → use `replace_in_patch`.

## The three `op` values

```json
{
  "type": "change_makefile",
  "path": "Makefile.DragonFly",
  "key": "USES",
  "value": "pkgconfig",
  "op": "append"
}
```

- **`op: "set"`** — emits `mk set VAR "value"`. Creates the
  assignment if absent; replaces if present. Use for variables
  that take a single value (PORTREVISION, GNU_CONFIGURE, etc.) or
  when you want to overwrite a list entirely.
- **`op: "append"`** — emits `mk add VAR "value"`. Mirrors make's
  `+=` semantics: creates the assignment if VAR isn't defined,
  appends the token if it is. Use for list-shaped variables (USES,
  CFLAGS, LIB_DEPENDS, etc.).
- **`op: "remove"`** — emits `mk remove VAR "value"`. Removes the
  token from a list assignment. The variable MUST exist (executor
  refuses with `assignment not found` otherwise); `remove` is for
  taking something OUT of a list, not for unsetting the variable.

## append semantics — create-or-append

Unlike a stricter interpretation, `op: "append"` on an undefined
variable does not refuse — it creates the assignment with the
token as its sole value. This matches make's `+=` operator and
means you do NOT need to pre-check whether the upstream Makefile
defines the variable before appending. Just append; the executor
handles both cases.

## The `path` field

`path` is the Makefile-relative filename inside the port subtree.
Typically `Makefile.DragonFly`. In a fully-converted dops port,
the assignment lands in `overlay.dops` as an `mk` directive (the
file path is used by the engine for diagnostic context, not for
filesystem routing).

## Before guessing a variable's semantics, check the framework

The ports framework (`Mk/bsd.port.mk` and friends) is the source of
truth for what a Makefile variable means: how it's parsed, what
shape its value must take, whether it's iterated as a list, whether
embedded whitespace breaks something. The build error you'll get
back if you guess wrong is usually opaque (a `.for` substitution
arity mismatch, a missing-file error from a path you didn't
construct, a "Wrong number of words" message from `.for`).

Before emitting a `change_makefile` for a variable you don't
recognize — or whose **value shape** you're inferring rather than
reading — grep the framework first:

```
grep("^[[:space:]]*<VARIABLE>[[:space:]]*[+:?]?=", "/work/freebsd-ports/Mk")
grep("\\$\\{<VARIABLE>\\}|\\$\\(<VARIABLE>\\)", "/work/freebsd-ports/Mk")
grep("\\.for .* in .*<VARIABLE>", "/work/freebsd-ports/Mk")
```

The first grep finds the variable's *definition* and default. The
second finds *consumers* — where the value is referenced. The third
finds `.for` iteration, which is the tell that the value is parsed
as a whitespace-separated list and each entry must match a fixed
arity (e.g. `BINARY_ALIAS` is iterated as `.for entry in
${BINARY_ALIAS}` with each entry parsed as `key=value` — values
containing a space break the parse with "Wrong number of words").

Other framework trees worth grepping when a Makefile assignment
fails compose:
- `/work/freebsd-ports/Mk/Uses/*.mk` — per-USES module behavior
  (`USES=cmake` pulls in `Uses/cmake.mk`, etc.).
- `/work/freebsd-ports/Mk/Features/*.mk` — feature toggles.
- `/work/freebsd-ports/Mk/Scripts/*.sh` — helpers some framework
  variables hand to a shell with quoting rules of their own.

Cheap reads beat expensive guesses. A failed `apply_intent +
materialize + dsynth_build` cycle is several thousand tokens; a
grep into `Mk/` is a few hundred and usually answers the question.

## Failure modes

- `op: "remove"` against an undefined variable → `ok=false`. Make
  the assignment exist via `op: "set"` first, or just leave it
  alone if removal is best-effort.
- `op: "remove"` of a token not present in the list → executor
  surfaces `token not found`. Idempotent retry is fine; check the
  substrate via `get_file overlay.dops` before re-emitting.
- Ambiguous match (multiple assignments to the same variable) →
  `ok=false` with `E_APPLY_AMBIGUOUS_MATCH`. Resolve by editing
  the source overlay manually or escalating.
