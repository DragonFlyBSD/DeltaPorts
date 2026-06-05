# Intent surface gap closure — implementation plan

Companion to `docs/intent-surface-gaps.md` (the reference matrix). This
document orders the work by impact and lists per-phase work items.

**Doc lifecycle**: this plan is rewritten per-phase as items land. The
reference matrix at `intent-surface-gaps.md` stays canonical; this plan
shrinks.

---

## Decision gate (before Phase 1)

Resolve these five questions once. Each touches multiple phases.

1. **Drop_X naming.** New "delete a non-patch line" intent: extend
   `drop_patch` to cover non-patch destinations, OR add a separate
   `drop_file`? Recommended: **`drop_file`** — keeps `drop_patch`'s
   name accurate and gives the agent a clearer mental model.
2. **`op=unset` self-strip semantics (A2).** Today the renderer
   deliberately keeps `op=unset` plain-append (see `_dops.py:412-422`)
   to preserve a set-then-unset-on-DragonFly-invented-variable case.
   Re-examine whether the original rationale survives if we self-strip
   only matching `mk set/add VAR ...` lines for the SAME key already in
   the same overlay. Recommended: **revisit when we get to A2**;
   document the decision in the renderer's docstring either way.
3. **Family C (generalized `edit_overlay`).** Skip for now (Family A+B
   are more legible and lower risk). Re-evaluate after Phase 4.
4. **Per-phase commit cadence.** One commit per landable work item
   (engine + tests + playbook + prompt update) — not phase-sized batches.
   Each item is independently revertable.
5. **Test budget per item.** Each new intent gets: parse test (schema),
   render test (correct dops emission), at least one renderer-refusal
   test, one round-trip test (the engine parses what we emitted). For
   modified intents (Family A): preserved-behavior test + new behavior
   test.

---

## Phase 1 — immediate unblocks (Family A self-stripping)

**Goal**: stop accumulating dead-weight in overlay.dops for the two
shapes already observed in production bundles. No new intent surface;
purely renderer behavior changes. Smallest possible code surface.

### Items

- [ ] **A1 — `change_makefile op=remove` self-strips matching `mk add`**
  - **Engine**: `_dops.py::change_makefile`. New helper
    `_strip_existing_mk_add(key, value)` modeled on
    `_strip_existing_mk_set`. When op=remove: scan overlay; if a
    matching `mk add VAR value` line exists, strip it and return
    `ok=true` without appending `mk remove`. If no match, fall back
    to current append behavior.
  - **Tests** (`test_edit_intent.py`):
    - new: op=remove strips matching mk add (no `mk remove` emitted)
    - new: op=remove with no matching add (fallback — appends `mk remove`)
    - preserved: op=remove against upstream-defined token still appends
  - **Playbook**: `intent-change_makefile.md` — document the
    self-strip in the "append semantics" section + a new
    "remove semantics" section. Note that add+remove pairs are no
    longer produced.
  - **Prompt**: `prompts.py:520-522` — update the `change_makefile`
    bullet to mention "op=remove cancels matching prior op=append on
    the same key, leaving no trace in overlay.dops."

- [ ] **A3 — `bump_portrevision` strips prior `mk set PORTREVISION`**
  - **Engine**: `_dops.py::bump_portrevision`. Pass
    `_strip_existing_mk_set("PORTREVISION")` to `_append_overlay`. One
    line change.
  - **Tests**: new test asserting two consecutive `bump_portrevision`
    calls produce exactly one `mk set PORTREVISION` line.
  - **Playbook**: `intent-bump_portrevision.md` if it exists (check;
    create one-paragraph file if not) — note idempotence.
  - **Prompt**: no change needed (line 523 already says "increment
    PORTREVISION", and idempotent re-emit matches the semantic).

### Phase 1 acceptance

- dmidecode-shape add+remove pair no longer produced.
- Repeated `bump_portrevision` is idempotent.
- All existing tests pass; no playbook contradiction with engine.

---

## Phase 2 — symmetric delete intents

**Goal**: close the "no way to delete X" gap for two of the highest-pain
shapes: non-patch file copies/materializes, and target heredoc blocks.
First introduction of new intent types in this push.

### Items

- [ ] **A4 — `drop_file` intent for non-patch `file copy`/`file materialize`**
  - **Schema**: `schemas/drop_file.json` — fields: `target` (relpath),
    `reason` (string, required like drop_patch).
  - **Grammar**: `grammar.py` — new `DropFile` dataclass; add to
    `INTENT_TYPES` and `INTENT_DATACLASSES`.
  - **Renderer**: `_dops.py::drop_file` — generalize the matching loop
    from `_strip_patch_apply_stmt` to match any `file copy ... -> <target>`
    or `file materialize ... -> <target>` line regardless of path shape.
    Delete the file on disk if present (symmetric with drop_patch).
    Refuse if `<target>` matches `dragonfly/patch-*` (route through
    drop_patch for that shape — keeps the two intents non-overlapping).
  - **Translator**: `translator.py:146-150` — add dispatcher entry.
  - **Tests**: positive (file copy line stripped, file deleted), positive
    (file materialize line stripped, file deleted), refusal on
    patch-shaped target (use drop_patch), refusal on missing match.
  - **Playbook**: new `intent-drop_file.md` — when to use vs drop_patch,
    examples for both `file copy` and `file materialize` shapes.
  - **Prompt**: add to intent list in `prompts.py`. Mention that
    drop_patch and drop_file are siblings (drop_patch for `dragonfly/patch-*`,
    drop_file for everything else).

- [ ] **A5 — `drop_target_block` intent for `mk target set/append` heredocs**
  - **Schema**: `schemas/drop_target_block.json` — fields: `block_name`,
    `reason`, `scope` (standard `["@any", "@current"]` enum, default
    `@any`).
  - **Grammar**: new `DropTargetBlock` dataclass with
    `scope: Literal["@any", "@current"] = "@any"`; register.
  - **Renderer**: `_dops.py::drop_target_block`. Apply the scope filter
    first (verified: the engine accepts same-name `mk target set` blocks
    across different scopes — `build_plan` returns ok=True with two ops;
    `semantic.py:163-172` has no duplicate-name check), then reuse
    `_replace_in_mk_target_block`'s block-finder to locate the open line
    + heredoc tag + close line within that scope. Strip the entire block
    (open through close, inclusive). Return ok=False if block not found.
    Refuse if name+scope still matches multiple blocks (ambiguous).
  - **Translator**: dispatcher entry.
  - **Tests**: positive (mk target set block removed entirely),
    positive (mk target append block removed), not-found,
    scope-filtered removal (same name in two scopes — only the
    targeted scope's block stripped), ambiguous (multiple same-name
    blocks at same scope → refuse).
  - **Playbook**: new `intent-drop_target_block.md` — when to use
    (convert produced a target heredoc that's no longer needed),
    distinction from B4 (drop_target_makefile, future).
  - **Prompt**: add to intent list.

### Phase 2 acceptance

- Agent can delete a `dfly-patch:` heredoc from overlay.dops.
- Agent can delete a `file copy`/`file materialize` line for a
  non-patch destination.
- Tracker bundles after this lands no longer show stuck-on-deletion
  thrash for these shapes.

---

## Phase 3 — first missing-family intent (conditional control)

**Goal**: redirect agents from "reach for add_patch to source-patch the
upstream Makefile" workarounds to a first-class intent for the
`mk disable-if` / `mk replace-if` directives the engine already supports.
Highest-leverage Family B item.

### Items

- [ ] **B1 — `change_condition` intent (covers `mk disable-if` and
       `mk replace-if`)**
  - **Schema**: `schemas/change_condition.json` — discriminator
    `mode: "disable" | "replace"`. For `disable`: `condition`
    (string — the upstream .if expr), `contains` (optional —
    body-anchor disambiguator). For `replace`: `condition` (the .if
    expr to match), `from`/`to` (replacement expr texts), `contains`
    (optional).
  - **Grammar**: new `ChangeCondition` dataclass; register.
  - **Renderer**: `_dops.py::change_condition`. For disable: emit
    `mk disable-if condition "X" [contains "Y"]`. For replace: emit
    `mk replace-if from "X" to "Y" [contains "Z"]`. Straightforward
    text emission — no strip prefilter needed in v1.
  - **Translator**: dispatcher entry.
  - **Tests**: parse (both modes), render (correct dops grammar
    in both shapes), round-trip (engine parser accepts what we emit),
    refusal on missing required fields.
  - **Playbook**: new `intent-change_condition.md` — when an upstream
    `.if defined(NLS)` should be disabled on DragonFly; when an `.if
    ${OPSYS} == FreeBSD` should be rewritten. Worked examples for both
    modes.
  - **Playbook updates** (cross-cutting):
    - `error-dragonfly-source-patches.md` — note that for upstream
      Makefile `.if` conditional changes, `change_condition` is the
      right tool, NOT a source patch via add_patch.
    - `toolchain-c.md` (and any other "compile-error" playbooks) — if
      they mention disabling features via source-patching the upstream
      Makefile, redirect to change_condition.
  - **Prompt**: add to intent list with one-line description.

### Phase 3 acceptance

- Agent can disable an upstream `.if` block without source-patching the
  Makefile.
- Bundle audit on next "disable upstream feature" shape shows
  `change_condition` instead of `add_patch` workaround.

---

## Phase 4 — file lifecycle completion

**Goal**: close the "no way to remove a file from the composed tree" gap.
Pairs naturally with A4 from Phase 2.

### Items

- [ ] **B6 — `remove_file_at_compose` intent (`file remove PATH`)**
  - **Schema**: `schemas/remove_file_at_compose.json` — fields:
    `path` (relpath), `on_missing` (enum: "skip" | "fail", default
    "fail").
  - **Grammar**: new `RemoveFileAtCompose` dataclass; register.
  - **Renderer**: `_dops.py::remove_file_at_compose`. Emit
    `file remove PATH [on-missing skip|fail]`.
  - **Translator**: dispatcher entry.
  - **Tests**: parse, render, on_missing handling, round-trip.
  - **Playbook**: new `intent-remove_file_at_compose.md` — when to
    use (upstream test fixture that breaks DragonFly, generated file
    we don't want, etc.). Distinguish from drop_file (drop_file removes
    a `file copy`/`file materialize` line; this one tells compose to
    delete a file from the materialized tree regardless of how it got
    there).
  - **Prompt**: add to intent list. Note the distinction from drop_file
    in the description.

### Phase 4 acceptance

- Agent can request compose-time file deletion.
- The three file-lifecycle intents (add_file, drop_file, remove_file_at_compose)
  have clear, non-overlapping use cases in the playbook set.

---

## Phase 5 — target heredoc create + op=unset cleanup

**Goal**: complete the target-heredoc lifecycle (create exists only at
convert-time today; A5 added delete; B3 adds create). Revisit A2 with
benefit of having lived with A1/A3 behavior.

### Items

- [ ] **B3 — `add_target_block` intent for `mk target set/append NAME <<TAG ... TAG`**
  - **Schema**: `schemas/add_target_block.json` — fields: `name`,
    `body` (multi-line string), `mode` (enum: "set" | "append"),
    `tag` (optional — defaults to auto-generated MK<N>).
  - **Grammar**: new `AddTargetBlock` dataclass; register.
  - **Renderer**: `_dops.py::add_target_block`. Emit
    `mk target set|append NAME <<TAG\n<body>\nTAG`. Auto-pick tag if
    not provided by walking existing overlay heredocs and finding the
    next free `MK<N>`.
  - **Translator**: dispatcher entry.
  - **Tests**: parse (both modes), render (correct heredoc shape),
    tag auto-generation, body with special characters (quotes,
    newlines, tabs), round-trip.
  - **Playbook**: new `intent-add_target_block.md` — when to introduce
    a new `dfly-patch:` or `pre-build:` target. Pair with
    intent-drop_target_block.md (A5) for the symmetric create/delete.
  - **Prompt**: add to intent list. Pair with drop_target_block in
    the description (siblings).

- [ ] **A2 — `change_makefile op=unset` self-strips matching `mk set/add VAR …`**
  - **First**: read `_dops.py:412-422` rationale comment. Re-examine
    whether the set-then-unset-on-DragonFly-invented-variable case
    is broken by self-stripping.
  - **Engine**: only land if the analysis confirms self-stripping is
    safe for that case. If not safe: document the limitation in the
    playbook instead (no engine change), and close A2 as "won't fix
    by design — see intent-change_makefile.md."
  - **Tests** (if landing): all four matrix cells:
    - set + unset (same overlay) → both lines stripped
    - add + unset (same overlay) → add stripped, unset emitted
    - unset alone → unset emitted (current behavior preserved)
    - re-emit unset → idempotent (no duplicate `mk unset` lines)
  - **Playbook**: `intent-change_makefile.md` — update op=unset section.
  - **Prompt**: minor wording adjustment if behavior changes.

### Phase 5 acceptance

- Agent can introduce new target heredocs without convert involvement.
- op=unset behavior is documented and consistent (either self-strips
  or explicitly doesn't, with rationale).

---

## Phase 6 — remainder (lower priority)

Lower-leverage items. Pick individually based on observed bundle needs.

- [ ] **B2 — `add_block` intent for `mk block set condition "X" <<TAG ... TAG`**
  - Conditional shell stanzas. Rare use case. Defer until a bundle
    actually needs it.

- [ ] **B4 — `drop_target_makefile` intent for `mk target remove NAME`**
  - Distinct from A5: this asks compose to delete a Makefile target
    by name (e.g. stray `do-install:` from upstream). A5 deletes the
    dops heredoc; B4 tells compose to remove a target from the
    composed Makefile.

- [ ] **B5 — `rename_target` intent for `mk target rename OLD -> NEW`**
  - Exotic. Land only if a bundle needs it.

- [ ] **B7 — `edit_line` intent for `text line-remove` and `text line-insert-after`**
  - Mode discriminator: "remove" (with `exact`) or "insert_after"
    (with `anchor` + `line`). Scope: same as `replace_in_patch`
    (refuse `dragonfly/` and `.dops`).

### Phase 6 acceptance

Land per item, no batch acceptance. Each item gets its own commit
+ playbook + prompt update.

---

## Phase 7 — deferred (Family C)

- [ ] **C1 — `edit_overlay(action, directive_kind, key, …)` generic dispatcher**
  - Only land if Phases 1–6 leave visible gaps that a generic surface
    would close more cleanly than another specific intent. Evaluate
    after Phase 4.

---

## Cross-cutting playbook updates

A separate sweep after Phase 3 lands. Walk the existing playbook set
and update any references that recommend the old patterns:

- [ ] `error-dragonfly-source-patches.md` — point at change_condition
  (B1) for upstream `.if` changes instead of source patches.
- [ ] `error-bsd-types-visibility.md` — its "Option 1: remove the
  restricting macros" currently recommends `add_patch` to delete
  `-D_POSIX_C_SOURCE` tokens; if that's actually a `mk replace-if`
  or `mk disable-if` shape, redirect to change_condition.
- [ ] `intent-add_patch.md` — its "Do not use when" list should
  reference the new intents (change_condition for .if blocks,
  add_target_block for new targets, etc.) as alternatives.
- [ ] `toolchain-c.md` (and any other classification playbooks) — if
  any recommend Makefile-level edits via source-patching, redirect.

---

## What's NOT in this plan (yet)

- Header directive editability (target/port/type/reason/maintainer) —
  matrix rows 1-5. Agent has no need observed; convert owns these.
  Add to a future phase only if a bundle needs it.
- Engine-level changes to the dops grammar itself. This plan stays
  within the existing grammar; all gaps close at the intent layer.

---

## Tracking

When an item lands:
1. Check the box here.
2. Update `intent-surface-gaps.md`'s coverage matrix (the relevant ❌
   becomes ✅, or ⚠️ becomes ✅, etc.).
3. The systemic-gaps section in `intent-surface-gaps.md` and the
   "Concrete scenarios" list should also be updated as items remove
   them.
4. Once a phase is fully landed, delete that phase's section from this
   plan (per the per-phase rewrite rule). The reference matrix is the
   permanent record.
