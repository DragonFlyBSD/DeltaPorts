---
triggers:
  intents: [drop_patch]
  flows: [patch]
tags: [obsolete-patch, materialize, patch-apply]
priority: 50
---

# drop_patch — remove a now-obsolete patch from the overlay

## When to use

The patch's logical purpose no longer applies — upstream merged
the fix, the file the patch targeted was deleted, the workaround
is obsolete on the current target. After `drop_patch` the overlay
no longer references the patch and compose stops trying to apply
it.

Do **not** use when:
- The patch is still conceptually correct but a hunk drifted →
  `replace_in_patch`.
- You want to replace one patch with a different one → `drop_patch`
  + `add_patch` in the same job. Two intents, not one.

## Two install shapes

The dops grammar has two ways a patch gets installed; `drop_patch`
handles both:

- **`patch apply dragonfly/patch-X`** — inline install. Compose
  applies the patch directly from the overlay tree at materialize
  time. `drop_patch` removes the line.
- **`file materialize dragonfly/patch-X -> dragonfly/patch-X`** —
  materialized install. Compose copies the patch file from the
  overlay into the compose tree; `bsd.port.mk`'s `do-patch` phase
  applies it at build time. `drop_patch` removes BOTH the
  materialize line AND the patch file on disk — leaving the file
  behind is the most common confusion (it sits there pretending to
  be installed when it isn't).

## Example

```json
{
  "type": "drop_patch",
  "target": "dragonfly/patch-src_obsolete.c",
  "reason": "upstream 1.50.0 fixed this in commit a1b2c3d"
}
```

The `reason` is operator-readable and lands in
`analysis/intent_log.json`. Be specific — "obsolete" alone is not
auditable.

## Failure modes the executor refuses

- The overlay has no install statement for `target` →
  `ok=false`. Check `get_file overlay.dops` first; the target may
  already be dropped (idempotent retry) or the basename may not
  match exactly.
- The overlay has an `mk target set <name>` block whose body
  references the target → the executor surfaces a hint that
  heredoc-body changes need `replace_in_dops_block`, not
  `drop_patch`. Do not reach for `change_makefile` or `add_file`
  as workarounds — they corrupt the overlay.
