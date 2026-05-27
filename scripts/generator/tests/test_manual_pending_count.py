"""Regression: ``agentic_status.manual_pending`` must match
``list_manual_requests(open_only=True)``.

Symptom: dashboard shows "1 pending" and the queue page renders
empty. Cause: count used ``status = 'pending'`` alone while the
list joined ``user_context`` and required
``context_rev > last_context_rev_handled``. After a re-triage flips
a UCR row back to ``status='pending'`` without bumping
``last_context_rev_handled``, the row qualifies for the count but
not the list.

These tests pin the two queries to the same semantic so the
dashboard never advertises work the queue can't show.
"""

from __future__ import annotations

import sqlite3

import pytest

from dportsv3.db.schema import init_db
from dportsv3.tracker.agentic_queries import (
    agentic_status,
    list_manual_requests,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _insert_ucr(
    conn, run_id, origin, bundle_id, status="pending",
    last_handled=0,
):
    conn.execute(
        """INSERT INTO user_context_requests
           (run_id, origin, bundle_id, requested_at, status,
            last_context_rev_handled)
           VALUES (?, ?, ?, '2026-05-27T00:00:00Z', ?, ?)""",
        (run_id, origin, bundle_id, status, last_handled),
    )
    conn.commit()


def _insert_user_context(conn, run_id, origin, context_rev):
    conn.execute(
        """INSERT INTO user_context
           (run_id, origin, context_text, updated_at, context_rev)
           VALUES (?, ?, 'text', '2026-05-27T00:00:00Z', ?)""",
        (run_id, origin, context_rev),
    )
    conn.commit()


def _pending_count(conn):
    return agentic_status(conn)["manual_pending"]


def _list_len(conn):
    return len(list_manual_requests(conn, open_only=True))


def test_count_matches_list_when_empty(conn):
    assert _pending_count(conn) == 0
    assert _list_len(conn) == 0


def test_count_matches_list_fresh_pending_no_context_yet(conn):
    """Operator hasn't answered yet. Both count and list show 1."""
    _insert_ucr(conn, "r1", "devel/foo", "b1",
                status="pending", last_handled=0)
    assert _pending_count(conn) == 1
    assert _list_len(conn) == 1


def test_count_matches_list_operator_answered_runner_not_yet(conn):
    """Operator answered (rev=1) but the runner sweep hasn't
    processed yet (handled=0). Both count and list show 1."""
    _insert_ucr(conn, "r1", "devel/foo", "b1",
                status="pending", last_handled=0)
    _insert_user_context(conn, "r1", "devel/foo", context_rev=1)
    assert _pending_count(conn) == 1
    assert _list_len(conn) == 1


def test_count_matches_list_after_retriage_handled_equals_rev(conn):
    """The reported bug: after re-triage, status='pending' but
    last_handled = context_rev. No new operator action to take.
    Pre-fix: count=1, list=0 (dashboard lies). Post-fix: both 0."""
    _insert_ucr(conn, "r1", "devel/foo", "b1",
                status="pending", last_handled=1)
    _insert_user_context(conn, "r1", "devel/foo", context_rev=1)
    assert _pending_count(conn) == 0
    assert _list_len(conn) == 0


def test_count_matches_list_new_round_after_prior_handled(conn):
    """Operator submitted a *new* round after the runner had
    handled the previous one. context_rev=2 > last_handled=1.
    Actionable again — both count and list show 1."""
    _insert_ucr(conn, "r1", "devel/foo", "b1",
                status="pending", last_handled=1)
    _insert_user_context(conn, "r1", "devel/foo", context_rev=2)
    assert _pending_count(conn) == 1
    assert _list_len(conn) == 1


def test_count_excludes_discarded(conn):
    """Discarded rows must not appear in either count or list,
    regardless of context state."""
    _insert_ucr(conn, "r1", "devel/foo", "b1",
                status="discarded", last_handled=0)
    assert _pending_count(conn) == 0
    assert _list_len(conn) == 0


def test_count_excludes_retriage_enqueued(conn):
    """Rows mid-flight (status='retriage_enqueued') aren't
    operator-actionable; both count and list omit them."""
    _insert_ucr(conn, "r1", "devel/foo", "b1",
                status="retriage_enqueued", last_handled=1)
    _insert_user_context(conn, "r1", "devel/foo", context_rev=1)
    assert _pending_count(conn) == 0
    assert _list_len(conn) == 0


def test_count_matches_list_mixed_population(conn):
    """Three rows in three different states — count and list
    agree on which are operator-actionable."""
    # Actionable: fresh pending, no context yet.
    _insert_ucr(conn, "r1", "devel/a", "b1",
                status="pending", last_handled=0)
    # Actionable: new operator round.
    _insert_ucr(conn, "r1", "devel/b", "b2",
                status="pending", last_handled=1)
    _insert_user_context(conn, "r1", "devel/b", context_rev=2)
    # Not actionable: handled equals rev.
    _insert_ucr(conn, "r1", "devel/c", "b3",
                status="pending", last_handled=1)
    _insert_user_context(conn, "r1", "devel/c", context_rev=1)
    # Not actionable: discarded.
    _insert_ucr(conn, "r1", "devel/d", "b4",
                status="discarded", last_handled=0)
    assert _pending_count(conn) == 2
    assert _list_len(conn) == 2
