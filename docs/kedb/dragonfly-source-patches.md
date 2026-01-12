# Known Issue: Creating DragonFly-specific source patches

## Pattern
- When upstream source needs modification for DragonFlyBSD
- Missing preprocessor symbols (e.g., `IFM_IEEE80211_VHT5G`)
- BSD-specific code paths needing conditional compilation
- Header differences from FreeBSD

## Cause
DragonFlyBSD shares BSD heritage but has diverged from FreeBSD. Some kernel
headers, system macros, and feature flags differ. When upstream code assumes
FreeBSD-specific features, patches are needed to add conditional compilation.

## Fix

### Option 1: DragonFly-specific patch in dragonfly/ directory

Create a patch file at `ports/<category>/<port>/dragonfly/patch-<description>`:

```diff
--- src/file.c.orig
+++ src/file.c
@@ -100,6 +100,8 @@ function_name(args)
 	some_code();
+#if defined(SOME_DRAGONFLY_SYMBOL)
 	code_using_symbol();
+#endif
 	more_code();
```

**IMPORTANT**: 
- Use `.orig` suffix for the original file (standard diff convention)
- The patch file goes in `dragonfly/` NOT `files/` (that's for FreeBSD patches)
- DragonFly patches apply AFTER FreeBSD `files/patch-*` files

### Option 2: Makefile.DragonFly with CFLAGS

For simple cases where a preprocessor define suffices:

```makefile
CFLAGS+=	-DMISSING_SYMBOL=0
```

### Option 3: Disable feature via OPTIONS

If the problematic code is behind an option:

```makefile
OPTIONS_DEFAULT:=	${OPTIONS_DEFAULT:NPROBLEMATIC_OPTION}
```

## Line Number Considerations

When creating DragonFly patches, be aware that:

1. **FreeBSD patches apply first**: If `files/patch-foo` exists and modifies the
   same file, your line numbers must account for those changes
   
2. **Check the intermediate state**: The source your patch applies to is AFTER
   FreeBSD patches but BEFORE DragonFly patches

3. **Use enough context**: Include 3+ context lines to handle minor line shifts

## Diff Format for Overlay

When generating a diff for the DeltaPorts overlay, the path format is:

```diff
--- /dev/null
+++ b/ports/net/example/dragonfly/patch-vht5g-guard
@@ -0,0 +1,10 @@
+--- src/drivers/driver_bsd.c.orig
++++ src/drivers/driver_bsd.c
+@@ -638,6 +638,8 @@ bsd_set_freq(void *priv, struct hostapd_freq_params *freq)
+ 	} else {
++#if defined(IFM_IEEE80211_VHT5G)
+ 		mode = freq->vht_enabled ? IFM_IEEE80211_VHT5G :
++#endif
+ 		...
```

Note: This creates a NEW file containing the patch content.

## Examples
- `net/hostapd`: Missing `IFM_IEEE80211_VHT5G` symbol - wrapped in `#if defined()` guard
- `net/wpa_supplicant`: Similar VHT5G issue on older DragonFly versions
