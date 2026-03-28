"""FastAPI application factory for the build tracker."""

from __future__ import annotations

import importlib
from contextlib import contextmanager
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, cast

from dportsv3.tracker.db import (
    ActiveBuildError,
    compare_builds,
    create_build_run,
    enqueue_ports,
    finish_build_run,
    get_active_builds_summary,
    get_build_results,
    get_build_run,
    get_diff,
    get_failures,
    get_port_history,
    get_port_status,
    get_target_summary,
    init_db,
    list_build_runs,
    open_db,
    record_results,
    update_port_status,
)
from dportsv3.tracker.models import (
    BuildCompareOut,
    BuildRunOut,
    DiffOut,
    EnqueueRequest,
    EnqueueResponse,
    FinishBuildRequest,
    PortStatusOut,
    RecordResultsRequest,
    RecordResultsResponse,
    StartBuildRequest,
    StartBuildResponse,
    UpdatePortStatusRequest,
)

_fastapi = (
    importlib.import_module("fastapi") if importlib_util.find_spec("fastapi") else None
)
_responses = (
    importlib.import_module("fastapi.responses") if _fastapi is not None else None
)
_staticfiles = (
    importlib.import_module("fastapi.staticfiles") if _fastapi is not None else None
)
_templating = (
    importlib.import_module("fastapi.templating") if _fastapi is not None else None
)

if (
    _fastapi is not None
    and _responses is not None
    and _staticfiles is not None
    and _templating is not None
):
    FastAPIType = _fastapi.FastAPI
    HTTPExceptionType = _fastapi.HTTPException
    QueryType = _fastapi.Query
    RequestType = _fastapi.Request
    HTMLResponseType = _responses.HTMLResponse
    StaticFilesType = _staticfiles.StaticFiles
    Jinja2TemplatesType = _templating.Jinja2Templates
else:

    class _MissingFastAPI:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Tracker server dependencies are not installed")

    class _MissingHTTPException(Exception):
        pass

    class _MissingRequest:
        pass

    class _MissingHTMLResponse:
        pass

    class _MissingStaticFiles:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Tracker server dependencies are not installed")

    class _MissingTemplates:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Tracker server dependencies are not installed")

    def _missing_query(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("Tracker server dependencies are not installed")

    FastAPIType = _MissingFastAPI
    HTTPExceptionType = _MissingHTTPException
    QueryType = _missing_query
    RequestType = _MissingRequest
    HTMLResponseType = _MissingHTMLResponse
    StaticFilesType = _MissingStaticFiles
    Jinja2TemplatesType = _MissingTemplates


def create_app(db_path: str | Path) -> Any:
    """Create one tracker FastAPI app instance."""
    if (
        _fastapi is None
        or _responses is None
        or _staticfiles is None
        or _templating is None
    ):
        raise RuntimeError(
            "Tracker server requires optional dependencies. Install with: "
            'pip install -e ".[tracker]"'
        )
    FastAPI = cast(Any, FastAPIType)
    HTTPException = cast(Any, HTTPExceptionType)
    Query = cast(Any, QueryType)
    HTMLResponse = cast(Any, HTMLResponseType)
    StaticFiles = cast(Any, StaticFilesType)
    Jinja2Templates = cast(Any, Jinja2TemplatesType)

    app: Any = FastAPI(title="DeltaPorts Build Tracker")
    app.state.db_path = str(db_path)
    templates_dir = Path(__file__).with_name("templates")
    static_dir = Path(__file__).with_name("static")
    templates: Any = Jinja2Templates(directory=str(templates_dir))
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.on_event("startup")
    def _startup() -> None:
        conn = init_db(app.state.db_path)
        conn.close()

    @app.on_event("shutdown")
    def _shutdown() -> None:
        return None

    @contextmanager
    def _conn() -> Any:
        conn = open_db(app.state.db_path)
        try:
            yield conn
        finally:
            conn.close()

    def _raise_http_error(exc: Exception) -> None:
        if isinstance(exc, ActiveBuildError):
            raise HTTPException(
                status_code=409,
                detail={
                    "error": str(exc),
                    "active_run": exc.active_run,
                },
            ) from exc
        if isinstance(exc, ValueError):
            message = str(exc)
            status_code = 404 if message.startswith("Unknown build run:") else 400
            raise HTTPException(status_code=status_code, detail=message) from exc
        raise exc

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

    @app.get("/", response_class=HTMLResponse)
    def dashboard_index(request: RequestType) -> Any:
        with _conn() as conn:
            active_builds = get_active_builds_summary(conn)
            return templates.TemplateResponse(
                request,
                "index.html",
                {
                    "title": "Targets",
                    "targets": get_target_summary(conn),
                    "active_builds": active_builds,
                    "refresh_seconds": 30 if active_builds else None,
                },
            )

    @app.get("/target/{target}", response_class=HTMLResponse)
    def dashboard_target(
        request: RequestType,
        target: str,
        status_filter: str = Query(default="all", alias="filter"),
        q: str = "",
        page: int = Query(default=1, ge=1),
    ) -> Any:
        with _conn() as conn:
            rows = get_port_status(conn, target=target)
            query = q.strip().lower()
            if status_filter == "failures":
                rows = [
                    row for row in rows if row.get("last_attempt_result") == "failure"
                ]
            elif status_filter == "successes":
                rows = [
                    row for row in rows if row.get("last_attempt_result") == "success"
                ]
            if query:
                rows = [
                    row for row in rows if query in str(row.get("origin", "")).lower()
                ]
            page_size = 100
            start = (page - 1) * page_size
            page_rows = rows[start : start + page_size]
            page_count = max(1, (len(rows) + page_size - 1) // page_size)
            return templates.TemplateResponse(
                request,
                "target.html",
                {
                    "title": target,
                    "target": target,
                    "rows": page_rows,
                    "status_filter": status_filter,
                    "query": q,
                    "page": page,
                    "page_count": page_count,
                    "page_size": page_size,
                    "total_rows": len(rows),
                },
            )

    @app.get("/target/{target}/{cat}/{port}", response_class=HTMLResponse)
    def dashboard_port_detail(
        request: RequestType, target: str, cat: str, port: str
    ) -> Any:
        origin = f"{cat}/{port}"
        with _conn() as conn:
            rows = get_port_status(conn, target=target, origin=origin)
            if not rows:
                raise HTTPException(
                    status_code=404, detail=f"Unknown port status: {target} {origin}"
                )
            return templates.TemplateResponse(
                request,
                "port_detail.html",
                {
                    "title": f"{origin} {target}",
                    "target": target,
                    "origin": origin,
                    "status": rows[0],
                    "history": get_port_history(conn, target, origin, limit=20),
                },
            )

    @app.get("/builds", response_class=HTMLResponse)
    def dashboard_builds(
        request: RequestType,
        target: str | None = None,
        build_type: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> Any:
        with _conn() as conn:
            runs = list_build_runs(
                conn, target=target, build_type=build_type, limit=limit
            )
            compare_links = _resolve_compare_links(runs)
            return templates.TemplateResponse(
                request,
                "builds.html",
                {
                    "title": "Builds",
                    "runs": runs,
                    "compare_links": compare_links,
                    "target": target,
                    "build_type": build_type,
                },
            )

    @app.get("/builds/compare", response_class=HTMLResponse)
    def dashboard_build_compare(request: RequestType, a: int, b: int) -> Any:
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "build_compare.html",
                {
                    "title": "Build Compare",
                    "compare": compare_builds(conn, a, b),
                },
            )

    @app.get("/builds/{run_id}", response_class=HTMLResponse)
    def dashboard_build_detail(
        request: RequestType,
        run_id: int,
        status_filter: str = Query(default="all", alias="filter"),
    ) -> Any:
        with _conn() as conn:
            payload = {
                "build_run": get_build_run(conn, run_id),
                "results": get_build_results(conn, run_id),
            }
            build = payload["build_run"]
            results = payload["results"]
            if status_filter == "failures":
                results = [row for row in results if row.get("result") == "failure"]
            elif status_filter == "successes":
                results = [row for row in results if row.get("result") == "success"]
            elif status_filter == "building":
                results = [row for row in results if row.get("status") == "building"]
            elif status_filter == "queued":
                results = [row for row in results if row.get("status") == "queued"]
            return templates.TemplateResponse(
                request,
                "build_detail.html",
                {
                    "title": f"Build {run_id}",
                    "build": build,
                    "results": results,
                    "status_filter": status_filter,
                    "refresh_seconds": 10 if build.get("finished_at") is None else None,
                },
            )

    @app.get("/diff", response_class=HTMLResponse)
    def dashboard_diff(
        request: RequestType,
        a: str | None = None,
        b: str | None = None,
    ) -> Any:
        with _conn() as conn:
            targets = get_target_summary(conn)
            diff_payload = get_diff(conn, a, b) if a and b else None
            return templates.TemplateResponse(
                request,
                "diff.html",
                {
                    "title": "Target Diff",
                    "targets": targets,
                    "target_a": a,
                    "target_b": b,
                    "diff": diff_payload,
                },
            )

    return app


def _resolve_compare_links(runs: list[dict[str, Any]]) -> dict[int, int]:
    links: dict[int, int] = {}
    by_group: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for run in runs:
        key = (str(run["target"]), str(run["build_type"]))
        by_group.setdefault(key, []).append(run)
    for group_runs in by_group.values():
        for index, run in enumerate(group_runs[:-1]):
            older_run = group_runs[index + 1]
            links[int(run["id"])] = int(older_run["id"])
    return links
