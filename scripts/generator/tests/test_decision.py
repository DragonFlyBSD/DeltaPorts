"""Unit tests for the policy decision engine.

Phase 3 Step 1. Covers:

- ``decide()`` priority order: env_broken > MANUAL classification >
  retry cap > auto_patch.
- ``PortHistory.load()`` against a synthetic in-memory state.db.
- ``PortHistory.empty()`` shape.
- ``Decision.to_dict()`` is JSON-friendly.

The parity sweep across every (classification, confidence) in the
shipped policy JSON lives in test_decision_parity.py (Step 3).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from dportsv3.agent.decision import (
    Decision,
    DEFAULT_MAX_ATTEMPTS,
    DEFAULT_WINDOW_HOURS,
    PortHistory,
    decide,
)
from dportsv3.agent.policy import Policy, Tier


# --- helpers ------------------------------------------------------------------


def _make_policy() -> Policy:
    """Minimal policy mirroring the shipped agentic-policy.json shape."""
    return Policy(
        tiers={
            "AUTO":   Tier(name="AUTO",   max_iterations=2, max_tokens=30000),
            "ASSIST": Tier(name="ASSIST", max_iterations=4, max_tokens=120000),
            "MANUAL": Tier(name="MANUAL", max_iterations=0, max_tokens=0),
        },
        classification_to_tier={
            "plist-error":         "AUTO",
            "fetch-checksum":      "AUTO",
            "pkg-format":          "AUTO",
            "compile-error":       "ASSIST",
            "patch-error":         "ASSIST",
            "link-error":          "ASSIST",
            "configure-error":     "ASSIST",
            "missing-dep":         "MANUAL",
            "fetch-error":         "MANUAL",
            "runtime-error":       "MANUAL",
            "dependency-conflict": "MANUAL",
            "unknown":             "MANUAL",
        },
        confidence_floor={"AUTO": "high", "ASSIST": "medium"},
    )


@dataclass
class _FakeHealth:
    """Stand-in for dportsv3.agent.health.EnvHealth (only .status read)."""
    status: str = "ready"


def _empty_history(origin="foo/bar") -> PortHistory:
    return PortHistory.empty(target="@test", origin=origin)


# --- decide() priority order --------------------------------------------------


def test_env_broken_yields_skip_regardless_of_classification():
    """Even an AUTO-tier classification + clean history yields skip
    when the env is broken."""
    d = decide(
        "plist-error", "high",
        _empty_history(),
        _FakeHealth(status="broken"),
        _make_policy(),
    )
    assert d.action == "skip"
    assert d.tier.name == "MANUAL"   # tier is MANUAL placeholder for skip
    assert "env_broken" in d.reason
    assert d.extra["env_health_status"] == "broken"


def test_env_broken_short_circuits_before_cap():
    history = PortHistory(target="@t", origin="foo/bar",
                          recent_failures=10)
    d = decide(
        "plist-error", "high",
        history,
        _FakeHealth(status="broken"),
        _make_policy(),
    )
    assert d.action == "skip"


def test_manual_classification_escalates():
    d = decide(
        "missing-dep", "high",
        _empty_history(),
        _FakeHealth(status="ready"),
        _make_policy(),
    )
    assert d.action == "escalate_manual"
    assert d.tier.name == "MANUAL"
    assert "missing-dep" in d.reason


def test_unknown_classification_routes_manual():
    """Unknown classifications fall back to MANUAL via the policy
    default in tier_for."""
    d = decide(
        "totally-novel-error", "high",
        _empty_history(),
        _FakeHealth(status="ready"),
        _make_policy(),
    )
    assert d.action == "escalate_manual"


def test_auto_high_clean_history_auto_patches():
    d = decide(
        "plist-error", "high",
        _empty_history(),
        _FakeHealth(status="ready"),
        _make_policy(),
    )
    assert d.action == "auto_patch"
    assert d.tier.name == "AUTO"
    assert d.tier.max_iterations == 2


def test_assist_medium_clean_history_auto_patches():
    d = decide(
        "compile-error", "medium",
        _empty_history(),
        _FakeHealth(status="ready"),
        _make_policy(),
    )
    assert d.action == "auto_patch"
    assert d.tier.name == "ASSIST"


def test_auto_with_low_confidence_downgrades_then_manual():
    """AUTO floor is "high"; with confidence=low the policy cascades
    AUTO → ASSIST → MANUAL (ASSIST floor is medium; low is below)."""
    d = decide(
        "plist-error", "low",
        _empty_history(),
        _FakeHealth(status="ready"),
        _make_policy(),
    )
    assert d.action == "escalate_manual"


def test_retry_cap_forces_manual_with_extra_fields():
    history = PortHistory(target="@test", origin="foo/bar",
                          recent_failures=3)
    d = decide(
        "plist-error", "high",
        history,
        _FakeHealth(status="ready"),
        _make_policy(),
        max_attempts=3,
        window_hours=2,
    )
    assert d.action == "escalate_manual"
    assert d.tier.name == "MANUAL"
    assert d.extra["original_tier"] == "AUTO"
    assert d.extra["recent_failures"] == 3
    assert d.extra["max_attempts"] == 3
    assert "retry cap" in d.reason


def test_cap_just_below_threshold_still_auto_patches():
    history = PortHistory(target="@test", origin="foo/bar",
                          recent_failures=2)
    d = decide(
        "plist-error", "high",
        history,
        _FakeHealth(status="ready"),
        _make_policy(),
        max_attempts=3,
    )
    assert d.action == "auto_patch"


def test_env_health_none_treated_as_ready():
    """``env_health=None`` (no probe data) shouldn't trigger skip —
    decide proceeds as if env is healthy."""
    d = decide(
        "plist-error", "high",
        _empty_history(),
        None,
        _make_policy(),
    )
    assert d.action == "auto_patch"


def test_decision_to_dict_json_friendly():
    d = decide(
        "plist-error", "high",
        _empty_history(),
        _FakeHealth(status="ready"),
        _make_policy(),
    )
    js = json.dumps(d.to_dict())
    loaded = json.loads(js)
    assert loaded["action"] == "auto_patch"
    assert loaded["tier"] == "AUTO"
    assert isinstance(loaded["extra"], dict)


# --- PortHistory.load --------------------------------------------------------


@pytest.fixture
def bundles_db():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        """CREATE TABLE bundles (
               bundle_id TEXT PRIMARY KEY,
               origin TEXT, target TEXT, result TEXT, last_seen_at TEXT
           )"""
    )
    yield conn
    conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_ago(n: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=n)).isoformat()


def test_port_history_load_counts_recent_failures(bundles_db):
    bundles_db.executemany(
        "INSERT INTO bundles VALUES (?, ?, ?, ?, ?)",
        [
            ("b1", "foo/bar", "@test", "failure", _now()),
            ("b2", "foo/bar", "@test", "failure", _hours_ago(0.5)),
            ("b3", "foo/bar", "@test", "failure", _hours_ago(5)),  # outside window
            ("b4", "foo/bar", "@test", "built", _hours_ago(0.1)),  # not a failure
            ("b5", "other/x", "@test", "failure", _now()),         # wrong origin
        ],
    )
    h = PortHistory.load(bundles_db, "@test", "foo/bar", window_hours=2)
    assert h.recent_failures == 2
    assert h.last_attempt_at is not None
    assert h.last_success_at is not None


def test_port_history_load_no_rows(bundles_db):
    h = PortHistory.load(bundles_db, "@test", "foo/bar", window_hours=2)
    assert h.recent_failures == 0
    assert h.last_success_at is None
    assert h.last_attempt_at is None


def test_port_history_load_handles_no_conn():
    h = PortHistory.load(None, "@test", "foo/bar", window_hours=2)
    assert h.recent_failures == 0
    assert h.origin == "foo/bar"


def test_port_history_load_handles_blank_origin(bundles_db):
    h = PortHistory.load(bundles_db, "@test", "", window_hours=2)
    assert h.recent_failures == 0


def test_port_history_load_legacy_null_target(bundles_db):
    """Legacy bundle rows pre-Phase-4-step-5 may have NULL target.
    When the caller passes target='', match those rows."""
    bundles_db.executemany(
        "INSERT INTO bundles VALUES (?, ?, ?, ?, ?)",
        [
            ("l1", "foo/bar", None, "failure", _now()),
            ("l2", "foo/bar", "",   "failure", _now()),
        ],
    )
    h = PortHistory.load(bundles_db, "", "foo/bar", window_hours=2)
    assert h.recent_failures == 2


def test_port_history_load_handles_sqlite_error(bundles_db):
    """A malformed schema mid-load shouldn't crash the runner;
    decide() needs to be able to proceed with empty history."""
    bundles_db.execute("DROP TABLE bundles")
    h = PortHistory.load(bundles_db, "@test", "foo/bar", window_hours=2)
    assert h.recent_failures == 0


def test_port_history_empty_classmethod():
    h = PortHistory.empty(target="@x", origin="cat/port")
    assert h.target == "@x"
    assert h.origin == "cat/port"
    assert h.recent_failures == 0


# --- default thresholds ------------------------------------------------------


def test_defaults_are_sane():
    """Make sure tweaking the defaults doesn't accidentally widen
    the cap to something that defeats the purpose."""
    assert 1 <= DEFAULT_MAX_ATTEMPTS <= 5
    assert 1 <= DEFAULT_WINDOW_HOURS <= 24
