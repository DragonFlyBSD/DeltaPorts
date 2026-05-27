"""Step 6 retry-cap policy — full decision matrix.

The plan's expected behaviour:

- Bundle failures alone (no patch attempts) → keep trying.
- Repeated failed patch attempts → escalate.
- Fresh operator context resets the cap once.
- Sticky failure signature escalates faster (same wall N times).
- Absolute bundle backstop fires as a safety net even without
  patch attempts.
- env_broken + classification=MANUAL short-circuits unchanged.

These tests drive ``decide()`` directly with synthetic
``PortHistory`` values — no DB, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from dportsv3.agent.decision import PortHistory, decide
from dportsv3.agent.policy import Policy, Tier


@dataclass
class _FakeHealth:
    status: str = "ready"


@pytest.fixture
def policy() -> Policy:
    return Policy(
        tiers={
            "AUTO":   Tier(name="AUTO",   max_iterations=2, max_tokens=30000),
            "ASSIST": Tier(name="ASSIST", max_iterations=4, max_tokens=120000),
            "MANUAL": Tier(name="MANUAL", max_iterations=0, max_tokens=0),
        },
        classification_to_tier={
            "plist-error":   "AUTO",
            "compile-error": "ASSIST",
            "missing-dep":   "MANUAL",
        },
        confidence_floor={"AUTO": "high", "ASSIST": "medium"},
    )


def _decide(history: PortHistory, policy: Policy, **kw) -> object:
    return decide(
        classification=kw.pop("classification", "plist-error"),
        confidence=kw.pop("confidence", "high"),
        history=history,
        env_health=_FakeHealth(status="ready"),
        policy=policy,
        **kw,
    )


# --- (1) bundles alone do not escalate -------------------------------------


def test_bundle_failures_alone_do_not_escalate(policy):
    """Plan: '3 bundle failures, 0 patch attempts → auto_patch'."""
    h = PortHistory(
        target="@t", origin="x/y",
        recent_failures=3, failed_patch_attempts=0,
    )
    d = _decide(h, policy)
    assert d.action == "auto_patch"
    assert d.tier.name == "AUTO"


def test_many_bundle_failures_below_backstop_do_not_escalate(policy):
    h = PortHistory(
        target="@t", origin="x/y",
        recent_failures=9, failed_patch_attempts=0,
    )
    d = _decide(h, policy, bundle_backstop=10)
    assert d.action == "auto_patch"


# --- (2) failed patch attempts escalate ------------------------------------


def test_three_failed_patch_attempts_escalate(policy):
    h = PortHistory(
        target="@t", origin="x/y",
        recent_failures=0, failed_patch_attempts=3,
    )
    d = _decide(h, policy, max_attempts=3)
    assert d.action == "escalate_manual"
    assert d.tier.name == "MANUAL"
    assert d.extra["escalation_cause"] == "patch_cap"
    assert d.extra["failed_patch_attempts"] == 3


def test_two_failed_patch_attempts_still_auto(policy):
    h = PortHistory(
        target="@t", origin="x/y",
        failed_patch_attempts=2,
    )
    d = _decide(h, policy, max_attempts=3)
    assert d.action == "auto_patch"


# --- (3) fresh user context resets the cap ---------------------------------


def test_fresh_user_context_overrides_cap(policy):
    """Plan: 'fresh user context permits another attempt'."""
    h = PortHistory(
        target="@t", origin="x/y",
        failed_patch_attempts=5,   # well past cap
        has_fresh_user_context=True,
    )
    d = _decide(h, policy, max_attempts=3)
    assert d.action == "auto_patch"
    assert d.tier.name == "AUTO"
    assert d.extra["cap_reset_via"] == "user_context"


def test_no_fresh_context_means_normal_cap(policy):
    """Same numbers as above but no fresh context — escalates."""
    h = PortHistory(
        target="@t", origin="x/y",
        failed_patch_attempts=5,
        has_fresh_user_context=False,
    )
    d = _decide(h, policy, max_attempts=3)
    assert d.action == "escalate_manual"


# --- (4) sticky signature escalates faster ---------------------------------


def test_sticky_signature_escalates(policy):
    h = PortHistory(
        target="@t", origin="x/y",
        failed_patch_attempts=3,
        last_failure_signature="abc123",
        signature_repeat_count=3,
    )
    d = _decide(h, policy, max_attempts=3, signature_stickiness=3)
    assert d.action == "escalate_manual"
    assert d.extra["escalation_cause"] == "sticky_signature"


def test_varying_signatures_do_not_trigger_sticky(policy):
    """Cap hit, but signatures vary — falls through to patch_cap reason."""
    h = PortHistory(
        target="@t", origin="x/y",
        failed_patch_attempts=3,
        last_failure_signature="abc123",
        signature_repeat_count=1,   # only 1 with this sig
    )
    d = _decide(h, policy, max_attempts=3, signature_stickiness=3)
    assert d.action == "escalate_manual"
    assert d.extra["escalation_cause"] == "patch_cap"


def test_sticky_signature_below_cap_does_not_escalate(policy):
    """Stickiness is gated on the patch cap being hit. 2 same-sig
    failures with no failed patches yet → keep trying."""
    h = PortHistory(
        target="@t", origin="x/y",
        failed_patch_attempts=2,
        last_failure_signature="abc123",
        signature_repeat_count=5,
    )
    d = _decide(h, policy, max_attempts=3, signature_stickiness=3)
    assert d.action == "auto_patch"


# --- (5) absolute bundle backstop ------------------------------------------


def test_bundle_backstop_triggers_when_far_past(policy):
    """Plan: 'absolute safety net even without patch attempts'."""
    h = PortHistory(
        target="@t", origin="x/y",
        recent_failures=15, failed_patch_attempts=0,
    )
    d = _decide(h, policy, max_attempts=3, bundle_backstop=10)
    assert d.action == "escalate_manual"
    assert d.extra["escalation_cause"] == "bundle_backstop"


def test_bundle_backstop_does_not_fire_under_threshold(policy):
    h = PortHistory(
        target="@t", origin="x/y",
        recent_failures=9, failed_patch_attempts=0,
    )
    d = _decide(h, policy, max_attempts=3, bundle_backstop=10)
    assert d.action == "auto_patch"


# --- (6) precedence among rules --------------------------------------------


def test_env_broken_short_circuits_before_cap(policy):
    """env_broken always wins regardless of how cooked the history is."""
    h = PortHistory(
        target="@t", origin="x/y",
        recent_failures=100, failed_patch_attempts=10,
    )
    d = decide(
        "plist-error", "high", h,
        env_health=_FakeHealth(status="broken"),
        policy=policy,
        max_attempts=3, bundle_backstop=10,
    )
    assert d.action == "skip"
    assert "env_broken" in d.reason


def test_manual_classification_short_circuits_before_cap(policy):
    """classification=MANUAL escalates with the resolved tier (not the
    forced-MANUAL retry tier), so the audit can see 'triage's call'."""
    h = PortHistory(
        target="@t", origin="x/y",
        failed_patch_attempts=10,   # would also escalate via patch_cap
    )
    d = _decide(h, policy, classification="missing-dep",
                confidence="high", max_attempts=3)
    assert d.action == "escalate_manual"
    # No escalation_cause set — this branch is the classification path,
    # distinguished from the patch_cap / sticky / backstop paths.
    assert "escalation_cause" not in d.extra


def test_fresh_context_promotes_manual_classification_to_assist(policy):
    """Step 29-A1: a MANUAL-tier classification with fresh operator
    context auto-promotes to ASSIST instead of escalating. Before
    A1 this was a structural dead-end — the /retry-with-context UX
    re-triaged, reproduced the same classification, and re-
    escalated forever. Now the patch agent gets a chance to apply
    the operator's directive. The patch-cap counter does not reset
    here (this rule fires before the patch-cap branch in decide())."""
    h = PortHistory(
        target="@t", origin="x/y",
        failed_patch_attempts=5,
        has_fresh_user_context=True,
    )
    d = _decide(h, policy, classification="missing-dep",
                confidence="high", max_attempts=3)
    assert d.action == "auto_patch"
    assert d.tier.name == "ASSIST"
    assert d.extra["original_tier"] == "MANUAL"
    assert d.extra["promoted_via"] == "user_context"


# --- (7) extra dict shape sanity -------------------------------------------


def test_extra_dict_carries_all_diagnostic_fields(policy):
    h = PortHistory(
        target="@t", origin="x/y",
        recent_failures=2, failed_patch_attempts=3,
        signature_repeat_count=2, last_failure_signature="abc",
    )
    d = _decide(h, policy, max_attempts=3, window_hours=2,
                bundle_backstop=10, signature_stickiness=3)
    # Whatever escalation cause fires, the extras should let an
    # operator reconstruct the decision after the fact.
    assert d.extra["failed_patch_attempts"] == 3
    assert d.extra["patch_cap"] == 3
    assert d.extra["recent_failures"] == 2
    assert d.extra["bundle_backstop"] == 10
    assert d.extra["window_hours"] == 2
    assert d.extra["signature_repeat_count"] == 2
    assert d.extra["signature_stickiness"] == 3
