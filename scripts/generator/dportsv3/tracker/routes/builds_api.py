"""Build-tracker API routes (/api/builds, status, failures, diff, health)."""

from __future__ import annotations

from dportsv3.tracker.routes._common import *  # noqa: F401,F403
from dportsv3.tracker.routes._common import _LOG  # noqa: F401


def register(app, ctx):
    _conn = ctx.conn
    _raise_http_error = ctx.raise_http_error
    templates = ctx.templates

    @app.post("/api/builds", response_model=StartBuildResponse)
    def start_build(payload: StartBuildRequest) -> dict[str, int]:
        run_id = 0
        try:
            with _conn() as conn:
                run_id = create_build_run(
                    conn,
                    target=payload.target,
                    build_type=payload.build_type,
                    started_at=payload.started_at,
                )
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")
        return {"id": run_id}

    @app.patch("/api/builds/{run_id}")
    def finish_build(run_id: int, payload: FinishBuildRequest) -> dict[str, bool]:
        try:
            with _conn() as conn:
                finish_build_run(
                    conn,
                    run_id=run_id,
                    finished_at=payload.finished_at,
                    commit_sha=payload.commit_sha,
                    commit_branch=payload.commit_branch,
                    commit_pushed_at=payload.commit_pushed_at,
                )
        except Exception as exc:
            _raise_http_error(exc)
        return {"ok": True}

    @app.post("/api/builds/{run_id}/results", response_model=RecordResultsResponse)
    def add_results(
        run_id: int,
        payload: RecordResultsRequest,
    ) -> dict[str, int]:
        recorded = 0
        try:
            with _conn() as conn:
                run = get_build_run(conn, run_id)
                recorded = record_results(
                    conn,
                    run_id=run_id,
                    target=str(run["target"]),
                    results=[item.model_dump() for item in payload.results],
                )
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")
        return {"recorded": recorded}

    @app.post("/api/builds/{run_id}/queue", response_model=EnqueueResponse)
    def enqueue(run_id: int, payload: EnqueueRequest) -> dict[str, int]:
        try:
            with _conn() as conn:
                count = enqueue_ports(
                    conn,
                    run_id,
                    [item.model_dump() for item in payload.ports],
                    total_expected=payload.total_expected,
                )
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")
        return {"queued": count}

    @app.patch("/api/builds/{run_id}/ports/{origin:path}/status")
    def patch_port_status(
        run_id: int,
        origin: str,
        payload: UpdatePortStatusRequest,
    ) -> dict[str, bool]:
        try:
            with _conn() as conn:
                update_port_status(conn, run_id, origin, payload.status)
        except Exception as exc:
            _raise_http_error(exc)
        return {"ok": True}

    @app.get("/api/builds", response_model=list[BuildRunOut])
    def api_list_builds(
        target: str | None = None,
        build_type: str | None = None,
        limit: int = Query(default=20, ge=1, le=1000),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_build_runs(
                conn, target=target, build_type=build_type, limit=limit
            )

    @app.get("/api/builds/compare", response_model=BuildCompareOut)
    def api_compare_builds(a: int, b: int) -> dict[str, Any]:
        try:
            with _conn() as conn:
                return compare_builds(conn, a, b)
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")

    @app.get("/api/builds/{run_id}")
    def api_get_build(run_id: int) -> dict[str, Any]:
        try:
            with _conn() as conn:
                return {
                    "build_run": get_build_run(conn, run_id),
                    "results": get_build_results(conn, run_id),
                }
        except Exception as exc:
            _raise_http_error(exc)
            raise AssertionError("unreachable")

    @app.get("/api/status", response_model=list[PortStatusOut])
    def api_status(
        target: str | None = None,
        origin: str | None = None,
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return get_port_status(conn, target=target, origin=origin)

    @app.get("/api/failures", response_model=list[PortStatusOut])
    def api_failures(target: str) -> list[dict[str, Any]]:
        with _conn() as conn:
            return get_failures(conn, target)

    @app.get("/api/diff", response_model=DiffOut)
    def api_diff(a: str, b: str) -> dict[str, Any]:
        with _conn() as conn:
            return get_diff(conn, a, b)

    # ------------------------------------------------------------------
    # Agentic-read endpoints (absorbed from the retired state-server).
    # ------------------------------------------------------------------

    @app.get("/api/health")
    def api_health() -> dict[str, str]:
        return {"status": "ok"}

