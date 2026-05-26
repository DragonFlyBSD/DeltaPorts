# Agent Playbooks

This directory holds the agent's knowledge library — markdown entries
covering known error patterns, intent recipes, convert-phase
procedures, and port-toolchain conventions. The runner attaches
relevant entries to triage / patch / convert payloads.

Successor to the original "Known Errors Database" (KEDB). The scope
broadened past errors-only as the agent loop matured: per-intent
usage recipes, convert-phase decisions (target directive picking,
domain classification), and toolchain-shaped patterns (autoconf,
cmake, …) all live here too. See `docs/agentic-consolidation-plan.md`
Step 27 for the design rationale.

## Categories (filename prefix)

Self-describing on `ls`:

- **`error-*.md`** — reactive: pattern fingerprint in build logs +
  cause + concrete fix. Triggered by triage classification.
  *Examples:* `error-plist-mismatch.md`, `error-freebsd-only-features.md`.
- **`intent-*.md`** — patch-flow intent usage recipes. When and how
  to use a given intent type, beyond the JSON schema. Triggered by
  intent type from the patch agent's tool surface.
  *Examples:* `intent-replace_in_dops_block.md`,
  `intent-add_patch-from-source.md`.
- **`convert-*.md`** — convert agent procedures. Decision trees for
  the LLM convert path: target directive picking, framework vs
  upstream-source classification. Triggered by convert phase.
  *Examples:* `convert-target-directive.md`,
  `convert-classify-patch-domain.md`.
- **`toolchain-*.md`** — proactive port-shape playbooks for the
  triage agent. The "usual suspects" for a recognized build system.
  Triggered by mechanical toolchain detection on the port.
  *Examples:* `toolchain-autoconf.md`, `toolchain-cmake.md`.

## How it works (today)

The runner bulk-loads every `*.md` in this directory (excluding
`README.md` and `TEMPLATE.md`) and attaches the content to triage
and patch payloads. The frontmatter described below is **ignored at
load time today** — it's there so Step 27b's selector can filter on
it without re-touching every file. The bulk-load behavior is
behavior-preserving with the legacy KEDB loader.

Step 27b will replace bulk-load with deterministic selection: each
entry's frontmatter triggers (classification / intent / toolchain /
convert phase) determine whether it lands in a given payload, and a
per-payload token budget gates entries by priority.

## Frontmatter (forward-looking — will be honored in Step 27b)

Every entry should carry the frontmatter shape below. Old entries
that predate this convention will be retro-fitted as part of 27b.

```yaml
---
triggers:
  classifications: []      # from triage; empty = any
  intents: []              # patch-flow intent types; empty = none
  toolchains: []           # from mechanical port detection
  convert_phases: []       # convert-agent procedure steps
  flows: [triage, patch]   # which agent roles see this entry
tags: []                   # operator-facing labels, no selector logic
priority: 100              # lower = drop later under budget; default 100
---
```

Empty trigger list means "wildcard for this kind" — the entry matches
on that axis regardless. An empty `triggers` block as a whole means
the entry is always loaded (use sparingly, for fundamental
references).

## Adding a new entry

1. Copy `TEMPLATE.md` to `<category>-<short-slug>.md` (e.g.
   `intent-drop_patch.md`, `error-libtool-relink-loop.md`).
2. Fill in the frontmatter for this entry's triggers.
3. Write the body: pattern / cause / fix, or whatever shape the
   category needs (the template covers the common pattern).
4. Restart the runner; it picks up new files on the next job.

## Guidelines

1. **Be specific.** Include exact error patterns, exact intent type
   names, exact toolchain detection signals.
2. **Explain the cause.** Help the agent understand *why*, not just
   what to do.
3. **One pattern per file.** If you find yourself writing two
   `## Pattern` sections, split into two files.
4. **Stay lightweight.** Hard cap of ~100 lines per entry; if longer,
   the entry is doing too much.
5. **Include 1-2 example port references.** Concrete anchor for the
   reader; not required if the pattern is obvious.
6. **Don't put structural prompt content here.** Loop discipline,
   tool surface descriptions, refusal codes, output formats live in
   `dportsv3/agent/prompts.py`. Playbooks are pattern content.
