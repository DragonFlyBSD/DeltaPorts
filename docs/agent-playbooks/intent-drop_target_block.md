---
triggers:
  intents: [drop_target_block]
  flows: [patch]
tags: [heredoc, mk-target, delete, block, overlay-cleanup]
priority: 45
---

# drop_target_block — remove an entire `mk target` heredoc block

## When to use

The overlay declares a target via `mk target set <name> <<TAG ...
TAG` (or `mk target append <name> <<TAG ... TAG`) and the whole
block should go — opening line, recipe body, and closing tag. Use it
when a target recipe is structurally wrong, obsolete, or was
produced by convert and shouldn't exist at all.

This is the block-level delete. Place it among its neighbours:

- **`drop_mk_directive`** removes a single `mk set/add/...` line.
- **`replace_in_dops_block`** edits text *inside* a block's body.
- **`drop_target_block`** removes the *whole* block.

Do **not** use when:
- The block is mostly right but one line in the body is wrong → edit
  it in place with `replace_in_dops_block`. Removing and rebuilding a
  block you could repair is wasteful and loses the rest of the
  recipe.
- You want to remove a top-level `mk` variable line → that's not a
  block; use `drop_mk_directive`.

## Block resolution

`block_name` matches the name token on the `mk target <action>
<name> <<TAG` opening line. Action may be `set` or `append`
(`rename` has no body and is never matched). The block extends from
the opening line through the line matching exactly `TAG` (the
heredoc closing tag — `MK`, `MK1`, etc., chosen by the producer);
tag quoting (`<<'MK'`) is tolerated. The whole extent, tags
included, is removed.

Adjacent blank lines are left as-is — the intent removes exactly the
block, no implicit cleanup of surrounding whitespace (cosmetic
blanks don't affect compose).

## Example

```json
{
  "type": "drop_target_block",
  "block_name": "do-build",
  "reason": "recipe overrode the default build with a command that no longer exists upstream"
}
```

The executor locates the `mk target set do-build <<TAG ... TAG`
block at the resolved scope and removes it. The `reason` is
operator-readable and lands in `analysis/intent_log.json`.

## Scoping

Accepts an optional `scope` field: `"@any"` (default — searches the
universal section) or `"@current"` (the build line you're running
on, resolved from the env). You never type a literal quarter
selector.

Scope matters here specifically: the engine **allows the same block
name under different sections** (a `do-build` block under `@any` and
another under a quarterly target are both legal). The locator filters
by the resolved scope, so a scoped drop removes the block in that
section and leaves same-name blocks in other sections untouched. If
two blocks share a name *within the same scope*, the drop refuses as
ambiguous — disambiguate via scope or hand-edit. See
`intent-scoping.md`.

## Failure modes the executor refuses

- **Zero matches** → `ok=false`. No block by that name at the
  resolved scope; your model of the substrate is wrong. Read
  `get_file overlay.dops` and check the exact name and section.
- **Multiple matches at the same scope** → `ok=false`, ambiguous;
  the executor will not guess which block to remove.
- **No overlay** → `ok=false`.
- **`scope: "@current"` with no env target** → `ok=false` with an
  escalate hint (calling-context bug).
- **Unbounded block (open line with no closing tag)** → `ok=false`,
  the overlay is corrupt. The executor refuses to remove "to end of
  file" on a guess; a corrupt overlay needs a human-authored fix.
