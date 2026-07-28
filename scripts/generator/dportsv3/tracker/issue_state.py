"""Single source of truth for an *issue's* operator-facing state, the
actions allowed on it, and how issues group into the worklist.

The issue layer sits above the occurrence layer (`fix_state`) exactly the
way Sentry's Issue sits above its Events: an issue is one fingerprinted
problem, an occurrence (bundle) is one failure of it. This module is to
the issue what `fix_state` is to the occurrence, and it is deliberately
built on top of `fix_state` rather than duplicating it — an open issue's
worklist band is derived by running the occurrence projection on its
*actionable occurrence*.

Two axes, combined here:

- **Lifecycle** — `issue.state` ∈ {unresolved, regressed, resolved,
  muted}. Drives *surfacing*: open issues in the worklist, resolved in
  the collapsed archive, muted in a collapsed muted section. `regressed`
  (a fix that came back) is projected loud.
- **Actionable occurrence** — the latest occurrence by timestamp. Its
  `fix_status` (via `fix_state.worklist_bucket`) drives *which* action
  band an open issue lands in (ready / verify / decide / owned).

Pure over dicts — no DB, no HTTP. Queries (WS6) assemble the issue rows +
their occurrences; endpoints (WS7) enforce the action gate; the UI (WS9)
renders the groups.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dportsv3.tracker import fix_state

# --- Issue-state vocabulary (one definition) -------------------------------

ISSUE_UNRESOLVED = "unresolved"
ISSUE_REGRESSED = "regressed"
ISSUE_RESOLVED = "resolved"
ISSUE_MUTED = "muted"

# The two "needs attention" states — an open problem the operator may act on.
ISSUE_OPEN_STATES: frozenset[str] = frozenset({ISSUE_UNRESOLVED, ISSUE_REGRESSED})


# --- Issue-action policy (authoritative gate; consumed by WS7 endpoints) ----

# action -> issue.state -> allowed? Mirrors the plan's operator transition
# table: mute an open issue, unmute a muted one, manually resolve anything
# not already resolved, reopen a resolved one.
ISSUE_ACTION_ALLOWED = {
    "mute": lambda s: s in ISSUE_OPEN_STATES,
    "unmute": lambda s: s == ISSUE_MUTED,
    "resolve": lambda s: s != ISSUE_RESOLVED,
    "reopen": lambda s: s == ISSUE_RESOLVED,
}


def issue_action_allowed(action: str, state: str | None) -> bool:
    """Authoritative state-gate for an issue-level action. Unknown action
    names are refused (True is never the default)."""
    gate = ISSUE_ACTION_ALLOWED.get(action)
    return bool(gate(state)) if gate else False


def issue_actions(issue: dict[str, Any]) -> dict[str, bool]:
    """Which issue-level controls the UI shows/enables, given its state.

    A straight mirror of the gate (unlike `bundle_actions`, the issue
    surface isn't intentionally narrowed) — mute↔unmute and resolve↔reopen
    are contextual opposites.
    """
    s = issue.get("state")
    return {
        "can_mute": issue_action_allowed("mute", s),
        "can_unmute": issue_action_allowed("unmute", s),
        "can_resolve": issue_action_allowed("resolve", s),
        "can_reopen": issue_action_allowed("reopen", s),
    }


# --- Lifecycle projection (consumed by templates for the issue badge) -------


@dataclass(frozen=True)
class IssueStatus:
    """One operator-facing lifecycle status for an issue."""
    key: str        # stable machine key
    label: str      # human text for the badge
    pill: str       # css pill class: built | failed | skipped | total | ignored


_ISSUE_STATUS: dict[str, IssueStatus] = {
    ISSUE_UNRESOLVED: IssueStatus("unresolved", "open", "total"),
    ISSUE_REGRESSED: IssueStatus("regressed", "regressed", "failed"),   # loud
    ISSUE_RESOLVED: IssueStatus("resolved", "resolved", "built"),
    ISSUE_MUTED: IssueStatus("muted", "muted", "ignored"),
}


def issue_status(issue: dict[str, Any]) -> IssueStatus:
    """Project `issue.state` into the lifecycle badge (key/label/pill)."""
    return _ISSUE_STATUS.get(
        issue.get("state"), IssueStatus("unknown", "—", "total")
    )


# --- Actionable occurrence + worklist bucketing -----------------------------

# Bucket key -> (heading, pill class) in display order. Extends the
# occurrence worklist with a collapsed `muted` section; `done` here means
# "resolved" (the issue archive), not an accepted occurrence.
ISSUE_WORKLIST_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("ready", "Ready to accept", "built"),
    ("verify", "Needs verify", "skipped"),
    ("decide", "Needs a decision", "failed"),
    ("owned", "You own", "total"),
    ("done", "Resolved", "ignored"),
    ("muted", "Muted", "ignored"),
)


def actionable_occurrence(
    occurrences: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """The occurrence the operator would act on: the newest by ``ts_utc``.

    Expects occurrences for one issue; returns None for an empty list.
    """
    if not occurrences:
        return None
    return max(occurrences, key=lambda o: (o.get("ts_utc") or ""))


def issue_bucket(
    issue: dict[str, Any], occurrences: list[dict[str, Any]],
) -> str | None:
    """The worklist bucket for an issue, or None when it isn't
    operator-actionable right now (muted issues get their own bucket).

    Lifecycle decides surfacing; the actionable occurrence decides the
    action band for an *open* issue:

    - muted → ``muted`` (collapsed section, discoverable + unmuteable);
    - resolved → ``done`` (the archive);
    - open, latest occurrence in-flight/unknown → None (runner is working
      it — nothing to do yet);
    - open, latest occurrence still actionable → its own band
      (ready/verify/decide/owned);
    - open, latest occurrence accepted → None (fix is shipping; awaiting
      delivery, not an operator action);
    - open, latest occurrence otherwise terminal-per-occurrence
      (rejected/discarded, or a merged occurrence under a manually-reopened
      issue) → ``decide`` — the attempt is spent but the problem persists,
      so it needs a fresh one;
    - open with no occurrences at all → ``decide`` (open, needs a look).
    """
    state = issue.get("state")
    if state == ISSUE_MUTED:
        return "muted"
    if state == ISSUE_RESOLVED:
        return "done"

    act = actionable_occurrence(occurrences)
    if act is None:
        return "decide"
    occ_key = fix_state.fix_status(act).key
    occ_bucket = fix_state.worklist_bucket(act)
    if occ_bucket is None:
        return None                      # in_progress / unknown
    if occ_bucket != "done":
        return occ_bucket                # ready / verify / decide / owned
    # Occurrence is terminal-per-occurrence, but the issue is still open.
    # A just-accepted fix is shipping (a merge will resolve the issue), so
    # it's not an operator action; anything else spent needs a fresh attempt.
    if occ_key == "accepted":
        return None                      # awaiting delivery
    return "decide"                      # rejected / discarded / reopened-merged


# Count at which an issue's recurrences read as systemic (loud, floats up).
_SYSTEMIC_THRESHOLD = 3


def issue_group(
    issue: dict[str, Any], occurrences: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project one issue + its occurrences into a render-ready group.

    Rollups (`times_seen`, `first_seen_at`, `last_seen_at`) come from the
    persisted issue row — the authoritative counters — while `count` is
    the number of occurrences actually supplied (a window may be smaller
    than lifetime `times_seen`). Occurrences are ordered newest-first and
    rolled up by distinct `fix_status` label, mirroring the old
    origin-band shape so the template stays close.
    """
    ordered = sorted(
        occurrences, key=lambda o: (o.get("ts_utc") or ""), reverse=True
    )
    rollup: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    for o in ordered:
        s = fix_state.fix_status(o)
        entry = seen.get(s.label)
        if entry is None:
            entry = {"label": s.label, "cls": s.pill, "n": 0}
            seen[s.label] = entry
            rollup.append(entry)
        entry["n"] += 1

    state = issue.get("state")
    times_seen = issue.get("times_seen") or len(ordered)
    latest = ordered[0] if ordered else None
    return {
        "issue_key": issue.get("issue_key"),
        "origin": issue.get("origin") or "—",
        "target": issue.get("target"),
        "state": state,
        "status": issue_status(issue),
        "times_seen": times_seen,
        "count": len(ordered),
        "first_seen_at": issue.get("first_seen_at"),
        "last_seen_at": issue.get("last_seen_at"),
        "regressed": state == ISSUE_REGRESSED,
        "muted": state == ISSUE_MUTED,
        "resolved": state == ISSUE_RESOLVED,
        "systemic": (times_seen or 0) >= _SYSTEMIC_THRESHOLD,
        "latest": latest,
        "latest_ts": (latest.get("ts_utc") if latest else "") or "",
        "occurrences": ordered,
        "rollup": rollup,
        "bucket": issue_bucket(issue, occurrences),
        "actions": issue_actions(issue),
    }


def build_issue_worklist(
    issues: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Bucket issues into the operator worklist by their actionable state.

    Each input issue dict carries its issue-row fields plus an
    ``occurrences`` list (the WS6 join). Returns a dict keyed by
    `ISSUE_WORKLIST_SECTIONS`; within each bucket, groups sort
    systemic-first (highest lifetime `times_seen`) then most-recent.
    Issues that aren't operator-actionable right now (bucket None) are
    omitted.
    """
    buckets: dict[str, list[dict[str, Any]]] = {
        key: [] for key, _label, _cls in ISSUE_WORKLIST_SECTIONS
    }
    for issue in issues:
        group = issue_group(issue, issue.get("occurrences") or [])
        bucket = group["bucket"]
        if bucket:
            buckets[bucket].append(group)
    for groups in buckets.values():
        groups.sort(
            key=lambda g: (g["times_seen"] or 0, g["latest_ts"]), reverse=True
        )
    return buckets
