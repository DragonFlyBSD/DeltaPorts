---
triggers:
  classifications: [plist-error, patch-error]
  flows: [patch]
tags: [deferred-patch, relevance-pass]
priority: 100
---

# Deferred-patch relevance pass

## When this applies

Your payload includes a `## Deferred from Convert` section (the header is
historical — the channel is now fed by the offline `diffs/` absorption pass,
not the retired convert agent; the schema is unchanged). The deferred-patch
channel ran compose against an overlay and rejected one or more framework
patches (typically `diffs/*.diff` files whose hunks no longer match current
upstream). Compose succeeded after dropping them; you were handed a partial
overlay plus the list of dropped patches to evaluate. If no such section is
present, this entry does not apply.

Each entry in the section carries:

- `path` — the framework diff file (e.g. `diffs/pkg-plist.diff`)
- `target_file` — what the patch was modifying
- `Original content` — the full diff as it lived in the overlay before
  it was dropped
- `Reject summary` — which hunks failed and where

## The mental model

A deferred patch is **intent, not authority**. The original diff
expressed *what to do* (e.g. "remove a set of platform-specific plist
entries"). When upstream churns, the literal hunks drift but the intent
often still applies — sometimes verbatim, sometimes in a smaller form,
sometimes not at all. Evaluate that per-patch and act.

You are NOT trying to make the literal diff apply again. You edit
`ports/<origin>/overlay.dops` directly (the same put_file → validate_dops
loop as any patch — see `flow-patch.md`), emitting whatever dops ops
achieve the intent against the current tree, or deciding the intent is
gone.

## The three verdicts

Every deferred patch needs exactly one verdict in your
`Patch Plan (JSON)`'s `deferred_verdicts` field. Each verdict is
`{path, verdict, rationale}` — `path` matches the deferred entry,
`verdict` is one of the three below, `rationale` is one operator-readable
sentence.

### `regenerated`

**When:** the original intent still applies; you can express it as dops
ops against current upstream.

**Mechanics:**
1. `get_file` (or `grep`) the current upstream `target_file`.
2. Identify the lines the original diff operated on.
3. Edit `overlay.dops` with the equivalent op:
   - Makefile change → `mk set/add/remove/unset` (a keyed-list rewrite
     that `mk` can't express → `text replace-once` against the file).
   - `pkg-plist` add/remove → `mk add PLIST_FILES "<path>"` for simple
     additions, else `text replace-once` on `pkg-plist`, else a
     refreshed `patch apply diffs/<file>.diff` re-cut against current
     line numbers.
4. `validate_dops`, then record:

```json
{
  "path": "diffs/<file>.diff",
  "verdict": "regenerated",
  "rationale": "intent still applies; re-expressed as mk add PLIST_FILES against current upstream"
}
```

### `dropped`

**When:** the original intent is no longer relevant. Common shapes:

- Upstream already removed the lines the patch was removing.
- The file's shape changed and the modification no longer makes sense
  (e.g. the platform-specific block was deleted altogether).
- The conditional the patch was guarding was eliminated upstream.

**Mechanics:**
1. `get_file` + `grep` to confirm the original target lines genuinely no
   longer exist in current upstream.
2. Emit NO op for this patch.
3. Record:

```json
{
  "path": "diffs/<file>.diff",
  "verdict": "dropped",
  "rationale": "upstream removed the lines this patch targeted (verified via grep)"
}
```

### `escalated`

**When:** you can't determine relevance, or you understand the intent but
can't safely regenerate it without operator judgment. Examples:

- The original diff did something subtle (multi-hunk interleaved context)
  and you're not confident your reproduction is equivalent.
- The target file's structure changed so the intent needs reshaping (not
  just rewriting) — a design call.
- The build still passes without the patch but you can't tell whether
  some runtime behavior would regress.

**Mechanics:**
1. Emit NO op for this patch.
2. Be specific about WHAT blocks you, not just "I'm not sure".
3. Record:

```json
{
  "path": "diffs/<file>.diff",
  "verdict": "escalated",
  "rationale": "multi-hunk patch reshapes a platform-conditional block; needs human review of intent"
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

**Check:** does `share/foo/data_freebsd.txt` still appear in current
upstream's `pkg-plist`?

- Still there (line drifted) → **regenerated**: `text replace-once` on
  `pkg-plist` to drop the line, or a re-cut `patch apply` at the new
  line numbers.
- Removed upstream entirely → **dropped**: the entry the patch removed
  doesn't exist anymore.
- Replaced by a different platform marker (e.g. now guarded by
  `%%PLATFORM%%`) → **escalated**: the DragonFly-side substitution is a
  design call.

### Pattern: Makefile delta (PLIST_SUB, USES, etc.)

A `diffs/Makefile.diff` is usually better expressed as a direct `mk` op
on the overlay than a re-cut diff.

- Intent "add `PLIST_SUB+= FOO=bar`", current Makefile lacks it →
  **regenerated**: `mk add PLIST_SUB "FOO=bar"`.
- Upstream Makefile already has it → **dropped**.

## Bundle outcome

The bundle is fully resolved when every deferred patch has a
`regenerated` or `dropped` verdict (with a successful rebuild). If
**any** verdict is `escalated`, the bundle routes to MANUAL with the
escalated subset visible to the operator — your `regenerated` and
`dropped` verdicts on the other patches still count and aren't wasted.

So: don't escalate to play it safe. If a patch is clearly irrelevant
(verified via grep), drop it. If it clearly still applies (verified via
context check), regenerate it. Reserve `escalated` for cases where the
right answer is a design call.
