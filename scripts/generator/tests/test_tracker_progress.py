"""Tests for the Phase 5 step 1 dsynth-progress adapter.

Seeds a build_run + build_results, hits the summary.json /
NN_history.json endpoints, and checks shape parity with the
dsynth-progress UI's expectations.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dportsv3.tracker.db import init_db, open_db
from dportsv3.tracker.server import create_app


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def seeded_state_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "state.db"
    conn = init_db(db_path)
    now = _now()
    cur = conn.execute(
        "INSERT INTO build_runs(target, build_type, started_at, total_expected) VALUES (?, ?, ?, ?)",
        ("@2026Q2", "test", now, 5),
    )
    run_id = cur.lastrowid
    rows = [
        (run_id, "devel/foo", "1.0", "success", now, "recorded"),
        (run_id, "devel/bar", "1.0", "failure", now, "recorded"),
        (run_id, "devel/baz", "1.0", "skipped", now, "recorded"),
        (run_id, "devel/qux", "1.0", "ignored", now, "recorded"),
        (run_id, "devel/inprogress", "1.0", "", now, "building"),
    ]
    conn.executemany(
        """INSERT INTO build_results
           (build_run_id, origin, version, result, recorded_at, status)
           VALUES (?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def client(seeded_state_db: Path) -> TestClient:
    app = create_app(seeded_state_db)
    with TestClient(app) as test_client:
        yield test_client


def test_progress_html_serves(client: TestClient) -> None:
    resp = client.get("/target/@2026Q2")
    assert resp.status_code == 200
    body = resp.text
    # Pinned base + key dsynth-progress hooks
    assert '<base href="/api/progress/@2026Q2/">' in body
    assert "progress.css" in body
    assert "progress.js" in body
    assert 'id="stats_built"' in body


def test_progress_summary_shape(client: TestClient) -> None:
    body = client.get("/api/progress/@2026Q2/summary.json").json()
    assert body["profile"] == "@2026Q2"
    assert body["active"] == 1  # finished_at IS NULL
    stats = body["stats"]
    assert stats["built"] == 1
    assert stats["failed"] == 1
    assert stats["skipped"] == 1
    assert stats["ignored"] == 1
    assert stats["queued"] == 5  # total_expected
    assert stats["meta"] == 0
    # 5 results total, chunk size 1000 → kfiles >= 1
    assert body["kfiles"] >= 1
    # One building row → one virtual builder slot
    assert len(body["builders"]) == 1
    assert body["builders"][0]["origin"] == "devel/inprogress"


def test_progress_history_chunk_one(client: TestClient) -> None:
    body = client.get("/api/progress/@2026Q2/01_history.json").json()
    # Excludes the 'building' row (active builder, not history)
    assert isinstance(body, list)
    assert len(body) == 4
    assert "devel/inprogress" not in {row["origin"] for row in body}
    # dsynth vocabulary
    results = {row["origin"]: row["result"] for row in body}
    assert results["devel/foo"] == "built"
    assert results["devel/bar"] == "failed"
    assert results["devel/baz"] == "skipped"
    assert results["devel/qux"] == "ignored"


def test_progress_history_past_last_chunk(client: TestClient) -> None:
    body = client.get("/api/progress/@2026Q2/99_history.json").json()
    assert body == []


def test_progress_summary_unknown_target(client: TestClient) -> None:
    body = client.get("/api/progress/@nonexistent/summary.json").json()
    assert body["kfiles"] == 0
    assert body["stats"]["built"] == 0
    assert body["builders"] == []


def test_build_progress_html_serves(client: TestClient, seeded_state_db: Path) -> None:
    import sqlite3
    conn = sqlite3.connect(str(seeded_state_db))
    run_id = conn.execute("SELECT id FROM build_runs LIMIT 1").fetchone()[0]
    conn.close()

    resp = client.get(f"/builds/{run_id}")
    assert resp.status_code == 200
    body = resp.text
    assert f'<base href="/api/progress/build/{run_id}/">' in body
    assert "progress.css" in body


def test_build_progress_summary_and_history(
    client: TestClient, seeded_state_db: Path
) -> None:
    import sqlite3
    conn = sqlite3.connect(str(seeded_state_db))
    run_id = conn.execute("SELECT id FROM build_runs LIMIT 1").fetchone()[0]
    conn.close()

    summary = client.get(f"/api/progress/build/{run_id}/summary.json").json()
    assert summary["stats"]["built"] == 1
    assert summary["stats"]["failed"] == 1
    assert summary["stats"]["skipped"] == 1
    assert summary["stats"]["ignored"] == 1
    assert len(summary["builders"]) == 1

    history = client.get(f"/api/progress/build/{run_id}/01_history.json").json()
    assert isinstance(history, list)
    assert len(history) == 4  # excludes building row


def test_build_progress_unknown_run_404(client: TestClient) -> None:
    assert client.get("/builds/99999").status_code == 404
    assert client.get("/api/progress/build/99999/summary.json").status_code == 404
    # History endpoint silently returns [] for unknown run (no run guard).
    body = client.get("/api/progress/build/99999/01_history.json").json()
    assert body == []
