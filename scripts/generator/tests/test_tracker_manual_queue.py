"""Tests for the manual escalation queue (Step 4, post-impl plan).

Covers:
- ``GET /agentic/manual`` list page renders the row and a link to detail.
- ``GET /agentic/manual/{run_id}/{origin}`` renders the handoff
  markdown inline.
- ``GET /api/manual-requests`` returns the same rows the list page sees.
- ``POST /api/manual-requests/{run_id}/{origin}/context`` happy path
  inserts a ``user_context`` row, bumps ``context_rev``, and emits a
  ``user_context_updated`` event.
- POST with empty / overlong context → 400.
- POST for a nonexistent request → 404.
- ``open_only`` filter excludes requests whose context has already
  been picked up by the runner (``context_rev <= last_context_rev_handled``).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dportsv3.db.schema import init_db as init_state_db
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def seeded_state_db(tmp_path: Path) -> Path:
    """State DB with two open manual requests + one resolved + a
    handoff artifact on the open one's bundle."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    init_state_db(conn)

    now = _now()
    # Handoff artifact file (filesystem-backed).
    handoff_path = tmp_path / "manual_handoff.md"
    handoff_path.write_text(
        "# Manual Handoff\n\n"
        "- **Origin:** `devel/foo`\n"
        "- **Reason:** triage classified as MANUAL\n\n"
        "## Operator Question\n\nWhat approach should the agent take?\n",
        encoding="utf-8",
    )

    conn.executemany(
        """INSERT INTO runs (run_id, profile, target, ts_start, last_seen_at)
           VALUES (?, ?, ?, ?, ?)""",
        [
            ("run-q2-001", "2026Q2", "@2026Q2", now, now),
            ("run-main-002", "main", "@main", now, now),
        ],
    )
    conn.executemany(
        """INSERT INTO bundles
           (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("b-foo", "run-q2-001", "devel/foo", "", now, "fail", "@2026Q2", now),
            ("b-bar", "run-q2-001", "devel/bar", "", now, "fail", "@2026Q2", now),
            ("b-resolved", "run-main-002", "devel/baz", "", now, "fail", "@main", now),
        ],
    )
    conn.execute(
        """INSERT INTO artifact_refs
           (bundle_id, relpath, backend, fs_path, kind, size, created_at)
           VALUES (?, ?, 'fs', ?, 'text', ?, ?)""",
        (
            "b-foo", "analysis/manual_handoff.md",
            str(handoff_path), handoff_path.stat().st_size, now,
        ),
    )
    # Two open requests: one with no operator context yet, one with
    # Two ``pending`` rows (visible in the open queue) and one
    # ``retriage_enqueued`` row (mid-flight; hidden from the open
    # queue, shown when ``open_only=False``).
    conn.executemany(
        """INSERT INTO user_context_requests
           (run_id, origin, bundle_id, confidence, classification,
            iteration, max_iterations, requested_at, status,
            last_context_rev_handled)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            ("run-q2-001", "devel/foo", "b-foo", "low", "missing-dep",
             1, 3, now, "pending", 0),
            ("run-q2-001", "devel/bar", "b-bar", "medium", "compile-error",
             2, 3, now, "pending", 0),
            # Mid-flight: runner picked up rev 1 and is processing.
            ("run-main-002", "devel/baz", "b-resolved", "high", "plist-error",
             1, 3, now, "retriage_enqueued", 1),
        ],
    )
    # The resolved request has its context_rev consumed.
    conn.execute(
        """INSERT INTO user_context (run_id, origin, context_text,
           updated_at, context_rev)
           VALUES (?, ?, ?, ?, ?)""",
        ("run-main-002", "devel/baz", "use freebsd-side patch", now, 1),
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client(seeded_state_db: Path, tmp_path: Path, monkeypatch) -> TestClient:
    # artifact_root only matters for blob-backend artifacts; the
    # handoff seeded above uses fs backend with an absolute fs_path
    # so artifact_root never gets consulted.
    monkeypatch.setenv("DPORTSV3_ARTIFACT_ROOT", str(tmp_path))
    app = create_app(seeded_state_db)
    with TestClient(app) as c:
        yield c


# --- list page / API --------------------------------------------------------


def test_manual_list_page_renders_open_rows(client: TestClient) -> None:
    resp = client.get("/agentic/manual")
    assert resp.status_code == 200
    body = resp.text
    assert "Manual Queue" in body
    assert "devel/foo" in body
    assert "devel/bar" in body
    # Resolved row should not appear under open_only=True.
    assert "devel/baz" not in body


def test_manual_list_page_open_only_false_shows_resolved(client: TestClient) -> None:
    resp = client.get("/agentic/manual", params={"open_only": False})
    assert resp.status_code == 200
    assert "devel/baz" in resp.text


def test_api_manual_requests_shape(client: TestClient) -> None:
    body = client.get("/api/manual-requests").json()
    rows = body["requests"]
    origins = {r["origin"] for r in rows}
    assert origins == {"devel/foo", "devel/bar"}
    foo = next(r for r in rows if r["origin"] == "devel/foo")
    assert foo["bundle_id"] == "b-foo"
    assert foo["classification"] == "missing-dep"
    assert foo["context_rev"] == 0


def test_api_manual_requests_include_resolved_when_open_only_false(
    client: TestClient,
) -> None:
    rows = client.get(
        "/api/manual-requests", params={"open_only": False}
    ).json()["requests"]
    assert {r["origin"] for r in rows} == {
        "devel/foo", "devel/bar", "devel/baz",
    }


# --- detail page ------------------------------------------------------------


def test_manual_detail_renders_handoff_markdown(client: TestClient) -> None:
    resp = client.get("/agentic/manual/run-q2-001/devel/foo")
    assert resp.status_code == 200
    body = resp.text
    # Handoff content rendered as markdown (header tag).
    assert "Manual Handoff" in body
    assert "What approach should the agent take" in body
    # Form is present.
    assert "Try again with this context" in body
    assert 'name="context_text"' in body


def test_manual_detail_404_for_unknown_request(client: TestClient) -> None:
    resp = client.get("/agentic/manual/run-q2-001/devel/nonexistent")
    assert resp.status_code == 404


def test_manual_detail_origin_with_slash_resolves(client: TestClient) -> None:
    """The origin path param uses ``{origin:path}`` so origin slashes
    (e.g. ``devel/foo``) survive routing without further encoding."""
    resp = client.get("/agentic/manual/run-q2-001/devel/foo")
    assert resp.status_code == 200


def test_manual_detail_renders_without_handoff_artifact(
    client: TestClient,
) -> None:
    """Older bundles predating Step 3 have no handoff; page still
    renders with an explanatory empty-state."""
    resp = client.get("/agentic/manual/run-q2-001/devel/bar")
    assert resp.status_code == 200
    assert "No <code>analysis/manual_handoff.md</code>" in resp.text


# --- POST happy path + validation -------------------------------------------


def test_post_context_inserts_row_and_bumps_rev(
    client: TestClient, seeded_state_db: Path,
) -> None:
    resp = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/context",
        json={"context_text": "Try the freebsd-side patch from FORTS r12345."},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["context_rev"] == 1

    # Row landed in user_context.
    conn = sqlite3.connect(str(seeded_state_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT context_text, context_rev FROM user_context "
        "WHERE run_id = ? AND origin = ?",
        ("run-q2-001", "devel/foo"),
    ).fetchone()
    assert row is not None
    assert "freebsd-side patch" in row["context_text"]
    assert row["context_rev"] == 1
    # Event emitted for activity log.
    ev = conn.execute(
        "SELECT data_json FROM events WHERE type = 'user_context_updated'"
    ).fetchone()
    assert ev is not None
    payload = json.loads(ev["data_json"])
    assert payload["run_id"] == "run-q2-001"
    assert payload["origin"] == "devel/foo"
    assert payload["context_rev"] == 1
    conn.close()


def test_post_context_second_submission_bumps_rev(client: TestClient) -> None:
    first = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/context",
        json={"context_text": "First attempt."},
    )
    assert first.json()["context_rev"] == 1
    second = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/context",
        json={"context_text": "Revised — try option B instead."},
    )
    assert second.json()["context_rev"] == 2


def test_post_context_empty_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/context",
        json={"context_text": "   "},
    )
    assert resp.status_code == 400
    assert "empty" in resp.json()["detail"]


def test_post_context_too_long_returns_400(client: TestClient) -> None:
    resp = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/context",
        json={"context_text": "x" * 8001},
    )
    assert resp.status_code == 400
    assert "8000" in resp.json()["detail"]


def test_post_context_unknown_request_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/manual-requests/run-q2-001/devel/nope/context",
        json={"context_text": "any"},
    )
    assert resp.status_code == 404


def test_post_context_origin_with_slash(client: TestClient) -> None:
    """Path param uses ``{origin:path}`` so ``devel/foo`` survives."""
    resp = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/context",
        json={"context_text": "ok"},
    )
    assert resp.status_code == 200


# --- dashboard link ----------------------------------------------------------


def test_agentic_index_links_to_manual_queue(client: TestClient) -> None:
    body = client.get("/agentic").text
    # Phase 6: the landing links to the manual queue via the agentic sub-nav.
    assert ">Manual</a>" in body
    assert "/agentic/manual" in body


# --- discard ---------------------------------------------------------------


def test_discard_marks_request_and_hides_from_open_list(
    client: TestClient, seeded_state_db: Path,
) -> None:
    resp = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/discard",
        json={"reason": "resolved out-of-band"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # No longer in open list.
    open_rows = client.get("/api/manual-requests").json()["requests"]
    assert all(r["origin"] != "devel/foo" for r in open_rows)

    # Still visible with open_only=false.
    all_rows = client.get(
        "/api/manual-requests", params={"open_only": False}
    ).json()["requests"]
    foo = next(r for r in all_rows if r["origin"] == "devel/foo")
    assert foo["status"] == "discarded"

    # Event emitted.
    conn = sqlite3.connect(str(seeded_state_db))
    conn.row_factory = sqlite3.Row
    ev = conn.execute(
        "SELECT data_json FROM events WHERE type = 'manual_request_discarded'"
    ).fetchone()
    assert ev is not None
    payload = json.loads(ev["data_json"])
    assert payload["origin"] == "devel/foo"
    assert payload["reason"] == "resolved out-of-band"
    conn.close()


def test_discard_unknown_returns_404(client: TestClient) -> None:
    resp = client.post(
        "/api/manual-requests/run-q2-001/devel/nope/discard",
        json={},
    )
    assert resp.status_code == 404


def test_discard_twice_is_idempotent(client: TestClient) -> None:
    first = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/discard", json={},
    )
    assert first.status_code == 200
    assert first.json()["discarded"] is True

    second = client.post(
        "/api/manual-requests/run-q2-001/devel/foo/discard", json={},
    )
    assert second.status_code == 200
    # Second call updates zero rows since status is already discarded.
    assert second.json()["discarded"] is False


def test_fresh_context_un_discards_request(
    client: TestClient, seeded_state_db: Path,
) -> None:
    """Operator changed their mind: submitting fresh context after
    discarding flips the request back to 'pending' so the runner
    sweep picks it up."""
    client.post(
        "/api/manual-requests/run-q2-001/devel/foo/discard", json={},
    )
    client.post(
        "/api/manual-requests/run-q2-001/devel/foo/context",
        json={"context_text": "actually — try this approach instead"},
    )

    conn = sqlite3.connect(str(seeded_state_db))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT status FROM user_context_requests "
        "WHERE run_id = ? AND origin = ?",
        ("run-q2-001", "devel/foo"),
    ).fetchone()
    assert row["status"] == "pending"
    conn.close()


def test_discard_button_in_list_page(client: TestClient) -> None:
    body = client.get("/agentic/manual").text
    assert "Discard" in body
    assert "data-run-id" in body


# --- "what the agent did" surfacing ----------------------------------------


def test_list_shows_retire_reason_and_patch_attempts(
    client: TestClient, seeded_state_db: Path,
) -> None:
    # Plant a retired patch job for devel/foo on @2026Q2.
    conn = sqlite3.connect(str(seeded_state_db))
    now = _now()
    conn.executemany(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target, retire_reason)
           VALUES (?, ?, ?, ?, '', '', ?, '', ?, ?, ?)""",
        [
            ("p1", "dead", "patch", "devel/foo", now, now, "@2026Q2",
             "patch_gave_up"),
            ("p2", "dead", "patch", "devel/foo", now, now, "@2026Q2",
             "patch_budget_exhausted"),
        ],
    )
    conn.commit()
    conn.close()

    rows = client.get("/api/manual-requests").json()["requests"]
    foo = next(r for r in rows if r["origin"] == "devel/foo")
    # Latest retire_reason wins; we inserted budget_exhausted second.
    assert foo["latest_retire_reason"] in (
        "patch_budget_exhausted", "patch_gave_up",
    )
    assert foo["patch_attempts"] == 2

    # Same data on list page.
    body = client.get("/agentic/manual").text
    assert "patch_" in body  # one of the retire reasons rendered as <code>
    assert "What the agent did" in body


def test_list_shows_placeholder_when_no_patch_attempt(
    client: TestClient,
) -> None:
    body = client.get("/agentic/manual").text
    # devel/bar in the fixture has no patch jobs.
    assert "no patch attempt yet" in body


def test_detail_shows_what_agent_did(
    client: TestClient, seeded_state_db: Path,
) -> None:
    conn = sqlite3.connect(str(seeded_state_db))
    now = _now()
    conn.execute(
        """INSERT INTO jobs
           (job_id, state, type, origin, flavor, bundle_dir,
            created_ts_utc, path, last_seen_at, target, retire_reason)
           VALUES ('p1', 'dead', 'patch', 'devel/foo', '', '',
                   ?, '', ?, '@2026Q2', 'patch_gave_up')""",
        (now, now),
    )
    conn.commit()
    conn.close()

    body = client.get("/agentic/manual/run-q2-001/devel/foo").text
    assert "What the agent did" in body
    assert "patch_gave_up" in body
    assert "patch attempt" in body
