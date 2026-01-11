# Known Issue: check-plist phase failures (Orphaned / Missing files)

## Pattern
- `Error: Orphaned: <path>`
- `Error: Missing: <path>`
- `===> Error: Plist issues found.`
- Stage succeeds but `check-plist` phase fails

Common variants:
- `Error: Orphaned: /man/man8/foo.8.gz` (file installed but not in plist)
- `Error: Missing: man/man8/foo.8.gz` (plist expects file but not installed)
- `Error: Orphaned: @dir /some/path` (absolute path in plist causes mismatch)

## Cause
The `check-plist` phase compares files actually installed in `STAGEDIR` against entries in `pkg-plist` (or `PLIST_FILES`). Mismatches occur when:

1. **Orphaned**: A file is installed but not listed in the plist (DragonFly builds extra files, or upstream plist is incomplete)
2. **Missing**: A file is listed in plist but not installed (DragonFly build skips some files, or upstream plist has stale entries)
3. **Path mismatch**: Absolute paths like `/etc/...` in plist vs relative `etc/...` expected by the framework

On DragonFlyBSD, differences in build configuration, enabled options, or platform-specific code paths can cause the installed file set to differ from FreeBSD.

## Fix

### Option 1: Single or few missing plist entries → `PLIST_FILES+=` in Makefile.DragonFly

For **Orphaned** errors (file exists but not in plist), add the missing entry:

```makefile
# ports/<cat>/<port>/Makefile.DragonFly
PLIST_FILES+=	man/man8/foo.8.gz
```

For multiple related entries:
```makefile
PLIST_FILES+=	libexec/nut/microsol-apc \
		man/man8/microsol-apc.8.gz
```

This is the preferred fix when only a small number of files need to be added.

### Option 2: Multiple changes or removals → `diffs/pkg-plist.diff`

For complex plist changes (many additions, removals, or path rewrites), create a unified diff:

```diff
# ports/<cat>/<port>/diffs/pkg-plist.diff
--- pkg-plist.orig	2023-01-02 19:50:10.000000000 +0100
+++ pkg-plist	2023-01-02 19:50:37.000000000 +0100
@@ -1,5 +1,5 @@
-@dir /etc/X11/xrdp
-/etc/X11/xrdp/xorg.conf
+@dir etc/X11/xrdp
+etc/X11/xrdp/xorg.conf
 lib/xorg/modules/drivers/xrdpdev_drv.so
```

Use this approach when:
- Multiple lines need to be added or removed
- Absolute paths need to be converted to relative paths
- Upstream plist has stale entries that should be removed

### Important: Avoid overriding FreeBSD-only make targets

Do NOT override standard targets like `do-install`, `post-install`, `pre-install` in `Makefile.DragonFly` — these can conflict with the FreeBSD port's own definitions.

Instead, use DeltaPorts-specific hooks:
- `dfly-patch:` — runs after `post-patch`
- `dfly-configure:` — runs after `post-configure`
- `dfly-build:` — runs after `post-build`
- `dfly-install:` — runs after `post-install` (for cleanup/fixups only)

For plist issues specifically, prefer `PLIST_FILES+=` or `diffs/pkg-plist.diff` over install-phase hacks.

## Examples

### Adding missing files via PLIST_FILES
- `sysutils/nut-devel`: Fixed orphaned files (commit `8ffa24d43d6`)
  ```makefile
  PLIST_FILES+=	libexec/nut/microsol-apc \
  		man/man8/microsol-apc.8.gz
  ```

- `www/jira-cli`: Added missing manpage
  ```makefile
  PLIST_FILES+=	man/man7/jira-sprint-add.7.gz
  ```

- `sysutils/cpu-microcode-intel`: Added directory entry (commit `440707d6763`)
  ```makefile
  PLIST_FILES+=	"@dir /boot/firmware"
  ```

### Patching pkg-plist via diffs/
- `x11-drivers/xorgxrdp`: Fixed absolute paths in plist
  ```diff
  -@dir /etc/X11/xrdp
  +@dir etc/X11/xrdp
  ```

- `net/freeswitch`: Added missing module (commit `2e2d6b59d43`)
  ```diff
  +lib/freeswitch/mod/mod_av.so
  ```

- `graphics/qt5-3d`: Added multiple missing cmake/plugin files (commit `9e2be93e20d`)

### Removing stale overlay entries
- `x11/cinnamon`: Removed `Makefile.DragonFly` that was adding obsolete plist entries (commit `e2753b339e6`)
