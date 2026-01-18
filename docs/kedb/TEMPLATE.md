# Known Issue: <short descriptive title>

## Pattern
- `<exact error message or pattern from build logs>`
- `<another pattern variant>`

## Cause
<1-3 sentences explaining why this happens on DragonFlyBSD>

## Fix

### Option 1: <fix approach name>
<description of the fix>

```makefile
# Example Makefile addition
CFLAGS+=        -DSOME_FLAG
```

### Option 2: <alternative fix>
<description>

```diff
--- file.orig
+++ file
@@ -1,3 +1,3 @@
 context line
-old line
+new line
 context line
```

## Examples
- `category/portname`: <brief description of how it was fixed or current status>
