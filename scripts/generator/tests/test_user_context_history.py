"""Step 29b: ``user_context_history`` is append-only; every
``upsert_user_context_text`` call preserves the round verbatim.

Covers:
- First write creates rev=1 history row alongside user_context row.
- Repeat writes append rev=2, rev=3, … without touching prior rows.
- ``submitted_by`` survives the round-trip (and NULL when omitted).
- ``list_user_context_history`` returns oldest → newest.
- The active ``user_context`` row still shows only the latest text
  (overwrite semantics preserved for existing read sites).
"""

from __future__ import annotations

import sqlite3

import pytest

from dportsv3.db.schema import init_db
from dportsv3.tracker.agentic_queries import (
    list_user_context_history,
    upsert_user_context_text,
)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_db(c)
    yield c
    c.close()


def _current_context(conn, run_id, origin):
    row = conn.execute(
        """SELECT context_text, context_rev FROM user_context
           WHERE run_id = ? AND origin = ?""",
        (run_id, origin),
    ).fetchone()
    return (row["context_text"], int(row["context_rev"])) if row else (None, 0)


def test_first_write_creates_history_row_and_user_context_row(conn):
    rev = upsert_user_context_text(
        conn, "run-1", "devel/foo", "round one text",
        submitted_by="op-a",
    )
    assert rev == 1

    history = list_user_context_history(conn, "run-1", "devel/foo")
    assert len(history) == 1
    assert history[0]["context_rev"] == 1
    assert history[0]["text"] == "round one text"
    assert history[0]["submitted_by"] == "op-a"
    assert history[0]["submitted_at"]  # ISO timestamp string, non-empty

    text, cur_rev = _current_context(conn, "run-1", "devel/foo")
    assert text == "round one text"
    assert cur_rev == 1


def test_repeat_writes_append_history_without_mutating_prior_rows(conn):
    upsert_user_context_text(
        conn, "run-1", "devel/foo", "round one", submitted_by="op-a",
    )
    upsert_user_context_text(
        conn, "run-1", "devel/foo", "round two", submitted_by="op-b",
    )
    upsert_user_context_text(
        conn, "run-1", "devel/foo", "round three", submitted_by="op-a",
    )

    history = list_user_context_history(conn, "run-1", "devel/foo")
    assert [h["context_rev"] for h in history] == [1, 2, 3]
    assert [h["text"] for h in history] == [
        "round one", "round two", "round three",
    ]
    assert [h["submitted_by"] for h in history] == [
        "op-a", "op-b", "op-a",
    ]

    text, cur_rev = _current_context(conn, "run-1", "devel/foo")
    assert text == "round three"
    assert cur_rev == 3


def test_submitted_by_is_optional_and_persists_as_null(conn):
    upsert_user_context_text(
        conn, "run-1", "devel/foo", "no operator named",
    )
    history = list_user_context_history(conn, "run-1", "devel/foo")
    assert len(history) == 1
    assert history[0]["submitted_by"] is None


def test_history_scoped_by_run_and_origin(conn):
    upsert_user_context_text(
        conn, "run-1", "devel/foo", "foo r1", submitted_by="op-a",
    )
    upsert_user_context_text(
        conn, "run-1", "devel/bar", "bar r1", submitted_by="op-a",
    )
    upsert_user_context_text(
        conn, "run-2", "devel/foo", "foo r1 run2", submitted_by="op-c",
    )

    foo_run1 = list_user_context_history(conn, "run-1", "devel/foo")
    bar_run1 = list_user_context_history(conn, "run-1", "devel/bar")
    foo_run2 = list_user_context_history(conn, "run-2", "devel/foo")

    assert [h["text"] for h in foo_run1] == ["foo r1"]
    assert [h["text"] for h in bar_run1] == ["bar r1"]
    assert [h["text"] for h in foo_run2] == ["foo r1 run2"]


def test_list_returns_empty_when_no_history(conn):
    assert list_user_context_history(conn, "run-x", "devel/never") == []
