# Agent Playbooks

This directory holds the agent's knowledge library — markdown entries
covering known error patterns, patch-flow procedures, convert-phase
procedures, and port-toolchain conventions. The runner attaches
relevant entries to triage / patch / convert payloads.

Successor to the original "Known Errors Database" (KEDB). The scope
broadened past errors-only as the agent loop matured: patch-flow
procedures (editing `overlay.dops`, the static-patch workflow),
convert-phase decisions (target directive picking, domain
classification), and toolchain-shaped patterns (autoconf, cmake, …)
all live here too. See `docs/agentic-architecture-backlog.md` Step 27
for the design rationale.

## Categories (filename prefix)

Self-describing on `ls`:

- **`error-*.md`** — reactive: pattern fingerprint in build logs +
  cause + concrete fix. Triggered by triage classification.
  *Examples:* `error-plist-mismatch.md`, `error-freebsd-only-features.md`.
- **`flow-*.md`** — flow-level procedures for a whole agent role.
  The patch agent edits `ports/<origin>/overlay.dops` free-hand in
  dops DSL, so its procedures (the put_file→validate_dops loop, `mk`
  directive traps, scoping, the static-patch workflow, recovery)
  live in one entry. Triggered by `flows`.
  *Example:* `flow-patch.md`.
- **`convert-*.md`** — convert agent procedures. Decision trees for
  the LLM convert path: target directive picking, framework vs
  upstream-source classification. Triggered by convert phase.
  *Examples:* `convert-target-directive.md`,
  `convert-classify-patch-domain.md`.
- **`toolchain-*.md`** — proactive port-shape playbooks for the
  triage agent. The "usual suspects" for a recognized build system.
  Triggered by mechanical toolchain detection on the port.
  *Examples:* `toolchain-autoconf.md`, `toolchain-cmake.md`.

## How it works

The runner runs each entry's frontmatter triggers through a
deterministic selector (`dportsv3/agent/playbooks.py`) and attaches
only the matching entries to a given triage / patch / convert
payload. `README.md` and `TEMPLATE.md` are excluded. A per-payload
token budget gates entries by priority (lower priority drops first).

An entry matches the `flows` axis against the agent role and narrows
on `classifications` (from triage), `toolchains` (from mechanical
port detection), and `convert_phases` (convert agent). Empty list on
an axis = wildcard for that axis.

## Frontmatter

Every entry carries the frontmatter shape below.

```yaml
---
triggers:
  classifications: []      # from triage; empty = any
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
   `error-libtool-relink-loop.md`, `toolchain-meson.md`).
2. Fill in the frontmatter for this entry's triggers.
3. Write the body: pattern / cause / fix, or whatever shape the
   category needs (the template covers the common pattern).
4. Restart the runner; it picks up new files on the next job.

## Guidelines

1. **Be specific.** Include exact error patterns, exact dops
   directives, exact toolchain detection signals.
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
