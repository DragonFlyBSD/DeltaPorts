---
triggers:
  intents: [add_patch]
  flows: [patch]
tags: [new-patch, dupe, genpatch]
priority: 50
---

# add_patch — introduce a new patch under dragonfly/

## When to use

A new failure exists on the current target that needs a new
upstream-source patch (or a new framework-side `diffs/*.diff`). The
overlay doesn't yet carry anything addressing it.

Do **not** use when:
- A logically-similar patch already exists and a single hunk
  drifted → `replace_in_patch`.
- The whole-file replacement is in scope and the existing patch is
  obsolete → `drop_patch` + `add_patch` in sequence.

## Two ways to supply the diff

### Inline (`diff` field)

You produce the unified diff yourself and pass it inline:

```json
{
  "type": "add_patch",
  "target": "dragonfly/patch-src_foo.c",
  "diff": "--- src/foo.c.orig\n+++ src/foo.c\n@@ -10,3 +10,3 @@\n ...\n"
}
```

Use this when the change is small and you can write the diff by
hand from prior `get_file` reads. The translator writes the diff
to disk and emits a `patch apply` statement.

### From a dupe-edited source (`from_dupe=true`)

For changes that are easier to make by editing the source file
directly than by hand-writing a diff. This is the canonical
workflow for non-trivial source patches:

1. `extract(origin)` — populates `WRKSRC`.
2. `dupe(<extract.wrksrc>/path/to/file.c)` — creates a `.orig`
   snapshot and exposes the file for editing.
3. `put_file <extract.wrksrc>/path/to/file.c <new content>` — edit
   the file. `put_file` to a WRKSRC path is allowed; to
   `ports/<origin>/` it is not.
4. `genpatch(<same path>)` — runs `diff -u` between the `.orig`
   and current content, deposits the result inside WRKSRC with a
   clean WRKSRC-relative name (e.g.
   `patch-src_include_foo.h`). The runner picked up WRKSRC from
   the prior `extract()` call automatically — no extra arg needed.
5. `apply_intent({type: "add_patch", target: "dragonfly/patch-...",
   from_dupe: true})` — the translator walks WRKSRC for the patch
   file matching the target's basename and stages it under
   `ports/<origin>/dragonfly/`.

## `dupe` is only step 1 of this flow

`dupe` exists exclusively to support `add_patch(from_dupe=true)`.
It is NOT:
- An investigation tool ("let me snapshot before reading")
- A way to take a "before" picture for diffing
- A tool for modifying an **existing** dragonfly/patch-* (use
  `replace_in_patch` instead)

A `dupe` call without a follow-up `add_patch(from_dupe=true)` in
the same attempt is wasted work and a strong signal you've reached
for the wrong tool.

## Failure modes

- Target already exists → `ok=false`. Use `replace_in_patch` to
  modify it, or `drop_patch` + `add_patch` to replace it.
- `from_dupe=true` but no matching `patch-*` file in WRKSRC →
  `ok=false`. Did `genpatch` actually run, and did `extract` run
  before it so WRKSRC was populated? Check `genpatch`'s return —
  `patch_basename` / `patch_path` are populated on success and
  null otherwise.
- Empty diff content (inline or from_dupe) → `ok=false`. A
  no-content patch is never the right answer.
