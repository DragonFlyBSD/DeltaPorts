"""Regression: ``agentic_status.manual_pending`` and
``list_manual_requests(open_only=True)`` agree on what "pending"
means, and a re-escalated bundle (status='pending' AND
last_handled == context_rev) is visible to the operator.

Two distinct bugs covered:

1. Initial mismatch — count showed N while the queue rendered
   empty (queue used a stricter filter than the count).
2. Re-escalation invisible — after the runner processed an
   operator round and the agent escalated again, the UCR row's
   ``last_context_rev_handled`` equaled the latest ``context_rev``,
   so the queue's ``rev > handled`` predicate excluded it. The
   operator saw nothing in the queue despite the bundle sitting
   in ``escalated_manual`` with operator action implicitly required.

Both bugs are fixed by simplifying the queue/count predicate to
``status = 'pending'``. The status column directly encodes
"operator action awaited"; the runner sweep keeps its own
``rev > handled`` gate so we don't get infinite re-triage loops.
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
    """The reported bug: bundle re-escalated after the operator's
    context was processed (status='pending', last_handled = context_rev).
    Operator should see this row in the queue — the agent's re-
    escalation means the prior round didn't unblock the build, and
    the operator can submit another round or discard.

    Pre-fix: count was 1 (status-only filter), list was 0 (rev>handled
    excluded). After the count fix to match the list, both reported
    0 — dashboard agreed with queue, but the bundle was invisible.
    Post-fix: both 1, by simplifying both queries to status='pending'."""
    _insert_ucr(conn, "r1", "devel/foo", "b1",
                status="pending", last_handled=1)
    _insert_user_context(conn, "r1", "devel/foo", context_rev=1)
    assert _pending_count(conn) == 1
    assert _list_len(conn) == 1


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
    """Four rows across distinct states — count and list agree on
    which are operator-actionable (i.e. status='pending')."""
    # Pending: fresh, no context yet.
    _insert_ucr(conn, "r1", "devel/a", "b1",
                status="pending", last_handled=0)
    # Pending: new operator round, runner hasn't picked up yet.
    _insert_ucr(conn, "r1", "devel/b", "b2",
                status="pending", last_handled=1)
    _insert_user_context(conn, "r1", "devel/b", context_rev=2)
    # Pending: re-escalated after the operator's context was handled.
    # This is the previously-invisible state — operator should see it.
    _insert_ucr(conn, "r1", "devel/c", "b3",
                status="pending", last_handled=1)
    _insert_user_context(conn, "r1", "devel/c", context_rev=1)
    # Mid-flight: runner enqueued a re-triage, not yet escalated.
    _insert_ucr(conn, "r1", "devel/d", "b4",
                status="retriage_enqueued", last_handled=2)
    _insert_user_context(conn, "r1", "devel/d", context_rev=2)
    # Discarded: terminal, not actionable.
    _insert_ucr(conn, "r1", "devel/e", "b5",
                status="discarded", last_handled=0)
    assert _pending_count(conn) == 3
    assert _list_len(conn) == 3
