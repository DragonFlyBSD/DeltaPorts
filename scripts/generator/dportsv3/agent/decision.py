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

    Step 6 enrichment — beyond raw bundle-failure count, ``decide()``
    needs to know:

    - ``failed_patch_attempts``: how many times the agent actually
      tried and failed (``patch_gave_up`` / ``patch_budget_exhausted``
      retire reasons). The primary signal — "is automation stuck?"
    - ``has_fresh_user_context``: did the operator just intervene?
      One free retry after fresh context lands.
    - ``last_failure_signature`` + ``signature_repeat_count``: if every
      attempt fails with the same first-error-line, the agent is
      stuck on the same wall; if signatures vary, it's progressing.
    """
    target: str
    origin: str
    recent_failures: int = 0
    last_success_at: str | None = None
    last_attempt_at: str | None = None
    # Step 6 fields.
    failed_patch_attempts: int = 0
    has_fresh_user_context: bool = False
    last_failure_signature: str | None = None
    signature_repeat_count: int = 0

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
        """Query state.db for this port's recent outcomes + agent attempts."""
        if conn is None or not origin:
            return cls.empty(target, origin)
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max(0, int(window_hours)))
        ).isoformat()
        # Per-query try/except so tests with stripped-down schemas
        # (and runtime situations where a column hasn't migrated yet)
        # still get a partial PortHistory rather than an empty()
        # short-circuit.
        recent_failures = 0
        last_success_at = None
        last_attempt_at = None
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
            if row is not None:
                recent_failures = int(row[0] or 0)
                last_success_at = row[1]
                last_attempt_at = row[2]
        except sqlite3.Error:
            pass

        failed_patch_attempts = 0
        last_failed_patch_at = ""
        try:
            row = conn.execute(
                """SELECT COUNT(*),
                          MAX(COALESCE(last_transition_at, last_seen_at, ''))
                   FROM jobs
                   WHERE origin = ?
                     AND type = 'patch'
                     AND retire_reason IN ('patch_gave_up', 'patch_budget_exhausted')
                     AND COALESCE(last_transition_at, last_seen_at, '') >= ?
                     AND (target = ?
                          OR (? = '' AND (target IS NULL OR target = '')))""",
                (origin, cutoff, target, target),
            ).fetchone()
            if row is not None:
                failed_patch_attempts = int(row[0] or 0)
                last_failed_patch_at = (row[1] or "")
        except sqlite3.Error:
            pass

        has_fresh_user_context = False
        try:
            row = conn.execute(
                """SELECT updated_at FROM user_context
                   WHERE origin = ? ORDER BY updated_at DESC LIMIT 1""",
                (origin,),
            ).fetchone()
            user_context_at = (row[0] if row and row[0] else "") or ""
            # If there are no failed patch attempts yet, any operator
            # context counts as "fresh". Otherwise it must be newer
            # than the last failed patch.
            if user_context_at:
                if not last_failed_patch_at:
                    has_fresh_user_context = True
                else:
                    has_fresh_user_context = (
                        user_context_at > last_failed_patch_at
                    )
        except sqlite3.Error:
            pass

        last_failure_signature = None
        signature_repeat_count = 0
        try:
            row = conn.execute(
                """SELECT error_signature FROM bundles
                   WHERE origin = ? AND result IN ('failure', 'fail')
                     AND error_signature IS NOT NULL
                     AND (target = ?
                          OR (? = '' AND (target IS NULL OR target = '')))
                   ORDER BY last_seen_at DESC LIMIT 1""",
                (origin, target, target),
            ).fetchone()
            last_failure_signature = (row[0] if row else None) or None
            if last_failure_signature:
                row = conn.execute(
                    """SELECT COUNT(*) FROM bundles
                       WHERE origin = ? AND result IN ('failure', 'fail')
                         AND error_signature = ?
                         AND last_seen_at >= ?
                         AND (target = ?
                              OR (? = '' AND (target IS NULL OR target = '')))""",
                    (origin, last_failure_signature, cutoff, target, target),
                ).fetchone()
                signature_repeat_count = int(row[0] or 0) if row else 0
        except sqlite3.Error:
            pass

        return cls(
            target=target,
            origin=origin,
            recent_failures=recent_failures,
            last_success_at=last_success_at,
            last_attempt_at=last_attempt_at,
            failed_patch_attempts=failed_patch_attempts,
            has_fresh_user_context=has_fresh_user_context,
            last_failure_signature=last_failure_signature,
            signature_repeat_count=signature_repeat_count,
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
#
# Step 6 renames the old single ``max_attempts`` knob into two:
# - ``patch_cap``         : the primary signal. Number of failed
#   agent patch attempts (patch_gave_up / patch_budget_exhausted)
#   that triggers escalation. Was ``max_attempts``.
# - ``bundle_backstop``   : absolute safety. If failure bundles for
#   this origin go *way* past the patch cap (even without any
#   patch attempts), escalate anyway. Catches the "agent never
#   engaged but something is clearly very wrong" case.
# - ``signature_stickiness``: how many consecutive same-signature
#   failure bundles count as "stuck on the same wall".
DEFAULT_MAX_ATTEMPTS = 3      # patch_cap (kept name for callers)
DEFAULT_WINDOW_HOURS = 2
DEFAULT_BUNDLE_BACKSTOP = 10
DEFAULT_SIGNATURE_STICKINESS = 3


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
    is_slave: bool = False,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    bundle_backstop: int = DEFAULT_BUNDLE_BACKSTOP,
    signature_stickiness: int = DEFAULT_SIGNATURE_STICKINESS,
) -> Decision:
    """Resolve the right action for one triage outcome.

    Step 6 priority order (first matching rule wins):

    1. ``env_health.status == "broken"`` → ``skip``.
    2. ``tier_for(...).name == "MANUAL"`` → ``escalate_manual``.
    3. ``failed_patch_attempts >= patch_cap`` AND fresh user context
       → ``auto_patch``. Operator just intervened; give it one more
       shot. (Cap is allowed to climb past patch_cap by one — if the
       fresh-context attempt also fails, the next decision lands on
       rule 4 instead.)
    4. ``failed_patch_attempts >= patch_cap`` AND sticky signature
       (``signature_repeat_count >= signature_stickiness``)
       → ``escalate_manual``. Agent has hit the same wall repeatedly.
    5. ``failed_patch_attempts >= patch_cap`` AND no fresh context
       → ``escalate_manual``. Agent flailed.
    6. ``recent_failures >= bundle_backstop`` → ``escalate_manual``.
       Absolute safety net even if the agent never engaged.
    7. Otherwise → ``auto_patch``.

    Key inversion vs. the legacy single-cap: ``recent_failures``
    (bundle ingest count) is no longer the primary signal. A port can
    fail N times due to upstream churn without escalating, as long as
    the agent gets its turns. Only N failed agent attempts (or the
    absolute backstop) triggers MANUAL.
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

    # (1b) Slave-port short-circuit. The dops pipeline has no master/slave
    # model: a slave's fix usually belongs in the master's PATCHDIR/overlay,
    # which the per-origin classify/compose can neither author nor verify.
    # Until that lands (backlog), refuse ASSIST and hand off to an operator.
    if is_slave:
        return Decision(
            action="escalate_manual",
            tier=_manual_tier(policy),
            reason="slave_port_unsupported: inherits from master; no master-aware dops support",
            extra={
                "is_slave": True,
                "classification": classification,
                "confidence": confidence,
            },
        )

    resolved = tier_for(policy, classification, confidence)

    # (2) Triage routes to MANUAL by classification/confidence —
    # *unless* the operator has provided fresh context. Step 29-A1:
    # the MANUAL-tier classifications (missing-dep, fetch-error,
    # runtime-error, dependency-conflict, unknown) are unconditional
    # in the policy table, which makes the operator's /retry-with-
    # context UX a dead-end — every round re-classifies the same way
    # and re-escalates. When operator context is present we promote
    # MANUAL → ASSIST so the patch agent runs and gets a chance to
    # apply the operator's directive. The patch agent sees the
    # operator's text via UserContextSection in PATCH_SECTIONS and
    # any prior changes.diff via PriorAttemptsSection.
    if resolved.name == "MANUAL":
        if history.has_fresh_user_context:
            promoted = policy.tiers.get("ASSIST") or Tier(name="ASSIST")
            return Decision(
                action="auto_patch",
                tier=promoted,
                reason=(
                    f"classification={classification} confidence={confidence} "
                    f"would resolve to MANUAL but fresh operator context "
                    f"promotes to ASSIST"
                ),
                extra={
                    "classification": classification,
                    "confidence": confidence,
                    "original_tier": "MANUAL",
                    "promoted_via": "user_context",
                },
            )
        return Decision(
            action="escalate_manual",
            tier=resolved,
            reason=(
                f"classification={classification} confidence={confidence} "
                f"resolved to MANUAL"
            ),
            extra={"classification": classification, "confidence": confidence},
        )

    # Common extra dict for the patch-cap branches.
    cap_extra = {
        "classification": classification,
        "confidence": confidence,
        "failed_patch_attempts": history.failed_patch_attempts,
        "patch_cap": max_attempts,
        "recent_failures": history.recent_failures,
        "bundle_backstop": bundle_backstop,
        "window_hours": window_hours,
        "has_fresh_user_context": history.has_fresh_user_context,
        "signature_repeat_count": history.signature_repeat_count,
        "signature_stickiness": signature_stickiness,
    }

    patch_cap_hit = history.failed_patch_attempts >= max_attempts

    # (3) Patch cap hit but operator provided fresh context — one
    # more shot. Allows up to ``patch_cap`` agent retries per
    # operator-context revision.
    if patch_cap_hit and history.has_fresh_user_context:
        return Decision(
            action="auto_patch",
            tier=resolved,
            reason=(
                f"patch cap reached ({history.failed_patch_attempts} failures) "
                f"but fresh operator context lands — retrying"
            ),
            extra={**cap_extra, "original_tier": resolved.name,
                   "cap_reset_via": "user_context"},
        )

    # (4) Patch cap hit + same failure signature seen N times → stuck.
    if patch_cap_hit and history.signature_repeat_count >= signature_stickiness:
        return Decision(
            action="escalate_manual",
            tier=_manual_tier(policy),
            reason=(
                f"patch cap reached and same failure signature recurred "
                f"{history.signature_repeat_count}× "
                f"(sig={history.last_failure_signature}) — automation stuck"
            ),
            extra={**cap_extra, "original_tier": resolved.name,
                   "escalation_cause": "sticky_signature"},
        )

    # (5) Patch cap hit, no fresh context, signatures vary or absent.
    if patch_cap_hit:
        return Decision(
            action="escalate_manual",
            tier=_manual_tier(policy),
            reason=(
                f"patch cap reached: {history.failed_patch_attempts} failed "
                f"agent attempts >= {max_attempts} in last {window_hours}h"
            ),
            extra={**cap_extra, "original_tier": resolved.name,
                   "escalation_cause": "patch_cap"},
        )

    # (6) Absolute backstop. Should rarely fire — it means the
    # bundles are piling up without the agent producing any patch
    # attempts (e.g. all the patch jobs are failing on env_broken).
    if history.recent_failures >= bundle_backstop:
        return Decision(
            action="escalate_manual",
            tier=_manual_tier(policy),
            reason=(
                f"absolute backstop: {history.recent_failures} failure bundles "
                f">= {bundle_backstop} in last {window_hours}h"
            ),
            extra={**cap_extra, "original_tier": resolved.name,
                   "escalation_cause": "bundle_backstop"},
        )

    # (7) Default — auto-patch.
    return Decision(
        action="auto_patch",
        tier=resolved,
        reason=(
            f"tier={resolved.name} for classification={classification}, "
            f"confidence={confidence}"
        ),
        extra={"classification": classification, "confidence": confidence,
               "failed_patch_attempts": history.failed_patch_attempts,
               "recent_failures": history.recent_failures},
    )
