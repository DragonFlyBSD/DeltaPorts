"""Tests for build queue tracking: enqueue, mark building, active summary."""

from __future__ import annotations

from pathlib import Path

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from dportsv3.tracker.db import (
    create_build_run,
    enqueue_ports,
    get_active_builds_summary,
    get_build_results,
    get_build_run,
    init_db,
    record_results,
    update_port_status,
)
from dportsv3.tracker.server import create_app


@pytest.fixture
def conn(tmp_path: Path):
    return init_db(tmp_path / "test.db")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    app = create_app(tmp_path / "tracker.db")
    with TestClient(app) as tc:
        yield tc


def _create_run(conn, target="@main", build_type="test"):
    return create_build_run(conn, target, build_type, started_at="2026-03-18T00:00:00+00:00")


# --- DB-level tests ---


def test_enqueue_and_record_overwrites(conn) -> None:
    run_id = _create_run(conn)
    ports = [
        {"origin": "devel/foo", "version": "1.0"},
        {"origin": "lang/bar", "version": "2.0"},
    ]
    count = enqueue_ports(conn, run_id, ports)
    assert count == 2

    results = get_build_results(conn, run_id)
    assert len(results) == 2
    statuses = {r["origin"]: r["status"] for r in results}
    assert statuses["devel/foo"] == "queued"
    assert statuses["lang/bar"] == "queued"

    # Now record a result — should overwrite the queued row
    record_results(conn, run_id, "@main", [
        {"origin": "devel/foo", "version": "1.0", "result": "success"},
    ])
    results = get_build_results(conn, run_id)
    by_origin = {r["origin"]: r for r in results}
    assert by_origin["devel/foo"]["status"] == "success"
    assert by_origin["devel/foo"]["result"] == "success"
    assert by_origin["lang/bar"]["status"] == "queued"


def test_mark_building(conn) -> None:
    run_id = _create_run(conn)
    enqueue_ports(conn, run_id, [{"origin": "devel/foo", "version": "1.0"}])

    update_port_status(conn, run_id, "devel/foo", "building")
    results = get_build_results(conn, run_id)
    assert results[0]["status"] == "building"


def test_active_builds_summary(conn) -> None:
    run_id = _create_run(conn)
    enqueue_ports(conn, run_id, [
        {"origin": "devel/foo", "version": "1.0"},
        {"origin": "lang/bar", "version": "2.0"},
        {"origin": "net/baz", "version": "3.0"},
    ], total_expected=3)

    update_port_status(conn, run_id, "devel/foo", "building")
    record_results(conn, run_id, "@main", [
        {"origin": "lang/bar", "version": "2.0", "result": "success"},
    ])

    summaries = get_active_builds_summary(conn)
    assert len(summaries) == 1
    s = summaries[0]
    assert s["id"] == run_id
    assert s["queued_count"] == 1  # net/baz
    assert s["building_count"] == 1  # devel/foo
    assert s["done_count"] == 1  # lang/bar (success)
    assert s["success_count"] == 1
    assert s["total_expected"] == 3


def test_enqueue_idempotent(conn) -> None:
    run_id = _create_run(conn)
    ports = [{"origin": "devel/foo", "version": "1.0"}]
    count1 = enqueue_ports(conn, run_id, ports)
    count2 = enqueue_ports(conn, run_id, ports)
    assert count1 == 1
    assert count2 == 0  # INSERT OR IGNORE
    results = get_build_results(conn, run_id)
    assert len(results) == 1


def test_build_run_includes_queue_counts(conn) -> None:
    run_id = _create_run(conn)
    enqueue_ports(conn, run_id, [
        {"origin": "devel/foo", "version": "1.0"},
        {"origin": "lang/bar", "version": "2.0"},
    ])
    update_port_status(conn, run_id, "devel/foo", "building")

    build = get_build_run(conn, run_id)
    assert build["queued_count"] == 1
    assert build["building_count"] == 1


def test_mark_building_unknown_port(conn) -> None:
    run_id = _create_run(conn)
    with pytest.raises(ValueError, match="No result row"):
        update_port_status(conn, run_id, "no/such", "building")


# --- API-level tests ---


def test_api_enqueue_ports(client: TestClient) -> None:
    start = client.post("/api/builds", json={
        "target": "@main",
        "build_type": "test",
    })
    run_id = start.json()["id"]

    resp = client.post(f"/api/builds/{run_id}/queue", json={
        "ports": [
            {"origin": "devel/foo", "version": "1.0"},
            {"origin": "lang/bar", "version": "2.0"},
        ],
        "total_expected": 100,
    })
    assert resp.status_code == 200
    assert resp.json()["queued"] == 2

    detail = client.get(f"/api/builds/{run_id}")
    build = detail.json()["build_run"]
    assert build["total_expected"] == 100
    assert build["queued_count"] == 2


def test_api_mark_building(client: TestClient) -> None:
    start = client.post("/api/builds", json={
        "target": "@main",
        "build_type": "test",
    })
    run_id = start.json()["id"]

    client.post(f"/api/builds/{run_id}/queue", json={
        "ports": [{"origin": "devel/foo", "version": "1.0"}],
    })

    resp = client.patch(f"/api/builds/{run_id}/ports/devel/foo/status", json={
        "status": "building",
    })
    assert resp.status_code == 200

    detail = client.get(f"/api/builds/{run_id}")
    assert detail.json()["build_run"]["building_count"] == 1


def test_api_mark_building_unknown_port(client: TestClient) -> None:
    start = client.post("/api/builds", json={
        "target": "@main",
        "build_type": "test",
    })
    run_id = start.json()["id"]

    resp = client.patch(f"/api/builds/{run_id}/ports/no/such/status", json={
        "status": "building",
    })
    assert resp.status_code == 400


def test_api_enqueue_then_record_overwrites(client: TestClient) -> None:
    start = client.post("/api/builds", json={
        "target": "@main",
        "build_type": "test",
    })
    run_id = start.json()["id"]

    client.post(f"/api/builds/{run_id}/queue", json={
        "ports": [{"origin": "devel/foo", "version": "1.0"}],
    })

    client.post(f"/api/builds/{run_id}/results", json={
        "results": [{"origin": "devel/foo", "version": "1.0", "result": "success"}],
    })

    detail = client.get(f"/api/builds/{run_id}")
    build = detail.json()["build_run"]
    assert build["queued_count"] == 0
    assert build["success_count"] == 1
