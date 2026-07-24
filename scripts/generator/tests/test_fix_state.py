"""Tests for tracker.fix_state — the bundle state projection + action policy.

The critical property is **behavior preservation**: `bundle_actions` must
reproduce the pre-refactor inline matrix exactly, and `ACTION_ALLOWED`
must reproduce each POST endpoint's state gate exactly. Both are pinned
here by re-implementing the old rules independently and asserting they
agree across the full state cross-product.
"""

from __future__ import annotations

import itertools

import pytest

from dportsv3.tracker import fix_state as fs


# Every resolution the system can produce, plus NULL.
ALL_RESOLUTIONS = [
    None,
    "agent_fixed",
    "agent_gave_up",
    "agent_budget_exhausted",
    "escalated_manual",
    "convert_gave_up",
    "triage_failed",
    "operator_owned",
    "accepted",
    "merged",
    "rejected",
    "discarded",
]
ALL_VERIFICATION = [None, "verified", "verification_failed"]

_TERMINAL = {"accepted", "merged", "rejected", "discarded"}
_FAILURE = {
    "agent_budget_exhausted", "agent_gave_up",
    "escalated_manual", "convert_gave_up",
}


# --- ACTION_ALLOWED: reproduce each endpoint's state gate independently ----


def _expected_allowed(action: str, r: str | None, v: str | None) -> bool:
    if action == "verify":
        return r not in ("accepted", "rejected")
    if action == "accept":
        return r not in _TERMINAL and v == "verified"
    if action == "reject":
        return r not in _TERMINAL
    if action == "take-over":
        return r in (_FAILURE | {None})
    if action == "discard":
        return r in (_FAILURE | {"operator_owned", None})
    if action == "retry":
        return r not in _TERMINAL
    if action == "release":
        return r == "operator_owned"
    if action == "reopen":
        return r in _TERMINAL
    raise AssertionError(action)


@pytest.mark.parametrize("action", sorted(fs.ACTION_ALLOWED))
@pytest.mark.parametrize("r,v", itertools.product(ALL_RESOLUTIONS, ALL_VERIFICATION))
def test_action_allowed_matches_endpoint_gates(action, r, v):
    assert fs.action_allowed(action, r, v) == _expected_allowed(action, r, v)


def test_action_allowed_unknown_action_refused():
    assert fs.action_allowed("nonexistent", "agent_fixed", "verified") is False


# --- bundle_actions: reproduce the pre-refactor inline matrix --------------


def _expected_surface(bundle: dict) -> dict:
    r = bundle.get("resolution")
    v = bundle.get("verification_status")
    has_meta = bool(bundle.get("target")) and bool(bundle.get("origin"))
    actionable = r == "agent_fixed"
    can_take_over = r in _FAILURE and has_meta
    can_discard = r in (_FAILURE | {"operator_owned"})
    can_retry = r in (_FAILURE | {"operator_owned", "agent_fixed"})
    can_reopen = r in _TERMINAL
    verify_eligible = actionable or r == "operator_owned"
    can_release = r == "operator_owned"
    can_accept = verify_eligible and v == "verified"
    show_accept_button = actionable or can_accept
    return {
        "show": (actionable or can_take_over or can_discard
                 or can_retry or can_reopen or can_release),
        "show_11c_group": actionable,
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


@pytest.mark.parametrize("r,v", itertools.product(ALL_RESOLUTIONS, ALL_VERIFICATION))
@pytest.mark.parametrize("meta", [True, False])
def test_bundle_actions_matches_prior_matrix(r, v, meta):
    bundle = {
        "resolution": r,
        "verification_status": v,
        "target": "2026Q3" if meta else "",
        "origin": "lang/python312" if meta else "",
    }
    assert fs.bundle_actions(bundle) == _expected_surface(bundle)


def test_surface_is_subset_of_allowed():
    """Every action the UI enables must be authorized by the gate — the
    UI may be narrower but never broader."""
    for r in ALL_RESOLUTIONS:
        for v in ALL_VERIFICATION:
            bundle = {"resolution": r, "verification_status": v,
                      "target": "t", "origin": "o"}
            acts = fs.bundle_actions(bundle)
            surface = {
                "verify": acts["can_verify"], "accept": acts["can_accept"],
                "reject": acts["can_reject"], "take-over": acts["can_take_over"],
                "discard": acts["can_discard"], "retry": acts["can_retry"],
                "release": acts["can_release"], "reopen": acts["can_reopen"],
            }
            for action, shown in surface.items():
                if shown:
                    assert fs.action_allowed(action, r, v), (
                        f"UI enables {action} on ({r},{v}) but the gate forbids it"
                    )


# --- fix_status projection -------------------------------------------------


@pytest.mark.parametrize("r,v,expected_key", [
    ("agent_fixed", "verified", "verified"),
    ("agent_fixed", "verification_failed", "verify_failed"),
    ("agent_fixed", None, "needs_review"),
    ("operator_owned", "verified", "owned_verified"),
    ("operator_owned", None, "operator_owned"),
    ("agent_gave_up", None, "agent_gave_up"),
    ("agent_budget_exhausted", None, "budget_out"),
    ("escalated_manual", None, "escalated"),
    ("convert_gave_up", None, "convert_gave_up"),
    ("triage_failed", None, "triage_failed"),
    ("accepted", None, "accepted"),
    ("merged", None, "merged"),
    ("merged", "verified", "merged"),   # verification is irrelevant once merged
    ("rejected", None, "rejected"),
    ("discarded", None, "discarded"),
])
def test_fix_status_resolution_keys(r, v, expected_key):
    assert fs.fix_status({"resolution": r, "verification_status": v}).key == expected_key


def test_merged_is_terminal_done_and_reopen_only():
    """A merged PR means the code shipped: terminal, in the `done`
    bucket, and the only action left is reopen (no re-Accept that would
    spawn a duplicate PR)."""
    assert "merged" in fs.TERMINAL_RESOLUTIONS
    assert fs._WORKLIST_BUCKET["merged"] == "done"
    acts = fs.bundle_actions({
        "resolution": "merged", "verification_status": "verified",
        "target": "@2026Q3", "origin": "ftp/curl",
    })
    assert acts["can_accept"] is False
    assert acts["can_reject"] is False
    assert acts["can_retry"] is False
    assert acts["can_verify"] is False
    assert acts["can_reopen"] is True
    # And the authoritative gate agrees: accept is refused, reopen allowed.
    assert fs.action_allowed("accept", "merged", "verified") is False
    assert fs.action_allowed("reopen", "merged", None) is True


def test_fix_status_null_resolution_inflight_vs_unknown():
    assert fs.fix_status({"resolution": None, "state": "patching"}).key == "in_progress"
    assert fs.fix_status({"resolution": None, "state": "done"}).key == "unknown"
    assert fs.fix_status({"resolution": None}).key == "unknown"


def test_fix_status_pill_classes_are_known():
    known = {"built", "failed", "skipped", "total", "ignored"}
    for r in ALL_RESOLUTIONS:
        for v in ALL_VERIFICATION:
            assert fs.fix_status(
                {"resolution": r, "verification_status": v}
            ).pill in known


# --- Worklist bucketing (Phase 6) --------------------------------------------


def test_build_worklist_buckets_by_fix_status():
    bundles = [
        {"bundle_id": "b1", "resolution": "agent_fixed",
         "verification_status": "verified"},        # ready
        {"bundle_id": "b2", "resolution": "agent_fixed"},          # verify
        {"bundle_id": "b3", "resolution": "agent_gave_up"},        # decide
        {"bundle_id": "b4", "resolution": "agent_budget_exhausted"},  # decide
        {"bundle_id": "b5", "resolution": "operator_owned"},       # owned
        {"bundle_id": "b6", "resolution": "accepted"},             # done
        {"bundle_id": "b7", "resolution": "rejected"},             # done
        {"bundle_id": "b8", "resolution": None},                   # omitted
    ]
    wl = fs.build_worklist(bundles)
    assert [b["bundle_id"] for b in wl["ready"]] == ["b1"]
    assert [b["bundle_id"] for b in wl["verify"]] == ["b2"]
    assert [b["bundle_id"] for b in wl["decide"]] == ["b3", "b4"]
    assert [b["bundle_id"] for b in wl["owned"]] == ["b5"]
    assert [b["bundle_id"] for b in wl["done"]] == ["b6", "b7"]
    # A NULL-resolution untriaged bundle isn't operator-actionable → omitted.
    bucketed = {b["bundle_id"] for bucket in wl.values() for b in bucket}
    assert "b8" not in bucketed


def test_build_worklist_preserves_input_order():
    bundles = [
        {"bundle_id": "z", "resolution": "agent_gave_up"},
        {"bundle_id": "a", "resolution": "agent_gave_up"},
    ]
    assert [b["bundle_id"] for b in fs.build_worklist(bundles)["decide"]] == ["z", "a"]


def test_worklist_sections_cover_every_bucket():
    section_keys = {k for k, _label, _cls in fs.WORKLIST_SECTIONS}
    assert set(fs._WORKLIST_BUCKET.values()) <= section_keys


def test_group_band_by_origin_dedups_and_rolls_up():
    band = [
        {"bundle_id": "p-1", "origin": "lang/python312",
         "ts_utc": "2026-07-23T00:30:00Z", "resolution": "triage_failed"},
        {"bundle_id": "p-2", "origin": "lang/python312",
         "ts_utc": "2026-07-23T11:36:00Z", "resolution": "agent_gave_up"},
        {"bundle_id": "p-3", "origin": "lang/python312",
         "ts_utc": "2026-07-23T00:32:00Z", "resolution": "agent_gave_up"},
        {"bundle_id": "q-1", "origin": "ftp/curl",
         "ts_utc": "2026-07-23T14:00:00Z", "resolution": "agent_gave_up"},
    ]
    groups = fs.group_band_by_origin(band)
    # Two ports; systemic (higher count) first.
    assert [g["origin"] for g in groups] == ["lang/python312", "ftp/curl"]
    lang, curl = groups
    assert lang["count"] == 3 and lang["is_group"] and lang["recurring"]
    assert curl["count"] == 1 and not curl["is_group"] and not curl["recurring"]
    # Attempts newest-first (11:36 > 00:32 > 00:30); latest is p-2.
    assert [b["bundle_id"] for b in lang["attempts"]] == ["p-2", "p-3", "p-1"]
    assert lang["latest"]["bundle_id"] == "p-2"
    # Rollup counts each distinct status label.
    rollup = {r["label"]: r["n"] for r in lang["rollup"]}
    assert rollup == {"agent gave up": 2, "triage failed": 1}


def test_group_band_by_origin_orders_ties_by_recency():
    band = [
        {"bundle_id": "old", "origin": "a/a", "ts_utc": "2026-07-01T00:00:00Z",
         "resolution": "agent_gave_up"},
        {"bundle_id": "new", "origin": "b/b", "ts_utc": "2026-07-20T00:00:00Z",
         "resolution": "agent_gave_up"},
    ]
    # Same count (1 each) → most-recent origin first.
    assert [g["origin"] for g in fs.group_band_by_origin(band)] == ["b/b", "a/a"]
