---
triggers:
  intents: [drop_file]
  flows: [patch]
tags: [install-directive, delete, resource, materialize, overlay-cleanup]
priority: 50
---

# drop_file — remove a `file copy` / `file materialize` install directive

## When to use

The overlay installs a non-patch file it should not — a port-local
resource (`file copy <src> -> <dest>`) or a staged upstream file
(`file materialize <src> -> <dest>`) — and you want both the
directive and the file gone. The symmetric inverse of `add_file`:
where that intent *adds* an install directive (and writes the
resource), this one *removes* the directive and **deletes the
on-disk file**.

Reach for it when:
- You emitted an `add_file` (or convert produced one) that is now
  obsolete — the resource is no longer needed, or it was the wrong
  file.
- A generated/staged file lingers in the overlay and should not be
  installed at all.

Deleting only the directive would orphan the bytes on disk, and the
orphaned file then blocks a later `add_file` to the same destination
with an "already exists" refusal. So `drop_file` removes both,
atomically — if the file delete fails, the directive removal is
rolled back so no half-applied state survives.

## The path-partition rule — patches go through `drop_patch`

`drop_file` and `drop_patch` never overlap, and the boundary is the
destination path:

- Destination under **`dragonfly/patch-*`** → that's a patch
  install. Use **`drop_patch`** (it matches both `patch apply` and
  `file materialize dragonfly/patch-* -> ...` shapes and removes the
  patch file). `drop_file` **refuses** a `dragonfly/patch-*` target
  outright and tells you to route it there.
- **Anything else** (port-local resources, staged headers, generated
  files) → `drop_file`.

Simple rule: patch destination → `drop_patch`; every other
destination → `drop_file`.

## Example

```json
{
  "type": "drop_file",
  "target": "files/pkg-message.dragonfly",
  "reason": "message no longer applies after the build switched to the bundled config"
}
```

`target` is the destination relpath — the `-> <target>` side of the
install directive, NOT the source. The executor finds the matching
`file copy ... -> files/pkg-message.dragonfly` (or `file
materialize ...`) line, removes it, and deletes
`files/pkg-message.dragonfly` from the port subtree.

The `reason` is operator-readable and lands in
`analysis/intent_log.json`. Be specific — it becomes the commit
rationale.

## Scoping

Accepts an optional `scope` field: `"@any"` (default — searches the
universal section) or `"@current"` (the build line you're running
on, resolved from the env). You never type a literal quarter
selector. Scope also disambiguates a destination that's installed
under more than one section — see `intent-scoping.md`.

## Failure modes the executor refuses

- **`dragonfly/patch-*` target** → `ok=false`; route to `drop_patch`.
- **Zero matches** → `ok=false`. No install directive for that
  destination at the resolved scope. Check the exact `-> <dest>`
  spelling via `get_file overlay.dops`; it may already be dropped
  (idempotent retry) or the path may not match exactly.
- **Multiple matches at the same scope** → `ok=false`, ambiguous;
  disambiguate via `scope` or hand-edit.
- **No overlay** → `ok=false`.
- **`scope: "@current"` with no env target** → `ok=false` with an
  escalate hint (calling-context bug).
- **On-disk delete fails** → `ok=false`, and the directive removal is
  rolled back; the substrate is left exactly as it was.

If the directive matches but no file exists on disk at the
destination (a materialize that was never staged, say), the executor
removes the directive and reports success without a file delete —
the goal state (no directive, no file) is reached either way.
