"""Parity smoke for the decision engine.

Phase 3 Step 3. Loads the shipped ``config/agentic-policy.json`` and
asserts that ``decide()`` produces the same tier mapping as the
legacy ``policy.tier_for`` for every ``(classification, confidence)``
combination — i.e. the Phase 3 cutover didn't accidentally
regress the rules embedded in the policy file.

Also covers the three new behaviors that ``tier_for`` alone
couldn't represent:
- ``decide()`` returns ``action="escalate_manual"`` not just a
  MANUAL tier when classification routes there.
- The retry cap (cap-driven MANUAL).
- The env-broken short-circuit (``action="skip"``).

If this test fails, either the policy JSON changed (review the
diff and update the expectations) or ``decide()`` has drifted from
the rules ``tier_for`` encodes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from dportsv3.agent.decision import PortHistory, decide
from dportsv3.agent.policy import load_policy, tier_for


# All three confidence values from the system prompt's allowed set.
CONFIDENCES = ("high", "medium", "low")


@dataclass
class _FakeHealth:
    status: str = "ready"


def _policy_path() -> Path:
    # __file__ = scripts/generator/tests/test_decision_parity.py
    #   parents[0]=tests, parents[1]=generator, parents[2]=scripts,
    #   parents[3]=repo root.
    #
    # Prefer the operator-local copy ``agentic-policy.json`` (real,
    # gitignored); fall back to the tracked ``agentic-policy.json.sample``
    # so the parity test works on fresh checkouts without manual setup.
    config_dir = Path(__file__).resolve().parents[3] / "config"
    local = config_dir / "agentic-policy.json"
    if local.is_file():
        return local
    return config_dir / "agentic-policy.json.sample"


@pytest.fixture(scope="module")
def policy():
    p = _policy_path()
    if not p.is_file():
        pytest.skip(f"policy JSON not found at {p}")
    return load_policy(p)


@pytest.fixture(scope="module")
def classifications(policy):
    """Every classification key the policy knows about,
    plus the synthetic "totally-novel" → MANUAL default."""
    return [*sorted(policy.classification_to_tier.keys()), "totally-novel"]


# --- Parity sweep -------------------------------------------------------------


def test_parity_against_legacy_tier_for(policy, classifications):
    """For every (classification, confidence), decide() with clean
    history + healthy env must yield an action consistent with the
    legacy tier_for resolution:

      legacy MANUAL   ↔  decide.action == "escalate_manual"
      legacy AUTO     ↔  decide.action == "auto_patch", tier=AUTO
      legacy ASSIST   ↔  decide.action == "auto_patch", tier=ASSIST
    """
    failures: list[str] = []
    for cls in classifications:
        for conf in CONFIDENCES:
            legacy_tier = tier_for(policy, cls, conf)
            dec = decide(
                classification=cls,
                confidence=conf,
                history=PortHistory.empty(target="@x", origin="cat/port"),
                env_health=_FakeHealth(status="ready"),
                policy=policy,
            )
            if legacy_tier.name == "MANUAL":
                if dec.action != "escalate_manual":
                    failures.append(
                        f"{cls!r}/{conf!r}: legacy MANUAL but "
                        f"decide.action={dec.action!r}"
                    )
                if dec.tier.name != "MANUAL":
                    failures.append(
                        f"{cls!r}/{conf!r}: legacy MANUAL but "
                        f"decide.tier={dec.tier.name!r}"
                    )
            else:
                if dec.action != "auto_patch":
                    failures.append(
                        f"{cls!r}/{conf!r}: legacy {legacy_tier.name} but "
                        f"decide.action={dec.action!r}"
                    )
                if dec.tier.name != legacy_tier.name:
                    failures.append(
                        f"{cls!r}/{conf!r}: legacy {legacy_tier.name} but "
                        f"decide.tier={dec.tier.name!r}"
                    )
    assert not failures, "\n".join(failures)


def test_every_combination_is_exercised(policy, classifications):
    """Sanity check on coverage — the test above only catches
    regressions for the combinations it iterates over."""
    total = len(classifications) * len(CONFIDENCES)
    assert total >= 12 * 3, (
        f"matrix shrunk unexpectedly: {len(classifications)} classifications "
        f"× {len(CONFIDENCES)} confidences = {total}"
    )


# --- Net-new decide() behaviors --------------------------------------------


def test_auto_clean_history_is_auto_patch(policy):
    """The happy path: AUTO tier classification + high confidence +
    no prior failures → auto_patch. ``fetch-checksum`` is the
    representative AUTO classification (plist-error / pkg-format were
    promoted to ASSIST after the lang/python311 budget incident —
    AUTO is now reserved for trivial fixes like distinfo bumps)."""
    dec = decide(
        classification="fetch-checksum",
        confidence="high",
        history=PortHistory.empty(target="@x", origin="cat/port"),
        env_health=_FakeHealth(status="ready"),
        policy=policy,
    )
    assert dec.action == "auto_patch"
    assert dec.tier.name == "AUTO"


def test_three_failed_patch_attempts_caps_to_manual(policy):
    """Step 6: the retry cap is now driven by ``failed_patch_attempts``,
    not raw ``recent_failures``. Three failed agent patches → MANUAL.
    Uses ``fetch-checksum`` as the representative AUTO classification
    (see test_auto_clean_history_is_auto_patch for rationale)."""
    dec = decide(
        classification="fetch-checksum",
        confidence="high",
        history=PortHistory(
            target="@x", origin="cat/port", failed_patch_attempts=3,
        ),
        env_health=_FakeHealth(status="ready"),
        policy=policy,
        max_attempts=3,
    )
    assert dec.action == "escalate_manual"
    assert dec.tier.name == "MANUAL"
    assert dec.extra.get("original_tier") == "AUTO"
    assert dec.extra.get("failed_patch_attempts") == 3


def test_env_broken_short_circuits_to_skip(policy):
    """Env-broken at decision time → skip, regardless of
    classification + history."""
    dec = decide(
        classification="plist-error",
        confidence="high",
        history=PortHistory.empty(target="@x", origin="cat/port"),
        env_health=_FakeHealth(status="broken"),
        policy=policy,
    )
    assert dec.action == "skip"
    assert "env_broken" in dec.reason


def test_env_broken_beats_cap(policy):
    """If both env-broken and cap-triggered apply, env-broken wins
    (the chroot is the fault, not the port; no point flagging
    MANUAL for a port that didn't run)."""
    dec = decide(
        classification="plist-error",
        confidence="high",
        history=PortHistory(
            target="@x", origin="cat/port", recent_failures=10,
        ),
        env_health=_FakeHealth(status="broken"),
        policy=policy,
        max_attempts=3,
    )
    assert dec.action == "skip"


def test_env_health_none_treated_as_ready(policy):
    """env_health=None (e.g. no env resolved) → decide proceeds
    as if env is healthy, doesn't short-circuit."""
    dec = decide(
        classification="plist-error",
        confidence="high",
        history=PortHistory.empty(target="@x", origin="cat/port"),
        env_health=None,
        policy=policy,
    )
    assert dec.action == "auto_patch"


def test_legacy_policy_file_is_still_valid_json():
    """If someone hand-edits config/agentic-policy.json and breaks
    it, the parity test in this file would obscure the real issue.
    Surface JSON-level breakage early."""
    raw = _policy_path().read_text()
    data = json.loads(raw)
    assert "tiers" in data
    assert "classification_to_tier" in data
    assert "confidence_floor" in data
