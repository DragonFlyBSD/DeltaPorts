"""FastAPI application factory for the build tracker."""

from __future__ import annotations

import importlib
import os
from contextlib import contextmanager
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, cast

from dportsv3.tracker.progress_adapter import (
    run_history_chunk,
    run_summary,
    target_history_chunk,
    target_summary,
)
from dportsv3.tracker.agentic_queries import (
    activity_for_job,
    agentic_status,
    bundles_for_run,
    distinct_targets,
    events_since,
    get_artifact_ref,
    get_bundle,
    get_job,
    get_run,
    list_bundles,
    list_jobs,
    list_port_bundles,
    list_runs,
    recent_activity,
    runner_status,
)
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
    FileResponseType = _responses.FileResponse
    StreamingResponseType = _responses.StreamingResponse
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
    FileResponseType = _MissingHTMLResponse
    StreamingResponseType = _MissingHTMLResponse


_INLINE_TEXT_MEDIA: dict[str, str] = {
    ".md":    "text/plain; charset=utf-8",
    ".txt":   "text/plain; charset=utf-8",
    ".log":   "text/plain; charset=utf-8",
    ".diff":  "text/plain; charset=utf-8",
    ".patch": "text/plain; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
    ".html":  "text/html; charset=utf-8",
    ".xml":   "application/xml; charset=utf-8",
    ".yaml":  "text/plain; charset=utf-8",
    ".yml":   "text/plain; charset=utf-8",
}


def _artifact_media_type(relpath: str, kind: str | None) -> tuple[str, bool]:
    """Pick a Content-Type and an inline-vs-attachment flag for an artifact.

    Text-like extensions render in the browser; gzip and unknown fall
    through to a download. ``kind`` is honored only for compressed
    payloads (the runner sets it on bundled logs).
    """
    if kind == "gzip":
        return "application/gzip", False
    ext = Path(relpath).suffix.lower()
    media = _INLINE_TEXT_MEDIA.get(ext)
    if media is not None:
        return media, True
    return "application/octet-stream", False


def _resolve_artifact_path(
    artifact_root: Path, ref: dict[str, Any]
) -> Path | None:
    """Locate the on-disk file for an artifact_refs row.

    Two backends:
    - 'blob': content-addressed under ``<artifact_root>/objects/sha256/aa/bb/<full>``
    - 'fs':   absolute ``fs_path`` recorded at upsert time
    """
    backend = ref.get("backend")
    if backend == "blob":
        sha = ref.get("sha256")
        if not sha or len(sha) < 4:
            return None
        return (
            artifact_root
            / "blobstore"
            / "objects"
            / "sha256"
            / sha[0:2]
            / sha[2:4]
            / sha
        )
    if backend == "fs":
        fs_path = ref.get("fs_path")
        if not fs_path:
            return None
        return Path(fs_path)
    return None


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
    FileResponse = cast(Any, FileResponseType)
    StreamingResponse = cast(Any, StreamingResponseType)

    app: Any = FastAPI(title="DeltaPorts Build Tracker")
    app.state.db_path = str(db_path)
    # Resolves /api/bundles/<id>/artifacts/<relpath> for the 'blob'
    # backend. Defaults match artifact-store's --logs-root default.
    app.state.artifact_root = Path(
        os.environ.get("DPORTSV3_ARTIFACT_ROOT", "/build/synth/logs/evidence")
    )
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

    # ------------------------------------------------------------------
    # Agentic-read endpoints (absorbed from the retired state-server).
    # ------------------------------------------------------------------

    @app.get("/api/health")
    def api_health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/agentic-status")
    def api_agentic_status() -> dict[str, Any]:
        with _conn() as conn:
            return agentic_status(conn)

    @app.get("/api/activity")
    def api_activity(
        limit: int = Query(default=10, ge=1, le=500),
        target: str | None = None,
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return recent_activity(conn, limit=limit, target=target)

    @app.get("/api/runner-status")
    def api_runner_status() -> dict[str, Any]:
        with _conn() as conn:
            return runner_status(conn)

    @app.get("/api/runs")
    def api_runs(
        target: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_runs(conn, target=target, limit=limit)

    @app.get("/api/runs/{run_id}")
    def api_run_detail(run_id: str) -> dict[str, Any]:
        with _conn() as conn:
            row = get_run(conn, run_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return row

    @app.get("/api/jobs")
    def api_jobs(
        state: str | None = None,
        target: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_jobs(conn, state=state, target=target, limit=limit)

    @app.get("/api/jobs/{job_id}")
    def api_job_detail(job_id: str) -> dict[str, Any]:
        with _conn() as conn:
            row = get_job(conn, job_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        return row

    @app.get("/api/bundles")
    def api_bundles(
        target: str | None = None,
        origin: str | None = None,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_bundles(conn, target=target, origin=origin, limit=limit)

    @app.get("/api/bundles/{bundle_id}")
    def api_bundle_detail(bundle_id: str) -> dict[str, Any]:
        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        return row

    @app.get("/api/ports/{origin:path}")
    def api_port_bundles(
        origin: str,
        target: str | None = None,
        limit: int = Query(default=50, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        with _conn() as conn:
            return list_port_bundles(conn, origin=origin, target=target, limit=limit)

    @app.get("/api/bundles/{bundle_id}/artifacts/{relpath:path}")
    def api_bundle_artifact(bundle_id: str, relpath: str) -> Any:
        # Resolve via artifact_refs, then stream from disk. Two backends:
        # 'blob' (content-addressed under blob_root/objects/sha256) or
        # 'fs' (absolute path in fs_path).
        with _conn() as conn:
            ref = get_artifact_ref(conn, bundle_id, relpath)
        if ref is None:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        path = _resolve_artifact_path(app.state.artifact_root, ref)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="Artifact file missing")
        media_type, inline = _artifact_media_type(relpath, ref.get("kind"))
        # Set Content-Disposition: inline for text-like artifacts so the
        # browser renders them instead of triggering a download.
        headers = {"Content-Disposition": f"inline; filename=\"{Path(relpath).name}\""} if inline else None
        return FileResponse(str(path), media_type=media_type, headers=headers)

    @app.get("/api/events")
    def api_events(
        target: str | None = None,
        last_id: int = 0,
    ) -> Any:
        # Server-sent events: poll the events table on a 1s tick, emit
        # rows with id > last_id, filter by target (best-effort — see
        # events_since docstring).
        import asyncio
        import json as _json

        async def _gen() -> Any:
            cursor = int(last_id)
            try:
                while True:
                    with _conn() as conn:
                        rows = events_since(conn, last_id=cursor, target=target)
                    for row in rows:
                        cursor = max(cursor, int(row["id"]))
                        payload = _json.dumps(row, default=str)
                        yield f"event: {row['type']}\ndata: {payload}\n\n"
                    await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                return

        return StreamingResponse(_gen(), media_type="text/event-stream")

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

    # ------------------------------------------------------------------
    # Phase 4 step 6: agentic HTML views.
    # ------------------------------------------------------------------

    @app.get("/agentic", response_class=HTMLResponse)
    def agentic_index(request: RequestType) -> Any:
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_index.html",
                {
                    "title": "Agentic",
                    "status": agentic_status(conn),
                    "recent_bundles": list_bundles(conn, limit=10),
                    "recent_jobs": list_jobs(conn, limit=10),
                },
            )

    @app.get("/agentic/bundles", response_class=HTMLResponse)
    def agentic_bundles(
        request: RequestType,
        target: str | None = None,
        origin: str | None = None,
    ) -> Any:
        target_value = target or None
        origin_value = (origin or "").strip() or None
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_bundles.html",
                {
                    "title": "Bundles",
                    "bundles": list_bundles(
                        conn, target=target_value, origin=origin_value, limit=200
                    ),
                    "target_options": distinct_targets(conn),
                    "selected_target": target_value,
                    "selected_origin": origin_value,
                },
            )

    @app.get("/agentic/bundles/{bundle_id}", response_class=HTMLResponse)
    def agentic_bundle_detail(request: RequestType, bundle_id: str) -> Any:
        with _conn() as conn:
            bundle = get_bundle(conn, bundle_id)
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        return templates.TemplateResponse(
            request,
            "agentic_bundle.html",
            {"title": bundle_id, "bundle": bundle},
        )

    @app.get("/agentic/jobs", response_class=HTMLResponse)
    def agentic_jobs(
        request: RequestType,
        target: str | None = None,
        state: str | None = None,
    ) -> Any:
        target_value = target or None
        state_value = state or None
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_jobs.html",
                {
                    "title": "Jobs",
                    "jobs": list_jobs(
                        conn, state=state_value, target=target_value, limit=200
                    ),
                    "target_options": distinct_targets(conn),
                    "selected_target": target_value,
                    "selected_state": state_value,
                },
            )

    @app.get("/agentic/jobs/{job_id}", response_class=HTMLResponse)
    def agentic_job_detail(
        request: RequestType,
        job_id: str,
        limit: int = 500,
    ) -> Any:
        limit = max(10, min(int(limit), 5000))
        with _conn() as conn:
            job = get_job(conn, job_id)
            activity = activity_for_job(conn, job_id, limit=limit) if job is not None else []
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        # Activity rows come back newest-first from the query; flip for
        # chronological reading (attempt 1 tools → attempt 2 tools → ...).
        activity = list(reversed(activity))
        return templates.TemplateResponse(
            request,
            "agentic_job.html",
            {
                "title": job_id,
                "job": job,
                "activity": activity,
                "limit": limit,
                "limit_options": [50, 200, 500, 2000, 5000],
            },
        )

    @app.get("/agentic/runs/{run_id}", response_class=HTMLResponse)
    def agentic_run_detail(request: RequestType, run_id: str) -> Any:
        with _conn() as conn:
            run = get_run(conn, run_id)
            bundles = bundles_for_run(conn, run_id) if run is not None else []
        if run is None:
            raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
        return templates.TemplateResponse(
            request,
            "agentic_run.html",
            {"title": run_id, "run": run, "bundles": bundles},
        )

    @app.get("/agentic/runner", response_class=HTMLResponse)
    def agentic_runner(request: RequestType) -> Any:
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_runner.html",
                {"title": "Runner", "runner": runner_status(conn)},
            )

    @app.get("/agentic/activity", response_class=HTMLResponse)
    def agentic_activity(
        request: RequestType,
        target: str | None = None,
        limit: int = 200,
    ) -> Any:
        target_value = target or None
        limit = max(10, min(int(limit), 5000))
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_activity.html",
                {
                    "title": "Activity",
                    "activity": recent_activity(conn, limit=limit, target=target_value),
                    "target_options": distinct_targets(conn),
                    "selected_target": target_value,
                    "limit": limit,
                    "limit_options": [50, 200, 500, 2000, 5000],
                },
            )

    # ------------------------------------------------------------------
    # Phase 5 step 1: dsynth-progress UI adapter. Lifts the
    # www/example/progress.{html,js,css} UI and feeds it from tracker
    # data via two JSON endpoints (summary + chunked history). No
    # change to the existing /target/{target} dashboard yet.
    # ------------------------------------------------------------------

    # JSON endpoints live under /api/progress/{target}/ to stay clear
    # of the legacy /target/{target}/{cat}/{port} catch-all. The HTML
    # page is served at the canonical /target/{target} below.

    @app.get("/api/progress/{target}/summary.json")
    def progress_summary(target: str) -> dict[str, Any]:
        with _conn() as conn:
            return target_summary(conn, target)

    @app.get("/api/progress/{target}/{chunk}_history.json")
    def progress_history(target: str, chunk: str) -> Any:
        try:
            chunk_index = int(chunk)
        except ValueError:
            raise HTTPException(status_code=404, detail="Bad chunk index")
        with _conn() as conn:
            # Returns [] past the last chunk — kfiles in summary.json
            # bounds the UI's fetch range so this is rarely hit.
            return target_history_chunk(conn, target, chunk_index)

    @app.get("/api/progress/build/{run_id}/summary.json")
    def progress_build_summary(run_id: int) -> dict[str, Any]:
        with _conn() as conn:
            summary = run_summary(conn, run_id)
        if summary is None:
            raise HTTPException(status_code=404, detail=f"Unknown build run: {run_id}")
        return summary

    @app.get("/api/progress/build/{run_id}/{chunk}_history.json")
    def progress_build_history(run_id: int, chunk: str) -> Any:
        try:
            chunk_index = int(chunk)
        except ValueError:
            raise HTTPException(status_code=404, detail="Bad chunk index")
        with _conn() as conn:
            return run_history_chunk(conn, run_id, chunk_index)

    @app.get("/target/{target}", response_class=HTMLResponse)
    def dashboard_target(request: RequestType, target: str) -> Any:
        # The page uses progress.{css,js} (lifted from dsynth-progress)
        # and fetches data from /api/progress/{target}/. The <base> tag
        # pins those relative URLs to the canonical API root.
        return templates.TemplateResponse(
            request,
            "progress.html",
            {
                "title": target,
                "target": target,
                "progress_base": f"/api/progress/{target}/",
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
    def dashboard_build_detail(request: RequestType, run_id: int) -> Any:
        # Build detail uses the same dsynth-progress UI as /target/{target},
        # just scoped to one run_id. Verify the run exists so unknown
        # IDs 404 here rather than at the JSON fetch.
        try:
            with _conn() as conn:
                build = get_build_run(conn, run_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return templates.TemplateResponse(
            request,
            "progress.html",
            {
                "title": f"Build {run_id}",
                "target": f"{build['target']} (run {run_id})",
                "progress_base": f"/api/progress/build/{run_id}/",
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
