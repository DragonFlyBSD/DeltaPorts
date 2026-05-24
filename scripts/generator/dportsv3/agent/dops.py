"""Port-scoped dops classification for the agent.

Uses the same overlay-artifact detection as
:mod:`dportsv3.compose_discovery` — which understands the *current*
DeltaPorts layout — instead of :mod:`dportsv3.migration.inventory`
which was written for an older legacy-program shape that didn't
include the ``dragonfly/`` directory the modern layout puts static
patches in.

States returned by :func:`classify`:

- ``converted``: the port has ``overlay.dops`` and no unambiguous
  unmigrated artifacts (``newport/`` or any ``Makefile.DragonFly*``).
  ``dragonfly/`` and ``diffs/`` may be valid peers referenced by dops.
- ``auto_safe_pending``: a plain ``Makefile.DragonFly`` is present
  whose contents are pure assignments (no conditionals, no recipes).
  The deterministic converter in ``migration.convert`` can handle
  the framework half without LLM judgment.
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

from dportsv3.agent.overlay_state import (
    CLASSIFICATION_STATES,
    OverlayAssessment,
    assess_overlay,
    facts_from_port_dir,
    facts_from_repo,
)


def classify(origin: str, repo_root: Path) -> str:
    """Return the agent-facing classification for one port.

    ``origin`` is the ``category/name`` slug; ``repo_root`` is the
    DeltaPorts checkout root (the directory that contains ``ports/``).
    """
    return assess(origin, repo_root).state


def assess(origin: str, repo_root: Path) -> OverlayAssessment:
    """Return the full overlay assessment for one port."""
    return assess_overlay(facts_from_repo(origin, repo_root))


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
    facts = facts_from_port_dir(origin, port_dir)
    compat_present = bool(
        facts.overlay_dops
        or facts.makefile_dragonfly
        or facts.targeted_makefile_dragonfly
        or facts.dragonfly_files
        or facts.diff_files
        or facts.newport
    )
    if not compat_present:
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
        "has_makefile_dragonfly": bool(facts.makefile_dragonfly),
        "has_diffs": bool(facts.diff_files or facts.dragonfly_files),
        "has_newport": facts.newport,
        "has_overlay_dops": facts.overlay_dops,
        "legacy_overlay": bool(
            facts.makefile_dragonfly
            or facts.targeted_makefile_dragonfly
            or facts.dragonfly_files
            or facts.diff_files
            or facts.newport
        ),
        "targets": [],
        "target_mode": "baseline",
        "available_targets": [],
        "complexity_signals": [],
        "churn": 0,
        "stale": False,
    }
