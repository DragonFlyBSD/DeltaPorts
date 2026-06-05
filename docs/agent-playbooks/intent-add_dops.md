---
triggers:
  intents: [add_dops]
  flows: [patch]
tags: [dops, overlay, heredoc, escape-hatch]
priority: 60
---

# add_dops — append a raw dops statement to `overlay.dops`

## When to use

You need to add a directive to `overlay.dops` that **no bespoke
intent expresses**. `add_dops` is the generic additive escape hatch:
it appends one (or more) raw dops statements to the overlay, places
them under the right `target` scope, then re-validates the whole
overlay against the engine grammar before accepting.

Reach for it when the structured intents don't cover the directive:
conditional control blocks, `file` directives with no `add_file`
shape, standalone `mk target` heredocs, or any new engine grammar
that post-dates the intent catalog.

Do **not** use it when a structured intent already fits — prefer the
dedicated one, which carries field-level recipes and failure-mode
guidance `add_dops` can't:

- A Makefile variable set/append/remove/unset → `change_makefile`.
- A new patch (inline diff or from a dupe) → `add_patch`.
- A port-local resource or `file materialize` → `add_file`.
- Editing *inside* an existing heredoc body → `replace_in_dops_block`.
- Bumping PORTREVISION → `bump_portrevision`.

`add_dops` is the right tool precisely when none of the above match.

## Input shape

```json
{
  "type": "add_dops",
  "dops": "mk set FOO \"bar\"",
  "scope": "@any"
}
```

- **`dops`** — a complete dops statement in engine grammar. Either a
  single line, or a whole heredoc block (opener through terminator).
- **`scope`** — `"@any"` (default; applies on every build line) or
  `"@current"` (resolves to the env's current target at apply time).
  Never type a literal `@2026Q2`; the runner injects the target.
  See `intent-scoping.md` for the cross-cutting rules.

## Heredoc blocks — exact form

The engine requires the **quoted** opener `<<'TAG'` and a terminator
line holding the **bare** tag, alone, with no leading or trailing
whitespace:

```
mk target set post-patch <<'MK1'
${REINPLACE_CMD} -e 's,/usr/local,${PREFIX},' ${WRKSRC}/Makefile
MK1
```

- Opener must be `<<'TAG'` (quoted) — a bare `<<TAG` is rejected with
  `E_PARSE_INVALID_HEREDOC_START: expected <<'TAG'`.
- The terminator line must equal the tag exactly (`MK1`, not `  MK1`
  or `MK1 `). Anything else and the block never closes.

Pass the whole block as one `dops` string; it lands as a unit, and the
section scan correctly skips `target`-looking lines inside the body.

## One statement per call (prefer)

Prefer **one statement per `add_dops` call**. Each call re-validates
the overlay and rolls back independently, so a single bad statement is
rejected on its own with a precise diagnostic. A multi-statement `dops`
payload is accepted — the whole append is atomic (all-or-nothing) — but
you lose per-statement granularity: one invalid line rolls back the
entire batch.

## Validation + rollback

After placement, the **entire** `overlay.dops` is re-checked through
the engine (`check_dsl`: lex → parse → document-level semantics). On
failure the append is undone — prior bytes restored, or the file
removed if this intent created it — and `ok=false` is returned with the
engine's diagnostic (code, message, line, column).

Two distinct failure shapes, so you know whether to retry:

- **Your statement is invalid** → `add_dops produced an invalid
  overlay; statement rejected by the engine: <diagnostic>`. Fix the
  statement (grammar, heredoc form, a duplicate document directive like
  a second `port`/`type`) and re-emit.
- **The overlay was already broken before your edit** → `overlay.dops
  was already invalid before this add_dops (pre-existing breakage —
  escalate, do not retry the statement)`. Your statement is not the
  problem; re-emitting it won't help. Escalate.

## Failure modes

- **Malformed grammar** (bad keyword, unbalanced heredoc, wrong opener)
  → rejected at validation, append rolled back. The diagnostic names
  the line and column.
- **Duplicate document directive** — `add_dops` of a second `port`,
  `type`, `reason`, or `maintainer` line → semantic error
  (`E_SEM_*`), rolled back. These are once-per-document.
- **`@any` with a selector** that the engine forbids →
  `E_SEM_INVALID_TARGET_SCOPE`, rolled back.
- **`@current` with no env target** → refused before placement (the
  runner has no target to resolve). Only emit `@current` on a job
  that's running on a concrete build line.
