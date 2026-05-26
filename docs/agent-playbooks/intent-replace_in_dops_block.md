---
triggers:
  intents: [replace_in_dops_block]
  flows: [patch]
tags: [heredoc, mk-target, dfly-patch]
priority: 40
---

# replace_in_dops_block — edit text inside an `mk target` heredoc body

## When to use

The overlay declares a target via `mk target set <name> <<TAG ...
TAG`, and you need to change the body content — typically a line
inside a multi-statement shell recipe (a `REINPLACE_CMD` for OS
detection, a `cp` of an extra file, etc.). This is the ONLY intent
that reaches into heredoc bodies; nothing else can.

Two common reasons to use it:

1. **Fix broken content inside the body.** Convert produced a body
   referencing `${WRKSRC}/../../path` when it should have been
   `${WRKSRC}/path`; the recipe runs but doesn't do what was
   intended.
2. **Extend the body with an additional line.** The recipe is
   correct as far as it goes, but a new file needs the same
   substitution. There is no separate "append to body" intent; you
   extend by replacing the body's last line with that line followed
   by your new line. (See pattern below.)

Do **not** use for:
- Editing a `dragonfly/patch-*` file → that's `replace_in_patch`.
- Editing a regular Makefile variable → `change_makefile`.

## Block resolution

The intent's `block_name` matches the second token of `mk target
<action> <name> <<TAG`. Action can be `set`, `append`, or
`remove`. The body runs until a line matching exactly `TAG` (the
heredoc closing tag, typically `MK`, `MK1`, etc. — chosen by the
producer). Tag quoting (`<<'MK'`) is tolerated.

## Fix-broken-line pattern

```json
{
  "type": "replace_in_dops_block",
  "block_name": "dfly-patch",
  "find": "${WRKSRC}/../../Makefile",
  "replace": "${WRKSRC}/Makefile"
}
```

`find` and `replace` operate inside the named block's body only;
text outside the block (other targets, top-level `mk` directives)
is untouched.

## Extend-body pattern

To add a new line to an existing recipe body, replace the last
existing line in the body with that line followed by the new line.
Pick a unique substring of the last line as `find`; include it
plus your new line in `replace`.

Given a body that ends with one `REINPLACE_CMD` and needs a second
covering an additional source file:

```
mk target set post-patch <<MK
        ${REINPLACE_CMD} -e 's/old-pattern/new-pattern/' \
                ${WRKSRC}/src/foo.c
MK
```

emit:

```json
{
  "type": "replace_in_dops_block",
  "block_name": "post-patch",
  "find": "${WRKSRC}/src/foo.c",
  "replace": "${WRKSRC}/src/foo.c\n\t${REINPLACE_CMD} -e 's/old-pattern/new-pattern/' \\\n\t\t${WRKSRC}/src/bar.c"
}
```

The newline + tab indentation in `replace` matches the body's
existing shell-recipe indentation so the rendered overlay stays
parseable as a Makefile target. The translator writes the result
verbatim; you control the formatting.

## Failure modes the executor refuses

- `block_name` not found → `ok=false` with the names of any
  similarly-named blocks in the overlay.
- `find` string not present in the body → `ok=false`. Check the
  exact body content via `get_file overlay.dops` first.
- `find == replace` (no-op) → `ok=false`. If you meant to confirm
  a prior intent landed, re-read the substrate; do not re-emit.
- `occurrence` requested exceeds matches → `ok=false` with the
  available count.
- Unbounded block (no closing tag in the overlay) → `ok=false`
  with `overlay.dops is corrupt`. The overlay itself needs a
  human-authored fix; the patch agent cannot recover from this.
