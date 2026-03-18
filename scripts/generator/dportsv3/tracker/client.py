"""HTTP client helpers for the build tracker API."""

from __future__ import annotations

import json
from typing import Any
from urllib import error, parse, request


def start_build(server_url: str, target: str, build_type: str) -> int:
    """Create one build run and return its ID."""
    payload = _request_json(
        server_url,
        "/api/builds",
        method="POST",
        payload={"target": target, "build_type": build_type},
    )
    return int(payload["id"])


def finish_build(
    server_url: str,
    run_id: int,
    *,
    finished_at: str | None = None,
    commit_sha: str | None = None,
    commit_branch: str | None = None,
    commit_pushed_at: str | None = None,
) -> None:
    """Mark one build run finished."""
    payload: dict[str, Any] = {}
    if finished_at is not None:
        payload["finished_at"] = finished_at
    if commit_sha is not None:
        payload["commit_sha"] = commit_sha
    if commit_branch is not None:
        payload["commit_branch"] = commit_branch
    if commit_pushed_at is not None:
        payload["commit_pushed_at"] = commit_pushed_at
    _request_json(
        server_url,
        f"/api/builds/{run_id}",
        method="PATCH",
        payload=payload,
    )


def record_result(
    server_url: str,
    run_id: int,
    origin: str,
    version: str,
    result: str,
    *,
    log_url: str | None = None,
) -> int:
    """Record one build result and return the recorded count."""
    item: dict[str, Any] = {
        "origin": origin,
        "version": version,
        "result": result,
    }
    if log_url is not None:
        item["log_url"] = log_url
    payload = record_results_batch(server_url, run_id, [item])
    return int(payload["recorded"])


def record_results_batch(
    server_url: str,
    run_id: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Record a batch of build results."""
    return _request_json(
        server_url,
        f"/api/builds/{run_id}/results",
        method="POST",
        payload={"results": results},
    )


def enqueue_ports(
    server_url: str,
    run_id: int,
    ports: list[dict[str, str]],
    total_expected: int | None = None,
) -> int:
    """Enqueue ports for a build run. Returns count queued."""
    payload: dict[str, Any] = {"ports": ports}
    if total_expected is not None:
        payload["total_expected"] = total_expected
    result = _request_json(
        server_url,
        f"/api/builds/{run_id}/queue",
        method="POST",
        payload=payload,
    )
    return int(result["queued"])


def mark_port_building(server_url: str, run_id: int, origin: str) -> None:
    """Mark one port as building."""
    _request_json(
        server_url,
        f"/api/builds/{run_id}/ports/{origin}/status",
        method="PATCH",
        payload={"status": "building"},
    )


def get_status(
    server_url: str,
    *,
    target: str | None = None,
    origin: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch current status rows."""
    query = _compact_query({"target": target, "origin": origin})
    payload = _request_json(server_url, "/api/status", query=query)
    return list(payload)


def get_failures(server_url: str, target: str) -> list[dict[str, Any]]:
    """Fetch failure rows for one target."""
    payload = _request_json(server_url, "/api/failures", query={"target": target})
    return list(payload)


def get_diff(server_url: str, target_a: str, target_b: str) -> dict[str, Any]:
    """Fetch target-to-target diff payload."""
    return _request_json(server_url, "/api/diff", query={"a": target_a, "b": target_b})


def get_build(server_url: str, run_id: int) -> dict[str, Any]:
    """Fetch one build run with results."""
    return _request_json(server_url, f"/api/builds/{run_id}")


def list_builds(
    server_url: str,
    *,
    target: str | None = None,
    build_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Fetch recent build runs."""
    payload = _request_json(
        server_url,
        "/api/builds",
        query=_compact_query(
            {
                "target": target,
                "build_type": build_type,
                "limit": str(limit),
            }
        ),
    )
    return list(payload)


def compare_builds(server_url: str, run_id_a: int, run_id_b: int) -> dict[str, Any]:
    """Fetch one build comparison payload."""
    return _request_json(
        server_url, "/api/builds/compare", query={"a": run_id_a, "b": run_id_b}
    )


def _request_json(
    server_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
) -> Any:
    url = _build_url(server_url, path, query)
    headers = {"Accept": "application/json"}
    body: bytes | None = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req) as response:
            raw = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(detail)
            detail = payload.get("detail", payload)
        except json.JSONDecodeError:
            pass
        raise RuntimeError(
            f"Tracker API error ({exc.code}) for {method} {path}: {detail}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(
            f"Tracker connection failed for {method} {path}: {exc.reason}"
        ) from exc

    if not raw:
        return None
    return json.loads(raw)


def _build_url(server_url: str, path: str, query: dict[str, Any] | None) -> str:
    base = server_url.rstrip("/")
    full = f"{base}{path}"
    if not query:
        return full
    query_text = parse.urlencode(query)
    return f"{full}?{query_text}"


def _compact_query(values: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}
