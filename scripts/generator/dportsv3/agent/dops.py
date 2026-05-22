"""Port-scoped dops classification for the agent.

Thin wrapper over ``dportsv3.migration.{inventory,classify}`` that
exposes one function: given an origin, return the port's current
state in vocabulary the agent runner uses.

The migration package's ``scan_inventory`` walks the entire ports
tree — the agent only needs one port at a time, so this module
reimplements the per-port scan inline and hands a one-item list to
``classify_inventory``.

States returned by :func:`classify`:

- ``converted``: the port has ``overlay.dops`` and no remaining
  legacy overlay artifacts (``Makefile.DragonFly`` / ``diffs/`` /
  ``newport/``). The agent does nothing.
- ``auto_safe_pending``: classifier bucket ``auto-safe`` and the
  port is not yet converted. The deterministic converter in
  ``migration.convert`` can handle it without LLM judgment.
- ``needs_judgment``: classifier bucket ``review-needed`` or
  ``fallback-only``. LLM territory — conditional blocks in
  ``Makefile.DragonFly``, raw diffs, newport scaffolding.
- ``stale``: classifier bucket ``stale``. Out of scope; do nothing.
- ``not_in_scope``: no overlay artifacts at all (or the port path
  doesn't exist). Nothing to convert.
"""

from __future__ import annotations

from pathlib import Path

from dportsv3.migration.classify import classify_inventory
from dportsv3.migration.inventory import _complexity_signals, _extract_targets


CLASSIFICATION_STATES = (
    "converted",
    "auto_safe_pending",
    "needs_judgment",
    "stale",
    "not_in_scope",
)


def _scan_one_port(port_dir: Path, origin: str) -> dict | None:
    """Build one inventory record for ``port_dir``.

    Mirrors the inner loop of :func:`dportsv3.migration.inventory.scan_inventory`
    but for a single port. Returns ``None`` if the port has no
    overlay artifacts at all (i.e. it would be skipped by
    ``scan_inventory``'s ``if not legacy_overlay and not has_overlay_dops``
    guard).
    """
    if not port_dir.is_dir():
        return None

    has_makefile_dragonfly = (port_dir / "Makefile.DragonFly").exists()
    has_diffs = (port_dir / "diffs").exists()
    has_newport = (port_dir / "newport").exists()
    has_overlay_dops = (port_dir / "overlay.dops").exists()

    legacy_overlay = has_makefile_dragonfly or has_diffs or has_newport
    if not legacy_overlay and not has_overlay_dops:
        return None

    signals, churn = _complexity_signals(port_dir, has_makefile_dragonfly)
    explicit_targets = [
        target for target in _extract_targets(port_dir) if target != "@any"
    ]
    baseline_capable = not explicit_targets
    target_mode = "baseline" if baseline_capable else "explicit"
    available_targets = sorted(
        set(explicit_targets + (["@any"] if baseline_capable else []))
    )
    category = origin.split("/", 1)[0] if "/" in origin else ""

    return {
        "origin": origin,
        "category": category,
        "path": str(port_dir),
        "has_makefile_dragonfly": has_makefile_dragonfly,
        "has_diffs": has_diffs,
        "has_newport": has_newport,
        "has_overlay_dops": has_overlay_dops,
        "legacy_overlay": legacy_overlay,
        "targets": available_targets,
        "target_mode": target_mode,
        "available_targets": available_targets,
        "complexity_signals": signals,
        "churn": churn,
        "stale": False,
    }


def classify(origin: str, repo_root: Path) -> str:
    """Return the agent-facing classification for one port.

    ``origin`` is the ``category/name`` slug; ``repo_root`` is the
    DeltaPorts checkout root (the directory that contains
    ``ports/``).
    """
    port_dir = Path(repo_root) / "ports" / origin
    record = _scan_one_port(port_dir, origin)
    if record is None:
        return "not_in_scope"

    classified = classify_inventory([record])[0]
    bucket = classified.get("bucket", "")

    if bucket == "stale":
        return "stale"

    has_dops = bool(classified.get("has_overlay_dops"))
    has_legacy = bool(classified.get("legacy_overlay"))

    # Converted = dops exists and the legacy artifacts have been
    # cleared. A port with both is mid-migration / inconsistent —
    # treat it as still needing work so the agent finishes the job.
    if has_dops and not has_legacy:
        return "converted"

    if bucket == "auto-safe":
        return "auto_safe_pending"

    # review-needed and fallback-only both want LLM judgment.
    return "needs_judgment"
