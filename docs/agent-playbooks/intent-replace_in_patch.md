---
triggers:
  intents: [replace_in_patch]
  flows: [patch]
tags: [text-replace, in-port-files]
priority: 50
---

# replace_in_patch — single-occurrence text edit on a non-patch in-port file

## When to use

A file in the port subtree (under `ports/<origin>/`) that is NOT a
patch needs a small literal-text substitution: e.g. tweaking a
`pkg-descr` line, fixing a stray reference in `files/extra-config.in`,
adjusting a port-local resource file that has no dedicated edit
intent.

The translator appends a `text replace-once file <target> from "X"
to "Y"` statement to `overlay.dops`; compose performs the
substitution on the materialized file at compose time.

## Do NOT use for patch files

**The validator refuses any `target` starting with `dragonfly/`.**
Patch files are output artifacts produced by `add_patch`. Editing
a diff in place to nudge line numbers or context produces a patch
that lies about its own bytes — the hunk body shifts but the hunk
header does not, and the result silently corrupts at compose or
dsynth-apply time.

If a patch is failing, drifted, or otherwise wrong, the correct
recovery is in `intent-add_patch.md` under "Recovering from a
failed `add_patch`":

1. `drop_patch(target=dragonfly/patch-…, reason=…)` — removes both
   the install directive and the on-disk file.
2. `add_patch(target=dragonfly/patch-…, diff=<corrected diff>)`
   — or `from_dupe=true` to regenerate from a WRKSRC edit.

This was the anti-pattern that broke `devel_jwasm` in June 2026:
a malformed `add_patch` was followed by `replace_in_patch` calls to
"fix" the bad diff, producing a series of `text replace-once` ops
against a file that was never staged in the compose tree. Every
subsequent `materialize_dports` failed with `E_APPLY_MISSING_SUBJECT`.

## Also do NOT use for .dops files

The validator refuses any `target` ending in `.dops`. Edits to the
DSL go through `change_makefile`, `drop_patch`, `add_patch`, or
`replace_in_dops_block` (for heredoc bodies inside `mk target set`).

## Example

```json
{
  "type": "replace_in_patch",
  "target": "files/extra-config.in",
  "find": "platform=linux",
  "replace": "platform=dragonfly"
}
```

## Failure modes the executor refuses

- `target` starts with `dragonfly/` → `IntentError` at validation
  time. Use `drop_patch` + `add_patch` instead.
- `target` ends with `.dops` → `IntentError` at validation time.
  Use the appropriate DSL-edit intent.
- `find` string not present in the target file at compose time →
  `E_APPLY_MISSING_MATCH`. Re-read the target via `get_file` and
  grep for the actual line.
- `find == replace` (no-op) → `ok=false`. If you meant to confirm
  a prior intent landed, check the substrate via `get_file`; don't
  re-emit.
- `occurrence` index out of range → `ok=false` with the available
  match count.
