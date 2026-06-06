---
triggers:
  flows: [patch]
tags: [overlay, dops, mk-var, scope, patch, genpatch, recovery]
priority: 55
---

# Patch-flow procedures — editing `overlay.dops` and port files

You edit `ports/<origin>/overlay.dops` **free-hand** in dops DSL: read
it with `grep`/`get_file`, write the new or changed lines with
`put_file`, then validate. There is no per-directive intent tool and no
file-delete tool — `put_file` (whole-file write) and `install_patches`
are your only write primitives, plus the build loop. The grammar itself
(every `mk`/`file`/`text` op, heredoc blocks, conditional ops) is in
`dops_reference()` — call it once when you're about to write a fresh
overlay. This file covers the *flow* knowledge the grammar reference
doesn't: the write/validate loop, `mk`-directive traps, scoping
judgment, the static-patch workflow, and recovery from a bad patch.

## Read the overlay through the engine, not raw

`get_file overlay.dops` returns the *literal* file — every `target @X`
section, every op regardless of scope. On a multi-target overlay you'd
have to apply the engine's scope filter in your head.

Use **`get_effective_overlay(origin)`** instead when you need to reason
about what compose will actually do. It runs the file through the engine
and returns:

- `target` — the env's compose target (the build line you're on).
- `effective_ops` — ops that **will** apply on this build, in
  declaration order, each tagged with `scope` and its engine `kind`
  (`mk.var.set`, `mk.var.token_add`, `patch.apply`, …). For `mk.var.*`
  the variable is in `name`, the value in `value`.
- `filtered_out` — ops scoped to *other* build lines, each with a
  `reason` for exclusion.

Raw `get_file overlay.dops` is still right for byte-exact inspection
(confirming a write landed in the section you intended); use the
effective view for "what applies here."

## Writing overlay.dops: the put_file → validate_dops loop

After every edit, **call `validate_dops(origin)` before you
`materialize_dports`.** A syntax error caught by validate costs nothing;
the same error caught by materialize wastes a whole build cycle.

- `validate_dops` runs the full engine check (lex → parse →
  document-level semantics) over the **entire** file. On failure it
  returns `ok=false` with a diagnostic carrying an `E_*` code plus
  `line:column`. Fix the offending line(s) and call it again until
  clean.
- **Change one logical thing at a time, then re-validate.** A single
  bad line is then rejected on its own with a precise diagnostic,
  instead of hiding inside a larger rewrite.
- Distinguish the two failure shapes:
  - **Your edit is invalid** (bad keyword, malformed heredoc, a
    duplicate document directive like a second `port`/`type`/`reason`/
    `maintainer` line) → fix the line and re-emit.
  - **The overlay was already broken before you touched it** → the file
    needs a human-authored fix. Retrying your edit won't help —
    **escalate** (`Rebuild Status: gave-up` with that diagnostic).
- For a `put_file` on any file you haven't `get_file`'d this session,
  pass `expected_sha256` from a prior read so the write is race-safe
  against stale content.

### Heredoc blocks — exact form

`mk target` recipes and `mk block` regions use heredocs. The engine is
strict:

```dops
mk target set post-patch <<'MK1'
${REINPLACE_CMD} -e 's,/usr/local,${PREFIX},' ${WRKSRC}/Makefile
MK1
```

- The opener must be **quoted**: `<<'MK1'`. A bare `<<MK1` is rejected
  with `E_PARSE_INVALID_HEREDOC_START: expected <<'TAG'`.
- The terminator line must equal the tag **exactly** — `MK1`, never
  `  MK1` or `MK1 `. Any leading/trailing whitespace and the block
  never closes (`overlay.dops is corrupt`), which no edit can recover
  from on a guess.
- Match the body's existing shell-recipe indentation (tabs) so the
  rendered overlay stays parseable as a Makefile target. To extend a
  recipe, rewrite the body with the extra line appended; there is no
  separate "append to body" op.

## `mk` directive semantics and traps

The grammar for `mk set/add/remove/unset` is in `dops_reference()`.
These are the non-obvious traps that cause silent wrong-builds:

### unset vs set-to-empty

To make an upstream-set variable *not present*, write `mk unset VAR` —
it deletes the whole assignment line, including whatever upstream
FreeBSD had. Do **not** "set it to empty": `FOO=` still counts as
**defined**, so framework code doing `.if defined(FOO)` stays true and
may still read an empty path and fail. `unset` is the clean atomic
answer (e.g. an upstream `LICENSE_FILE=${PORTSDIR}/COPYRIGHT` pointing
at a file absent from our tree — `mk unset LICENSE_FILE`, and the
license check falls back to the BSD2CLAUSE template default).

### add appends a token; it does NOT override a keyed value

`mk add VAR "token"` mirrors make's `+=`: create-or-append, idempotent
(re-adding a present token is a no-op). It is **not** a way to change
the value of an existing key inside a key-valued list like `PLIST_SUB`,
`SUB_LIST`, `MAKE_ENV`. If upstream has `PLIST_SUB= OSMAJOR=<x>` and you
`mk add PLIST_SUB "OSMAJOR=<y>"`, **both** land in the flattened value.
The framework builds a repeated `sed -e s!%%OSMAJOR%%!…!g` list, and
sed processes `-e` flags **first-match-wins**: the upstream (broken)
value wins and your "override" is dead code. There is no `mk` op to
rewrite an existing keyed value — use a patch or `text replace-once`
against the generated file.

### remove needs the variable to exist

`mk remove VAR "token"` takes a token *out* of a list; the assignment
must already exist (else `assignment not found`) and the token must be
present (else `token not found`). To make a whole variable go away you
want `unset`, not `remove`.

### ambiguity and accumulation

- A `set`/`unset`/`remove` that matches **more than one** upstream
  assignment of the same variable refuses with `E_APPLY_AMBIGUOUS_MATCH`
  — the engine won't guess which to rewrite. Narrow it with scope (see
  below) or hand-resolve. (`add` does not refuse on multi-assignment.)
- Re-emitting `mk set VAR` for the same key **accumulates** lines in the
  overlay. The composed Makefile is still correct (ops play in
  declaration order, last-wins), but the file carries every copy. To
  drop a superseded `mk` line, edit `overlay.dops` and delete that exact
  line (whole-token key match — `USE` won't match `USES`).

### grep the framework before guessing a variable

`Mk/bsd.port.mk` and friends are the source of truth for what a Makefile
variable means and what value-shape it requires. A wrong guess yields an
opaque error (`.for` arity mismatch, "Wrong number of words", a
missing-file error from a path you didn't construct). Before writing an
`mk` op for a variable whose value-shape you're inferring, grep first —
a few hundred tokens against `Mk/` beats a multi-thousand-token failed
build cycle:

```
grep("^[[:space:]]*<VAR>[[:space:]]*[+:?]?=", "/work/freebsd-ports/Mk")
grep("\\$\\{<VAR>\\}|\\$\\(<VAR>\\)", "/work/freebsd-ports/Mk")
grep("\\.for .* in .*<VAR>", "/work/freebsd-ports/Mk")
```

The first finds the definition/default; the second finds consumers; the
third finds `.for` iteration — the tell that the value is a
whitespace-separated list with fixed per-entry arity. Also worth
grepping: `Mk/Uses/*.mk` (per-`USES` behavior), `Mk/Features/*.mk`,
`Mk/Scripts/*.sh`.

## Scoping — `@any` vs a build-line target

The active scope is set by a `target` directive; ops inherit the most
recently named scope. `target @any` (the default prologue scope) applies
on **every** DragonFly build line. A `target @2026Q2` (or `@main`, etc.)
section applies only on that build line.

**Most fixes are universal.** DragonFly-vs-FreeBSD differences are
platform-level — they apply regardless of which quarterly snapshot
you're on. Keep them in the `@any` scope. Scoping a universal fix to one
build line over-restricts it: the same failure re-surfaces next quarter.

Reach for a build-line-specific `target @<quarter>` section only when the
fix genuinely differs per build line — upstream source that diverges
between quarterly snapshots so one patch can't cover both, or a framework
value deprecated between snapshots that older lines still need. The
concrete target you'd write is the env's compose target, visible as
`target` in `get_effective_overlay` (and from `env_verify`); write that
literal section header.

Scope is also a **disambiguation lever**: when an op refuses as ambiguous
because the same line/block exists under both `@any` and a quarterly
section, placing the op under the specific section targets exactly one.

PORTREVISION is an exception: it's part of the port's package identity,
so a bump scoped to one build line ships an un-bumped package on the
others. Keep PORTREVISION bumps in `@any`.

## Creating a static source patch (`dragonfly/*`)

`dragonfly/*` patches target **upstream source** inside the distfile
(`Makefile.am`, `src/foo.c`, …) — files that don't exist at compose
time, only after `do-extract` at build time. **Never `patch apply` a
`dragonfly/*` patch**; stage it with `file materialize dragonfly/X ->
dragonfly/X` so `bsd.port.mk`'s `do-patch` applies it at build time.
(`diffs/*.diff` framework patches are the opposite domain — see the
quickref's "Two kinds of patches".)

For a non-trivial source change, edit the source and let the engine
produce the diff rather than hand-writing one:

1. `make_extract(origin)` — populates WRKSRC; use the returned `wrksrc`
   path for all reads from here on. This is `do-extract` only: the
   distfile is unpacked **pristine**, no patches applied.
2. `make_patch(origin)` — **run this when the file you're about to edit
   is also touched by a FreeBSD `files/patch-*` (or an existing
   `dragonfly/*` patch).** It runs `do-patch`, which applies `files/*`
   then `dragonfly/*` in order, leaving WRKSRC in the real build-time
   state. `dragonfly/*` patches apply **after** `files/*` at build time,
   so your new patch's context must reflect the post-`files/*` source —
   not pristine upstream. Skip this step only when the target file is
   untouched by any framework patch (pristine == build state). The
   broken `dragonfly/*` patch you're regenerating has already been
   dropped from `overlay.dops` by convert's defer pass, so `do-patch`
   won't choke on it. On failure, the rejecting patch is named in the
   tool's `stdout_tail`.
3. `dupe(<wrksrc>/path/to/file.c)` — snapshots a `.orig` and exposes the
   file for editing. **Dupe AFTER make_patch** so the baseline is the
   post-`do-patch` state — that baseline is what genpatch diffs against.
4. `put_file <wrksrc>/path/to/file.c <new content>` — edit it.
   `put_file` to a WRKSRC path is allowed; to `ports/<origin>/` it is
   not — edit the overlay there, not the extracted source.
5. `genpatch(<same path>)` — runs `diff -u` between `.orig` and current,
   depositing a WRKSRC-relative `patch-*` file. (It picks up WRKSRC from
   the prior `make_extract` automatically.) Because the `.orig` baseline
   is post-`do-patch`, the hunk context matches what `do-patch` sees at
   build time and the patch applies cleanly.
6. `install_patches(origin)` — copies the generated patch into
   `ports/<origin>/dragonfly/`, then add the `file materialize` line to
   `overlay.dops` so compose stages it.

**`dupe` is only one step of this flow.** It exists solely to support
patch generation — it is not an investigation tool, not a "before"
snapshot for reading, and not a way to edit an existing
`dragonfly/patch-*`. A `dupe` with no follow-up `genpatch`/
`install_patches` in the same attempt is wasted work and a sign you
reached for the wrong tool. For a small change you can also write the
unified diff by hand from prior `get_file` reads and stage it directly.

## Recovering from a broken patch — never text-edit the diff

When a patch applies dirty (`E_APPLY_PATCH_FAILED`, "malformed patch",
wrong hunk count, line-number mismatch), the patch file is broken and
the only correct recovery is **remove and regenerate**:

1. Remove its install line from `overlay.dops` (rewrite via `put_file`)
   so compose stops referencing it.
2. Regenerate a correct patch via the dupe→genpatch flow above, or stage
   a corrected hand-written diff.

**Do not** text-edit the diff to "fix" line numbers. Editing a diff
shifts the hunk *body* but not the hunk *header*, producing a patch that
lies about its own bytes. The classic failure shape: a malformed patch
is "fixed" with successive in-place text edits, and every subsequent
`materialize_dports` then dies with `E_APPLY_MISSING_SUBJECT` against a
file that was never staged. Patch files are output artifacts — treat
them as regenerate-only.

## Removing directives and files

There is no delete tool. To stop applying something, **edit
`overlay.dops` and remove the relevant line or block**, then
re-validate:

- A `patch apply` / `file materialize` / `file copy` line → delete the
  line; compose stops applying it. (In dops mode the compat auto-copy is
  suppressed, so an unreferenced `dragonfly/*` source file left on disk
  is inert — it only reaches the build via an explicit `file materialize`
  line.)
- A whole `mk target <name> <<TAG … TAG` heredoc block → delete the
  opening line through the closing tag, inclusive.
- A single `mk set/add/remove/unset` line → delete that exact line.

When you remove a directive, do it as one focused `put_file` rewrite and
re-validate; don't try to counter a wrong op with a second op when
deleting the line is cleaner. The runner reconciles orphaned on-disk
artifacts at delivery time, so you don't need a file-delete primitive to
leave the substrate correct.

## Bumping PORTREVISION

When the port already builds at this upstream version but you changed
*how* it builds (added a patch, edited flags), bump PORTREVISION so
packagers rebuild the binary package. Write it as an explicit
`mk set PORTREVISION "<N>"` (compute `<N>` from the current value — a
hardcoded `"1"` is wrong if upstream already declares a higher
revision). Make the bump the **last** edit of the attempt: bumping
before you've confirmed the behavior change works leaves a stray
revision an operator has to walk back. PORTREVISION is not for the
first time a port is touched — that's an introduction, not a rebuild.
