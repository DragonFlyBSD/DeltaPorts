# dops Quick Reference (on-demand)

This is the condensed cheat-sheet returned by the ``dops_reference``
tool. Call it **once** at most per patch attempt, only when you've
confirmed `overlay.dops` doesn't exist for the origin and you're
about to write one. Re-calling on later turns wastes tokens.

For the full normative grammar see `docs/dsl-v0.md`. This file is
the minimal subset patch agents need 90% of the time.

## File shape

```dops
# overlay.dops at ports/<category>/<name>/overlay.dops
port <category>/<name>
type port
target @any                  # or @main, @2026Q2, @2026Q1, etc.
reason "<one-line why this overlay exists>"

# ... operations follow, each on its own logical line ...
```

- One origin per file. `port` directive is required exactly once.
- `target` sets the active scope. Multiple `target` directives are
  allowed; operations inherit the most recently named scope.
- Comments start with `#` (outside heredoc bodies).

## Makefile variable ops

```dops
mk set    DRAGONFLY_NEEDS_FOO "yes"
mk unset  USES_BROKEN_ON_DRAGONFLY
mk add    USES libtool
mk remove USES gmake

# Optional behavior when the var isn't found:
mk set FOO "bar" on-missing error    # default: fail if not found
mk set FOO "bar" on-missing warn     # warn + insert new
mk set FOO "bar" on-missing noop     # silently insert new
```

## Conditional / block ops

```dops
# Comment out an .if block matching an exact condition.
mk disable-if condition "${OPSYS} == FreeBSD"

# Rewrite one .if condition to another.
mk replace-if from "${OPSYS} == FreeBSD" \
              to   "${OPSYS} == FreeBSD || ${OPSYS} == DragonFly"

# Insert/replace a whole .if ... .endif region.
mk block set condition "${OPSYS} == DragonFly" <<'MK'
	CFLAGS+=	-DNEEDS_DRAGONFLY_SHIM
	LDFLAGS+=	-lcompat
MK
```

`contains "<anchor>"` filters candidates by substring when the same
condition appears more than once.

## Make target / recipe ops

```dops
mk target set post-extract <<'MK'
	@${REINPLACE_CMD} -e 's,freebsd\*),freebsd*|dragonfly*),' \
		${WRKSRC}/configure
MK

mk target append pre-configure <<'MK'
	@${ECHO_CMD} 'building for DragonFly' >&2
MK

mk target remove pre-install
mk target rename do-install -> do-install-dragonfly
```

## File / text ops

```dops
# Stage a file from the DeltaPorts overlay (source) into the
# materialized port_root. THIS is the op for "bring something
# from the overlay into the compose output."
file materialize dragonfly/keep-alive.c -> files/keep-alive.c

# Same op, common case: stage a `dragonfly/*` patch alongside the
# port so bsd.port.mk's do-patch picks it up at build time.
file materialize dragonfly/patch-Makefile.am -> dragonfly/patch-Makefile.am

# Duplicate a file WITHIN port_root (e.g. after an earlier op
# staged it). Both src and dst are relative to the materialized
# port tree; `file copy` does NOT read from the source overlay.
file copy Makefile.in.dragonfly -> Makefile.in

# Drop a file out of the port.
file remove files/patch-stale on-missing warn

# Edit a single line of any file in the port tree.
text line-remove file Makefile exact "BROKEN= unsupported"
text line-insert-after file Makefile \
     anchor "USES= cpe libtool" \
     line   "USES+= compiler:c11"
text replace-once file configure.ac \
     from "AC_DEFINE(HAVE_FREEBSD)" \
     to   "AC_DEFINE(HAVE_FREEBSDLIKE)"
```

`text replace-once` is the single most useful op for converting
static patches against generated files. If the patch's hunk is just
"change line A to line B", a `text replace-once` against the
generated file (or against the *source* of the generated file, e.g.
Makefile.am instead of Makefile.in) replaces it.

## Two kinds of patches — different ops

DeltaPorts has two separate patch domains. They look similar but
compose treats them entirely differently. Mixing them up is the
single most common conversion mistake.

### A. `diffs/*.diff` — framework-level patches

Patches under the port's `diffs/` directory target the **FreeBSD
ports framework files** that compose materializes into `port_root`:
the port's own `Makefile`, `distinfo`, `pkg-descr`, etc. After
compose's seed_output stage these files exist in `port_root`, so a
`patch -p0 -i diffs/X.diff` with `cwd=port_root` legitimately finds
its target and applies cleanly.

Direct-ops mostly *replaces* this whole category:

- `mk set/add/remove/replace-if/block set` and `text replace-once`
  express the same change semantically. Always prefer these.
- `patch apply diffs/X.diff` exists as a fallback when the patch
  is too gnarly for semantic ops. The engine reads the patch from
  the source overlay and applies it against the materialized
  framework files in `port_root` — works as advertised.

If `diffs/Makefile.diff` adjusts a variable in the port's Makefile,
convert it to a `mk` op. Only fall back to `patch apply diffs/...`
when semantic ops genuinely cannot express the change.

### B. `dragonfly/*` — upstream-source patches

Patches under `dragonfly/` target **upstream source files** that
live inside the distfile tarball (e.g. `Makefile.am`, `Makefile.in`,
`src/foo.c`). Those files are NOT in `port_root` at compose time —
they're inside the tarball and only appear at build time when
`bsd.port.mk`'s `do-extract` runs.

**Never use `patch apply` for `dragonfly/*` patches.** The compose
engine has no extracted source to apply against; the patch will
fail with no target file.

The correct op is `file materialize` — stage the patch from the
DeltaPorts overlay into `port_root` so the build phase picks it up.
(Note: `file copy` is a *within-`port_root`* duplication op, NOT
overlay→port_root; the names are easy to confuse.)

```dops
file materialize dragonfly/patch-Makefile.am -> dragonfly/patch-Makefile.am
file materialize dragonfly/patch-Makefile.in -> dragonfly/patch-Makefile.in
```

This matches what compat-mode's `payload_files` flow already does
automatically when there's no `overlay.dops`. Under dops mode that
auto-copy is suppressed (see `I_COMPOSE_MODE_DOPS_SUPPRESSES_COMPAT`),
so the dops must declare the staging explicitly.

If a `dragonfly/*` patch is simple enough — e.g. one OS-detection
substitution — prefer rewriting it as a `mk target` recipe with
`REINPLACE_CMD` in `post-extract`, which is more durable than a
static patch against generated files.

## On-missing modifiers

Most ops accept `on-missing error|warn|noop`. Default is `error`.
`warn` is the right choice when an op is idempotent across targets
(e.g. removing a fix that's already been upstreamed in some
branches).

## When to use which

| Symptom | Op |
|---|---|
| OS detection patch (configure, configure.ac) | `text replace-once` against configure.ac, or `mk target` with REINPLACE_CMD in post-extract |
| Adding a CFLAG / LDFLAG | `mk add CFLAGS -D<NAME>` or `mk set CFLAGS "..."` |
| Removing FreeBSD-only USES feature | `mk remove USES <feature>` |
| Adjusting a Makefile.in / config file at build time | `mk target set/append pre-configure` with REINPLACE_CMD recipe |
| Substituting one identifier in a source file | `text replace-once` against the file |
| Inserting a whole `.if DragonFly ... .endif` block | `mk block set condition "..."` |
| Framework patch logic that doesn't reduce to mk/text | `patch apply diffs/X.diff` (only for `diffs/`, not `dragonfly/`) |
| Upstream-source patch (anything under `dragonfly/`) | `file materialize dragonfly/X -> dragonfly/X` (stage, do NOT patch) |

## Conversion workflow (when overlay.dops doesn't exist yet)

1. List `/work/DeltaPorts/ports/<origin>/` and identify the compat
   artifacts: `Makefile.DragonFly[.<target>]`, `diffs/*.diff`,
   `dragonfly/patch-*` files, and any `newport/`.
2. Classify each artifact by **domain** first (see "Two kinds of
   patches" above), THEN by complexity:

   **Framework patches (`Makefile.DragonFly`, `diffs/*.diff`):**
   - Single-line/few-line substitution → `text replace-once` or
     `mk set/add/remove`.
   - OS-detection `.if` block → `mk replace-if` / `mk disable-if`
     / `mk block set`.
   - Recipe addition/override → `mk target set/append`.
   - Genuinely complex (multi-hunk, conditional logic) → fall back
     to `patch apply diffs/X.diff` (engine applies against
     compose-materialized framework files in `port_root` — works).

   **Upstream-source patches (`dragonfly/*`):**
   - Simple bounded substitution → consider rewriting as
     `mk target set post-extract` with `REINPLACE_CMD`, which is
     more durable than a static patch against generated files.
   - Otherwise → `file materialize dragonfly/X -> dragonfly/X`. ALWAYS
     stage; NEVER `patch apply` for these. `bsd.port.mk`'s
     `do-patch` will apply them at build time.

3. Write `/work/DeltaPorts/ports/<origin>/overlay.dops` with the
   equivalent ops.
4. For any artifact you migrated to a semantic op, delete the
   redundant compat file from the overlay (`put_file` with empty
   content is not enough — use the worker's file-removal path if
   exposed, or note the cleanup in your Conversion Proof so the
   handler can finalize it).
5. **Do not run a build.** Verification is the handler's job and
   runs as `reapply` (compose), not `dsynth_build`. Your task ends
   with the rewrite + the Conversion Proof block.
