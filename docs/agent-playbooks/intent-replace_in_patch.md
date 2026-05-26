---
triggers:
  intents: [replace_in_patch]
  flows: [patch]
tags: [drift-fix, hunks]
priority: 50
---

# replace_in_patch — fix a drifted hunk inside an existing patch

## When to use

Single-line drift inside `dragonfly/patch-*` against the upstream
source: a context line changed upstream, a function name moved, an
ifdef guard shifted by one tab. The patch still represents the
right logical change; one line in the file no longer matches.

Do **not** use when:
- The patch is structurally obsolete → `drop_patch`.
- Whole hunks need rewriting → `drop_patch` + `add_patch`.
- The change is inside a `mk target set` heredoc body in
  `overlay.dops` → `replace_in_dops_block`.

## Multi-hunk drift in the same patch

When `dsynth_log` reports `N out of M hunks failed` against one
patch file, identify **every** drifted hunk before emitting any
intent. Apply-build-diagnose-apply per hunk burns 10+ turns
rediagnosing what one careful read catches.

Procedure:

1. `get_file` the failing `dragonfly/patch-*` once.
2. `grep` each hunk's context lines against the extracted upstream
   source in one pass.
3. For each drifted hunk, emit one `replace_in_patch` intent.
4. `materialize_dports` + `dsynth_build` once at the end.

## Example

Given a patch hunk whose context line shifted from
`if (foo->bar == 0)` to `if (foo->bar == NULL)` upstream:

```json
{
  "type": "replace_in_patch",
  "target": "dragonfly/patch-src_foo.c",
  "find": "if (foo->bar == 0)",
  "replace": "if (foo->bar == NULL)"
}
```

The translator appends a `text replace-once` statement to
`overlay.dops`; compose applies it at materialize time. The patch
file itself is not edited in place — the dops statement does the
substitution when the patch is staged.

## Failure modes the executor refuses

- `find` string not present in the target file → `ok=false`. Most
  often a sign the find string itself contains drift relative to
  the on-disk patch; re-read the patch and grep for the actual
  line.
- `find == replace` (no-op) → `ok=false`. If you meant to confirm
  a prior intent landed, check the substrate via `get_file`; don't
  re-emit.
- `occurrence` index out of range → `ok=false` with the available
  match count.
