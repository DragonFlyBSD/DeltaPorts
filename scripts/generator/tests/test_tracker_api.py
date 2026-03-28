from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dportsv3.tracker.server import create_app
import dportsv3.tracker.server as tracker_server


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "tracker.db")
    with TestClient(app) as test_client:
        yield test_client


def test_api_build_lifecycle_round_trip(client: TestClient) -> None:
    start = client.post(
        "/api/builds",
        json={
            "target": "@main",
            "build_type": "release",
            "started_at": "2026-03-17T08:00:00+00:00",
        },
    )
    assert start.status_code == 200
    run_id = start.json()["id"]

    record = client.post(
        f"/api/builds/{run_id}/results",
        json={
            "results": [
                {
                    "origin": "devel/foo",
                    "version": "1.0",
                    "result": "success",
                    "log_url": "https://logs.example/devel/foo.log.gz",
                },
                {
                    "origin": "lang/bar",
                    "version": "2.0",
                    "result": "failure",
                },
            ]
        },
    )
    assert record.status_code == 200
    assert record.json() == {"recorded": 2}

    finish = client.patch(
        f"/api/builds/{run_id}",
        json={
            "finished_at": "2026-03-17T18:00:00+00:00",
            "commit_sha": "abc123",
            "commit_branch": "main",
            "commit_pushed_at": "2026-03-17T18:10:00+00:00",
        },
    )
    assert finish.status_code == 200
    assert finish.json() == {"ok": True}

    build = client.get(f"/api/builds/{run_id}")
    assert build.status_code == 200
    payload = build.json()
    assert payload["build_run"]["commit_sha"] == "abc123"
    assert payload["build_run"]["success_count"] == 1
    assert payload["build_run"]["failure_count"] == 1
    assert payload["results"][0]["origin"] == "devel/foo"
    assert payload["results"][0]["log_url"] == "https://logs.example/devel/foo.log.gz"


def test_api_rejects_duplicate_active_build_for_same_target_and_type(
    client: TestClient,
) -> None:
    first = client.post(
        "/api/builds",
        json={"target": "@2026Q1", "build_type": "test"},
    )
    assert first.status_code == 200

    second = client.post(
        "/api/builds",
        json={"target": "@2026Q1", "build_type": "test"},
    )

    assert second.status_code == 409
    detail = second.json()["detail"]
    assert detail["active_run"]["id"] == first.json()["id"]


def test_api_list_builds_filters_by_build_type(client: TestClient) -> None:
    release = client.post(
        "/api/builds",
        json={
            "target": "@main",
            "build_type": "release",
            "started_at": "2026-03-17T08:00:00+00:00",
        },
    )
    assert release.status_code == 200
    test = client.post(
        "/api/builds",
        json={
            "target": "@main",
            "build_type": "test",
            "started_at": "2026-03-17T09:00:00+00:00",
        },
    )
    assert test.status_code == 200

    release_runs = client.get(
        "/api/builds", params={"target": "@main", "build_type": "release"}
    )

    assert release_runs.status_code == 200
    payload = release_runs.json()
    assert len(payload) == 1
    assert payload[0]["id"] == release.json()["id"]
    assert payload[0]["build_type"] == "release"


def test_api_compare_builds_returns_bucket_summary(client: TestClient) -> None:
    run_a = client.post(
        "/api/builds",
        json={
            "target": "@main",
            "build_type": "release",
            "started_at": "2026-03-10T08:00:00+00:00",
        },
    ).json()["id"]
    run_b = client.post(
        "/api/builds",
        json={
            "target": "@2026Q1",
            "build_type": "release",
            "started_at": "2026-03-14T08:00:00+00:00",
        },
    ).json()["id"]

    client.post(
        f"/api/builds/{run_a}/results",
        json={
            "results": [
                {"origin": "devel/fixme", "version": "1.0", "result": "failure"},
                {"origin": "www/regress", "version": "2.0", "result": "success"},
            ]
        },
    )
    client.post(
        f"/api/builds/{run_b}/results",
        json={
            "results": [
                {"origin": "devel/fixme", "version": "1.1", "result": "success"},
                {"origin": "www/regress", "version": "2.1", "result": "failure"},
            ]
        },
    )

    response = client.get("/api/builds/compare", params={"a": run_a, "b": run_b})

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"] == {
        "new_successes": 1,
        "new_failures": 1,
        "still_failing": 0,
        "still_succeeding": 0,
        "added": 0,
        "removed": 0,
        "version_changes": 2,
    }


def test_api_status_failures_and_diff_endpoints(client: TestClient) -> None:
    run_main = client.post(
        "/api/builds",
        json={"target": "@main", "build_type": "release"},
    ).json()["id"]
    run_q = client.post(
        "/api/builds",
        json={"target": "@2026Q1", "build_type": "release"},
    ).json()["id"]

    client.post(
        f"/api/builds/{run_main}/results",
        json={
            "results": [
                {"origin": "devel/foo", "version": "1.0", "result": "failure"},
                {"origin": "editors/bar", "version": "2.0", "result": "success"},
            ]
        },
    )
    client.post(
        f"/api/builds/{run_q}/results",
        json={
            "results": [
                {"origin": "devel/foo", "version": "1.0", "result": "success"},
                {"origin": "lang/baz", "version": "3.0", "result": "failure"},
            ]
        },
    )

    status = client.get(
        "/api/status", params={"target": "@main", "origin": "devel/foo"}
    )
    failures = client.get("/api/failures", params={"target": "@main"})
    diff = client.get("/api/diff", params={"a": "@main", "b": "@2026Q1"})

    assert status.status_code == 200
    assert status.json()[0]["last_attempt_result"] == "failure"
    assert failures.status_code == 200
    assert [row["origin"] for row in failures.json()] == ["devel/foo"]
    assert diff.status_code == 200
    assert [row["origin"] for row in diff.json()["differ"]] == ["devel/foo"]


def test_api_uses_fresh_db_connection_per_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    open_count = 0
    real_open_db = tracker_server.open_db

    def _counting_open_db(db_path: str | Path):
        nonlocal open_count
        open_count += 1
        return real_open_db(db_path)

    monkeypatch.setattr(tracker_server, "open_db", _counting_open_db)

    app = create_app(tmp_path / "tracker.db")
    with TestClient(app) as test_client:
        start = test_client.post(
            "/api/builds",
            json={"target": "@main", "build_type": "test"},
        )
        assert start.status_code == 200
        run_id = start.json()["id"]

        enqueue = test_client.post(
            f"/api/builds/{run_id}/queue",
            json={"ports": [{"origin": "devel/foo", "version": "1.0"}]},
        )
        assert enqueue.status_code == 200

        detail = test_client.get(f"/api/builds/{run_id}")
        assert detail.status_code == 200

    assert open_count >= 3
