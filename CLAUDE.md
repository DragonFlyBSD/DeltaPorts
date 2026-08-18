# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

## What this repository is

A **data** repository: the overlays and patches that turn the FreeBSD Ports
Collection into DragonFly Ports.

- `ports/<category>/<port>/` — per-port overlay (`STATUS`, `overlay.dops`,
  `dragonfly/`, `diffs/`, `newport/`)
- `special/` — non-port overlays (`Mk`, `Templates`)
- `scripts/` — the shell generator, plus Tinderbox/builder hooks

There is no build or test suite here. Changes are validated by composing and
building the affected ports.

## The tooling lives elsewhere

The Python toolchain — the compose pipeline, the `overlay.dops` DSL and
engine, the migration program, the build tracker, the chroot dev-env manager
and the agentic build-failure repair loop — is a separate project,
**Polytropos**. It reads this repository as an input via `--delta-root` /
`$DPORTS_DELTA_ROOT`.

Do not reintroduce it here. If a task needs a change to the DSL, the compose
pipeline, the tracker or the agent, that change belongs in Polytropos; what
belongs here is the overlay data those tools consume.
