"""Trust-tier + budget policy.

Loads ``config/agentic-policy.json`` and maps a triage
``(classification, confidence)`` pair to a tier with budget.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CONFIDENCE_ORDER = ["low", "medium", "high"]


@dataclass
class Tier:
    name: str  # "AUTO" | "ASSIST" | "MANUAL"
    max_iterations: int = 0
    max_tokens: int = 0


@dataclass
class Policy:
    tiers: dict[str, Tier]
    classification_to_tier: dict[str, str]
    confidence_floor: dict[str, str]


def load_policy(path: Path | str) -> Policy:
    raw = json.loads(Path(path).read_text())
    tiers = {
        name: Tier(
            name=name,
            max_iterations=int(spec.get("max_iterations", 0)),
            max_tokens=int(spec.get("max_tokens", 0)),
        )
        for name, spec in raw.get("tiers", {}).items()
    }
    return Policy(
        tiers=tiers,
        classification_to_tier=dict(raw.get("classification_to_tier", {})),
        confidence_floor=dict(raw.get("confidence_floor", {})),
    )


def _confidence_at_least(value: str, floor: str) -> bool:
    if value not in CONFIDENCE_ORDER or floor not in CONFIDENCE_ORDER:
        return False
    return CONFIDENCE_ORDER.index(value) >= CONFIDENCE_ORDER.index(floor)


def tier_for(policy: Policy, classification: str, confidence: str) -> Tier:
    """Resolve the tier for a triage outcome, cascading confidence_floor downgrades.

    Each tier carries a ``confidence_floor`` that the triage confidence
    must meet. If confidence is below the floor, the tier is downgraded
    one step (AUTO → ASSIST → MANUAL) and the new tier's floor is
    re-evaluated. Cascades until either the floor is met or MANUAL is
    reached. Unknown classifications start at MANUAL.

    Examples (with floors AUTO=high, ASSIST=medium):
        plist-error + high   → AUTO
        plist-error + medium → ASSIST (AUTO floor not met → downgrade)
        plist-error + low    → MANUAL (cascades AUTO → ASSIST → MANUAL)
        compile-error + low  → MANUAL (ASSIST floor not met → downgrade)
    """
    tier_name = policy.classification_to_tier.get(classification, "MANUAL")
    # Cascade downgrades until the confidence floor is satisfied or we
    # land at MANUAL (no further downgrade possible).
    while True:
        floor = policy.confidence_floor.get(tier_name)
        if not floor or _confidence_at_least(confidence, floor):
            break
        next_name = _downgrade(tier_name)
        if next_name == tier_name:
            break
        tier_name = next_name
    return policy.tiers.get(tier_name) or Tier(name="MANUAL")


def _downgrade(tier_name: str) -> str:
    if tier_name == "AUTO":
        return "ASSIST"
    if tier_name == "ASSIST":
        return "MANUAL"
    return tier_name
