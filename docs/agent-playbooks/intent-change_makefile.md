---
triggers:
  intents: [change_makefile]
  flows: [patch]
tags: [mk-var, USES, CFLAGS]
priority: 50
---

# change_makefile — set / append / remove / unset a Makefile variable

## When to use

The port's Makefile needs a variable assignment changed: add a
USES module, append a CFLAGS flag, drop a stray definition,
**delete an upstream assignment that's wrong for our target**. The
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

## The four `op` values

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
  taking something OUT of a list, NOT for deleting the variable.
- **`op: "unset"`** — emits `mk unset VAR`. **Deletes the variable's
  entire assignment line from the composed Makefile**, including
  whatever upstream FreeBSD had. Symmetric inverse of `set`:
  - `mk set FOO "bar"` finds upstream's `FOO=...` line and replaces it
  - `mk unset FOO` finds upstream's `FOO=...` line and deletes it
  - The `value` field is ignored on unset (and may be omitted from
    the wire payload; the JSON-schema makes it optional for this op).

  Use `unset` when the fix is "this upstream-set variable must not be
  present on DragonFly" — e.g. `LICENSE_FILE=${PORTSDIR}/COPYRIGHT`
  references a file absent from our tree, and the BSD2CLAUSE license
  template makes the assignment unnecessary anyway.

## When to pick unset vs set-to-empty vs add_patch

For "the upstream assigns a variable that shouldn't be present":

- **`op: "unset"`** is the right answer almost always — clean, atomic,
  single dops line, no patch file, no risk of accidentally leaving
  `FOO=` in the Makefile (which is NOT equivalent to FOO not being
  defined: some framework code does `.if defined(FOO)` and an empty
  string still counts as defined).
- **`op: "set"` with an empty value** is almost never what you want.
  It writes `FOO= ` to the Makefile, which keeps `.if defined(FOO)`
  true. If you accidentally pick this for the LICENSE_FILE case,
  the framework still tries to read the empty path and fails.
- **`add_patch` with a unified diff deleting the line** also works
  but is heavier — a whole patch file plus a `patch apply` directive
  for what is structurally a single-variable edit. Reserve `add_patch`
  for cases where the change spans multiple variables, recipes, or
  isn't expressible as a single `mk` op.

## Worked example — drop an upstream assignment

A port that fails with `Missing license file for BSD2CLAUSE in
/xports/COPYRIGHT` because upstream FreeBSD set
`LICENSE_FILE=${PORTSDIR}/COPYRIGHT` and that file doesn't exist
on DragonFly:

```json
{
  "type": "change_makefile",
  "path": "Makefile",
  "key": "LICENSE_FILE",
  "op": "unset"
}
```

Produces, in `overlay.dops`:

```
mk unset LICENSE_FILE
```

At compose time, the executor deletes the `LICENSE_FILE=...` line
from the composed Makefile. The license-check then uses the
BSD2CLAUSE template default and the build proceeds.

## append semantics — create-or-append

Unlike a stricter interpretation, `op: "append"` on an undefined
variable does not refuse — it creates the assignment with the
token as its sole value. This matches make's `+=` operator and
means you do NOT need to pre-check whether the upstream Makefile
defines the variable before appending. Just append; the executor
handles both cases.

The executor handles three shapes of existing assignment, all
transparently:

- **No prior assignment** → creates `VAR= token` at the top of the
  Makefile (before the first `.include`/target).
- **One prior assignment, single line** → appends the token in place,
  preserving the original operator (`=`, `+=`, `?=`, etc.). `USES+=
  cmake` plus `op=append value=ssl` stays `USES+= cmake ssl`.
- **One prior assignment with line-continuation `\`, or multiple
  prior assignments** → leaves all existing lines untouched and
  appends a fresh `VAR+= token` line at the top insertion point.
  `make` flattens it into the accumulated value at evaluation time,
  so insertion position doesn't change the resulting value.

If the new token is already present in any matched assignment, the
op is a no-op (`mk-token-exists`); idempotent retries are safe.

## append adds; it does NOT override

`op: "append"` is for adding a **new** token to a list variable. It
is NOT a way to change the *value* of an existing key inside a
key-valued list like `PLIST_SUB`, `SUB_LIST`, `MAKE_ENV`.

Concretely: if upstream has `PLIST_SUB= OSMAJOR=${OSVERSION:...}`
and you append `PLIST_SUB+= OSMAJOR=${OSREL:R}`, both entries land
in the flattened `PLIST_SUB`. The framework then builds a sed
expression list with `-e s!%%OSMAJOR%%!.../g` repeated twice, and
**sed processes `-e` flags first-match-wins**: the first
substitution replaces every `%%OSMAJOR%%` occurrence, and the
second `-e` has nothing left to match. Result: the upstream
(broken) value silently wins; your "override" is dead code.

For genuine "change an existing key's value" cases there is no
`change_makefile` op today — you need a precision Makefile edit
(future `replace_in_makefile` intent) or a patch on the generated
Makefile.

## Scoping

Accepts an optional `scope` field: `"@any"` (default — the variable
edit applies on every build line) or `"@current"` (applies only on
the build line you're running on). Reach for `@current` only when
the fix is genuinely build-line-specific — e.g. a USES value
deprecated between quarterly snapshots that older build lines still
need. DragonFly-vs-FreeBSD differences in Makefile assignments are
universal; default to `@any`. See `intent-scoping.md` for the
cross-cutting rules.

### One operator-visible behavior note for `op=set`

Re-emitting `op=set` for the same key accumulates lines on disk.
The composed Makefile is still correct (the engine plays ops in
declaration order, last-wins), but `overlay.dops` carries every
re-emission. This changed with Step 38e — pre-38e an implicit
prefilter scrubbed prior `mk set KEY` lines; the prefilter was
removed because it was scope-blind and would have corrupted
multi-target overlays. Until an explicit "delete a prior mk set
line" intent lands (tracked in
`docs/intent-surface-gaps-plan.md`), repeated `op=set` produces
visible substrate noise that doesn't affect correctness.

`op=append` and `op=remove` have always accumulated (multiple
`mk add` / `mk remove` are semantically distinct list operations);
38e didn't change those. `op=unset` is also unchanged — it has
always been plain-append.

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

- `op: "remove"` against an undefined variable → `ok=false`,
  `assignment not found`. If your goal is to make the variable
  go away entirely, you wanted `op: "unset"`, not `"remove"`.
  If you wanted to drop a token from a list and the list doesn't
  exist, the change is a no-op — leave it alone.
- `op: "remove"` of a token not present in the list → executor
  surfaces `token not found`. Idempotent retry is fine; check the
  substrate via `get_file overlay.dops` before re-emitting.
- `op: "unset"` against an undefined variable → `ok=false`,
  `assignment not found`. Strict behavior — the variable must
  actually be assigned somewhere in the composed Makefile to be
  unset. If upstream stopped setting the variable between releases,
  the unset becomes a no-op and you may safely drop the intent
  on the next iteration.
- Ambiguous match (multiple assignments to the same variable) on
  `op: "set"`, `"unset"`, or `"remove"` → `ok=false` with
  `E_APPLY_AMBIGUOUS_MATCH`. The executor refuses to pick one of
  several upstream assignments to rewrite. Resolve by editing the
  source overlay manually or escalating. (`op: "append"` does
  NOT refuse on multi-assignment — see "append semantics" above.)
