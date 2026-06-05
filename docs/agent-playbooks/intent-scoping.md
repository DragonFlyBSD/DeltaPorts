---
triggers:
  flows: [patch]
tags: [scope, target, multi-target, get_effective_overlay]
priority: 60
---

# Target scope on intents — `@any` vs `@current`

## The two-value vocabulary

Most intent types accept an optional `scope` field with two values:

- **`scope: "@any"`** (default — applies on every DragonFly build line).
  Omit the field or set it explicitly; the result is the same.
- **`scope: "@current"`** (applies only on the build line you're
  running on right now). The runner injects the concrete target —
  `@2026Q2`, `@main`, etc. — at apply time from the env's compose
  target. **You never type a literal `@2026Q2` or any other quarter
  selector.** The schema rejects it.

The only intents that do **not** accept `scope` are `drop_patch` and
`replace_in_dops_block`. Both operate on named entities (a specific
patch path, a specific heredoc block) where scope wouldn't add
expressiveness; the schemas refuse the field outright via
`additionalProperties: false`.

### Scope as a disambiguation lever for deletes

For the delete intents — `drop_mk_directive`, `drop_file`,
`drop_target_block` — `scope` does double duty. It narrows the
*search* to one section, which is also how you resolve an ambiguous
match: if the same line / install directive / block name exists under
both `@any` and a quarterly section, an unscoped delete sees a match
in each and refuses; the scoped delete targets exactly one. When a
delete refuses as "ambiguous," reach for `scope` before hand-editing.

## When to use `@current`

Reach for `@current` only when the fix is genuinely specific to the
build line you're running on. Concretely:

- **Upstream source diverges between quarterly snapshots.** A patch
  that lines up against the @2026Q2 source tree won't necessarily
  apply against @2026Q3. If you can't write a single patch that
  works everywhere, scope each to its build line.
- **Framework values deprecated between snapshots.** `USES=alias`
  was removed from the ports framework at some point; a fix to
  drop it should only apply on the post-deprecation build line so
  earlier snapshots that still expect it don't break.
- **Build-line-specific configuration.** Rare in practice but real
  — different `CFLAGS` for different compiler defaults shipped per
  quarterly branch, etc.

## When NOT to use `@current`

**Most fixes are universal.** DragonFly-vs-FreeBSD differences are
platform-level: they apply on every DragonFly build regardless of
which quarterly snapshot you're running on. Scoping these to
`@current` would over-restrict — the fix would silently not apply
on the next build line, and you'd see the same failure re-surface
next quarter.

Default to `@any`. Use `@current` deliberately, not reflexively.

## Reading scope-resolved state — `get_effective_overlay`

The agent's natural instinct is `get_file overlay.dops` to read the
overlay's content. That works but returns the *raw* file: every
`target @X` section, every op regardless of scope. On a multi-target
overlay you'd have to mentally walk the sections and apply the
engine's filter (keep ops whose scope is `@any` or matches the env's
target) yourself.

Use `get_effective_overlay(origin)` instead. It runs the file
through the engine and returns:

- `target`: the env's compose target.
- `effective_ops`: the ops that **will** apply on this build, in
  declaration order, with `scope` tags so you can see which layer
  each op came from.
- `filtered_out`: ops scoped to *other* build lines, each carrying
  a `reason` string saying why they were excluded.
- `overlay_path`: relpath of overlay.dops (for cross-references).

Each op in `effective_ops` / `filtered_out` is a structured dict
with `id`, `kind` (engine op kind like `mk.var.set` /
`mk.var.token_add` / `patch.apply`), `scope`, and op-specific
payload fields. For `mk.var.*` ops the variable name is in the
`name` field; the value is in `value`.

**Use `get_effective_overlay` whenever you need to know "what will
compose actually apply on this build."** Raw `get_file overlay.dops`
is still useful for byte-exact inspection (verifying a write landed
in the right section, etc.) but error-prone for reasoning about
effective state on multi-target overlays.

## Read vs write surface

| Concern | Tool to use |
|---|---|
| What will compose apply on this build? | `get_effective_overlay(origin)` |
| What does the literal file look like (sections, blank lines, etc.)? | `get_file overlay.dops` |
| Emit a universal fix | `apply_intent({..., scope: "@any"})` (or omit scope) |
| Emit a build-line-specific fix | `apply_intent({..., scope: "@current"})` |

## Worked example

Suppose triage reports that `USES=alias` causes a build error on
the build line you're running. Investigation (`grep` in `Mk/`)
shows the framework deprecated this USES value in some quarterly
snapshot; older build lines may still expect it.

If you're certain the deprecation only affects your build line:

```json
{
  "type": "change_makefile",
  "path": "Makefile",
  "key": "USES",
  "value": "alias",
  "op": "remove",
  "scope": "@current"
}
```

The renderer resolves `@current` → e.g. `@2026Q3`, emits the `mk
remove USES "alias"` statement under a `target @2026Q3` section in
`overlay.dops`. Other build lines that still need `USES=alias` are
unaffected. `get_effective_overlay(origin)` would then show the new
op in `effective_ops` for @2026Q3 and in `filtered_out` for @2026Q2.

If you're not sure the deprecation is build-line-specific, default
to `@any` and let the operator promote later if it turns out to
need narrowing.

## What @current resolution looks like under the hood

- The runner reads `job["target"]` (e.g. `"@2026Q2"`) at attempt
  start and calls `worker.set_env_target(env, target)`.
- When you emit an intent with `scope: "@current"`, the renderer
  reads `t.target` from that cache and substitutes it into the
  written dops statement. The substrate stores the concrete value
  (`target @2026Q2`), not the literal `@current` (which isn't
  valid engine grammar).
- If the runner failed to populate the cache (a calling-context
  bug — should not happen in production runs), the intent refuses
  with: *"intent requested scope=@current but the runner did not
  populate an env target ... worker.set_env_target. Retrying will
  not help — escalate."*

## Substrate accumulation after Step 38e

One operator-visible behavior change: re-emitting
`change_makefile op=set` for the same key accumulates lines on
disk. Pre-38e an implicit prefilter scrubbed prior `mk set KEY`
lines; that prefilter was removed because it was scope-blind and
would have corrupted multi-target overlays. The composed Makefile
is still correct (the engine plays ops in declaration order,
last-wins), but the substrate carries every re-emission.

To delete a prior `mk set` line explicitly, use `drop_mk_directive`
(see `intent-drop_mk_directive.md`). Re-emitting `op=set` still
produces visible substrate noise, but it doesn't affect correctness,
and the noise is now removable rather than permanent.
