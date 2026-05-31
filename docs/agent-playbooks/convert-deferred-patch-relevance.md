---
triggers:
  classifications: [plist-error, patch-error]
  flows: [patch]
tags: [deferred-patch, convert-handoff, relevance-pass]
priority: 100
---

# Deferred-from-Convert relevance pass

## When this applies

Your payload includes a `## Deferred from Convert` section. The
convert handler ran compose against the agent's overlay and
rejected one or more framework patches (typically `diffs/*.diff`
files whose hunks no longer match current upstream). Compose
succeeded after dropping them; convert finished with a partial
overlay and handed you a list of patches to evaluate.

Each entry in the section carries:

- `path` — the framework diff file (e.g. `diffs/pkg-plist.diff`)
- `target_file` — what the patch was modifying
- `Original content` — the full diff as it lived in the overlay
  before convert dropped it
- `Reject summary` — which hunks failed and where

## The mental model

A deferred patch is **intent, not authority**. The original diff
expressed *what to do* (e.g. "remove a set of platform-specific
plist entries"). When upstream churns, the literal hunks drift but
the intent often still applies — sometimes verbatim, sometimes in
a smaller form, sometimes not at all. Your job is to evaluate that
per-patch and act.

You are NOT trying to make the literal diff apply again. You are
deciding whether the original intent is still relevant, then
emitting whatever intent (`add_patch`, `replace_in_patch`,
`change_makefile`, etc.) achieves it against the current tree.

## The three verdicts

Every deferred patch needs exactly one verdict in your
`Patch Plan (JSON)`'s `deferred_verdicts` field.

### `regenerated`

**When:** the original intent still applies; you can emit one or
more intents that achieve it against the current upstream tree.

**Mechanics:**
1. Use `get_file` to read the current upstream `target_file`.
2. Identify the lines the original diff was operating on.
3. Emit `add_patch` (for new diffs) or `replace_in_patch` (when
   you're modifying an existing patch the overlay still references).
4. Record the verdict:

```json
{
  "path": "diffs/<file>.diff",
  "verdict": "regenerated",
  "rationale": "intent still applies; emitted add_patch with hunks at new line numbers",
  "intents_emitted": ["add_patch"]
}
```

### `dropped`

**When:** the original intent is no longer relevant. The most
common shapes:

- Upstream already removed the lines the patch was removing.
- The file's shape changed and the modification no longer makes
  sense (e.g. the platform-specific block was deleted altogether).
- The conditional the patch was guarding was eliminated upstream.

**Mechanics:**
1. Use `get_file` + `grep` to confirm the original target lines
   genuinely no longer exist in current upstream.
2. Do NOT emit an intent.
3. Record the verdict:

```json
{
  "path": "diffs/<file>.diff",
  "verdict": "dropped",
  "rationale": "upstream removed the lines this patch targeted (verified via grep)",
  "intents_emitted": []
}
```

### `escalated`

**When:** you can't determine relevance, or you understand the
intent but can't safely regenerate it without operator judgment.
Examples:

- The original diff did something subtle (multi-hunk interleaved
  context) and you're not confident your reproduction is equivalent.
- The target file's structure changed so the patch's intent needs
  reshaping (not just rewriting) — that's a design call.
- The build still passes without the patch but you can't tell
  whether some runtime behavior would regress.

**Mechanics:**
1. Do NOT emit an intent.
2. Be specific about WHAT blocks you, not just "I'm not sure".
3. Record the verdict:

```json
{
  "path": "diffs/<file>.diff",
  "verdict": "escalated",
  "rationale": "multi-hunk patch reshapes a platform-conditional block; needs human review of intent",
  "intents_emitted": []
}
```

## Worked examples

### Pattern: platform-specific files removed from a plist

Original diff (deferred):
```diff
--- pkg-plist.orig
+++ pkg-plist
@@ -100,3 +100,2 @@
 share/foo/data.txt
-share/foo/data_freebsd.txt
 share/foo/other.txt
```

**Check:** does `share/foo/data_freebsd.txt` still appear in
current upstream's `pkg-plist`?

- Still there at line 103 (drifted from 101) → **regenerated**
  with `add_patch` at the new line.
- Removed upstream entirely → **dropped**: the entry the patch was
  removing doesn't exist anymore.
- Replaced by a different platform-specific marker (e.g. now
  guarded by `%%PLATFORM%%`) → **escalated**: deciding the
  DragonFly-side substitution is a design call.

### Pattern: Makefile delta (PLIST_SUB, USES, etc.)

Original diff modifies a Makefile in `diffs/Makefile.diff`. Likely
better expressed as `change_makefile` against the overlay rather
than a fresh `diffs/*.diff`.

If the original intent was "add `PLIST_SUB+= FOO=bar`" and the
current Makefile lacks it → **regenerated** with `change_makefile`
appending the line.

If the upstream Makefile already has it (the intent was reached by
upstream) → **dropped**.

## Bundle outcome

The bundle is considered fully resolved when every deferred patch
has a `regenerated` or `dropped` verdict (with a successful
rebuild). If **any** verdict is `escalated`, the bundle routes to
MANUAL with the escalated subset visible to the operator — your
`regenerated` and `dropped` verdicts on the other patches still
count and aren't wasted.

So: don't escalate to play it safe. If a patch is clearly
irrelevant (verified via grep), drop it. If it clearly still
applies (verified via context check), regenerate it. Reserve
`escalated` for the cases where the right answer is a design call.
