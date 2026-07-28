"""WS6 — issue read queries (agentic_queries.issues).

Exercised against a real state.db seeded through the artifact-store writer
(WS3), so the reads are pinned to the shape the writer actually produces:

- `list_issues` returns persisted rollups, systemic-first, with optional
  target/origin/state filters;
- one origin failing two different ways yields two distinct issues;
- `get_issue` / `occurrences_for_issue` attach occurrences newest-first;
- `issues_with_occurrences` is the single-query feed that drives
  `issue_state.build_issue_worklist`.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from dportsv3.artifact_store import ArtifactStore
from dportsv3.db.schema import init_db
from dportsv3.tracker import issue_state
from dportsv3.tracker.agentic_queries import (
    get_issue,
    issues_with_occurrences,
    list_issues,
    occurrences_for_issue,
)


@pytest.fixture
def store():
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    s = ArtifactStore.__new__(ArtifactStore)
    s.conn = conn
    s._lock = threading.Lock()
    return s


def _up(store, bundle_id, origin, errors, ts, target="@2026Q3"):
    store.upsert_run_bundle({
        "run_id": "r1", "profile": "p", "ts_utc": ts, "bundle_id": bundle_id,
        "origin": origin, "flavor": "", "result": "failure",
        "target": target, "errors_text": errors,
    })


def _seed(store):
    # ftp/curl: two occurrences, same fingerprint → one issue (times_seen 2)
    _up(store, "c1", "ftp/curl", "cc: error: A\n", "2026-07-25T00:00:00Z")
    _up(store, "c2", "ftp/curl", "cc: error: A\n", "2026-07-25T05:00:00Z")
    # ftp/curl: a different fingerprint → a SECOND issue for the same origin
    _up(store, "c3", "ftp/curl", "ld: undefined symbol B\n", "2026-07-25T06:00:00Z")
    # lang/python: one issue seen 4× (systemic)
    for i, hh in enumerate(["01", "02", "03", "04"]):
        _up(store, f"p{i}", "lang/python", "cc: error: C\n",
            f"2026-07-25T{hh}:00:00Z")


def test_list_issues_systemic_first_with_rollups(store):
    _seed(store)
    rows = list_issues(store.conn)
    # python (4) before curl-A (2) before curl-B (1)
    assert [(r["origin"], r["times_seen"]) for r in rows] == [
        ("lang/python", 4), ("ftp/curl", 2), ("ftp/curl", 1),
    ]
    # rollups are the persisted columns, not derived
    py = rows[0]
    assert py["first_seen_at"] == "2026-07-25T01:00:00Z"
    assert py["last_seen_at"] == "2026-07-25T04:00:00Z"
    assert py["latest_bundle_id"] == "p3"


def test_one_origin_two_fingerprints_two_issues(store):
    _seed(store)
    curl = list_issues(store.conn, origin="ftp/curl")
    assert len(curl) == 2
    assert {i["times_seen"] for i in curl} == {2, 1}
    assert len({i["issue_key"] for i in curl}) == 2


def test_state_filter(store):
    _seed(store)
    assert len(list_issues(store.conn, states=["unresolved"])) == 3
    assert list_issues(store.conn, states=["resolved"]) == []
    # empty allow-list is treated as "no state filter", not "match none"
    assert len(list_issues(store.conn, states=[])) == 3


def test_target_filter(store):
    _seed(store)
    _up(store, "o1", "x/y", "cc: error: Z\n", "2026-07-25T00:00:00Z",
        target="@main")
    assert {i["origin"] for i in list_issues(store.conn, target="@main")} == {"x/y"}
    assert all(i["target"] == "@2026Q3"
               for i in list_issues(store.conn, target="@2026Q3"))


def test_get_issue_and_occurrences_newest_first(store):
    _seed(store)
    key = [i["issue_key"] for i in list_issues(store.conn, origin="ftp/curl")
           if i["times_seen"] == 2][0]
    issue = get_issue(store.conn, key)
    assert issue["issue_key"] == key
    assert [o["bundle_id"] for o in issue["occurrences"]] == ["c2", "c1"]
    # standalone occurrences_for_issue agrees
    assert [o["bundle_id"] for o in occurrences_for_issue(store.conn, key)] == \
        ["c2", "c1"]


def test_get_issue_missing_returns_none(store):
    assert get_issue(store.conn, "does-not-exist") is None


def test_issues_with_occurrences_feeds_worklist(store):
    _seed(store)
    # Give the python issue's latest occurrence a verified fix so it is
    # operator-actionable through the full stack.
    store.conn.execute(
        "UPDATE bundles SET resolution='agent_fixed', verification_status='verified' "
        "WHERE bundle_id='p3'"
    )
    store.conn.commit()

    feed = issues_with_occurrences(store.conn)
    assert all("occurrences" in i for i in feed)
    # the join attaches the right occurrences to the right issue
    py = next(i for i in feed if i["origin"] == "lang/python")
    assert {o["bundle_id"] for o in py["occurrences"]} == {"p0", "p1", "p2", "p3"}

    wl = issue_state.build_issue_worklist(feed)
    assert [(g["origin"], g["times_seen"]) for g in wl["ready"]] == \
        [("lang/python", 4)]


def test_issues_with_occurrences_empty_db(store):
    assert issues_with_occurrences(store.conn) == []
