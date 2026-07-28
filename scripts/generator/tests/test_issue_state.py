"""WS5 — the issue state model (tracker.issue_state).

Pins the read-side projection that sits above `fix_state`:

- lifecycle badge per `issue.state` (regressed loud, muted/resolved toned);
- the issue-action gate + UI surface (mute↔unmute, resolve↔reopen);
- the bucket rule combining lifecycle (surfacing) with the *actionable
  occurrence* (the action band), including the subtle terminal-latest-
  but-open cases;
- systemic-first worklist ordering.
"""

from __future__ import annotations

import itertools

import pytest

from dportsv3.tracker import issue_state as I


def _occ(bundle_id, resolution, verification=None, ts="2026-07-25T00:00:00Z",
         state=None):
    d = {"bundle_id": bundle_id, "resolution": resolution,
         "verification_status": verification, "ts_utc": ts}
    if state:
        d["state"] = state
    return d


def _issue(state, *, key="k", origin="ftp/curl", target="@2026Q3",
           times_seen=5, occurrences=None):
    return {
        "issue_key": key, "state": state, "origin": origin, "target": target,
        "times_seen": times_seen,
        "first_seen_at": "2026-07-20T00:00:00Z",
        "last_seen_at": "2026-07-25T00:00:00Z",
        "occurrences": occurrences or [],
    }


# --- lifecycle projection --------------------------------------------------


@pytest.mark.parametrize("state,key,pill", [
    ("unresolved", "unresolved", "total"),
    ("regressed", "regressed", "failed"),   # loud
    ("resolved", "resolved", "built"),
    ("muted", "muted", "ignored"),
])
def test_issue_status_projection(state, key, pill):
    st = I.issue_status({"state": state})
    assert (st.key, st.pill) == (key, pill)


def test_issue_status_unknown_state():
    assert I.issue_status({"state": "bogus"}).key == "unknown"
    assert I.issue_status({}).key == "unknown"


def test_issue_status_pill_classes_are_known():
    known = {"built", "failed", "skipped", "total", "ignored"}
    for state in ["unresolved", "regressed", "resolved", "muted", None]:
        assert I.issue_status({"state": state}).pill in known


# --- action gate + surface -------------------------------------------------


def _expected_allowed(action, state):
    if action == "mute":
        return state in ("unresolved", "regressed")
    if action == "unmute":
        return state == "muted"
    if action == "resolve":
        return state != "resolved"
    if action == "reopen":
        return state == "resolved"
    raise AssertionError(action)


@pytest.mark.parametrize("action", sorted(I.ISSUE_ACTION_ALLOWED))
@pytest.mark.parametrize("state", ["unresolved", "regressed", "resolved", "muted"])
def test_issue_action_gate(action, state):
    assert I.issue_action_allowed(action, state) == _expected_allowed(action, state)


def test_issue_action_unknown_refused():
    assert I.issue_action_allowed("frobnicate", "unresolved") is False


def test_issue_actions_surface_matches_gate():
    for state in ["unresolved", "regressed", "resolved", "muted"]:
        acts = I.issue_actions({"state": state})
        assert acts == {
            "can_mute": I.issue_action_allowed("mute", state),
            "can_unmute": I.issue_action_allowed("unmute", state),
            "can_resolve": I.issue_action_allowed("resolve", state),
            "can_reopen": I.issue_action_allowed("reopen", state),
        }


# --- actionable occurrence -------------------------------------------------


def test_actionable_occurrence_is_newest():
    occs = [
        _occ("old", "agent_gave_up", ts="2026-07-24T00:00:00Z"),
        _occ("new", "agent_fixed", "verified", ts="2026-07-25T09:00:00Z"),
        _occ("mid", "triage_failed", ts="2026-07-24T12:00:00Z"),
    ]
    assert I.actionable_occurrence(occs)["bundle_id"] == "new"


def test_actionable_occurrence_empty_is_none():
    assert I.actionable_occurrence([]) is None


# --- bucket rule (lifecycle × actionable occurrence) -----------------------


@pytest.mark.parametrize("state,occ,expected", [
    # open issue, band from the actionable occurrence
    ("unresolved", _occ("b", "agent_fixed", "verified"), "ready"),
    ("unresolved", _occ("b", "agent_fixed"), "verify"),
    ("unresolved", _occ("b", "agent_gave_up"), "decide"),
    ("unresolved", _occ("b", "operator_owned"), "owned"),
    ("regressed", _occ("b", "agent_fixed", "verified"), "ready"),
    # latest occurrence in-flight → not actionable yet
    ("unresolved", _occ("b", None, state="patching"), None),
    # terminal-per-occurrence but issue still open:
    ("unresolved", _occ("b", "accepted", "verified"), None),   # awaiting delivery
    ("unresolved", _occ("b", "rejected"), "decide"),           # try again
    ("unresolved", _occ("b", "discarded"), "decide"),
    # lifecycle-terminal issues
    ("resolved", _occ("b", "merged"), "done"),
    ("muted", _occ("b", "agent_gave_up"), "muted"),
    # operator reopened a resolved issue whose latest occurrence merged:
    # the merge is spent, the problem's back open → needs a fresh attempt
    ("unresolved", _occ("b", "merged"), "decide"),
])
def test_issue_bucket(state, occ, expected):
    issue = _issue(state, occurrences=[occ])
    assert I.issue_bucket(issue, [occ]) == expected


def test_open_issue_without_occurrences_needs_a_look():
    assert I.issue_bucket(_issue("unresolved", occurrences=[]), []) == "decide"


def test_bucket_uses_latest_not_any_occurrence():
    """A stale verified attempt does not keep a since-rejected issue in
    'ready' — the newest occurrence wins."""
    old = _occ("old", "agent_fixed", "verified", ts="2026-07-24T00:00:00Z")
    new = _occ("new", "rejected", ts="2026-07-25T00:00:00Z")
    issue = _issue("unresolved", occurrences=[old, new])
    assert I.issue_bucket(issue, [old, new]) == "decide"


# --- group projection + worklist ------------------------------------------


def test_issue_group_shape_and_rollup():
    occs = [
        _occ("a", "agent_gave_up", ts="2026-07-25T00:30:00Z"),
        _occ("b", "agent_gave_up", ts="2026-07-25T11:00:00Z"),
        _occ("c", "triage_failed", ts="2026-07-25T00:32:00Z"),
    ]
    issue = _issue("regressed", times_seen=9, occurrences=occs)
    g = I.issue_group(issue, occs)
    assert g["state"] == "regressed"
    assert g["regressed"] is True and g["systemic"] is True
    assert g["times_seen"] == 9           # from the issue row, not len(occs)
    assert g["count"] == 3
    # newest-first: b (11:00) > c (00:32) > a (00:30)
    assert [o["bundle_id"] for o in g["occurrences"]] == ["b", "c", "a"]
    assert g["latest"]["bundle_id"] == "b"
    rollup = {r["label"]: r["n"] for r in g["rollup"]}
    assert rollup == {"agent gave up": 2, "triage failed": 1}
    # regressed + verified? no — latest is gave_up → decide band
    assert g["bucket"] == "decide"


def test_build_issue_worklist_buckets_and_orders():
    issues = [
        _issue("unresolved", key="k1", origin="a/a", times_seen=1,
               occurrences=[_occ("x", "agent_gave_up", ts="2026-07-25T01:00:00Z")]),
        _issue("unresolved", key="k2", origin="b/b", times_seen=7,
               occurrences=[_occ("y", "agent_gave_up", ts="2026-07-25T00:00:00Z")]),
        _issue("unresolved", key="k3", origin="c/c", times_seen=2,
               occurrences=[_occ("z", "agent_fixed", "verified")]),
        _issue("muted", key="k4", origin="d/d",
               occurrences=[_occ("w", "agent_gave_up")]),
        _issue("resolved", key="k5", origin="e/e",
               occurrences=[_occ("v", "merged")]),
        # in-flight latest → omitted entirely
        _issue("unresolved", key="k6", origin="f/f",
               occurrences=[_occ("u", None, state="patching")]),
    ]
    wl = I.build_issue_worklist(issues)
    # decide bucket: systemic-first (k2 times_seen=7 before k1 times_seen=1)
    assert [g["issue_key"] for g in wl["decide"]] == ["k2", "k1"]
    assert [g["issue_key"] for g in wl["ready"]] == ["k3"]
    assert [g["issue_key"] for g in wl["muted"]] == ["k4"]
    assert [g["issue_key"] for g in wl["done"]] == ["k5"]
    # k6 (in-flight) is not operator-actionable → in no bucket
    everywhere = {g["issue_key"] for b in wl.values() for g in b}
    assert "k6" not in everywhere


def test_worklist_sections_cover_every_bucket():
    section_keys = {k for k, _l, _c in I.ISSUE_WORKLIST_SECTIONS}
    # every non-None bucket issue_bucket can emit is a real section
    assert {"ready", "verify", "decide", "owned", "done", "muted"} <= section_keys
    # and an occurrence's own bucket (from fix_state, reused for the
    # actionable occurrence) is always a valid issue-worklist section.
    from dportsv3.tracker import fix_state
    assert set(fix_state._WORKLIST_BUCKET.values()) <= section_keys
