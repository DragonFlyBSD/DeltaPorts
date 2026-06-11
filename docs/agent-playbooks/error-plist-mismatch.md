---
triggers:
  classifications: [plist-error, pkg-format]
  flows: [triage, patch]
tags: [check-plist, pkg-plist]
priority: 100
---

# check-plist failures (Orphaned / Missing files)

## Pattern
- `Error: Orphaned: <path>` — installed in `STAGEDIR` but not listed
- `Error: Missing: <path>` — listed but not installed
- `Error: Orphaned: @dir /some/path` — absolute path in plist
- `===> Error: Plist issues found.`
- Stage succeeds, then `check-plist` fails

## Cause
`check-plist` compares files actually installed in `STAGEDIR` against the
plist entries (`pkg-plist`, or `PLIST_FILES` in the Makefile). On
DragonFly, build-config / option / platform differences make the
installed set diverge from FreeBSD:

1. **Orphaned** — a file is installed but not listed (DragonFly builds an
   extra file, or upstream's plist is incomplete).
2. **Missing** — a file is listed but not installed (DragonFly skips it,
   or upstream's plist has a stale entry).
3. **Path mismatch** — absolute `/etc/...` in the plist vs relative
   `etc/...` the framework expects.

## Fix — edit `overlay.dops`, never `Makefile.DragonFly`

You author dops ops only. `Makefile.DragonFly` authoring is refused
(authoring lock), and an `overlay.dops` suppresses the compat path, so a
`diffs/pkg-plist.diff` on a dops port is dead. The two plist surfaces:

- **Port lists files in `pkg-plist`** → edit `pkg-plist` with `text`
  ops (most ports).
- **Port lists files via `PLIST_FILES` in the Makefile** → use `mk add`.

### Orphaned — add the entry

`pkg-plist` port — insert after a stable neighbor line:
```dops
text line-insert-after file pkg-plist \
     anchor "man/man8/foo.8.gz" \
     line   "man/man8/bar.8.gz"
```

`PLIST_FILES` port — append the token:
```dops
mk add PLIST_FILES "man/man8/bar.8.gz"
```
`mk add` mirrors `+=` (idempotent). For a directory entry, the value is
`@dir <path>` (e.g. `mk add PLIST_FILES "@dir /boot/firmware"`).

### Missing — remove the stale entry

Only after confirming (via the build log / `grep`) that the file
genuinely isn't installed on DragonFly and shouldn't be:
```dops
text line-remove file pkg-plist exact "lib/foo/legacy.so"
```
If the file *should* install but doesn't, that's a build problem, not a
plist problem — fix the build, don't delete the entry.

### Path mismatch — rewrite the line

```dops
text replace-once file pkg-plist \
     from "@dir /etc/X11/xrdp" \
     to   "@dir etc/X11/xrdp"
```

### Many changes at once

When a single `pkg-plist` needs many additions/removals/rewrites, a
re-cut diff is cleaner than a wall of `text` ops:
```dops
patch apply diffs/pkg-plist.diff
```
(`patch apply` is valid for `diffs/*` framework files — NOT for
`dragonfly/*`, which must be `file materialize`d.)

## Don't reach for install-phase hacks

Don't add `do-install` / `post-install` recipe overrides to fix a plist
mismatch. If a genuine post-install fixup is unavoidable, express it as a
dops recipe op — `mk target set post-install <<'TAG' … TAG'` — not a
compat `dfly-install:` target. For plain orphaned/missing/path issues,
the `text` / `mk add PLIST_FILES` ops above are the right tools.
