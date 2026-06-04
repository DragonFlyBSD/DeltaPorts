---
triggers:
  intents: [add_file]
  flows: [patch]
tags: [resource, materialize]
priority: 50
---

# add_file — add a port-local file (resource or materialized)

## When to use

The port needs a file that isn't a patch — a `pkg-message`
fragment, an OS-specific script, a config snippet copied into the
build tree. Two kinds depending on where the source lives.

Do **not** use when:
- The file is a patch under `dragonfly/` → `add_patch` instead.
- The destination starts with `Makefile.DragonFly` → refused
  (would re-create the half-migrated substrate the runner just
  resolved). Use `change_makefile` to express Makefile.DragonFly-
  shaped variable edits as `mk` directives in `overlay.dops`.

## Two kinds

### `kind: "resource"` — content you supply inline

You provide the file's content directly in the `content` field;
the translator writes it to the port subtree AND emits a `file
copy` directive so compose copies it into the materialized tree.

```json
{
  "type": "add_file",
  "dest": "files/pkg-message.dragonfly",
  "kind": "resource",
  "content": "This port runs in a chroot on DragonFly; see ..."
}
```

### `kind: "materialize"` — source lives in the dragonfly/ tree

Use when the file already exists in the overlay (e.g. you put it
there via a separate `put_file` to a non-port-subtree path) and
you want compose to materialize it into the build tree.

```json
{
  "type": "add_file",
  "dest": "dragonfly/extra-file.h",
  "kind": "materialize",
  "source": "dragonfly/extra-file.h"
}
```

The translator emits a `file materialize <source> -> <dest>`
directive in `overlay.dops`.

## Scoping

Accepts an optional `scope` field: `"@any"` (default — file installs
on every build line) or `"@current"` (file installs only on the
build line you're running on). Use `@current` when the file is
genuinely build-line-specific (a config snippet that only makes
sense against this snapshot's framework, etc.). Most port-local
resources are universal — default to `@any`. See
`intent-scoping.md` for the cross-cutting rules.

## Path safety

All `dest` values must be port-subtree relpaths — no `..`, no
leading `/`, no absolute paths. The translator validates this
before any write; intents that try to escape the port subtree are
refused with a clear error.

## Failure modes

- Resource `dest` already exists → `ok=false`. The intent is
  additive; use `put_file` to a non-port-subtree path + a separate
  intent if you genuinely need to replace, or escalate.
- `dest` resolves outside the port subtree → refused.
- `dest` starts with `Makefile.DragonFly` → refused (substrate
  invariant; would re-create the half-migrated state).
- `kind: "materialize"` with a `source` that doesn't exist in the
  port tree → compose will fail at materialize time, not at the
  intent layer (the intent is descriptive; compose validates).
