"""Tests for tracker.delivery_sync — lazy, on-render reconciliation of a
bundle's upstream PR state into the terminal ``merged`` resolution.

The reconciler is the auto-detect half of the accept path: a delivered
GitHub PR that merges upstream should flip its bundle terminal (out of
the worklist, Accept/Reject gone, no duplicate re-Accept) without a
daemon. These tests pin the state machine — merged / closed / still-open
/ throttled / non-github / transport-failure — against a real state.db
using a fake merge-state probe (no network, no delivery config).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dportsv3.db.schema import init_db
from dportsv3.tracker import delivery_sync
from dportsv3.tracker.agentic_queries import (
    insert_review_request,
    latest_review_request_for_bundle,
    open_delivery_bundle_ids,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "state.db"
    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    init_db(conn)
    conn.close()
    return p


def _open(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _seed_bundle(conn, bundle_id, *, resolution="agent_fixed",
                 verification="verified", origin="ftp/curl",
                 target="@2026Q3") -> None:
    now = _now()
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
              result, target, path, last_seen_at, resolution,
              verification_status)
           VALUES (?, 'r', ?, '', ?, 'failure', ?, '', ?, ?, ?)""",
        (bundle_id, origin, now, target, now, resolution, verification),
    )
    conn.commit()


def _seed_review(conn, bundle_id, *, provider="github", status="created",
                 pr_id="1567", branch=None, last_synced_at=None) -> int:
    rid = insert_review_request(
        conn, bundle_id=bundle_id, provider=provider, status=status,
        provider_pr_id=pr_id, url=f"https://gh/pr/{pr_id}",
        branch=branch or f"agentic/{bundle_id}",
    )
    if last_synced_at is not None:
        conn.execute(
            "UPDATE bundle_review_requests SET last_synced_at = ? WHERE id = ?",
            (last_synced_at, rid),
        )
    conn.commit()
    return rid


def _state(conn, bundle_id):
    r = conn.execute(
        "SELECT resolution, pre_terminal_resolution FROM bundles WHERE bundle_id = ?",
        (bundle_id,),
    ).fetchone()
    rr = latest_review_request_for_bundle(conn, bundle_id)
    return r, rr


def _merged_probe(**over):
    payload = {"merged": True, "state": "closed", "url": "https://gh/pr/1567"}
    payload.update(over)
    return lambda pr_id: payload


# ---------------------------------------------------------------------
# The merge transition
# ---------------------------------------------------------------------


def test_merge_flips_bundle_terminal_and_snapshots_prior(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="agent_fixed")
    _seed_review(conn, "b1", status="created")
    conn.close()

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=_merged_probe(),
    )
    assert result == "merged"

    conn = _open(db_path)
    try:
        r, rr = _state(conn, "b1")
    finally:
        conn.close()
    # Bundle terminal; prior resolution preserved for reopen.
    assert r["resolution"] == "merged"
    assert r["pre_terminal_resolution"] == "agent_fixed"
    # Delivery row moved to merged with the upstream url.
    assert rr["status"] == "merged"
    assert rr["url"] == "https://gh/pr/1567"


def test_merge_emits_both_events(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1")
    _seed_review(conn, "b1")
    conn.close()

    delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=_merged_probe(),
    )
    conn = _open(db_path)
    try:
        events = [row["type"] for row in conn.execute(
            "SELECT type FROM events ORDER BY id"
        ).fetchall()]
    finally:
        conn.close()
    assert "bundle_merged" in events
    assert "bundle_delivery_status_changed" in events


def test_merge_overrides_even_a_rejected_resolution(db_path):
    """Upstream reality wins: a PR that merged means the code shipped,
    so even an operator 'rejected' is superseded."""
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="rejected")
    _seed_review(conn, "b1")
    conn.close()

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=_merged_probe(),
    )
    assert result == "merged"
    conn = _open(db_path)
    try:
        r, _ = _state(conn, "b1")
    finally:
        conn.close()
    assert r["resolution"] == "merged"
    assert r["pre_terminal_resolution"] == "rejected"


# ---------------------------------------------------------------------
# The no-op / guard paths
# ---------------------------------------------------------------------


def test_still_open_pr_is_not_flipped_but_sync_is_recorded(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1")
    _seed_review(conn, "b1", status="created")
    conn.close()

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=lambda pr: {"merged": False, "state": "open", "url": None},
    )
    assert result is None
    conn = _open(db_path)
    try:
        r, rr = _state(conn, "b1")
    finally:
        conn.close()
    assert r["resolution"] == "agent_fixed"      # untouched
    assert rr["status"] == "created"             # still open
    assert rr["last_synced_at"] is not None      # but we recorded the check


def test_closed_unmerged_marks_row_but_leaves_resolution(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="agent_fixed")
    _seed_review(conn, "b1")
    conn.close()

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=lambda pr: {"merged": False, "state": "closed", "url": None},
    )
    assert result == "closed"
    conn = _open(db_path)
    try:
        r, rr = _state(conn, "b1")
    finally:
        conn.close()
    assert rr["status"] == "closed"
    assert r["resolution"] == "agent_fixed"      # operator decides


def test_throttle_skips_recent_poll(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1")
    # Synced 10s ago; the 300s window hasn't elapsed.
    recent = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    _seed_review(conn, "b1", last_synced_at=recent)
    conn.close()

    calls = {"n": 0}

    def probe(pr):
        calls["n"] += 1
        return {"merged": True, "state": "closed", "url": "x"}

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", merge_state_fn=probe,
    )
    assert result is None
    assert calls["n"] == 0                       # never hit the API


def test_never_synced_row_is_stale_and_polls(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1")
    _seed_review(conn, "b1", last_synced_at=None)
    conn.close()

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1",   # default 300s window
        merge_state_fn=_merged_probe(),
    )
    assert result == "merged"


def test_non_github_provider_is_never_polled(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="accepted")
    _seed_review(conn, "b1", provider="local-patch", pr_id="outbox-1.patch")
    conn.close()

    calls = {"n": 0}

    def probe(pr):
        calls["n"] += 1
        return {"merged": True, "state": "closed", "url": "x"}

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=probe,
    )
    assert result is None
    assert calls["n"] == 0


def test_terminal_delivery_row_is_not_repolled(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="merged")
    _seed_review(conn, "b1", status="merged")
    conn.close()

    calls = {"n": 0}

    def probe(pr):
        calls["n"] += 1
        return {"merged": True, "state": "closed", "url": "x"}

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=probe,
    )
    assert result is None
    assert calls["n"] == 0


def test_missing_pr_id_is_noop(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1")
    _seed_review(conn, "b1", pr_id=None)
    conn.close()

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=_merged_probe(),
    )
    assert result is None


def test_no_delivery_row_is_noop(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1")
    conn.close()
    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=_merged_probe(),
    )
    assert result is None


def test_transport_failure_bumps_sync_and_does_not_flip(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1")
    _seed_review(conn, "b1", status="created", last_synced_at=None)
    conn.close()

    def boom(pr):
        raise RuntimeError("502 from GitHub")

    result = delivery_sync.reconcile_bundle_delivery(
        db_path=str(db_path), bundle_id="b1", min_interval_s=0,
        merge_state_fn=boom,
    )
    assert result is None
    conn = _open(db_path)
    try:
        r, rr = _state(conn, "b1")
    finally:
        conn.close()
    assert r["resolution"] == "agent_fixed"      # not flipped on a failed check
    assert rr["status"] == "created"
    assert rr["last_synced_at"] is not None       # but throttle advanced


# ---------------------------------------------------------------------
# set_bundle_merged_resolution — the shared writer
# ---------------------------------------------------------------------


def test_set_bundle_merged_resolution_idempotent(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="accepted")
    conn.close()

    write = sqlite3.connect(str(db_path), isolation_level=None)
    write.row_factory = sqlite3.Row
    try:
        prior = delivery_sync.set_bundle_merged_resolution(
            write, "b1", now_iso=_now(), source="manual",
        )
        assert prior == "accepted"
        # Second call is a no-op — already merged, no duplicate event.
        prior2 = delivery_sync.set_bundle_merged_resolution(
            write, "b1", now_iso=_now(), source="manual",
        )
        assert prior2 is None
    finally:
        write.close()

    conn = _open(db_path)
    try:
        merged_events = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE type = 'bundle_merged'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert merged_events == 1


def test_set_bundle_merged_resolution_unknown_bundle_is_noop(db_path):
    write = sqlite3.connect(str(db_path), isolation_level=None)
    write.row_factory = sqlite3.Row
    try:
        assert delivery_sync.set_bundle_merged_resolution(
            write, "nope", now_iso=_now(), source="manual",
        ) is None
    finally:
        write.close()


# ---------------------------------------------------------------------
# WS4 — issue ↔ occurrence coupling (a merged occurrence resolves its issue)
# ---------------------------------------------------------------------


def _seed_issue(conn, issue_key, *, state="unresolved", origin="ftp/curl",
                target="@2026Q3") -> None:
    now = _now()
    conn.execute(
        """INSERT INTO issues (issue_key, target, origin, fingerprint, state,
              times_seen, first_seen_at, last_seen_at, updated_at)
           VALUES (?, ?, ?, 'fp', ?, 1, ?, ?, ?)""",
        (issue_key, target, origin, state, now, now, now),
    )
    conn.commit()


def _link_bundle_to_issue(conn, bundle_id, issue_key) -> None:
    conn.execute(
        "UPDATE bundles SET issue_key = ? WHERE bundle_id = ?",
        (issue_key, bundle_id),
    )
    conn.commit()


@pytest.mark.parametrize("start_state", ["unresolved", "regressed", "muted"])
def test_merge_resolves_the_linked_issue(db_path, start_state):
    """A merged occurrence resolves its issue regardless of the issue's
    prior open state — the problem shipped."""
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="accepted")
    _seed_issue(conn, "iss1", state=start_state)
    _link_bundle_to_issue(conn, "b1", "iss1")
    conn.close()

    write = sqlite3.connect(str(db_path), isolation_level=None)
    write.row_factory = sqlite3.Row
    try:
        delivery_sync.set_bundle_merged_resolution(
            write, "b1", now_iso="2026-07-25T01:00:00Z", source="poll",
        )
    finally:
        write.close()

    conn = _open(db_path)
    try:
        issue = conn.execute(
            "SELECT state, resolved_at FROM issues WHERE issue_key = 'iss1'"
        ).fetchone()
        resolved_events = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE type = 'issue_resolved'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert issue["state"] == "resolved"
    assert issue["resolved_at"] == "2026-07-25T01:00:00Z"
    assert resolved_events == 1


def test_merge_resolve_is_idempotent_on_issue(db_path):
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="accepted")
    _seed_issue(conn, "iss1", state="unresolved")
    _link_bundle_to_issue(conn, "b1", "iss1")
    conn.close()

    write = sqlite3.connect(str(db_path), isolation_level=None)
    write.row_factory = sqlite3.Row
    try:
        # First merge resolves; a second merge is a bundle no-op and must
        # not re-resolve or emit a second issue_resolved.
        delivery_sync.set_bundle_merged_resolution(
            write, "b1", now_iso=_now(), source="poll")
        delivery_sync.set_bundle_merged_resolution(
            write, "b1", now_iso=_now(), source="poll")
        # Directly re-invoking on an already-resolved issue is also a no-op.
        assert delivery_sync.resolve_issue_for_bundle(
            write, "b1", now_iso=_now(), source="poll") is None
    finally:
        write.close()

    conn = _open(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) AS n FROM events WHERE type = 'issue_resolved'"
        ).fetchone()["n"]
    finally:
        conn.close()
    assert n == 1


def test_merge_of_bundle_without_issue_key_is_safe(db_path):
    """A pre-issue / degenerate occurrence merges fine; no issue touched."""
    conn = _open(db_path)
    _seed_bundle(conn, "b1", resolution="accepted")  # issue_key stays NULL
    conn.close()

    write = sqlite3.connect(str(db_path), isolation_level=None)
    write.row_factory = sqlite3.Row
    try:
        prior = delivery_sync.set_bundle_merged_resolution(
            write, "b1", now_iso=_now(), source="poll")
        assert prior == "accepted"  # bundle still merges
        assert delivery_sync.resolve_issue_for_bundle(
            write, "b1", now_iso=_now(), source="poll") is None
    finally:
        write.close()

    conn = _open(db_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0] == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------
# open_delivery_bundle_ids — the poll candidate set
# ---------------------------------------------------------------------


def test_open_delivery_bundle_ids_selects_only_latest_open_github(db_path):
    conn = _open(db_path)
    # open github → candidate
    _seed_bundle(conn, "open-gh")
    _seed_review(conn, "open-gh", provider="github", status="created")
    # updated github → candidate (idempotency state is still open)
    _seed_bundle(conn, "updated-gh")
    _seed_review(conn, "updated-gh", provider="github", status="updated")
    # merged github → excluded (terminal)
    _seed_bundle(conn, "merged-gh", resolution="merged")
    _seed_review(conn, "merged-gh", provider="github", status="merged")
    # open local-patch → excluded by provider filter
    _seed_bundle(conn, "open-lp")
    _seed_review(conn, "open-lp", provider="local-patch", status="created",
                 pr_id="outbox.patch")
    # a bundle whose LATEST row is closed even though an older row was open
    _seed_bundle(conn, "reclosed")
    _seed_review(conn, "reclosed", provider="github", status="created",
                 branch="agentic/reclosed-old")
    _seed_review(conn, "reclosed", provider="github", status="closed",
                 branch="agentic/reclosed-new")
    conn.commit()
    try:
        ids = open_delivery_bundle_ids(conn, provider="github")
    finally:
        conn.close()

    assert set(ids) == {"open-gh", "updated-gh"}
