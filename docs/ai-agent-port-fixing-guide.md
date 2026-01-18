# AI Agent Guide: Fixing FreeBSD Ports for DragonFly BSD

## Overview

You are tasked with fixing FreeBSD ports to work on DragonFly BSD. FreeBSD ports are maintained in a separate tree, and DragonFly-specific customizations are stored in the **DeltaPorts** repository as overlays.

Your goal: Analyze build errors, patch failures, or compatibility issues, then apply the appropriate fix using the DeltaPorts overlay system.

---

## Port Customization Methods

You have 7 methods available to fix ports. Choose based on the type of fix needed:

### 1. **Diff Patches** (Most Common)
**Location:** `ports/<category>/<port>/diffs/<filename>.diff`

**When to use:**
- Small targeted changes to existing files
- Modifying Makefile variables
- Patching source code
- Fixing pkg-plist entries

**Format:** Unified diff format (context diffs)

**Example:**
```diff
--- Makefile.orig
+++ Makefile
@@ -10,7 +10,7 @@
 USES=          cmake
 
-LIB_DEPENDS=   libfoo.so:devel/foo
+LIB_DEPENDS=   libfoo.so:devel/foo \
+               libbar.so:devel/bar
```

**How to create:**
```bash
cd /usr/ports/category/port
# Make your changes
diff -u Makefile.orig Makefile > /path/to/deltaports/ports/category/port/diffs/Makefile.diff
```

**File naming convention:**
- `Makefile.diff` - patches Makefile
- `pkg-plist.diff` - patches pkg-plist
- `files_patch-foo.diff` - patches files/patch-foo (underscore separates subdirs)
- `scripts_configure.diff` - patches scripts/configure

### 2. **Makefile.DragonFly** (Add-on Variables)
**Location:** `ports/<category>/<port>/Makefile.DragonFly`

**When to use:**
- Adding DragonFly-specific dependencies
- Setting compiler flags for DragonFly
- Disabling options that don't work
- Adding conditional logic based on OPSYS
- When you don't want to patch the original Makefile

**How it works:** Content is appended to the port's Makefile during merge

**Example:**
```make
# Makefile.DragonFly

# DragonFly needs an extra library
LIB_DEPENDS+=   libepoll-shim.so:devel/libepoll-shim

# Use clang-specific flags
CFLAGS+=        -Wno-error=implicit-function-declaration

# Disable broken option
OPTIONS_EXCLUDE=        SYSTEMD

# Mark as broken on old DragonFly versions
.if ${DFLYVERSION} < 600000
BROKEN=         requires DragonFly 6.0 or later
.endif
```

**Best practices:**
- Always add comments explaining why each change is needed
- Use `+=` to append to existing variables
- Use `.if` conditionals for version-specific fixes

### 3. **dragonfly/ Overlay Directory** (Replace/Add Files)
**Location:** `ports/<category>/<port>/dragonfly/`

**When to use:**
- Adding entirely new files (patches, scripts)
- Replacing files completely (simpler than large diffs)
- Adding DragonFly-specific documentation
- Providing alternative pkg-message content

**How it works:** Files are copied directly into the merged port, overwriting if they exist

**Example structure:**
```
ports/www/nginx/dragonfly/
├── files/
│   ├── patch-src-os-unix-ngx_dragonfly.h    # New DragonFly-specific patch
│   └── patch-src-core-ngx_cycle.c           # Replace FreeBSD's version
├── pkg-message                               # Replace entire pkg-message
└── Makefile.DragonFly.inc                    # Additional makefile fragment
```

**When to use dragonfly/ vs diffs:**
- Large file changes → Use `dragonfly/` (full replacement)
- Small targeted changes → Use `diffs/` (patch)
- New files that don't exist in FreeBSD → Use `dragonfly/`

### 4. **REMOVE File** (Delete Unwanted Files)
**Location:** `ports/<category>/<port>/diffs/REMOVE`

**When to use:**
- Removing FreeBSD-specific patches that break DragonFly
- Removing files that cause conflicts
- Deleting obsolete scripts

**Format:** Plain text, one file path per line (relative to port directory)

**Example:**
```
# diffs/REMOVE
# Remove FreeBSD-specific patches that break on DragonFly
files/patch-freebsd-only
files/patch-linux-detection
scripts/check-freebsd-version.sh
```

**Best practices:**
- Add comments explaining why each file is removed
- Paths are relative to the port directory

### 5. **Quarterly-Specific Diffs** (Version-Specific Fixes)
**Location:** `ports/<category>/<port>/diffs/@2025Q1/<filename>.diff`

**When to use:**
- Fix only applies to a specific FreeBSD quarterly branch
- Different FreeBSD versions need different patches
- Temporary workaround until FreeBSD fixes upstream

**Example structure:**
```
ports/www/nginx/diffs/
├── Makefile.diff              # Applied to all quarterlies
├── @2025Q1/
│   └── patch-old-api.diff     # Only for 2025Q1
└── @2025Q2/
    └── patch-new-api.diff     # Only for 2025Q2
```

**Quarterly format:** `@<YEAR>Q<1-4>` (e.g., `@2025Q1`, `@2026Q3`)

**Precedence:** Quarterly-specific diffs are applied AFTER base diffs

### 6. **DPORT (Custom Port - Not FreeBSD Derived)**
**Location:** `ports/<category>/<port>/newport/`

**When to use:**
- Port doesn't exist in FreeBSD (DragonFly-specific tool)
- Port is so heavily modified that starting fresh is simpler
- Port has different upstream sources on DragonFly

**How it works:** Ignore FreeBSD entirely, use `newport/` as the complete port

**Example structure:**
```
ports/sysutils/dragonfly-toolbox/
├── overlay.toml          # Must set type = "dport"
└── newport/
    ├── Makefile
    ├── distinfo
    ├── pkg-descr
    ├── pkg-plist
    └── files/
        └── patch-src.c
```

**overlay.toml must specify:**
```toml
version = "1.0"
origin = "sysutils/dragonfly-toolbox"
type = "dport"  # This tells the system to use newport/
description = "DragonFly-specific system tools"
```

### 7. **MASK (Exclude Port from DPorts)**
**Location:** `ports/<category>/<port>/overlay.toml`

**When to use:**
- Port is fundamentally incompatible with DragonFly (Linux-only, FreeBSD-specific kernel features)
- Port has unresolved security issues
- Port requires components not available on DragonFly
- Temporary exclusion while working on major fixes

**How to mask:**
```toml
version = "1.0"
origin = "emulators/linux-only-thing"
type = "mask"
reason = "Requires Linux-specific kernel features not available on DragonFly"
```

**Best practices:**
- Always provide a clear `reason` field
- Check if dependencies of popular ports are masked (breaks build chains)

---

## Automatic Transformations (Already Handled)

These transformations are applied automatically during the merge. **You don't need to fix these manually:**

| Issue | Automatic Fix | Example |
|-------|--------------|---------|
| Architecture references use `amd64` | Changed to `x86_64` | `BROKEN_amd64=` → `BROKEN_x86_64=` |
| OpenMP dependency | Removed (`libomp` not needed on DragonFly) | `libomp.so:devel/openmp` → deleted |
| Perl shebang | Fixed to DragonFly path | `#!/usr/bin/perl` → `#!/usr/local/bin/perl` |
| UIDs/GIDs | DragonFly-specific users added | `avenger:*:60149:`, `cbsd:*:60150:` |

**Don't create patches for these issues - they're handled automatically.**

---

## Decision Tree: Choosing the Right Method

```
Is the port completely incompatible with DragonFly?
├─ YES → Use MASK (overlay.toml with type="mask")
└─ NO → Continue

Is this a DragonFly-only port (not in FreeBSD)?
├─ YES → Use DPORT (newport/ directory)
└─ NO → Continue

Does the fix only apply to one FreeBSD quarterly?
├─ YES → Use Quarterly-Specific Diffs (diffs/@YEAR_Q/)
└─ NO → Continue

Do you need to remove files from the FreeBSD port?
├─ YES → Use REMOVE file (diffs/REMOVE)
└─ NO → Continue

Are you adding new files that don't exist in FreeBSD?
├─ YES → Use dragonfly/ directory
└─ NO → Continue

Are you making a large change (>50 lines) to a single file?
├─ YES → Consider dragonfly/ (full file replacement)
└─ NO → Continue

Do you need to add/modify Makefile variables without patching?
├─ YES → Use Makefile.DragonFly
└─ NO → Continue

Everything else (small targeted changes):
└─ Use Diffs (diffs/*.diff)
```

---

## Strategy for Common Error Types

### Build Errors

#### 1. **Missing Dependencies**
```
Error: undefined reference to `epoll_create'
```

**Fix:** Add missing library dependency in Makefile.DragonFly
```make
# Makefile.DragonFly
LIB_DEPENDS+=   libepoll-shim.so:devel/libepoll-shim
```

#### 2. **Compiler Errors (Implicit Declarations, Warnings-as-Errors)**
```
Error: implicit declaration of function 'strlcpy'
```

**Fix:** Add compiler flags in Makefile.DragonFly
```make
# Makefile.DragonFly
CFLAGS+=        -Wno-error=implicit-function-declaration
# Or include the right header via a diff patch
```

#### 3. **Platform Detection Issues**
```
Error: configure: error: unsupported operating system
```

**Fix:** Create a diff to patch configure or configure.ac
```diff
--- configure.orig
+++ configure
@@ -1234,6 +1234,9 @@
     freebsd*)
         os_type="freebsd"
         ;;
+    dragonfly*)
+        os_type="freebsd"  # DragonFly is BSD-compatible
+        ;;
```

#### 4. **Header/Include Issues**
```
Error: sys/event.h: No such file or directory
```

**Fix:** Patch source to use correct headers (create diff)
```diff
--- src/main.c.orig
+++ src/main.c
@@ -10,7 +10,11 @@
 #include <sys/types.h>
+#ifdef __DragonFly__
+#include <sys/event.h>
+#else
 #include <linux/event.h>
+#endif
```

#### 5. **Linker Errors**
```
Error: undefined reference to `pthread_create'
```

**Fix:** Add library to LDFLAGS in Makefile.DragonFly
```make
# Makefile.DragonFly
LDFLAGS+=       -lpthread
```

### Patch Application Errors

#### 6. **Patch Doesn't Apply (Offset/Context Mismatch)**
```
Error: patch: **** malformed patch at line 15
```

**Strategy:**
1. Check if FreeBSD port version changed (quarterly mismatch)
2. Use quarterly-specific diff if this is version-dependent
3. Regenerate the diff against the new version

**Fix:** Create fresh diff or quarterly-specific diff
```bash
# Create new diff
cd /usr/ports/category/port
# Make changes
diff -u file.orig file > /path/to/diffs/file.diff

# OR create quarterly-specific
mkdir -p /path/to/diffs/@2025Q2
diff -u file.orig file > /path/to/diffs/@2025Q2/file.diff
```

#### 7. **Conflicting Patches**
```
Error: patch: **** previously applied patch detected; assume -R
```

**Strategy:**
1. Check if FreeBSD already applied a similar fix upstream
2. Remove the now-unnecessary diff
3. Update overlay.toml to remove from `diffs` list

**Fix:** Remove the obsolete diff file and update overlay.toml

### Runtime/Packaging Errors

#### 8. **Missing Files in Package (pkg-plist errors)**
```
Error: files missing from pkg-plist
```

**Fix:** Patch pkg-plist with a diff
```diff
--- pkg-plist.orig
+++ pkg-plist
@@ -10,6 +10,7 @@
 bin/program
 lib/libfoo.so
+lib/libfoo.so.1  # DragonFly uses different library versioning
```

#### 9. **Wrong File Permissions**
```
Error: file installed with wrong permissions
```

**Fix:** Patch Makefile to fix installation
```diff
--- Makefile.orig
+++ Makefile
@@ -20,1 +20,1 @@
-       ${INSTALL_DATA} ${WRKSRC}/file ${STAGEDIR}${PREFIX}/bin/
+       ${INSTALL_SCRIPT} ${WRKSRC}/file ${STAGEDIR}${PREFIX}/bin/
```

#### 10. **FreeBSD-Specific Runtime Checks**
```
Error: startup script checks for FreeBSD kernel
```

**Fix:** Patch the script with a diff or replace via dragonfly/
```diff
--- files/startup.sh.orig
+++ files/startup.sh
@@ -5,7 +5,7 @@
-if [ "$(uname -s)" != "FreeBSD" ]; then
+if [ "$(uname -s)" != "FreeBSD" ] && [ "$(uname -s)" != "DragonFly" ]; then
     echo "Unsupported OS"
     exit 1
```

---

## overlay.toml Structure

Every customized port MUST have an `overlay.toml` manifest:

```toml
version = "1.0"
origin = "category/portname"
type = "port"  # or "dport", "mask"

# Optional: describe the customization
description = "Fixed build on DragonFly by adding libepoll-shim dependency"

# Optional: link to bug reports, upstream issues
upstream_url = "https://github.com/project/issues/1234"

# Specify which FreeBSD quarterly branches this applies to
# Omit for "all quarterlies"
quarterlies = ["2025Q1", "2025Q2"]

# Customization metadata
[customizations]
has_diffs = true
has_makefile_dragonfly = true
has_dragonfly_dir = false

# List of diff files
diffs = [
    "Makefile.diff",
    "files_patch-src.diff"
]

# Quarterly-specific diffs
[customizations.quarterly_diffs]
"2025Q1" = ["old-api.diff"]
"2025Q2" = ["new-api.diff"]
```

---

## Best Practices

### 1. **Minimal Changes**
- Make the smallest change that fixes the issue
- Don't refactor FreeBSD code unless necessary
- Prefer Makefile.DragonFly over patching Makefile when possible

### 2. **Upstream First**
- If the fix applies to FreeBSD too, submit upstream first
- Reference upstream PR/commit in overlay.toml
- Remove DragonFly-specific fix once FreeBSD merges it

### 3. **Documentation**
- Add comments in Makefile.DragonFly explaining each change
- Use clear commit messages
- Document workarounds in overlay.toml `description` field

### 4. **Testing**
- Test that the port builds: `cd /usr/dports/category/port && make`
- Test that it installs: `make install`
- Test that it packages: `make package`
- Test basic functionality after installation

### 5. **Quarterly Awareness**
- Check which FreeBSD quarterly you're working with
- If fix is version-specific, use quarterly-specific diffs
- Test against multiple quarterlies if possible

---

## File Locations Reference

```
DeltaPorts repository structure:

ports/
└── <category>/
    └── <portname>/
        ├── overlay.toml              # Required manifest
        ├── Makefile.DragonFly        # Optional: append to Makefile
        ├── diffs/                    # Optional: patches
        │   ├── REMOVE                # Optional: files to delete
        │   ├── Makefile.diff
        │   ├── pkg-plist.diff
        │   ├── @2025Q1/              # Optional: quarterly-specific
        │   │   └── old-version.diff
        │   └── @2025Q2/
        │       └── new-version.diff
        ├── dragonfly/                # Optional: overlay files
        │   ├── files/
        │   │   └── patch-new.c
        │   └── pkg-message
        └── newport/                  # Optional: for type="dport"
            ├── Makefile
            └── pkg-descr

special/                              # Infrastructure (don't usually modify)
├── Mk/
│   ├── diffs/
│   │   └── bsd.port.mk.diff
│   └── replacements/
│       └── Uses/
│           └── linux.mk
├── Templates/
│   └── diffs/
└── treetop/
    └── diffs/
```

---

## Workflow

### When Given an Error to Fix:

1. **Analyze the Error**
   - Read the full build log
   - Identify the root cause (missing dep, compiler error, patch failure, etc.)
   - Check if it's already fixed automatically (amd64, libomp, etc.)

2. **Choose the Method**
   - Use the decision tree above
   - Consider impact: small change = diff, large change = dragonfly/, broken = mask

3. **Implement the Fix**
   - Create the appropriate files in DeltaPorts repository
   - Update overlay.toml with metadata

4. **Test the Fix**
   - Merge the port: `dports merge category/portname --target 2025Q1`
   - Build it: `cd /usr/dports/category/portname && make`
   - Verify the fix worked

5. **Document**
   - Add clear comments
   - Update overlay.toml description
   - Commit with informative message

---

## Example Scenarios

### Scenario 1: Missing Library Dependency

**Error:**
```
/usr/bin/ld: undefined reference to `epoll_create1'
```

**Analysis:** Port uses Linux epoll API, needs shim library on BSD

**Solution:** Makefile.DragonFly
```make
# Makefile.DragonFly
# Add epoll-shim for Linux compatibility
LIB_DEPENDS+=   libepoll-shim.so:devel/libepoll-shim
```

**overlay.toml:**
```toml
version = "1.0"
origin = "www/example"
type = "port"
description = "Add libepoll-shim for Linux epoll API compatibility"

[customizations]
has_makefile_dragonfly = true
```

### Scenario 2: Compiler Warning Treated as Error

**Error:**
```
error: implicit declaration of function 'strlcpy' [-Werror=implicit-function-declaration]
```

**Analysis:** DragonFly has strlcpy but header not included

**Solution:** Create diff patch
```diff
--- src/utils.c.orig
+++ src/utils.c
@@ -1,5 +1,6 @@
 #include <stdio.h>
 #include <stdlib.h>
+#include <string.h>
 
 void copy_string(char *dst, const char *src) {
     strlcpy(dst, src, 256);
```

**Save as:** `diffs/files_patch-src-utils.c.diff`

**overlay.toml:**
```toml
version = "1.0"
origin = "devel/example"
type = "port"
description = "Add string.h include for strlcpy declaration"

[customizations]
has_diffs = true
diffs = ["files_patch-src-utils.c.diff"]
```

### Scenario 3: FreeBSD-Specific Patch Breaks DragonFly

**Error:**
```
patching file configure
Hunk #1 FAILED at 234
```

**Analysis:** FreeBSD has a patch that doesn't apply cleanly on DragonFly

**Solution:** Remove the FreeBSD patch
```
# diffs/REMOVE
files/patch-configure
```

**Then create DragonFly version:** `dragonfly/files/patch-configure`

**overlay.toml:**
```toml
version = "1.0"
origin = "net/example"
type = "port"
description = "Replace FreeBSD-specific configure patch with DragonFly version"

[customizations]
has_diffs = true
has_dragonfly_dir = true
diffs = []
```

---

## Commands Reference

```bash
# Merge a single port
dports merge category/portname --target 2025Q1

# Merge all ports
dports merge all --target 2025Q1

# Check if overlay is valid
dports check category/portname

# Verify patches apply
dports verify category/portname

# List all customized ports
dports list --customized

# Show diff between FreeBSD and DragonFly version
dports diff category/portname
```

---

## Summary

You have 7 methods to fix ports:
1. **Diffs** - Small targeted patches (most common)
2. **Makefile.DragonFly** - Add variables without patching
3. **dragonfly/** - Replace/add files
4. **REMOVE** - Delete unwanted files
5. **Quarterly diffs** - Version-specific fixes
6. **DPORT** - Entirely custom ports
7. **MASK** - Exclude broken ports

**Choose based on:**
- Size of change (small = diff, large = dragonfly/)
- Type of change (variables = Makefile.DragonFly, code = diff)
- Scope (version-specific = quarterly, platform-specific = any method)

**Always:**
- Create overlay.toml manifest
- Document your changes
- Test the build
- Prefer minimal changes

This guide should provide you with everything needed to diagnose and fix FreeBSD ports for DragonFly BSD using the DeltaPorts overlay system.
