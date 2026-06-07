"""HTTP client helpers for the build tracker API."""

from __future__ import annotations

import json
from pathlib import Path
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


# --------------------------------------------------------------------
# Agentic reads — bundle / job / activity / artifact
#
# Used by the `dportsv3 tracker get-*` CLI subcommands so operators
# (and the dportsv3-agentic-analyzer subagent) can read tracker state
# without curl-grep-jq pipelines. All HTTP, all GET, no DB cross-talk.
# --------------------------------------------------------------------


def get_bundle(
    server_url: str, bundle_id: str, *, include_jobs: bool = False,
) -> dict[str, Any]:
    """Fetch one bundle's full detail (includes ``artifacts`` list).

    With ``include_jobs=True`` the response also carries a ``jobs``
    list of every job whose bundle_dir references this bundle —
    saves the analyzer from a separate list-jobs join.
    """
    query = _compact_query({"include": "jobs" if include_jobs else None})
    return _request_json(
        server_url, f"/api/bundles/{bundle_id}", query=query,
    )


def list_bundles(
    server_url: str,
    *,
    target: str | None = None,
    origin: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List bundles, newest first. Filter by target / origin."""
    return _request_json(
        server_url, "/api/bundles",
        query=_compact_query({"target": target, "origin": origin, "limit": limit}),
    )


def list_port_bundles(
    server_url: str,
    origin: str,
    *,
    target: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """All bundles for an origin (path-encoded). Newest first."""
    # Path-encode the origin so category/portname survives the URL parse.
    encoded = parse.quote(origin, safe="")
    return _request_json(
        server_url, f"/api/ports/{encoded}",
        query=_compact_query({"target": target, "limit": limit}),
    )


def get_job(server_url: str, job_id: str) -> dict[str, Any]:
    """Fetch one job by ID."""
    return _request_json(server_url, f"/api/jobs/{job_id}")


def list_jobs(
    server_url: str,
    *,
    state: str | None = None,
    target: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List jobs, optionally filtered by state and/or target."""
    return _request_json(
        server_url, "/api/jobs",
        query=_compact_query({"state": state, "target": target, "limit": limit}),
    )


def get_activity(
    server_url: str,
    *,
    job_id: str | None = None,
    target: str | None = None,
    stage_filter: str | None = None,
    since_id: int = 0,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Activity log rows. With ``job_id`` returns per-job entries
    (oldest-first when paged via ``since_id``); otherwise returns
    the most recent entries newest-first.
    """
    return _request_json(
        server_url, "/api/activity",
        query=_compact_query({
            "job_id": job_id, "target": target,
            "stage_filter": stage_filter,
            "since_id": since_id, "limit": limit,
        }),
    )


def fetch_artifact(
    server_url: str, bundle_id: str, relpath: str,
) -> bytes:
    """Fetch raw artifact bytes (logs, JSON, diffs — whatever).

    Returns the bytes verbatim — caller decides whether to decode
    text or stream binary. The HTTP layer is bytes-aware so this
    can handle multi-MB logs without re-encoding."""
    base = server_url.rstrip("/")
    encoded = "/".join(parse.quote(p, safe="") for p in relpath.split("/"))
    url = f"{base}/api/bundles/{bundle_id}/artifacts/{encoded}"
    try:
        with request.urlopen(url) as response:
            return response.read()
    except error.HTTPError as exc:
        raise RuntimeError(
            f"Tracker API error ({exc.code}) fetching artifact "
            f"{bundle_id}/{relpath}: "
            f"{exc.read().decode('utf-8', errors='replace')}"
        ) from exc
    except error.URLError as exc:
        raise RuntimeError(
            f"Tracker connection failed for artifact "
            f"{bundle_id}/{relpath}: {exc.reason}"
        ) from exc


def download_bundle(
    server_url: str, bundle_id: str, out_dir: Path,
) -> dict[str, Any]:
    """Materialize one bundle's full contents to ``out_dir``.

    Writes ``meta.json`` (bundle row + jobs as returned by
    ``GET /api/bundles/{id}?include=jobs``) and then every artifact
    listed under ``artifacts`` into its ``relpath`` under ``out_dir``.

    The result is a self-contained directory an analyzer agent can
    point at without further tracker queries.

    Returns ``{"bundle_id", "out_dir", "artifact_count", "bytes"}``.
    """
    import json as _json

    out_dir.mkdir(parents=True, exist_ok=True)
    meta = get_bundle(server_url, bundle_id, include_jobs=True)
    (out_dir / "meta.json").write_text(
        _json.dumps(meta, indent=2, default=str),
        encoding="utf-8",
    )

    artifacts = meta.get("artifacts") or []
    total_bytes = 0
    for art in artifacts:
        relpath = art.get("relpath")
        if not relpath:
            continue
        data = fetch_artifact(server_url, bundle_id, relpath)
        dest = out_dir / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        total_bytes += len(data)
    return {
        "bundle_id": bundle_id,
        "out_dir": str(out_dir),
        "artifact_count": len(artifacts),
        "bytes": total_bytes,
    }


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
