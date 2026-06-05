# Intent surface gap analysis

Reference document for closing the gaps between the patch agent's intent
surface and the dops directive grammar. Used as implementation tracker.

## Problem statement

The patch agent's intent surface was historically **constructive-heavy and
corrective-thin**. Step 39 (Family A delete intents) closed the worst of it.
Out of 23 distinct dops directive shapes the engine understands, **7** now
have fully-symmetric create+delete intent coverage (was 1), **8** have no
intent at all, and **14** still have no delete path from `overlay.dops`.

> **Design note — explicit deletes, not self-stripping.** The original
> version of this plan (Phase 1, Family A) closed the corrective gap by
> making create/delete pairs *self-strip*: re-emitting `op=set` scrubbed the
> prior `mk set` line, `op=remove` would cancel a matching `mk add`, etc.
> **Step 38e reversed that premise.** Implicit prefilters were scope-blind
> and could corrupt multi-target overlays, so `_strip_existing_mk_set` was
> removed and the project adopted "each intent does one thing, no implicit
> cleanup." The corrective gap was instead closed by **explicit** delete
> intents (`drop_mk_directive`, `drop_file`, `drop_target_block` — Step 39),
> which are scope-aware and hard-refuse on zero/ambiguous matches. Re-emitting
> `op=set` now accumulates lines (last-wins at compose time); the old line is
> removed deliberately via `drop_mk_directive`, never automatically.

When the agent needs to undo or surgically remove its own prior output or a
convert artifact, it now reaches for the matching `drop_*` intent. What
remains thin is **conditional control flow** (`mk disable-if`/`replace-if`),
**compose-time file removal** (`file remove`), and **target-heredoc
creation** — Family B, tracked below and as Step 40 in the architecture
backlog.

---

## Reference: intent → dops mapping

Canonical list. 10 intent types total. Source: `grammar.py` `INTENT_TYPES` +
`_dops.py` renderer bodies. Post-38e there are **no strip-prefilters** — every
renderer appends or deletes exactly what it names; nothing is scrubbed
implicitly.

| Intent | Emits into / removes from `overlay.dops` | Match / refuse | Side effect on disk |
|---|---|---|---|
| `add_file` (kind=resource) | appends `file copy <dest> -> <dest>` | — | writes file under `ports/<origin>/<dest>` |
| `add_file` (kind=materialize) | appends `file materialize <src> -> <dst>` | — | — |
| `add_patch` (inline) | appends `patch apply <target>` | refuses if file exists | writes patch under `ports/<origin>/<target>` |
| `add_patch` (from_dupe=true) | appends `patch apply <target>` | refuses if file exists | reads from WRKSRC genpatch, writes patch |
| `bump_portrevision` | appends `mk set PORTREVISION "1"` | none (re-emit accumulates; delete via `drop_mk_directive`) | — |
| `change_makefile` op=set | appends `mk set VAR "value"` | none (re-emit accumulates, last-wins) | — |
| `change_makefile` op=append | appends `mk add VAR "value"` | — | — |
| `change_makefile` op=remove | appends `mk remove VAR "value"` | — | — |
| `change_makefile` op=unset | appends `mk unset VAR` | — | — |
| `drop_mk_directive` | removes one `mk set/unset/add/remove VAR` line | scope-aware; refuses on zero or ambiguous match | — |
| `drop_file` | removes a non-patch `file copy`/`file materialize ... -> <target>` line | refuses on `dragonfly/patch-*` (use `drop_patch`); zero/ambiguous | deletes the materialized file if present |
| `drop_patch` | removes `patch apply <target>` OR patch-shaped `file materialize` | match on both `dragonfly/patch-*` shapes | deletes patch file on disk |
| `drop_target_block` | removes a whole `mk target set/append NAME <<TAG ... TAG` block | scope-aware; refuses on zero/ambiguous; refuses on corrupt (unbounded) heredoc | — |
| `replace_in_dops_block` | edits body of `mk target set/append <name> <<TAG ... TAG` | in-place body edit | — |
| `replace_in_patch` (non-dragonfly, non-`.dops` only) | appends `text replace-once file <target> from "X" to "Y"` | — | — |

---

## Reference: dops directive coverage matrix

23 distinct directive shapes. Source: walked from `engine/parser.py:178-201`
through every `_parse_*` method. Columns: can the agent **create**,
**modify in place**, or **delete** the directive from `overlay.dops`?

Legend: ✅ = supported, ⚠️ = partial / awkward, ❌ = **no intent**.

| # | Directive | Create | Modify | Delete | Gap notes |
|---|---|---|---|---|---|
| 1  | `target @x` | ⚠️ convert-only | ❌ | ❌ | header |
| 2  | `port <origin>` | ⚠️ convert-only | ❌ | ❌ | header |
| 3  | `type port` | ⚠️ convert-only | ❌ | ❌ | header |
| 4  | `reason "..."` | ⚠️ convert-only | ❌ | ❌ | header |
| 5  | `maintainer "..."` | ⚠️ convert-only | ❌ | ❌ | optional header |
| 6  | `mk set VAR "v"` | ✅ change_makefile op=set | ⚠️ re-emit appends (last-wins, accumulates post-38e) | ✅ drop_mk_directive kind=set | delete the prior line explicitly; no auto-strip |
| 7  | `mk unset VAR` | ✅ change_makefile op=unset | ❌ (re-emit appends) | ✅ drop_mk_directive kind=unset | |
| 8  | `mk add VAR tok` | ✅ change_makefile op=append | ❌ | ✅ drop_mk_directive kind=add (matches key+token) | `op=remove` appends a counter-op; drop removes the line |
| 9  | `mk remove VAR tok` | ✅ change_makefile op=remove | ❌ | ✅ drop_mk_directive kind=remove (matches key+token) | |
| 10 | `mk disable-if condition "X" [contains "Y"]` | ❌ | ❌ | ❌ | cannot disable upstream `.if` blocks |
| 11 | `mk replace-if from "X" to "Y" [contains "Z"]` | ❌ | ❌ | ❌ | cannot rewrite upstream conditions |
| 12 | `mk block set condition "X" <<TAG ... TAG` | ❌ | ❌ | ❌ | conditional heredocs — `replace_in_dops_block` does NOT cover this shape |
| 13 | `mk target set NAME <<TAG ... TAG` | ❌ (convert-only) | ✅ replace_in_dops_block | ✅ drop_target_block | create still convert-only (B3) |
| 14 | `mk target append NAME <<TAG ... TAG` | ❌ | ✅ replace_in_dops_block | ✅ drop_target_block | create still convert-only (B3) |
| 15 | `mk target remove NAME` (Makefile-level target deletion, no body) | ❌ | n/a | ❌ | distinct from deleting the `mk target set` heredoc itself |
| 16 | `mk target rename OLD -> NEW` | ❌ | n/a | ❌ | no intent at all |
| 17 | `file copy SRC -> DST` | ✅ add_file kind=resource | ❌ | ✅ drop_file (non-patch) / drop_patch (`dragonfly/patch-*`) | partitioned by destination shape |
| 18 | `file materialize SRC -> DST` | ✅ add_file kind=materialize | ❌ | ✅ drop_file (non-patch) / drop_patch (`dragonfly/patch-*`) | partitioned by destination shape |
| 19 | `file remove PATH` | ❌ | ❌ | ❌ | agent cannot tell compose to remove a file |
| 20 | `text line-remove file P exact "X"` | ❌ | ❌ | ❌ | no intent |
| 21 | `text line-insert-after file P anchor "X" line "Y"` | ❌ | ❌ | ❌ | no intent |
| 22 | `text replace-once file P from "X" to "Y"` | ⚠️ replace_in_patch — refused for `dragonfly/` and `.dops` | ❌ | ❌ | scope-limited, no delete |
| 23 | `patch apply PATH` | ✅ add_patch | ❌ | ✅ drop_patch (also deletes file) | only fully-symmetric create/delete pair |

### Counts

- **Directives with NO intent at all** (Create + Modify + Delete all ❌, headers excluded): 10, 11, 12, 15, 16, 19, 20, 21 — **8 of 18 non-header rows** (unchanged by Step 39; these are Family B/header territory).
- **Directives with NO delete intent**: all rows except 6, 7, 8, 9, 13, 14, 17, 18, 23 — **14 of 23** (was 20 before Step 39).
- **Fully agent-manageable directives** (Create + Delete both ✅): rows 6, 7, 8, 9, 17, 18, 23 — **7 of 23** (was 1). Rows 13/14 have delete but create is still convert-only (Family B3).

---

## Systemic gaps by shape

### Shape A — `mk` directive deletes — **CLOSED by Step 39 (`drop_mk_directive`)**

Every `mk set/unset/add/remove` line now has an explicit, scope-aware delete
path. The agent stops a counter-op accumulating by deleting the original line:

| Want to undo… | Move today | Result on disk |
|---|---|---|
| `mk set VAR "x"` | `drop_mk_directive kind=set key=VAR` | the `mk set` line is removed |
| `mk unset VAR` | `drop_mk_directive kind=unset key=VAR` | the `mk unset` line is removed |
| `mk add VAR tok` | `drop_mk_directive kind=add key=VAR value=tok` | the `mk add` line is removed (no counter-op) |
| `mk remove VAR tok` | `drop_mk_directive kind=remove key=VAR value=tok` | the `mk remove` line is removed |

`change_makefile` renderers are still append-only (each emits exactly one line,
last-wins at compose) — but the substrate no longer has to accumulate, because
deletion is now an explicit intent rather than an implicit prefilter.

### Shape B — entire directive families have no intent at all

| Family | Directives | Why it matters |
|---|---|---|
| Conditional control flow | `mk disable-if`, `mk replace-if`, `mk block set` (rows 10, 11, 12) | Cannot disable an upstream `.if defined(X)` block or rewrite `.if ${OPSYS} == FreeBSD`. Agent reaches for `add_patch` to source-patch the Makefile — heavyweight and wrong shape. |
| Target heredocs | `mk target set`/`append` **create** (rows 13–14), `mk target remove` (15), `mk target rename` (16) | Convert can produce target heredocs; agent can edit bodies (`replace_in_dops_block`) and now delete whole blocks (`drop_target_block`, Step 39), but still cannot create a new one (B3), drop a Makefile target by name (B4), or rename (B5). |
| Compose-time file deletion | `file remove` (row 19) | Cannot tell compose to remove a file from the materialized tree (e.g. an upstream test fixture that breaks on DragonFly). |
| Line-level text editing | `text line-remove`, `text line-insert-after` (rows 20, 21) | Only line-level op the agent has is `text replace-once` via `replace_in_patch`. |

### Shape C — asymmetric coverage — **largely CLOSED by Step 39**

- `add_patch` ↔ `drop_patch` (existing) and `add_file` ↔ `drop_file` (Step 39)
  are now symmetric create/delete pairs, partitioned by destination shape
  (`dragonfly/patch-*` → drop_patch, everything else → drop_file).
- `change_makefile` ↔ `drop_mk_directive` is symmetric across all four kinds.
- Remaining asymmetry: target heredocs have delete (`drop_target_block`) but
  no agent-side create (still convert-only — Family B3, `add_target_block`).
- `bump_portrevision` re-emit still accumulates (no auto-strip, by design
  post-38e); delete a stale `mk set PORTREVISION` via `drop_mk_directive`.

### Shape D — heredoc bodies are read-modify-write only

`replace_in_dops_block` can edit text inside `mk target set/append` heredocs
and `drop_target_block` (Step 39) can now delete the whole block — but the
agent still cannot *create* a `mk target` heredoc (Family B3) and nothing
covers `mk block set condition` heredocs (Family B2).

---

## Concrete scenarios the agent cannot express

Step 39 closed the delete-side scenarios (a convert-produced target heredoc
that's no longer needed → `drop_target_block`; a wrong `mk add` → `drop_mk_directive`;
a stray non-patch `file copy`/`file materialize` → `drop_file`). What remains
are the Family B create/control-flow shapes, which still surface as "agent
thrash" or a workaround that composes correctly but leaves substrate dirty:

1. Upstream Makefile has `.if defined(NLS)` that should be disabled on
   DragonFly → no intent for `mk disable-if`. Agent reaches for `add_patch`
   (heavyweight and wrong shape — the framework has a dedicated directive).
   → Family B1 (`change_condition`).
2. Agent wants to add a new `dfly-patch:` target heredoc to overlay.dops
   → no agent-side create. Convert-only territory. → Family B3
   (`add_target_block`).
3. Agent wants compose to remove a file from the materialized tree (an
   upstream test fixture that breaks on DragonFly) → no `file remove`
   intent. → Family B6 (`remove_file_at_compose`).
4. Agent needs a single-line removal inside a patched Makefile (no
   replacement, just delete) → no intent for `text line-remove`.
   → Family B7 (`edit_line`).

---

## Implementation work items

Ordered by leverage (highest first). Each item is independently landable.
Mark `[x]` when committed.

### Family A — explicit deletes — **SHIPPED (Step 39)**

> **History.** Family A was originally specced as *self-stripping* (A1/A2/A3:
> make create/delete pairs auto-cancel in the substrate). **Step 38e reversed
> that approach** — implicit prefilters were scope-blind and corrupted
> multi-target overlays, so the self-strip mechanism was removed and the
> project adopted "no implicit cleanup." Family A was re-solved with *explicit*
> scope-aware delete intents:

- [x] **`drop_mk_directive`** (replaces A1/A2/A3) — removes one
  `mk set/unset/add/remove VAR` line; `set`/`unset` match by key, `add`/`remove`
  match key + token; scope-aware; hard-refuses on zero or ambiguous match.
  Shipped Step 39a (`bfb6ae8bcde`).
- [x] **A4 → `drop_file`** — removes a non-patch `file copy`/`file materialize`
  line and deletes the materialized file; refuses `dragonfly/patch-*` (routes
  to `drop_patch`). Decision: **option 2** (separate intent, keeps names
  accurate). Shipped Step 39b (`ff9bf706b53`).
- [x] **A5 → `drop_target_block`** — removes a whole `mk target set/append NAME`
  heredoc (open through close); scope-aware; refuses on zero/ambiguous/corrupt.
  Shipped Step 39c (`4830bc9342d`). Playbooks + prompt wiring: Step 39d
  (`a1b67fd40cc`).

The old A1/A2/A3 self-strip items and Decision Q4 (op=unset self-strip
semantics) are **abandoned by design** — re-emit accumulates, deletion is
explicit. See the architecture backlog Step 38e/39 for the full record.

### Family B — missing directive intents (higher cost per item)

Each item adds one new intent type for a directive family that has no
agent surface today.

- [ ] **B1: `change_condition` intent → `mk disable-if` and `mk replace-if`**
  - Single intent with mode discriminator (`disable` vs `replace`). Lets
    the agent disable or rewrite upstream `.if` blocks without source-patching.
  - Schema fields: `condition` (the expr to match), `contains` (optional
    body-anchor disambiguator), and for `replace`: `from`/`to` for the
    new condition expr.
  - Files: new schema, grammar dataclass, `_dops.py` renderer.

- [ ] **B2: `add_block` intent → `mk block set condition "X" <<TAG ... TAG`**
  - Lets the agent inject a conditional heredoc block (shell stanza
    gated on a `.if` condition).
  - Schema fields: `condition`, `body` (multi-line), `tag` (optional —
    auto-generate like `MK1`, `MK2`, …).
  - Files: new schema, grammar dataclass, `_dops.py` renderer.

- [ ] **B3: `add_target_block` intent → `mk target set/append NAME <<TAG ... TAG`**
  - Lets the agent introduce a new Makefile target (e.g. `dfly-patch:`)
    or append to an existing one.
  - Schema fields: `name`, `body`, `mode` (set vs append), `tag` (optional).
  - Files: new schema, grammar dataclass, `_dops.py` renderer.

- [ ] **B4: `drop_target_makefile` intent → `mk target remove NAME`**
  - Distinct from A5: this asks the compose engine to delete a Makefile
    target by name (e.g. a stray `do-install:` upstream defines that
    breaks on DragonFly). A5 deletes the dops heredoc; this one tells
    compose to remove the target from the composed Makefile.
  - Schema fields: `name`.
  - Files: new schema, grammar dataclass, `_dops.py` renderer.

- [ ] **B5: `rename_target` intent → `mk target rename OLD -> NEW`**
  - Mostly for completeness; rename is rare but exists in the grammar.
  - Schema fields: `old`, `new`.

- [ ] **B6: `remove_file_at_compose` intent → `file remove PATH`**
  - Lets the agent remove a file from the composed port tree.
  - Schema fields: `path`, `on_missing` (skip / fail).

- [ ] **B7: `edit_line` intent → `text line-remove` and `text line-insert-after`**
  - Mode discriminator: `mode: "remove"` (with `exact`) or `mode:
    "insert_after"` (with `anchor` + `line`).
  - Scope: same as `replace_in_patch` (refuse dragonfly/ and .dops).
  - Files: new schema, grammar dataclass, `_dops.py` renderer.

### Family C — generalization (deferred — bigger blast radius)

- [ ] **C1: Single `edit_overlay(action, directive_kind, key, …)` intent**
  - Generic dispatcher: agent specifies what shape and what key, engine
    matches+strips or creates as appropriate.
  - Defer until Family A and B are landed and we see usage patterns. If
    Family A + B close the visible gaps, this is unnecessary; if agents
    routinely need shapes outside the enumeration, this becomes the
    pressure-relief valve.

### Cross-cutting playbook updates

After each Family A or B item lands, update the relevant playbook:

- [x] `intent-change_makefile.md` — cross-links to `drop_mk_directive` for
  deleting a prior line; notes re-emit accumulates (last-wins), no auto-strip.
- [x] `intent-drop_mk_directive.md` (new, Step 39d).
- [x] `intent-drop_file.md` (new, Step 39d) — path-partition vs `drop_patch`.
- [x] `intent-drop_target_block.md` (new, Step 39d).
- [ ] Family B (when landed): `intent-change_condition.md` (B1),
  `intent-add_block.md` (B2), `intent-add_target_block.md` (B3), etc.

---

## Decision points

Family A decisions are all resolved (see history above). Remaining:

1. ~~Scope of this push~~ — **resolved**: Family A shipped (Step 39);
   Family B is next (Step 40).
2. **Naming convention** — **resolved**: kept the `add_X` / `drop_X`
   symmetry (`drop_file`, `drop_target_block`, `drop_mk_directive`).
3. ~~`drop_patch` generalization vs new `drop_file`~~ — **resolved**:
   separate `drop_file`, partitioned by `dragonfly/patch-*`.
4. ~~`op=unset` self-strip semantics~~ — **moot post-38e**: no renderer
   self-strips; deletion is explicit via `drop_mk_directive`.
5. **Family C now or later** — **deferred** behind a 41a re-evaluation
   gate (see architecture backlog Step 41); land Family B specifics first.
