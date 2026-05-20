"""Policy decision engine for the agentic loop.

Phase 3 of the agentic framework. Consolidates three previously-
scattered concerns into one ``decide()`` function:

1. Tier resolution (``policy.tier_for(classification, confidence)``
   with confidence-floor downgrades).
2. Per-(target, origin) retry cap — was inline in the runner's
   ``_process_triage_job_harness``.
3. Env-health gating at decision time — a broken chroot yields
   ``action="skip"`` so we don't escalate a port for a fault that
   isn't the port's.

The runner reads the returned ``Decision.action`` and routes:

- ``auto_patch``       → fire ``TRIAGE_OK`` + enqueue a patch job
- ``escalate_manual``  → fire ``TRIAGE_OK`` + ``ESCALATE_MANUAL``
- ``skip``             → fire ``TRIAGE_OK`` and stop; the runner
                         gate will pause the loop on env_broken
                         independently.

``decide()`` is pure (no I/O); ``PortHistory.load(conn, ...)`` is
the one place that touches state.db, and tests can use
``PortHistory.empty(...)`` to drive ``decide()`` against any
synthetic state.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Literal

from .policy import Policy, Tier, tier_for


Action = Literal["auto_patch", "escalate_manual", "skip"]


@dataclass
class PortHistory:
    """Per-(target, origin) outcome history within a rolling window.

    The runner used to compute ``recent_failure_count`` inline;
    that logic moves here so ``decide()`` is the single consumer
    and unit tests can stand up a synthetic history without
    touching the DB.
    """
    target: str
    origin: str
    recent_failures: int = 0
    last_success_at: str | None = None
    last_attempt_at: str | None = None

    @classmethod
    def empty(cls, target: str, origin: str) -> "PortHistory":
        """No-history default — first-attempt-feeling input."""
        return cls(target=target, origin=origin)

    @classmethod
    def load(
        cls,
        conn: sqlite3.Connection | None,
        target: str,
        origin: str,
        window_hours: int,
    ) -> "PortHistory":
        """Query the bundles table for this port's recent outcomes."""
        if conn is None or not origin:
            return cls.empty(target, origin)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max(0, int(window_hours)))
        ).isoformat()
        try:
            row = conn.execute(
                """SELECT
                     SUM(CASE WHEN result = 'failure' AND last_seen_at >= ?
                              THEN 1 ELSE 0 END)                              AS recent_failures,
                     MAX(CASE WHEN result = 'built'   THEN last_seen_at END)  AS last_success_at,
                     MAX(last_seen_at)                                        AS last_attempt_at
                   FROM bundles
                   WHERE origin = ?
                     AND (target = ?
                          OR (? = '' AND (target IS NULL OR target = '')))""",
                (cutoff, origin, target, target),
            ).fetchone()
        except sqlite3.Error:
            return cls.empty(target, origin)
        if row is None:
            return cls.empty(target, origin)
        return cls(
            target=target,
            origin=origin,
            recent_failures=int(row[0] or 0),
            last_success_at=row[1],
            last_attempt_at=row[2],
        )


@dataclass
class Decision:
    """The orchestrator's input — one ``decide()`` call → one value."""
    action: Action
    tier: Tier
    reason: str
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "tier": self.tier.name,
            "reason": self.reason,
            "extra": dict(self.extra),
        }


# Default thresholds. Operator overrides via env vars consumed at the
# runner-side call site (kept out of decide() so the function is pure).
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_WINDOW_HOURS = 2


def _manual_tier(policy: Policy) -> Tier:
    """Pick the MANUAL tier from the policy, with a synthesized
    fallback for malformed config."""
    return policy.tiers.get("MANUAL") or Tier(name="MANUAL")


def decide(
    classification: str,
    confidence: str,
    history: PortHistory,
    env_health,                  # dportsv3.agent.health.EnvHealth | None
    policy: Policy,
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    window_hours: int = DEFAULT_WINDOW_HOURS,
) -> Decision:
    """Resolve the right action for one triage outcome.

    Priority order (first matching rule wins):

    1. ``env_health.status == "broken"`` → ``skip``. The runner gate
       will pause the loop; no point flagging the port as MANUAL for
       a chroot-level fault.
    2. ``tier_for(...).name == "MANUAL"`` → ``escalate_manual``.
       Triage's own classification says manual.
    3. ``history.recent_failures >= max_attempts`` → ``escalate_manual``
       with tier forced to MANUAL. The retry cap: the agent keeps
       failing on this port; stop burning tokens.
    4. Otherwise → ``auto_patch`` with the resolved tier.
    """
    # (1) Env-broken short-circuit.
    if env_health is not None and getattr(env_health, "status", None) == "broken":
        return Decision(
            action="skip",
            tier=_manual_tier(policy),
            reason="env_broken: deferring decision until health is ready",
            extra={
                "env_health_status": "broken",
                "classification": classification,
                "confidence": confidence,
            },
        )

    resolved = tier_for(policy, classification, confidence)

    # (2) Triage routes to MANUAL by classification/confidence.
    if resolved.name == "MANUAL":
        return Decision(
            action="escalate_manual",
            tier=resolved,
            reason=(
                f"classification={classification} confidence={confidence} "
                f"resolved to MANUAL"
            ),
            extra={"classification": classification, "confidence": confidence},
        )

    # (3) Retry cap.
    if history.recent_failures >= max_attempts:
        return Decision(
            action="escalate_manual",
            tier=_manual_tier(policy),
            reason=(
                f"retry cap: {history.recent_failures} failures "
                f">= {max_attempts} in last {window_hours}h"
            ),
            extra={
                "original_tier": resolved.name,
                "classification": classification,
                "confidence": confidence,
                "recent_failures": history.recent_failures,
                "max_attempts": max_attempts,
                "window_hours": window_hours,
            },
        )

    # (4) Default — auto-patch.
    return Decision(
        action="auto_patch",
        tier=resolved,
        reason=(
            f"tier={resolved.name} for classification={classification}, "
            f"confidence={confidence}"
        ),
        extra={"classification": classification, "confidence": confidence},
    )
