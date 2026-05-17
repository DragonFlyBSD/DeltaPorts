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
    """Resolve the tier for a triage outcome, applying confidence_floor downgrades.

    AUTO with confidence below its floor → ASSIST.
    ASSIST with confidence below its floor → MANUAL.
    Unknown classification → MANUAL.
    """
    tier_name = policy.classification_to_tier.get(classification, "MANUAL")

    floor = policy.confidence_floor.get(tier_name)
    if floor and not _confidence_at_least(confidence, floor):
        tier_name = _downgrade(tier_name)

    return policy.tiers.get(tier_name) or Tier(name="MANUAL")


def _downgrade(tier_name: str) -> str:
    if tier_name == "AUTO":
        return "ASSIST"
    if tier_name == "ASSIST":
        return "MANUAL"
    return tier_name
