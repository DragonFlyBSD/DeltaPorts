# Intent surface gap closure — implementation plan

Companion to `docs/intent-surface-gaps.md` (the reference matrix). This
document orders the *remaining* work by impact. The reference matrix stays
canonical; this plan shrinks as phases land.

**Status:** Family A (the delete-side gap) is **shipped as Step 39**. What
remains is **Family B** — directive families with no agent surface at all.
Family B is tracked as **Step 40** in the architecture backlog; this doc
carries the per-intent detail.

---

## What shipped (Family A) — and why the approach changed

The original version of this plan closed Family A by *self-stripping*:
re-emitting `op=set` would scrub the prior `mk set` line, `op=remove` would
cancel a matching `mk add`, `bump_portrevision` would dedupe, etc. (old
items A1/A2/A3).

**Step 38e reversed that premise.** Implicit prefilters were scope-blind and
could corrupt multi-target overlays, so `_strip_existing_mk_set` was deleted
and the project adopted "each intent does one thing, no implicit cleanup."
The corrective gap was then closed with **explicit, scope-aware delete
intents** instead:

- `drop_mk_directive` (39a, `bfb6ae8bcde`) — replaces A1/A2/A3.
- `drop_file` (39b, `ff9bf706b53`) — was A4. Decision: separate intent (not
  a generalized `drop_patch`); partitioned by `dragonfly/patch-*`.
- `drop_target_block` (39c, `4830bc9342d`) — was A5.
- Playbooks + prompt wiring (39d, `a1b67fd40cc`).

Consequence for the live behavior model: re-emitting `change_makefile op=set`
**accumulates** lines (last-wins at compose); delete a stale line explicitly
via `drop_mk_directive`. The old "op=unset self-strip" question (decision Q4)
is **moot** — nothing self-strips.

---

## Decision gate (carried forward)

1. ~~Drop_X naming~~ — **resolved**: `drop_file` / `drop_target_block` /
   `drop_mk_directive`, keeping `add_X` ↔ `drop_X` symmetry.
2. ~~`op=unset` self-strip semantics~~ — **moot post-38e**: no renderer
   self-strips.
3. **Family C (generalized `edit_overlay`)** — deferred behind a re-evaluation
   gate (architecture backlog Step 41a). Land Family B specifics first.
4. **Commit cadence** — one commit per landable work item (engine + tests +
   playbook + prompt). Each item independently revertable.
5. **Test budget per item** — parse test (schema), render test (correct dops
   emission), at least one renderer-refusal test, one round-trip test (engine
   parses what we emitted).

---

## Phase B1 — conditional control (highest-leverage Family B item)

**Goal**: redirect agents from "reach for `add_patch` to source-patch the
upstream Makefile" workarounds to a first-class intent for the
`mk disable-if` / `mk replace-if` directives the engine already supports.

### Items

- [ ] **`change_condition` intent (covers `mk disable-if` and `mk replace-if`)**
  - **Schema**: `schemas/change_condition.json` — discriminator
    `mode: "disable" | "replace"`. For `disable`: `condition` (the upstream
    .if expr), `contains` (optional body-anchor disambiguator). For
    `replace`: `condition` (the .if expr to match), `from`/`to` (replacement
    expr texts), `contains` (optional). Standard `scope` enum.
  - **Grammar**: new `ChangeCondition` dataclass; register in `INTENT_TYPES`,
    `Intent` union, `INTENT_DATACLASSES`.
  - **Renderer**: `_dops.py::change_condition`. For disable: emit
    `mk disable-if condition "X" [contains "Y"]`. For replace: emit
    `mk replace-if from "X" to "Y" [contains "Z"]`. Text emission; no strip
    prefilter (consistent with post-38e "no implicit cleanup").
  - **Translator**: dispatcher entry.
  - **Tests**: parse (both modes), render (correct dops grammar in both
    shapes), round-trip (engine parser accepts what we emit), refusal on
    missing required fields.
  - **Playbook**: new `intent-change_condition.md` — when an upstream
    `.if defined(NLS)` should be disabled on DragonFly; when an
    `.if ${OPSYS} == FreeBSD` should be rewritten. Worked examples both modes.
  - **Cross-cutting playbook updates**: `error-dragonfly-source-patches.md`,
    `toolchain-c.md` — redirect Makefile-`.if` changes to `change_condition`,
    not a source patch via `add_patch`.
  - **Prompt**: add to intent list with one-line description.

### Acceptance

- Agent can disable/rewrite an upstream `.if` block without source-patching.

---

## Phase B3 — target heredoc create

**Goal**: complete the target-heredoc lifecycle. Delete shipped as
`drop_target_block` (Step 39); create is still convert-only.

### Items

- [ ] **`add_target_block` intent (`mk target set/append NAME <<TAG ... TAG`)**
  - **Schema**: `schemas/add_target_block.json` — fields: `name`, `body`
    (multi-line), `mode` (enum: "set" | "append"), `tag` (optional —
    auto-generated `MK<N>` if omitted), standard `scope`.
  - **Grammar**: new `AddTargetBlock` dataclass; register.
  - **Renderer**: `_dops.py::add_target_block`. Emit
    `mk target set|append NAME <<TAG\n<body>\nTAG`. Auto-pick tag by walking
    existing overlay heredocs for the next free `MK<N>`.
  - **Translator**: dispatcher entry.
  - **Tests**: parse (both modes), render (correct heredoc shape), tag
    auto-generation, body with quotes/newlines/tabs, round-trip.
  - **Playbook**: new `intent-add_target_block.md` — pairs with the existing
    `intent-drop_target_block.md` as the symmetric create/delete.
  - **Prompt**: add to intent list, paired with `drop_target_block`.

### Acceptance

- Agent can introduce a new target heredoc without convert involvement.

---

## Phase B6 — compose-time file removal

**Goal**: close the "no way to remove a file from the composed tree" gap.
Pairs naturally with `drop_file` (Step 39).

### Items

- [ ] **`remove_file_at_compose` intent (`file remove PATH`)**
  - **Schema**: `schemas/remove_file_at_compose.json` — `path` (relpath),
    `on_missing` (enum: "skip" | "fail", default "fail"), standard `scope`.
  - **Grammar**: new `RemoveFileAtCompose` dataclass; register.
  - **Renderer**: `_dops.py::remove_file_at_compose`. Emit
    `file remove PATH [on-missing skip|fail]`.
  - **Translator**: dispatcher entry.
  - **Tests**: parse, render, on_missing handling, round-trip.
  - **Playbook**: new `intent-remove_file_at_compose.md` — distinguish from
    `drop_file` (which removes a `file copy`/`file materialize` *line*; this
    tells compose to delete a file from the materialized tree regardless of
    how it got there).
  - **Prompt**: add to intent list, noting the distinction from `drop_file`.

### Acceptance

- Agent can request compose-time file deletion; the three file-lifecycle
  intents (add_file, drop_file, remove_file_at_compose) have clear,
  non-overlapping use cases in the playbook set.

---

## Phase B-remainder (lower priority — land per observed bundle need)

- [ ] **`add_block` (`mk block set condition "X" <<TAG ... TAG`)** —
  conditional shell stanzas. Rare; defer until a bundle needs it.
- [ ] **`drop_target_makefile` (`mk target remove NAME`)** — asks compose to
  delete a Makefile target by name (distinct from `drop_target_block`, which
  deletes the dops heredoc).
- [ ] **`rename_target` (`mk target rename OLD -> NEW`)** — exotic; land only
  if a bundle needs it.
- [ ] **`edit_line` (`text line-remove` / `text line-insert-after`)** — mode
  discriminator "remove" (with `exact`) or "insert_after" (with `anchor` +
  `line`). Scope: same refusals as `replace_in_patch` (`dragonfly/`, `.dops`).

Land per item, no batch acceptance. Each gets its own commit + playbook +
prompt update.

> **Related (Step 40d):** the latent scope-blindness in
> `replace_in_dops_block` — it edits block bodies without honoring the same
> `@any`/`@current` scope discipline the Family A deletes now enforce. Fix
> alongside this Family B thread.

---

## Deferred — Family C (generalized `edit_overlay`)

- [ ] **`edit_overlay(action, directive_kind, key, …)` generic dispatcher** —
  only land if the Family B specifics leave visible gaps a generic surface
  would close more cleanly. Behind a re-evaluation gate (architecture backlog
  Step 41a).

---

## What's NOT in this plan

- Header directive editability (target/port/type/reason/maintainer) — matrix
  rows 1-5. No agent need observed; convert owns these.
- Engine-level changes to the dops grammar itself. This plan stays within the
  existing grammar; all gaps close at the intent layer.

---

## Tracking

When an item lands:
1. Check the box here.
2. Flip the relevant cell in `intent-surface-gaps.md`'s coverage matrix.
3. Update its systemic-gaps section and "Concrete scenarios" list as items
   remove them.
4. Once a phase is fully landed, delete that phase's section from this plan
   (per-phase rewrite rule). The reference matrix is the permanent record.
