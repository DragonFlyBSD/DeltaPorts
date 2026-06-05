---
triggers:
  intents: [drop_mk_directive]
  flows: [patch]
tags: [mk-var, delete, undo, overlay-cleanup]
priority: 50
---

# drop_mk_directive — remove a single `mk` line from the overlay

## When to use

The overlay carries an `mk set/unset/add/remove VAR` directive that
should not be there. The symmetric inverse of `change_makefile`:
where that intent *writes* an `mk` line into `overlay.dops`, this one
*removes* the matching line. Reach for it when:

- You emitted a `change_makefile` (or convert produced one) that is
  now wrong, and you want it gone — not countered by a second op.
- An `mk add VAR "token"` accumulated in the overlay and the token
  should no longer be added at all (dropping the `add` line is
  cleaner than emitting a `remove` that leaves both lines on disk).
- A stray `mk set VAR "value"` is overriding something it shouldn't.

This is a **substrate edit**, not a Makefile-value edit. It deletes a
line from `overlay.dops`; it does not reach into the composed
Makefile directly.

Do **not** use when:
- You want to take a token *out of* a list assignment that should
  still exist → `change_makefile op=remove` (emits `mk remove`,
  which is itself a directive you could later drop).
- The directive you want gone is an install line (`file copy` /
  `file materialize`) → `drop_file`.
- The thing to remove is a whole `mk target <name> <<TAG ... TAG`
  heredoc block → `drop_target_block`.

## `kind` selects the line shape

`kind` must match the directive shape on disk; the executor matches
the exact line, scope-filtered:

```json
{
  "type": "drop_mk_directive",
  "kind": "add",
  "key": "USES",
  "value": "pkgconfig"
}
```

- **`kind: "set"`** — matches `mk set KEY ...` by **key alone**. The
  `value` field is ignored and may be omitted; you don't have to echo
  the exact on-disk value to remove a `set` line.
- **`kind: "unset"`** — matches `mk unset KEY` exactly. `value`
  ignored.
- **`kind: "add"`** — matches `mk add KEY "value"` exactly. The
  `value` token **must** match, quoted the same way `change_makefile`
  emits it. This is how you target one token among several adds.
- **`kind: "remove"`** — matches `mk remove KEY "value"` exactly.
  Same value-matching rule as `add`.

The key boundary is whole-token: `key: "USE"` will not match an
`mk set USES ...` line.

## Scoping

Accepts an optional `scope` field: `"@any"` (default — searches the
universal section) or `"@current"` (searches only the build line
you're running on, resolved from the env at apply time). You never
type a literal quarter selector; the schema rejects it.

Scope is also the **disambiguation lever**: if the same directive
exists under two sections (e.g. `@any` and `@2026Q2`), an unscoped
drop sees one match per section and the scoped drop targets exactly
one. See `intent-scoping.md`.

## Failure modes the executor refuses

- **Zero matches** → `ok=false`. The line not existing means your
  model of the substrate is wrong; the executor refuses rather than
  silently no-op'ing. Read `get_file overlay.dops` (or
  `get_effective_overlay`) and check the exact line shape, key
  spelling, and `value` quoting before re-emitting.
- **Multiple matches at the same scope** → `ok=false`, ambiguous.
  The executor will not guess which of several identical lines to
  remove. Disambiguate via `scope`, or hand-edit. (Note: identical
  accumulated lines refuse the same way distinct ones do — behavior
  does not branch on whether the duplicate lines happen to be
  byte-identical.)
- **No overlay** → `ok=false`; there's nothing to remove from.
- **`scope: "@current"` with no env target** → `ok=false` with an
  escalate hint; a calling-context bug, retrying won't help.

This intent only ever removes one line. It never reorders sections
or repairs the `@any`-first invariant — it does exactly one thing.
