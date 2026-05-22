"""Port-scoped dops classification for the agent.

Uses the same overlay-artifact detection as
:mod:`dportsv3.compose_discovery` — which understands the *current*
DeltaPorts layout — instead of :mod:`dportsv3.migration.inventory`
which was written for an older legacy-program shape that didn't
include the ``dragonfly/`` directory the modern layout puts static
patches in.

States returned by :func:`classify`:

- ``converted``: the port has ``overlay.dops`` and no remaining
  compat overlay artifacts (``dragonfly/`` / ``diffs/`` /
  ``newport/`` / any ``Makefile.DragonFly[.target]``). The agent
  does nothing.
- ``auto_safe_pending``: a plain ``Makefile.DragonFly`` is present
  whose contents are pure assignments (no conditionals, no recipes).
  The deterministic converter in ``migration.convert`` can handle
  the framework half without LLM judgment; any ``dragonfly/`` patch
  files still need the LLM to classify them.
- ``needs_judgment``: any other compat shape. LLM has to classify
  each artifact (framework / source-simple / source-complex).
- ``not_in_scope``: no overlay artifacts at all (or the port path
  doesn't exist). Nothing to convert.

The ``stale`` state from the legacy migration vocabulary is not
emitted here — staleness is a manual operator flag (carried on
``STATUS``) that the agent layer does not consult.
"""

from __future__ import annotations

from pathlib import Path

from dportsv3.migration.classify import classify_inventory


CLASSIFICATION_STATES = (
    "converted",
    "auto_safe_pending",
    "needs_judgment",
    "stale",
    "not_in_scope",
)


_MAKEFILE_DRAGONFLY_NAMES = (
    "Makefile.DragonFly",
    "Makefile.DragonFly.@any",
)


def _detect_compat_artifacts(port_dir: Path) -> dict:
    """Return a flag-bag of compat-overlay artifacts.

    Mirrors :func:`dportsv3.compose_discovery.discover_overlay_contexts`'s
    per-port detection. Includes the modern ``dragonfly/`` directory
    that the older migration ``inventory.py`` misses.
    """
    if not port_dir.is_dir():
        return {"present": False}

    diffs_dir = port_dir / "diffs"
    has_any_diff = diffs_dir.exists() and any(
        path.is_file() and path.suffix in {".diff", ".patch"}
        for path in diffs_dir.rglob("*")
    )

    dragonfly_dir = port_dir / "dragonfly"
    has_any_dragonfly = dragonfly_dir.exists() and any(
        path.is_file() for path in dragonfly_dir.rglob("*")
    )

    has_newport = (port_dir / "newport").exists()

    has_makefile_dragonfly = any(
        (port_dir / name).exists() for name in _MAKEFILE_DRAGONFLY_NAMES
    )
    # Per-target Makefile.DragonFly.<target> variants — match the
    # discovery layer's naming convention.
    has_makefile_dragonfly_targeted = any(
        path.name.startswith("Makefile.DragonFly.")
        and path.name not in _MAKEFILE_DRAGONFLY_NAMES
        for path in port_dir.iterdir()
        if path.is_file()
    )

    has_dops = (port_dir / "overlay.dops").exists()

    return {
        "present": (
            has_any_diff or has_any_dragonfly or has_newport
            or has_makefile_dragonfly or has_makefile_dragonfly_targeted
            or has_dops
        ),
        "has_diffs": has_any_diff,
        "has_dragonfly": has_any_dragonfly,
        "has_newport": has_newport,
        "has_makefile_dragonfly": has_makefile_dragonfly,
        "has_makefile_dragonfly_targeted": has_makefile_dragonfly_targeted,
        "has_dops": has_dops,
    }


def _has_compat_artifacts(detected: dict) -> bool:
    """True if any compat (non-dops) overlay artifact is present."""
    return any(
        detected.get(key, False) for key in (
            "has_diffs", "has_dragonfly", "has_newport",
            "has_makefile_dragonfly", "has_makefile_dragonfly_targeted",
        )
    )


def classify(origin: str, repo_root: Path) -> str:
    """Return the agent-facing classification for one port.

    ``origin`` is the ``category/name`` slug; ``repo_root`` is the
    DeltaPorts checkout root (the directory that contains ``ports/``).
    """
    port_dir = Path(repo_root) / "ports" / origin
    detected = _detect_compat_artifacts(port_dir)
    if not detected.get("present"):
        return "not_in_scope"

    has_dops = detected["has_dops"]
    has_compat = _has_compat_artifacts(detected)

    # Converted = dops file present and no compat artifacts left.
    if has_dops and not has_compat:
        return "converted"

    # No compat and no dops shouldn't happen — guard against it.
    if not has_compat:
        return "not_in_scope"

    # auto_safe_pending is only meaningful when ``Makefile.DragonFly``
    # exists AND is the only compat artifact AND parses as
    # assignment-only. Otherwise the LLM has to do the work.
    if (
        detected["has_makefile_dragonfly"]
        and not detected["has_dragonfly"]
        and not detected["has_diffs"]
        and not detected["has_newport"]
        and not detected["has_makefile_dragonfly_targeted"]
    ):
        # Reuse the legacy classifier to decide auto-safe vs review.
        # Synthesize a minimal record that satisfies its inputs.
        record = {
            "origin": origin,
            "path": str(port_dir),
            "has_makefile_dragonfly": True,
            "has_diffs": False,
            "has_newport": False,
            "has_overlay_dops": has_dops,
            "stale": False,
        }
        classified = classify_inventory([record])[0]
        if classified.get("bucket") == "auto-safe":
            return "auto_safe_pending"

    return "needs_judgment"


# Re-exported for callers that previously used the synthesized record
# directly (e.g. ``runner._maybe_defer_to_convert``). Kept as a thin
# wrapper around the discovery flags so callers don't need to know
# the inventory schema.
def _scan_one_port(port_dir: Path, origin: str) -> dict | None:
    """Backwards-compatible record builder for callers expecting the
    migration inventory shape. Returns ``None`` if the port has no
    overlay artifacts at all.

    Most agent callers should switch to :func:`classify` or directly
    use :func:`_detect_compat_artifacts`; this helper is retained for
    the deterministic-converter call site that still needs a
    migration-shaped record.
    """
    if not port_dir.is_dir():
        return None
    detected = _detect_compat_artifacts(port_dir)
    if not detected.get("present"):
        return None

    # Compute migration-inventory-compatible fields. We pass through
    # ``has_diffs`` (mapping ``dragonfly/`` onto it too, since the
    # migration classifier doesn't know about ``dragonfly/`` and
    # treating dragonfly-only ports as "fallback-only / raw_diffs"
    # is the closest existing bucket and routes to ``needs_judgment``).
    return {
        "origin": origin,
        "category": origin.split("/", 1)[0] if "/" in origin else "",
        "path": str(port_dir),
        "has_makefile_dragonfly": detected["has_makefile_dragonfly"],
        "has_diffs": detected["has_diffs"] or detected["has_dragonfly"],
        "has_newport": detected["has_newport"],
        "has_overlay_dops": detected["has_dops"],
        "legacy_overlay": _has_compat_artifacts(detected),
        "targets": [],
        "target_mode": "baseline",
        "available_targets": [],
        "complexity_signals": [],
        "churn": 0,
        "stale": False,
    }
