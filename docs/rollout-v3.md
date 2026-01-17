# dportsv3 Compose-First Rollout Runbook

## Purpose

This runbook defines the operational workflow for quarter bring-up using
compose-first execution. Pilot commands remain available for transition but are
no longer the primary path.

## Prerequisites

- Delta repository with `ports/` and optional `special/` checked out.
- FreeBSD ports checkout at the target branch.
- `dportsv3` available from `scripts/generator` environment.

## 1) Build Inventory and Optional Wave Selection

```bash
.venv/bin/python -m dportsv3 migrate inventory --root . --json > artifacts/inventory.json
.venv/bin/python -m dportsv3 migrate classify artifacts/inventory.json --json > artifacts/classified.json
.venv/bin/python -m dportsv3 migrate wave-plan artifacts/classified.json --target @2026Q1 --json
```

Notes:
- Wave planning now includes explicit-target and baseline (`@any`) candidates.
- Use wave output for visibility; compose itself remains the execution primitive.

## 2) Compose Target Tree (Framework + Ports)

```bash
.venv/bin/python -m dportsv3 compose \
  --target @2026Q1 \
  --delta-root . \
  --freebsd-root ../freebsd-ports \
  --output artifacts/compose/@2026Q1 \
  --replace-output \
  --oracle-profile local \
  --json
```

Notes:
- `special/` is applied with copy -> patch -> replacements ordering.
- Per-origin mode dispatch is automatic:
  - `overlay.dops` present -> dops mode only
  - `overlay.dops` absent -> compatibility mode only
- Compatibility payload lookup uses explicit target first, then `@any` baseline.

## 3) Manual Fix + Rerun Loop

1. Review compose report stage errors/warnings and per-origin notes.
2. Fix patch failures or compatibility payload conflicts in source overlays.
3. Rerun the same compose command into the same or a new output root.

Reruns are deterministic for identical source inputs.

## 4) CI / Gate Review

- Validate compose report totals (`errors`, `fallback_patch_count`, oracle fields).
- Run strict mode for gate enforcement when required:

```bash
.venv/bin/python -m dportsv3 compose \
  --target @2026Q1 \
  --delta-root . \
  --freebsd-root ../freebsd-ports \
  --output artifacts/compose/@2026Q1 \
  --replace-output \
  --strict \
  --oracle-profile ci \
  --json
```

## 5) Transition Notes for Existing `@main` Overlays

- Unscoped legacy overlays are treated as baseline (`@any`) in migration reports.
- Keep explicit quarter-only deltas in target-scoped directories.
- Introduce `@any` where quarterly reuse is desired and keep quarter-specific
  overrides in explicit target layers.
