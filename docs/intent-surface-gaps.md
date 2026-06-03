# Intent surface gap analysis

Reference document for closing the gaps between the patch agent's intent
surface and the dops directive grammar. Used as implementation tracker.

## Problem statement

The patch agent's intent surface is **constructive-heavy and corrective-thin**.
Most intents create or append; very few modify or delete. Out of 23 distinct
dops directive shapes the engine understands, only **1** has fully-symmetric
create+delete intent coverage. **13** have no intent at all. **20** have no
delete path from `overlay.dops`.

When the agent needs to undo, surgically remove, or restructure something —
its own prior output, a convert artifact, an upstream wrong-for-DragonFly
assignment — its only move is to append a counter-op or reach for a
heavyweight workaround. Either choice accumulates dead-weight in
`overlay.dops` or produces the wrong shape of fix.

The shape of the gaps suggests the intent surface was built outward from
the convert agent's needs (lots of CREATE, one-shot direction). The patch
agent's needs are inverse: tweak, undo, surgically remove. That asymmetry
is not reflected in the intent set.

---

## Reference: intent → dops mapping

Canonical list. 7 intent types total. Source: `grammar.py:16-24` +
`_dops.py` renderer bodies.

| Intent | Emits into `overlay.dops` | Strip-prefilter | Side effect on disk |
|---|---|---|---|
| `add_file` (kind=resource) | `file copy <dest> -> <dest>` | none | writes file under `ports/<origin>/<dest>` |
| `add_file` (kind=materialize) | `file materialize <src> -> <dst>` | none | — |
| `add_patch` (inline) | `patch apply <target>` | refuses if file exists | writes patch under `ports/<origin>/<target>` |
| `add_patch` (from_dupe=true) | `patch apply <target>` | refuses if file exists | reads from WRKSRC genpatch, writes patch |
| `bump_portrevision` | `mk set PORTREVISION "1"` | **none** (creates duplicates on re-emit) | — |
| `change_makefile` op=set | `mk set VAR "value"` | strips prior `mk set VAR ...` | — |
| `change_makefile` op=append | `mk add VAR "value"` | none | — |
| `change_makefile` op=remove | `mk remove VAR "value"` | none (does NOT strip `mk add VAR value`) | — |
| `change_makefile` op=unset | `mk unset VAR` | none (does NOT strip `mk set/add VAR ...`) | — |
| `drop_patch` | strips `patch apply <target>` OR `file materialize ... -> <target>` (only `dragonfly/patch-*` shape on `file materialize`) | match-and-strip on both shapes | deletes patch file on disk (both shapes) |
| `replace_in_dops_block` | edits body of `mk target set/append <name> <<TAG ... TAG` | n/a (in-place body edit) | — |
| `replace_in_patch` (non-dragonfly, non-`.dops` only) | `text replace-once file <target> from "X" to "Y"` | none | — |

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
| 6  | `mk set VAR "v"` | ✅ change_makefile op=set | ✅ re-emit auto-strips | ❌ | no intent to delete a `mk set` line outright |
| 7  | `mk unset VAR` | ✅ change_makefile op=unset | ❌ (re-emit appends) | ❌ | no intent to delete |
| 8  | `mk add VAR tok` | ✅ change_makefile op=append | ❌ | ❌ | `op=remove` appends a counter-op, doesn't strip |
| 9  | `mk remove VAR tok` | ✅ change_makefile op=remove | ❌ | ❌ | no intent to delete |
| 10 | `mk disable-if condition "X" [contains "Y"]` | ❌ | ❌ | ❌ | cannot disable upstream `.if` blocks |
| 11 | `mk replace-if from "X" to "Y" [contains "Z"]` | ❌ | ❌ | ❌ | cannot rewrite upstream conditions |
| 12 | `mk block set condition "X" <<TAG ... TAG` | ❌ | ❌ | ❌ | conditional heredocs — `replace_in_dops_block` does NOT cover this shape |
| 13 | `mk target set NAME <<TAG ... TAG` | ❌ (convert-only) | ✅ replace_in_dops_block | ❌ | cannot delete a target heredoc |
| 14 | `mk target append NAME <<TAG ... TAG` | ❌ | ✅ replace_in_dops_block | ❌ | playbook doesn't mention modify, but the code supports it |
| 15 | `mk target remove NAME` (Makefile-level target deletion, no body) | ❌ | n/a | ❌ | distinct from deleting the `mk target set` heredoc itself |
| 16 | `mk target rename OLD -> NEW` | ❌ | n/a | ❌ | no intent at all |
| 17 | `file copy SRC -> DST` | ✅ add_file kind=resource | ❌ | ⚠️ drop_patch only for `dragonfly/patch-*` destinations | non-patch file copies cannot be removed |
| 18 | `file materialize SRC -> DST` | ✅ add_file kind=materialize | ❌ | ⚠️ drop_patch only for `dragonfly/patch-*` destinations | non-patch materializes cannot be removed |
| 19 | `file remove PATH` | ❌ | ❌ | ❌ | agent cannot tell compose to remove a file |
| 20 | `text line-remove file P exact "X"` | ❌ | ❌ | ❌ | no intent |
| 21 | `text line-insert-after file P anchor "X" line "Y"` | ❌ | ❌ | ❌ | no intent |
| 22 | `text replace-once file P from "X" to "Y"` | ⚠️ replace_in_patch — refused for `dragonfly/` and `.dops` | ❌ | ❌ | scope-limited, no delete |
| 23 | `patch apply PATH` | ✅ add_patch | ❌ | ✅ drop_patch (also deletes file) | only fully-symmetric create/delete pair |

### Counts

- **Directives with NO intent at all** (Create + Modify + Delete all ❌, headers excluded): 10, 11, 12, 15, 16, 19, 20, 21 — **8 of 18 non-header rows**.
- **Directives with NO delete intent**: rows 1–22 except 23 (and partial for 17, 18) — **20 of 23**.
- **Fully agent-manageable directives** (Create + Delete): only row 23. **1 of 23**.

---

## Systemic gaps by shape

### Shape A — no general "delete a dops line" intent

Every `mk` directive lacks a delete path. The agent can only add counter-ops:

| Want to undo… | Best available today | Result on disk |
|---|---|---|
| `mk set VAR "x"` | nothing (op=unset emits a new line) | both lines stay |
| `mk unset VAR` | nothing | line stays forever |
| `mk add VAR tok` | `op=remove` appends `mk remove` | add + remove pair |
| `mk remove VAR tok` | nothing | line stays forever |

Every `change_makefile` op except `op=set` is append-only at the substrate
level. Composed Makefile may come out right; overlay accumulates dead-weight.

### Shape B — entire directive families have no intent at all

| Family | Directives | Why it matters |
|---|---|---|
| Conditional control flow | `mk disable-if`, `mk replace-if`, `mk block set` (rows 10, 11, 12) | Cannot disable an upstream `.if defined(X)` block or rewrite `.if ${OPSYS} == FreeBSD`. Agent reaches for `add_patch` to source-patch the Makefile — heavyweight and wrong shape. |
| Target heredocs | `mk target set` create, `mk target append` create, `mk target remove`, `mk target rename` (rows 13–16) | Convert can produce target heredocs; agent can edit bodies but cannot delete the whole block, create a new one, or rename. |
| Compose-time file deletion | `file remove` (row 19) | Cannot tell compose to remove a file from the materialized tree (e.g. an upstream test fixture that breaks on DragonFly). |
| Line-level text editing | `text line-remove`, `text line-insert-after` (rows 20, 21) | Only line-level op the agent has is `text replace-once` via `replace_in_patch`. |

### Shape C — asymmetric coverage

- `add_patch` ↔ `drop_patch` is the only fully-symmetric create/delete pair.
- `add_file` has no `drop_file` counterpart (drop_patch covers only patch-shaped paths).
- `bump_portrevision` lacks a strip-prefilter — re-emit creates duplicates.

### Shape D — heredoc bodies are read-modify-write only

`replace_in_dops_block` can edit text inside `mk target set/append` heredocs
but cannot create the heredoc, cannot delete it, doesn't cover `mk block
set condition` heredocs.

---

## Concrete scenarios the agent cannot express

These all surface as "agent thrash" or "agent emits a workaround that
happens to compose correctly but leaves substrate dirty":

1. Convert produced a target heredoc (e.g. `dfly-patch:`) that's no longer
   needed → no way to delete it. Best the agent can do is gut the body
   to `@true` via `replace_in_dops_block`, leaving an empty target on disk.
2. A prior `mk add USES <tok>` is now wrong → only available move is to
   append `mk remove USES "<tok>"`. Add+remove pair persists on disk.
3. Convert emitted a `file copy ports/<origin>/files/extra.c -> files/extra.c`
   that should be removed → no intent. `drop_patch` refuses non-patch paths.
4. Upstream Makefile has `.if defined(NLS)` that should be disabled on
   DragonFly → no intent for `mk disable-if`. Agent reaches for `add_patch`
   (heavyweight and wrong shape — the framework has a dedicated directive).
5. Agent wants to add a new `dfly-patch:` target heredoc to overlay.dops
   → no intent. Convert-only territory.
6. Agent wants to delete a previously-emitted `file materialize` line for
   a generated file → no intent.
7. Agent needs a single-line removal inside a patched Makefile (no
   replacement, just delete) → no intent for `text line-remove`.

---

## Implementation work items

Ordered by leverage (highest first). Each item is independently landable.
Mark `[x]` when committed.

### Family A — symmetric create/delete and self-stripping (low blast radius)

Extend existing intent renderers so create/delete pairs cancel out cleanly
in the substrate. No new intent surface. Each item is a `_dops.py` change
+ test.

- [ ] **A1: `change_makefile op=remove` self-strips matching `mk add VAR <value>`**
  - When `op=remove` is called and overlay has a matching `mk add VAR value`
    line, strip the `mk add` and emit nothing. If no match, fall back to
    current behavior (append `mk remove`).
  - Closes the dmidecode shape. Symmetric with how `op=set` already strips
    prior `mk set` via `_strip_existing_mk_set`.
  - Files: `_dops.py::change_makefile`, new helper `_strip_existing_mk_add`.
  - Tests: positive (strip), negative (no match → fallback), preserved
    (existing op=remove behavior on upstream-defined tokens).

- [ ] **A2: `change_makefile op=unset` self-strips matching `mk set VAR ...` and `mk add VAR ...`**
  - When `op=unset` is called and overlay has matching `mk set VAR ...`
    or `mk add VAR ...` lines, strip them. Emit `mk unset` only if the
    variable is also defined upstream (no way to detect — so always emit
    as a backstop; engine handles no-op gracefully).
  - Note: the existing renderer doc deliberately keeps `op=unset`
    plain-append (line 412-422) because of the agent-invented-variable
    case. Re-examine: does the rationale still hold if we self-strip
    only matching `mk set/add` for the SAME key emitted earlier in this
    same overlay? Need to think through this carefully before landing.

- [ ] **A3: `bump_portrevision` strips prior `mk set PORTREVISION ...` like op=set does**
  - Currently re-emit creates duplicate `mk set PORTREVISION` lines.
  - One-line fix: pass `_strip_existing_mk_set("PORTREVISION")` to
    `_append_overlay`.
  - Files: `_dops.py::bump_portrevision`.

- [ ] **A4: `drop_patch` (or new `drop_file`) handles non-patch file copy/materialize**
  - Today drop_patch refuses targets not matching `dragonfly/patch-*` for
    the `file materialize` shape.
  - Two options:
    1. Extend `drop_patch` to handle any `file copy` / `file materialize`
       destination (loosen the looks-like-patch guard). Same intent name
       but broader meaning.
    2. New `drop_file` intent for non-patch destinations; keep `drop_patch`
       patch-shaped.
  - Decision: pick one. Option 1 is fewer intents; option 2 is cleaner naming.
  - Files: `_dops.py::drop_patch` + possibly new schema + grammar entry.

- [ ] **A5: `drop_target_block` for `mk target set/append` heredocs**
  - New intent. Matcher reuses `replace_in_dops_block`'s block-finder.
  - Strips the open line, body, close line. Returns `ok=False` if block
    not found (consistent with drop_patch shape).
  - Files: new schema, grammar entry, `_dops.py` renderer.
  - Tests: positive (existing `mk target set X <<TAG ... TAG` → gone),
    not-found, multiple blocks of same name (refuse or strip first?).

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

- [ ] `intent-change_makefile.md` — document op=remove self-stripping (A1),
  op=unset self-stripping (A2), and that `mk add … mk remove` add+remove
  pairs are no longer produced.
- [ ] `intent-bump_portrevision.md` (if it exists; create if not) — note
  that re-emission is idempotent (A3).
- [ ] `intent-drop_patch.md` or new `intent-drop_file.md` — scope (A4).
- [ ] New `intent-drop_target_block.md` (A5), `intent-change_condition.md`
  (B1), `intent-add_block.md` (B2), `intent-add_target_block.md` (B3),
  etc.

---

## Decision points (resolve before starting any item)

1. **Scope of this push** — Family A only (minimum viable correctness),
   Family B only (close the worst no-tool gaps), or both?
2. **Naming convention** — keep the `add_X` / `drop_X` symmetry (A4 →
   `drop_file`, A5 → `drop_target_block`), or adopt a verb taxonomy
   (`create_X`, `delete_X`, `edit_X`)?
3. **`drop_patch` generalization (A4 option 1) vs new `drop_file`
   (A4 option 2)** — extending `drop_patch` to non-patch paths blurs its
   name; a new `drop_file` is cleaner but adds an intent.
4. **`op=unset` self-strip semantics (A2)** — the existing renderer
   deliberately keeps it plain-append per `_dops.py:412-422`. Re-examine
   whether the rationale still holds for "same-overlay set+unset" case;
   document the decision either way.
5. **Family C now or later** — write the door but don't open it (skip
   for now), or land C1 instead of B1–B7 (single bigger change)?
