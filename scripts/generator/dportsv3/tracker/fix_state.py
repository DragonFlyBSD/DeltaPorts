"""Single source of truth for a failure bundle's operator-facing state
and the actions allowed on it.

Two concepts, previously smeared across the bundle-detail view handler
(a ~130-line inline matrix) and each of the 8 operator POST endpoints
(each re-deriving its own allowed-resolution set):

- ``fix_status(bundle)`` — the **state projection**. Folds the three raw
  vocabularies (``bundle.resolution``, ``verification_status``, and the
  in-flight ``job.state``) into one operator-facing status: a key, a
  human label, and a pill class. Templates render this instead of the
  raw columns.

- Action policy, with a deliberate two-level split:
    * ``ACTION_ALLOWED[action](resolution, verification_status)`` — the
      **authoritative gate**. This is what an operator POST endpoint
      checks: "may this action run against a bundle in this state?"
      Endpoints keep their own metadata/concurrency guards (target /
      origin / run_id present, skip-lock ownership) and their own HTTP
      shaping — those were never duplicated policy.
    * ``bundle_actions(bundle)`` — the **UI surface**. Which buttons a
      page shows/enables. Deliberately *narrower* than ``ACTION_ALLOWED``
      (e.g. take-over is authorized on a NULL-resolution bundle via the
      CLI, but the UI hides it to avoid noise on un-triaged bundles).
      Returning both, from one place, is what lets that intended
      narrowing be explicit instead of drifting silently.

Resolution vocabulary is the agent-set half from ``agent.lifecycle``
(``_EVENT_TO_RESOLUTION``) plus the operator-set half defined here; both
are re-exported as constants so nothing string-literals them again. No
legacy handling — the DB is wiped, not migrated, so there are no old
column values to tolerate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

# --- Resolution vocabulary (one definition) --------------------------------

# Agent/loop-set resolutions (mirrors agent.lifecycle._EVENT_TO_RESOLUTION).
RESOLUTION_AGENT_FIXED = "agent_fixed"
RESOLUTION_AGENT_GAVE_UP = "agent_gave_up"
RESOLUTION_AGENT_BUDGET = "agent_budget_exhausted"
RESOLUTION_ESCALATED = "escalated_manual"
RESOLUTION_CONVERT_GAVE_UP = "convert_gave_up"
RESOLUTION_TRIAGE_FAILED = "triage_failed"

# Operator-set resolutions (set by the tracker's POST endpoints).
RESOLUTION_ACCEPTED = "accepted"
RESOLUTION_REJECTED = "rejected"
RESOLUTION_DISCARDED = "discarded"
RESOLUTION_OPERATOR_OWNED = "operator_owned"

# Delivery-set resolution: the bundle's PR landed upstream. Set by the
# lazy merge-reconciler (delivery_sync) or the manual delivery-status
# endpoint. Dominant + terminal — a merged PR means the codebase already
# changed, so there is nothing left to accept/reject/verify. Whatever the
# resolution was (agent_fixed after a reopen, accepted, even rejected),
# upstream reality wins.
RESOLUTION_MERGED = "merged"

# Terminal decisions — nothing acts on these except reopen.
TERMINAL_RESOLUTIONS: frozenset[str] = frozenset(
    {
        RESOLUTION_ACCEPTED, RESOLUTION_REJECTED,
        RESOLUTION_DISCARDED, RESOLUTION_MERGED,
    }
)

# "The agent tried and lost" outcomes — the take-over / discard / retry
# lane. triage_failed is deliberately NOT here (it signals infra, not a
# lost fight) and matches the pre-refactor behavior.
FAILURE_RESOLUTIONS: frozenset[str] = frozenset(
    {
        RESOLUTION_AGENT_BUDGET,
        RESOLUTION_AGENT_GAVE_UP,
        RESOLUTION_ESCALATED,
        RESOLUTION_CONVERT_GAVE_UP,
    }
)

VERIFIED = "verified"
VERIFICATION_FAILED = "verification_failed"


# --- Action gate (authoritative; consumed by the POST endpoints) -----------

# action name -> (resolution, verification_status) -> allowed?
# Captures ONLY the state gate each endpoint checks today; the endpoint
# keeps its metadata/concurrency guards and HTTP shaping.
ACTION_ALLOWED: dict[str, Callable[[str | None, str | None], bool]] = {
    # verify blocks only the two hard-terminal decisions (notably NOT
    # discarded — a discarded bundle can still be verified).
    "verify": lambda r, v: r not in (RESOLUTION_ACCEPTED, RESOLUTION_REJECTED),
    "accept": lambda r, v: r not in TERMINAL_RESOLUTIONS and v == VERIFIED,
    "reject": lambda r, v: r not in TERMINAL_RESOLUTIONS,
    "take-over": lambda r, v: r in (FAILURE_RESOLUTIONS | {None}),
    "discard": lambda r, v: r in (
        FAILURE_RESOLUTIONS | {RESOLUTION_OPERATOR_OWNED, None}
    ),
    "retry": lambda r, v: r not in TERMINAL_RESOLUTIONS,
    "release": lambda r, v: r == RESOLUTION_OPERATOR_OWNED,
    "reopen": lambda r, v: r in TERMINAL_RESOLUTIONS,
}


def action_allowed(
    action: str, resolution: str | None, verification_status: str | None,
) -> bool:
    """Authoritative state-gate for an operator action. Unknown action
    names are refused (True is never the default)."""
    gate = ACTION_ALLOWED.get(action)
    return bool(gate(resolution, verification_status)) if gate else False


# --- UI surface (consumed by the bundle-detail view) -----------------------


def bundle_actions(bundle: dict[str, Any]) -> dict[str, Any]:
    """Which operator actions the bundle-detail page shows/enables.

    Pure over ``resolution`` / ``verification_status`` / ``target`` /
    ``origin`` — no DB access (the verify env-picker data is a live read
    the caller merges in). Field names + semantics are preserved exactly
    from the pre-refactor inline matrix so the template is unchanged.

    Narrower than ``ACTION_ALLOWED`` on purpose (see module docstring).
    """
    r = bundle.get("resolution")
    v = bundle.get("verification_status")
    has_meta = bool((bundle.get("target") or "").strip()) and bool(
        (bundle.get("origin") or "").strip()
    )

    actionable = r == RESOLUTION_AGENT_FIXED
    can_take_over = r in FAILURE_RESOLUTIONS and has_meta
    can_discard = r in (FAILURE_RESOLUTIONS | {RESOLUTION_OPERATOR_OWNED})
    can_retry = r in (
        FAILURE_RESOLUTIONS | {RESOLUTION_OPERATOR_OWNED, RESOLUTION_AGENT_FIXED}
    )
    can_reopen = r in TERMINAL_RESOLUTIONS
    verify_eligible = actionable or r == RESOLUTION_OPERATOR_OWNED
    can_release = r == RESOLUTION_OPERATOR_OWNED
    can_accept = verify_eligible and v == VERIFIED
    # Accept renders (possibly disabled) on the agent_fixed lane so the
    # operator sees the verify→accept path; enabled only once verified.
    show_accept_button = actionable or can_accept

    return {
        "show": (
            actionable or can_take_over or can_discard
            or can_retry or can_reopen or can_release
        ),
        "show_11c_group": actionable,   # gates the Reject group
        "show_accept_button": show_accept_button,
        "can_verify": verify_eligible,
        "can_accept": can_accept,
        "can_reject": actionable,
        "can_take_over": can_take_over,
        "can_discard": can_discard,
        "can_retry": can_retry,
        "can_reopen": can_reopen,
        "can_release": can_release,
    }


# --- State projection (consumed by templates for the status pill) ----------

# In-flight job states — a bundle with resolution=NULL whose job is still
# working. (Terminal job states done/dead/escalated always carry a
# resolution, so they resolve via the resolution branch below.)
_INFLIGHT_JOB_STATES: frozenset[str] = frozenset(
    {
        "queued", "claimed", "triaging", "triaged",
        "patching", "verifying", "converting", "verifying_fix",
    }
)


@dataclass(frozen=True)
class FixStatus:
    """One operator-facing status for a failure bundle."""
    key: str        # stable machine key
    label: str      # human text for the pill
    pill: str       # css pill class: built | failed | skipped | total | ignored


# (resolution -> FixStatus) for the resolutions that don't depend on
# verification_status. The verification-sensitive ones are handled first.
_RESOLUTION_STATUS: dict[str, FixStatus] = {
    RESOLUTION_AGENT_GAVE_UP: FixStatus("agent_gave_up", "agent gave up", "failed"),
    RESOLUTION_AGENT_BUDGET: FixStatus("budget_out", "budget out", "failed"),
    RESOLUTION_ESCALATED: FixStatus("escalated", "escalated", "skipped"),
    RESOLUTION_CONVERT_GAVE_UP: FixStatus("convert_gave_up", "convert gave up", "failed"),
    RESOLUTION_TRIAGE_FAILED: FixStatus("triage_failed", "triage failed", "failed"),
    RESOLUTION_ACCEPTED: FixStatus("accepted", "accepted", "built"),
    RESOLUTION_MERGED: FixStatus("merged", "merged upstream", "built"),
    RESOLUTION_REJECTED: FixStatus("rejected", "rejected", "failed"),
    RESOLUTION_DISCARDED: FixStatus("discarded", "discarded", "skipped"),
}


def fix_status(bundle: dict[str, Any]) -> FixStatus:
    """Project the raw (resolution, verification_status, job.state)
    columns into one operator-facing status.

    Order matters: the verification-sensitive resolutions
    (agent_fixed / operator_owned) resolve first, then the fixed-mapping
    resolutions, then the resolution=NULL in-flight/unknown fallback.
    """
    r = bundle.get("resolution")
    v = bundle.get("verification_status")

    if r == RESOLUTION_AGENT_FIXED:
        if v == VERIFIED:
            return FixStatus("verified", "verified", "built")
        if v == VERIFICATION_FAILED:
            return FixStatus("verify_failed", "verify failed", "failed")
        return FixStatus("needs_review", "agent fixed — verify", "built")

    if r == RESOLUTION_OPERATOR_OWNED:
        if v == VERIFIED:
            return FixStatus("owned_verified", "you own · verified", "built")
        return FixStatus("operator_owned", "you own this", "skipped")

    mapped = _RESOLUTION_STATUS.get(r) if r is not None else None
    if mapped is not None:
        return mapped

    # resolution is NULL: distinguish in-flight from unknown via job.state.
    state = bundle.get("state") or bundle.get("job_state")
    if state in _INFLIGHT_JOB_STATES:
        return FixStatus("in_progress", "in progress", "total")
    return FixStatus("unknown", "—", "total")


# --- Worklist bucketing (Phase 6) --------------------------------------------

# fix_status.key -> worklist bucket. Keys not listed (in_progress, unknown)
# are runner-transient or untriaged and don't belong in the operator worklist.
_WORKLIST_BUCKET: dict[str, str] = {
    "verified": "ready",
    "owned_verified": "ready",
    "needs_review": "verify",
    "verify_failed": "decide",
    "agent_gave_up": "decide",
    "budget_out": "decide",
    "escalated": "decide",
    "convert_gave_up": "decide",
    "triage_failed": "decide",
    "operator_owned": "owned",
    "accepted": "done",
    "merged": "done",
    "rejected": "done",
    "discarded": "done",
}

# Bucket key -> (heading, pill class) in display order. The landing renders
# sections in this order; `done` is collapsed.
WORKLIST_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("ready", "Ready to accept", "built"),
    ("verify", "Needs verify", "skipped"),
    ("decide", "Needs a decision", "failed"),
    ("owned", "You own", "total"),
    ("done", "Recently resolved", "ignored"),
)


def build_worklist(
    bundles: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Group bundle rows into operator-workflow buckets by fix_status.

    Returns a dict keyed by bucket (ready/verify/decide/owned/done); each
    value holds that bucket's bundles in input order. Bundles projecting to
    in_progress (runner working) or unknown (fresh/untriaged) are omitted —
    they aren't operator-actionable from the landing.
    """
    buckets: dict[str, list[dict[str, Any]]] = {
        key: [] for key, _label, _cls in WORKLIST_SECTIONS
    }
    for bundle in bundles:
        bucket = _WORKLIST_BUCKET.get(fix_status(bundle).key)
        if bucket:
            buckets[bucket].append(bundle)
    return buckets


# Count at which a port's repeated failures read as systemic, not a
# one-off — earns the "recurring" tag and floats to the top of its band.
_RECURRING_THRESHOLD = 3


def group_band_by_origin(
    bundles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Dedup one worklist band by port origin (the Sentry model).

    Bundles for the same origin collapse into one group carrying a
    ``count``, a status ``rollup`` (each distinct fix_status label with
    its occurrence count), and the ``attempts`` newest-first. A group of
    one renders as a plain row; ``>1`` is expandable. Groups sort
    systemic-first (highest count) then most-recent, so a port failing
    over and over is the loudest thing in the band instead of N
    look-alike rows burying the signal.
    """
    by_origin: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for b in bundles:
        origin = b.get("origin") or "—"
        if origin not in by_origin:
            by_origin[origin] = []
            order.append(origin)
        by_origin[origin].append(b)

    groups: list[dict[str, Any]] = []
    for origin in order:
        attempts = sorted(
            by_origin[origin],
            key=lambda b: (b.get("ts_utc") or ""),
            reverse=True,
        )
        rollup: list[dict[str, Any]] = []
        seen: dict[str, dict[str, Any]] = {}
        for b in attempts:
            s = fix_status(b)
            entry = seen.get(s.label)
            if entry is None:
                entry = {"label": s.label, "cls": s.pill, "n": 0}
                seen[s.label] = entry
                rollup.append(entry)
            entry["n"] += 1
        count = len(attempts)
        groups.append({
            "origin": origin,
            "count": count,
            "is_group": count > 1,
            "recurring": count >= _RECURRING_THRESHOLD,
            "latest": attempts[0],
            "latest_ts": attempts[0].get("ts_utc") or "",
            "attempts": attempts,
            "rollup": rollup,
        })
    # Systemic first (highest count), then most-recent failure.
    groups.sort(key=lambda g: (g["count"], g["latest_ts"]), reverse=True)
    return groups
