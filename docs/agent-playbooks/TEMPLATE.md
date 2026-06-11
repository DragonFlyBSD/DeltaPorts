---
# The selector attaches this entry to a payload when its triggers
# match. Empty list = wildcard for that axis. Empty `triggers` block
# as a whole = always loaded (use sparingly).
triggers:
  classifications: []      # e.g. [patch-error, compile-error]; from triage
  toolchains: []           # e.g. [autoconf, cmake]; from port toolchain detection
  flows: [triage, patch]   # which agent roles can see this entry
tags: []                   # operator-facing labels; no selector logic
priority: 100              # smaller = drop later under budget; default 100
---

# <category prefix>: <short descriptive title>

<!--
File naming: <category>-<short-slug>.md
  error-*       reactive build-error patterns
  flow-*        flow-level procedures for an agent role
  toolchain-*   port toolchain "usual suspects" playbooks

Keep entries focused: one pattern per file, ~100 lines max.

Pick the trigger axis that best matches WHEN the agent needs this
knowledge:

  - error-*       primary axis: classifications
  - flow-*        primary axis: flows
  - toolchain-*   primary axis: toolchains
-->

## Pattern
- `<exact error message, directive, or detection signal>`
- `<another variant>`

## Cause
<1-3 sentences explaining the underlying reason.>

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
