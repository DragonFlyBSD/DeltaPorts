---
# Frontmatter is forward-looking — ignored at load time today,
# honored by Step 27b's selector. Set the triggers that describe
# WHEN this entry should be attached to a payload. Empty list =
# wildcard for that axis. Empty `triggers` block as a whole = always
# loaded (use sparingly).
triggers:
  classifications: []      # e.g. [patch-error, compile-error]; from triage
  intents: []              # e.g. [replace_in_dops_block]; from patch-flow tool surface
  toolchains: []           # e.g. [autoconf, cmake]; from port toolchain detection
  convert_phases: []       # e.g. [picking_target]; for convert agent
  flows: [triage, patch]   # which agent roles can see this entry
tags: []                   # operator-facing labels; no selector logic
priority: 100              # smaller = drop later under budget; default 100
---

# <category prefix>: <short descriptive title>

<!--
File naming: <category>-<short-slug>.md
  error-*       reactive build-error patterns
  intent-*      patch-flow intent usage recipes
  convert-*     convert-agent procedures
  toolchain-*   port toolchain "usual suspects" playbooks

Keep entries focused: one pattern per file, ~100 lines max.

Pick ONE primary trigger axis per entry. An entry that declares
BOTH `triggers.classifications` AND `triggers.intents` will
attach via the system payload (load_playbooks) AND show up in
intent_reference results — double-attach. Pick the axis that
best matches WHEN the agent needs this knowledge:

  - error-*       primary axis: classifications
  - intent-*      primary axis: intents
  - convert-*     primary axis: convert_phases + flows=[convert]
  - toolchain-*   primary axis: toolchains
-->

## Pattern
- `<exact error message, intent name, or detection signal>`
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
