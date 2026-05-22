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
# Drop a file alongside the port (from the DeltaPorts overlay).
file copy dragonfly/keep-alive.c -> files/keep-alive.c

# Copy a file from the overlay tree into the port at build time.
file materialize templates/Makefile.in.dragonfly -> Makefile.in

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

## Patch fallback

```dops
# When no semantic op fits, fall back to applying a static patch.
patch apply dragonfly/patch-too-complex-for-dops
```

Use sparingly. If you're reaching for `patch apply`, the patch
either (a) genuinely needs to be hand-written (multi-hunk, complex
context), or (b) hasn't yet been reduced to a `text` / `mk` op.

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
| Patch logic that doesn't reduce to any of the above | `patch apply` (fall back) |

## Conversion workflow (when overlay.dops doesn't exist yet)

1. List `/work/DeltaPorts/ports/<origin>/` and identify the compat
   artifacts: `Makefile.DragonFly[.<target>]`, `diffs/*.diff`,
   `dragonfly/patch-*` files, and any `newport/`.
2. For each patch / Makefile line, classify:
   - Single-line/few-line substitution → convert to `text replace-once`.
   - OS-detection block → convert to `mk replace-if` or `mk target`.
   - Complex multi-hunk patch → fall back to `patch apply` for now,
     plan to decompose later.
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
