---
name: dops-convert
description: Convert a DeltaPorts compat port (Makefile.DragonFly with conditionals/.for, non-variable Makefile.diff hunks, and other diffs/ artifacts the deterministic translator could not handle) into an engine-valid overlay.dops, offline. Use for the Step 48 mass-convert "blocked"/"deferred" tail. Produces a written overlay.dops validated against the dops engine; faithfulness is the steady-state build loop's job, not yours.
---

# DeltaPorts compat → dops conversion (offline)

## What this skill is for

The Step 48 mass-convert already handled the deterministic bulk. You get
the **tail** the deterministic translator escalated: ports whose
`Makefile.DragonFly` has `.if/.elif/.else`/`.for`, whose `Makefile.diff`
has recipe/conditional hunks, or that carry odd `diffs/` artifacts. Your
job is to translate each port's compat artifacts into a single
`overlay.dops` that the **dops engine accepts** (parses + checks + plans).

**Your success bar is ENGINE-VALID, not faithful.** A converted port's
output need not byte-match compat. The steady-state build loop verifies
behavior and fixes residual divergence later. So: capture the intent as
best you can with real ops, make it engine-valid, move on. Do **not**
try to build anything — there is no build env here.

## The hard rules (these bite silently — read first)

1. **No partial absorption.** Writing `overlay.dops` flips the port to
   dops-mode, which *suppresses the entire compat path*. So you MUST
   absorb **every** compat artifact of the port into the one overlay:
   `Makefile.DragonFly`, **all** `diffs/*`, and `dragonfly/`. If you
   cannot handle even one artifact, **escalate the whole port** — do not
   write a partial overlay (it would silently drop the unhandled
   artifact's effect).

2. **`dragonfly/` STAYS on disk.** `dragonfly/*` are upstream-source
   patches; the overlay references them via `file materialize` and the
   engine reads them from `dragonfly/` at compose time. Emit
   `file materialize dragonfly/X -> dragonfly/X` for each file and
   **do NOT delete `dragonfly/`** — it is the materialize source.
   Never `patch apply` a `dragonfly/*` file (no extracted source exists).

3. **Delete what you absorbed.** After writing the overlay, delete
   `Makefile.DragonFly*` and every `diffs/*` file you translated (and the
   now-empty `diffs/` dir). Keep `dragonfly/`.
   **EXCEPTION — `patch apply diffs/X`:** if your overlay references a diff
   via `patch apply diffs/X`, that diff is NOT absorbed into ops — it is
   applied verbatim at compose time, so **KEEP it**. Deleting it makes apply
   fail with `E_APPLY_MISSING_SUBJECT` (patch file does not exist). Only
   delete `diffs/*` files whose effect you fully translated into mk/text ops.

4. **STATUS + type.** Read `ports/<origin>/STATUS` first line:
   - `PORT` or absent → header `type port`, and **delete STATUS**.
   - `MASK` / `DPORT` / `LOCK` → header `type mask|dport|lock` (must
     match), and **keep STATUS**.

5. **`target @any`** is the default and correct for unscoped artifacts —
   an unscoped fragment applies on every branch. Only use `@main` /
   `@2026Qn` if the source artifact is itself target-scoped
   (`Makefile.DragonFly.@2026Q2`, `diffs/@2026Q2/...`).

## File shape

```dops
port <category>/<name>
type port
reason "<one-line why>"
target @any

# ops, each on its own logical line
```

## Op grammar (authoritative: scripts/generator/dportsv3/agent/dops_quickref.md)

```dops
# variables
mk set   VAR "value"          # always quote the value
mk eval  VAR "${VAR:mod}"     # self-referential / immediate `:=` (see below)
mk add   VAR token            # quote tokens with >,:,",spaces
mk remove VAR token
mk unset VAR

# conditionals
mk disable-if condition "${OPSYS} == FreeBSD"
mk replace-if from "${OPSYS} == FreeBSD" to "${OPSYS} == DragonFly"
mk block set condition "${OPSYS} == DragonFly" <<'MK'
	CFLAGS+=	-DSHIM
MK

# recipes
mk target set post-extract <<'MK'
	@${REINPLACE_CMD} -e 's,a,b,' ${WRKSRC}/configure
MK
mk target append pre-configure <<'MK'
	@${ECHO_CMD} hi
MK
mk target remove pre-install
mk target rename do-install -> do-install-dragonfly

# files / text
file materialize dragonfly/patch-X -> dragonfly/patch-X   # stage upstream-source patch
file remove files/patch-stale on-missing noop
text line-remove file Makefile exact "BROKEN= x" on-missing noop
text line-insert-after file pkg-plist anchor "<prev line>" line "<new>" on-missing noop
text replace-once file Makefile from "<old single line>" to "<new single line>"

# last resort for a gnarly diffs/*.diff (framework files only, NOT dragonfly/)
patch apply diffs/X.diff
```

Most ops accept `on-missing error|warn|noop` (default `error`). Use
`noop` when the change may already be absent on some branches.

## Per-artifact conversion recipes

**`Makefile.DragonFly`** (the fragment — its lines are appended to the port Makefile):
- `VAR= x` / `VAR?= x` / `VAR:= x` → `mk set VAR "x"`
- `VAR+= a b` → `mk add VAR a` + `mk add VAR b` (or `mk add VAR "a b"`)
- `.undef VAR` → `mk unset VAR`
- `.if COND ... .endif` block → `mk block set condition "COND" <<'MK' ... MK`,
  or `mk replace-if` / `mk disable-if` if it edits an existing upstream `.if`
- **Named pattern — `.if !defined(DPORTS_BUILDER)` guard** (very common):
  `.if !defined(DPORTS_BUILDER)` / `MANUAL_PACKAGE_BUILD= ...` / `.endif`
  → `mk block set condition "!defined(DPORTS_BUILDER)" <<'MK'` …body… `MK`
- **Self-referential assignment `VAR:= ${VAR:mod}`** (filter `:N`/`:M`,
  substitute `:S`/`:C`, prepend, append — the value expands the same var) →
  `mk eval VAR "${VAR:mod}"`. This appends a verbatim immediate `:=` line and
  is faithful for EVERY modifier. Do NOT use `mk set` for a self-referential
  value — it renders a fatal recursive `=` ("Variable X is recursive").
- `target:` recipe → `mk target set target <<'MK' ...recipe... MK`
- `.for ... .endfor` → usually genuinely hard; if you cannot express it
  cleanly, **escalate** the port.

**`diffs/Makefile.diff`** (unified diff vs the port Makefile):
- in-place variable change → `mk set/add/remove`
- `.if` condition change → `mk replace-if from "..." to "..."`
- single-line change → `text replace-once file Makefile from "..." to "..."`
- recipe block change → `mk target set`
- if too gnarly → `patch apply diffs/Makefile.diff` (engine applies it
  against the materialized Makefile — valid fallback for `diffs/` only)

**`diffs/REMOVE`** → one `file remove <path> on-missing noop` per line.

**`diffs/pkg-message.diff` / `diffs/pkg-descr.diff`** → `text replace-once`
per changed line, or `patch apply diffs/X.diff`.

**`diffs/*.in.diff` / `diffs/files_*.diff`** (patch a port template) →
`patch apply diffs/X.diff` (framework-file domain).

**`dragonfly/*`** → `file materialize dragonfly/X -> dragonfly/X` for each
file. KEEP `dragonfly/`.

**`diffs/pkg-plist.diff`** → line ops on `pkg-plist`: removals →
`text line-remove file pkg-plist exact "<line>" on-missing noop`;
single-line changes → `text replace-once file pkg-plist from "<old>" to "<new>"`;
additions → `text line-insert-after file pkg-plist anchor "<preceding line>" line "<new>" on-missing noop`
(anchor must be a line unique in the upstream pkg-plist — if not, escalate).
NOTE: pkg-plist order is load-bearing (`@mode`/`@owner`/`@group` apply to
following lines; `@exec`/`@unexec` run in order) — keep edits in place;
never reorder.

**`diffs/distinfo.diff`** → distinfo is generated checksum data; if you
can't reproduce it as ops, **escalate** (don't guess hashes).

## Workflow per port

1. `ls ports/<origin>/` — enumerate artifacts (`Makefile.DragonFly*`,
   `diffs/*`, `dragonfly/*`, `STATUS`).
2. Read each artifact. Translate per the recipes above into one op list.
   If ANY artifact is beyond you → escalate (skip; report it), do not
   write a partial overlay.
3. Write `ports/<origin>/overlay.dops` (header + ops).
4. **Validate against the engine** (this is your only correctness gate):
   ```
   scripts/generator/.venv/bin/python -c "from dportsv3.engine.api import build_plan; from pathlib import Path; p=Path('ports/<origin>/overlay.dops'); r=build_plan(p.read_text(), p); print('OK' if r.ok else [(d.code, d.message) for d in r.diagnostics])"
   ```
   If not `OK`, fix the ops from the diagnostics and re-run until `OK`.
   (Common: unquoted value with `>`/`:`/`"`; heredoc tag mismatch;
   invalid op syntax — consult dops_quickref.md.)
5. Delete absorbed artifacts: `Makefile.DragonFly*`, the translated
   `diffs/*` files, empty `diffs/`, and `STATUS` (only if type=port).
   Keep `dragonfly/`.
6. Record the result (origin, converted/escalated, one-line note).

## Ambiguous variables (multiply-defined upstream)

`mk set` / `mk remove` edit an *existing* assignment in the upstream
Makefile. If the target variable is assigned **more than once** upstream
(e.g. `CMAKE_ARGS` defined `=` then `+=`, or `OPTIONS_DEFAULT` defined
twice), the op is ambiguous and fails at *compose-apply* time with
`E_APPLY_AMBIGUOUS_MATCH` — even though `build_plan` (engine-valid) passes.

**`contains` is NOT valid on `mk set`/`mk add`/`mk remove`** (only on
`mk disable-if`/`mk replace-if`/`mk block set`). To handle a multiply-defined
var, pick by INTENT:
- **append a token** (the DragonFly fragment used `VAR+= tok`) →
  `mk add VAR tok`. `mk add` is never ambiguous — on a multiply-defined var
  it appends a fresh `VAR+= tok` line, which accumulates correctly.
- **replace the whole value** (the fragment used `VAR= ...` or `VAR:= ...`,
  i.e. a trailing override) → `mk eval VAR "..."`. This appends an immediate
  `VAR:= ...` line that overrides all prior definitions — faithful to the
  fragment's replace intent. (Check the original operator: `+=` ⇒ `mk add`,
  `=`/`:=` ⇒ `mk eval`.)
- **multiply-matched `.if` block** (a `mk block`/`disable-if`/`replace-if`
  condition matches more than one upstream `.if`) → add `contains
  "<substring unique to the intended block>"`.
- if you can't pin it, **escalate** the port.

A `text replace-once` whose `from` string occurs more than once also fails
ambiguous — use a longer `from` that is unique in the file, or escalate.

Note: `build_plan` is necessary but NOT sufficient — it plans the overlay
but never applies it against the real upstream Makefile. Ambiguous-match
and missing-subject errors only surface at compose-apply, which is the
authoritative gate the main agent runs per batch.

## Escalation

Escalate (leave the port untouched, report it) when:
- a `.for` loop or deeply nested/computed conditional has no clean dops form,
- a `distinfo.diff` needs hashes you can't derive,
- a `pkg-plist` addition has no unique anchor,
- the overlay won't go engine-valid after a couple of honest attempts.

Escalation is a fine outcome — a port we leave compat is safe; a port we
half-convert is not.

## Reporting

Per batch, return: count converted vs escalated, the escalated origins
with one-line reasons, and any recurring pattern worth folding back into
this skill (note under "Skill update suggestions").
