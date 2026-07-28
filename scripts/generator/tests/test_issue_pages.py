"""WS9b — the issues list + issue-detail HTML pages.

These are the primary navigable surface of the issue model: a filterable
issues index and a per-issue page with the occurrence timeline and the
mute/unmute/resolve/reopen controls (wired to the WS7 endpoints). Driven
through the real app against a writer-seeded DB.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest
from fastapi.testclient import TestClient

from dportsv3.artifact_store import ArtifactStore
from dportsv3.db.schema import init_db
from dportsv3.fingerprint import compute_fingerprint, issue_key
from dportsv3.tracker.server import create_app


@pytest.fixture
def app_db(tmp_path):
    db = str(tmp_path / "state.db")
    conn = sqlite3.connect(db, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    store = ArtifactStore.__new__(ArtifactStore)
    store.conn = conn
    store._lock = threading.Lock()

    def up(bundle_id, origin, errors, ts, target="@2026Q3"):
        store.upsert_run_bundle({
            "run_id": "r", "profile": "p", "ts_utc": ts, "bundle_id": bundle_id,
            "origin": origin, "flavor": "", "result": "failure",
            "target": target, "errors_text": errors,
        })

    # ftp/curl: one issue, two occurrences
    up("c1", "ftp/curl", "cc: error A\n", "2026-07-25T00:00:00Z")
    up("c2", "ftp/curl", "cc: error A\n", "2026-07-25T05:00:00Z")
    # lang/python: a separate issue
    up("p1", "lang/python", "cc: error C\n", "2026-07-25T01:00:00Z")
    conn.commit()
    conn.close()
    return db


def _curl_key():
    return issue_key("@2026Q3", "ftp/curl", compute_fingerprint("cc: error A\n"))


@pytest.fixture
def client(app_db):
    with TestClient(create_app(app_db)) as c:
        yield c, app_db


def test_issues_list_renders_rows_and_links(client):
    c, _ = client
    body = c.get("/agentic/issues").text
    assert "Issues" in body
    assert "ftp/curl" in body and "lang/python" in body
    assert "×2" in body                       # curl seen twice
    assert f"/agentic/issues/{_curl_key()}" in body   # links to detail


def test_issues_list_state_filter(client):
    c, db = client
    # unresolved shows both; resolved shows none
    assert "ftp/curl" in c.get("/agentic/issues?state=unresolved").text
    assert "No issues match" in c.get("/agentic/issues?state=resolved").text


def test_issue_detail_shows_occurrences_and_controls(client):
    c, _ = client
    body = c.get(f"/agentic/issues/{_curl_key()}").text
    assert "ftp/curl" in body
    assert "Occurrences · 2" in body
    assert "c1" in body and "c2" in body
    # an open issue can be muted or resolved (not unmuted/reopened)
    assert ">Mute<" in body and ">Resolve<" in body
    assert ">Unmute<" not in body and ">Reopen<" not in body


def test_issue_detail_unknown_404(client):
    c, _ = client
    assert c.get("/agentic/issues/does-not-exist").status_code == 404


def test_issue_detail_controls_reflect_state_after_action(client):
    c, _ = client
    key = _curl_key()
    # Mute via the endpoint the button calls, then the page flips controls.
    assert c.post(f"/api/issues/{key}/mute", json={}).status_code == 200
    body = c.get(f"/agentic/issues/{key}").text
    assert ">Unmute<" in body        # now unmuteable
    assert ">Mute<" not in body      # ...and no longer muteable
    # and the muted issue is reachable via the muted filter
    assert "ftp/curl" in c.get("/agentic/issues?state=muted").text


# --- WS9c: occurrence page framed under its issue --------------------------


def test_occurrence_page_frames_under_its_issue(client):
    c, _ = client
    key = _curl_key()
    body = c.get("/agentic/bundles/c1").text
    # The issue strip element links up to the issue detail...
    assert 'class="occ-issue-strip"' in body
    assert f"/agentic/issues/{key}" in body
    assert "seen ×2" in body                  # rollup from the issue row
    # ...the breadcrumb routes Agentic > Issues > origin > occurrence...
    assert ">Issues<" in body
    # ...and the raw bundle_id is demoted under an origin-led header.
    assert "occ-h1" in body and "ftp/curl" in body
    assert "occ-subid" in body and "c1" in body


def test_occurrence_page_without_issue_hides_strip(client, app_db):
    """A bundle with no issue_key (degenerate / pre-issue row) still
    renders — the issue strip is simply absent."""
    import sqlite3
    conn = sqlite3.connect(app_db)
    conn.execute(
        "INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc, result, "
        "target, last_seen_at) VALUES ('orphan','r','a/a','','2026-07-25T00:00:00Z',"
        "'failure','@2026Q3','2026-07-25T00:00:00Z')"
    )
    conn.commit()
    conn.close()
    body = c_get(client, "/agentic/bundles/orphan")
    assert 'class="occ-issue-strip"' not in body   # no strip element
    assert "a/a" in body   # still renders the occurrence


def c_get(client, path):
    c, _ = client
    r = c.get(path)
    assert r.status_code == 200
    return r.text
