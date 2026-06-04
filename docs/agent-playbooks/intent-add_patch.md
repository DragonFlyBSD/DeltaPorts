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
  drifted → `drop_patch` + `add_patch` with a corrected diff
  (or `add_patch from_dupe=true` to regenerate from a fresh
  WRKSRC edit). Patch files are output artifacts; do not edit
  them in place — see "Recovering from a failed `add_patch`"
  below.
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
- A tool for modifying an **existing** dragonfly/patch-* — see
  "Recovering from a failed `add_patch`" below; the path is
  `drop_patch` + `add_patch`, never an in-place text edit on the
  diff itself.

A `dupe` call without a follow-up `add_patch(from_dupe=true)` in
the same attempt is wasted work and a strong signal you've reached
for the wrong tool.

## Recovering from a failed `add_patch`

When `add_patch` succeeds but `materialize_dports` then rejects the
diff (`E_APPLY_PATCH_FAILED` with "malformed patch", wrong hunk
count, line-number mismatch, etc.), the patch file on disk is
broken and the only correct recovery is:

1. `drop_patch(target=…, reason=…)` — removes both the install
   directive from `overlay.dops` AND the broken patch file from
   disk. (As of 2026-06, drop_patch deletes the file for both
   install shapes — `patch apply` and `file materialize` —
   symmetrically.)
2. `add_patch(target=…, diff=<corrected diff>)` — with the
   hunk header line counts fixed and the body verified by
   inspection.
3. Or: `add_patch(target=…, from_dupe=true)` — edit WRKSRC,
   run `genpatch`, let the engine pick up the regenerated diff.

**Do not** reach for `replace_in_patch` to nudge a broken diff. The
validator refuses any `replace_in_patch` whose target starts with
`dragonfly/` — patch files are output artifacts. Text-editing a
diff to fix line numbers shifts the hunk body but not the hunk
header, and the result is a patch that lies about its own bytes.
This anti-pattern was observed driving `devel_jwasm` into a state
where every `materialize_dports` failed with `E_APPLY_MISSING_SUBJECT`.

## Scoping

Accepts an optional `scope` field: `"@any"` (default — patch
installs on every build line) or `"@current"` (patch installs only
on the build line you're running on). Use `@current` when upstream
source structure differs between quarterly snapshots and the patch
only makes sense against the version present on this build. Most
patches are universal — DragonFly-vs-FreeBSD differences usually
apply everywhere — so default to `@any`. See `intent-scoping.md`
for the cross-cutting rules.

## Failure modes

- Target already exists → `ok=false`. The previous `add_patch`
  is still installed in `overlay.dops` (otherwise `drop_patch` would
  have deleted both the file and the directive). Use `drop_patch`
  to clean state, then `add_patch` again.
- `from_dupe=true` but no matching `patch-*` file in WRKSRC →
  `ok=false`. Did `genpatch` actually run, and did `extract` run
  before it so WRKSRC was populated? Check `genpatch`'s return —
  `patch_basename` / `patch_path` are populated on success and
  null otherwise.
- Empty diff content (inline or from_dupe) → `ok=false`. A
  no-content patch is never the right answer.
