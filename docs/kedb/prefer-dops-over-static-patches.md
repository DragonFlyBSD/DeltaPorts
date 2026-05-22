# Known Issue: Static patch fails after upstream version bump

## Pattern

- Triage classification = `patch-error`.
- Failure message:
  - `<N> out of <M> hunks failed--saving rejects to <file>.rej`
  - `===> FAILED Applying dragonfly patch-<file>`
  - `===> FAILED to apply cleanly dragonfly patch(es)  patch-<file>`
- The failing patch lives at `ports/<origin>/dragonfly/patch-*`.
- The DeltaPorts STATUS file shows a `Last success:` version that is
  older than the upstream `DISTVERSION` in the materialized
  `Makefile` (i.e. FreeBSD upstream has moved forward while the
  DeltaPorts overlay's patches haven't been updated).

## Cause

DeltaPorts overlays FreeBSD ports. When FreeBSD bumps a port's
`DISTVERSION`, `materialize_dports` picks up the new upstream Makefile
and distinfo automatically — but the DragonFly-specific patches under
`ports/<origin>/dragonfly/` do not update themselves. If the patch
targets a file that upstream regenerates between versions
(e.g. `Makefile.in` produced by `automake`, `configure` produced by
`autoconf`, anything autotools), the hunk context shifts on every
upstream bump and the patch fails.

This is a recurring failure mode. The cheap fix is to regenerate the
patch against the new context (works once, breaks again on the next
bump). The durable fix is to express the intent as a `dops`
operation that survives upstream churn.

## Fix

### Option 1 (preferred for autotools-generated files) — convert to dops

If the failing patch's target file is *generated* (configure,
Makefile.in, libtool m4 outputs, etc.) and the change is conceptually
a small textual substitution, express it as a dops `text` or
`mk replace-if` operation against the *generated output*, applied at
`pre-configure` or `post-patch` time.

```dops
# overlay.dops
port devel/libuv
type port
target @any
reason "DragonFly needs FreeBSD case-matches to include dragonfly too"

# Instead of dragonfly/patch-Makefile.in (regenerated every upstream bump),
# express the same logical change as a substitution that runs at build time
# regardless of how upstream regenerated the file:

text replace-once file ${WRKSRC}/Makefile.in \
    from "freebsd*)" \
    to   "freebsd*|dragonfly*)"
```

When to prefer this:

- The patch logic is a small number of substitutions on lines that
  reliably exist across versions (OS detection, platform conditionals).
- The target file is generated (likely to regenerate on upstream
  release).
- The substitution is contextual enough that a one-line `from→to`
  uniquely identifies the target line.

Pair with **removing the original static patch file**:

```sh
git rm ports/<origin>/dragonfly/patch-<file>
```

The dops operation replaces it.

### Option 2 (preferred for upstream source) — patch the *source*, not the output

If the change is conceptually against `Makefile.am` (which generates
`Makefile.in`) or `configure.ac` (which generates `configure`),
patch the *source* and add a re-generation step. The patch survives
upstream bumps because the source files change less frequently than
their generated outputs.

```diff
--- Makefile.am.orig
+++ Makefile.am
@@ -10,7 +10,7 @@
-if FREEBSD
+if FREEBSD_OR_DRAGONFLY
```

Then either rely on `USES= autoreconf` (upstream conventions) or add
an explicit `pre-configure` step in `Makefile.DragonFly`.

### Option 3 (last resort) — regenerate the static patch

Only when neither dops nor source-side patching is feasible:

1. Use `wrksrc` from the `extract` tool's response (do not guess
   the path from `DISTVERSION` — the obj tree contains stale leftovers
   from prior builds).
2. Read the new upstream file, locate the same logical change, and
   regenerate the patch with the new context.
3. **Accept that this will break again on the next upstream bump.**
   Note in the commit message that this is a regenerate-only fix and
   that a dops conversion is the durable alternative.

## When NOT to convert

Keep the static patch when:

- The change spans many lines or multiple hunks with complex context
  dependencies that don't reduce to a small set of substitutions.
- The patch adds or removes whole functions / blocks rather than
  tweaking lines.
- The target file is hand-maintained upstream (not generated). These
  patches don't decay on every release because the source is stable.

## Examples

- `devel/libuv`: `dragonfly/patch-Makefile.in` is a candidate — the
  hunks adjust autotools `FREEBSD_TRUE` and `am__append_*` blocks
  that automake regenerates on each upstream bump. Converting the
  per-line conditionals to `text replace-once` ops against the
  generated `Makefile.in` (or to patches against `Makefile.am`)
  would remove this whole class of recurring breakage.
- A future scan should flag every `ports/*/*/dragonfly/patch-Makefile.in`
  and `ports/*/*/dragonfly/patch-configure` as a candidate for dops
  conversion.
