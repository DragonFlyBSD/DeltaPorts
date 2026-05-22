"""FastAPI application factory for the build tracker."""

from __future__ import annotations

import importlib
import html
import json
import os
import re
import sqlite3
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
    active_job_for_port,
    activity_for_job,
    agentic_status,
    bundles_for_run,
    discard_manual_request,
    distinct_targets,
    events_since,
    env_health_statuses,
    get_artifact_ref,
    get_bundle,
    get_job,
    get_manual_request,
    get_run,
    job_events_for_job,
    list_bundles,
    list_jobs,
    list_manual_requests,
    list_port_bundles,
    list_runs,
    port_attempt_summary,
    recent_activity,
    runner_status,
    token_usage_for_job,
    token_usage_for_port,
    upsert_user_context_text,
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
    ManualContextRequest,
    ManualContextResponse,
    ManualDiscardRequest,
    ManualDiscardResponse,
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
    ".rej":   "text/plain; charset=utf-8",
    ".dops":  "text/plain; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
    ".html":  "text/html; charset=utf-8",
    ".xml":   "application/xml; charset=utf-8",
    ".yaml":  "text/plain; charset=utf-8",
    ".yml":   "text/plain; charset=utf-8",
}

_INLINE_TEXT_NAMES = {"Makefile", "distinfo", "pkg-plist", "pkg-descr"}


_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_INLINE_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")


def _render_inline(escaped: str) -> str:
    """Apply inline ``code`` and ``**bold**`` to already-HTML-escaped text.

    ``code`` spans are extracted to sentinels before ``**bold**`` is
    processed, then re-substituted. Without that, asterisks inside
    backticks (e.g. ``` `**literal**` ```) would be wrongly bolded.
    The patterns reject newlines so a stray asterisk or backtick on
    its own line can't accidentally span paragraphs.
    """
    placeholders: list[str] = []

    def _stash(m):
        placeholders.append(m.group(1))
        return f"\x00C{len(placeholders) - 1}\x00"

    s = _INLINE_CODE_RE.sub(_stash, escaped)
    s = _INLINE_BOLD_RE.sub(r"<strong>\1</strong>", s)
    for i, content in enumerate(placeholders):
        s = s.replace(f"\x00C{i}\x00", f"<code>{content}</code>")
    return s


def _render_markdown(text: str) -> str:
    """Render the small Markdown subset used by agent artifacts.

    Keep this stdlib-only and escape all content before wrapping it in
    HTML. It is intentionally conservative: headings, paragraphs,
    bullet lists, fenced code blocks, and inline ``code`` + ``**bold**``
    cover triage/patch reports and the manual_handoff artifact.
    """
    out: list[str] = []
    paragraph: list[str] = []
    bullets_open = False
    code_open = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            out.append("<p>" + "<br>".join(paragraph) + "</p>")
            paragraph = []

    def close_bullets() -> None:
        nonlocal bullets_open
        if bullets_open:
            out.append("</ul>")
            bullets_open = False

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```"):
            flush_paragraph()
            close_bullets()
            if code_open:
                out.append(
                    "<pre class=\"artifact-content\"><code>"
                    + html.escape("\n".join(code_lines))
                    + "</code></pre>"
                )
                code_lines = []
                code_open = False
            else:
                code_open = True
            continue
        if code_open:
            code_lines.append(line)
            continue
        if not stripped:
            flush_paragraph()
            close_bullets()
            continue
        if stripped.startswith("#"):
            flush_paragraph()
            close_bullets()
            marker, _, title = stripped.partition(" ")
            if title and 1 <= len(marker) <= 6 and set(marker) == {"#"}:
                level = min(len(marker) + 1, 6)
                out.append(
                    f"<h{level}>"
                    + _render_inline(html.escape(title))
                    + f"</h{level}>"
                )
                continue
        if stripped.startswith("- "):
            flush_paragraph()
            if not bullets_open:
                out.append("<ul>")
                bullets_open = True
            out.append(
                "<li>"
                + _render_inline(html.escape(stripped[2:].strip()))
                + "</li>"
            )
            continue
        paragraph.append(_render_inline(html.escape(stripped)))
    if code_open:
        out.append(
            "<pre class=\"artifact-content\"><code>"
            + html.escape("\n".join(code_lines))
            + "</code></pre>"
        )
    flush_paragraph()
    close_bullets()
    return "\n".join(out)


def _artifact_media_type(relpath: str, kind: str | None) -> tuple[str, bool]:
    """Pick a Content-Type and an inline-vs-attachment flag for an artifact.

    Text-like extensions render in the browser; gzip and unknown fall
    through to a download. ``kind`` is honored only for compressed
    payloads (the runner sets it on bundled logs).
    """
    if kind == "gzip":
        return "application/gzip", False
    artifact_path = Path(relpath)
    if artifact_path.name in _INLINE_TEXT_NAMES:
        return "text/plain; charset=utf-8", True
    ext = artifact_path.suffix.lower()
    media = _INLINE_TEXT_MEDIA.get(ext)
    if media is not None:
        return media, True
    return "application/octet-stream", False


def _artifact_view_data(
    artifact_root: Path,
    bundle_id: str,
    relpath: str,
    ref: dict[str, Any],
) -> dict[str, Any] | None:
    path = _resolve_artifact_path(artifact_root, ref)
    if path is None or not path.exists():
        return None
    media_type, inline = _artifact_media_type(relpath, ref.get("kind"))
    suffix = Path(relpath).suffix.lower()
    is_json = suffix == ".json"
    is_markdown = suffix == ".md"
    content: str | None = None
    render_kind = "download"
    error: str | None = None
    if inline:
        render_kind = "markdown" if is_markdown else ("json" if is_json else "text")
        try:
            raw = path.read_text(errors="replace")
            if is_markdown:
                content = _render_markdown(raw)
            elif is_json:
                try:
                    content = json.dumps(json.loads(raw), indent=2, sort_keys=True)
                except ValueError as exc:
                    content = raw
                    error = f"invalid JSON: {exc}"
            else:
                content = raw
        except OSError as exc:
            error = str(exc)
            content = ""
    return {
        "bundle_id": bundle_id,
        "relpath": relpath,
        "ref": ref,
        "media_type": media_type,
        "inline": inline,
        "render_kind": render_kind,
        "content": content,
        "error": error,
        "filename": Path(relpath).name,
        "size": path.stat().st_size if path.exists() else ref.get("size"),
    }


_DEFAULT_ARTIFACT_PRIORITY = (
    # Operator-facing summaries first — these are what the operator
    # wants to land on when they open a bundle.
    "analysis/proposed_fix.md",     # success path: actionable recipe
    "analysis/manual_handoff.md",   # escalation path: what to do next
    # Then the agent's own outputs, then raw evidence.
    "analysis/triage.md",
    "analysis/patch.md",
    "logs/errors.txt",
    "meta.txt",
)


def _default_artifact_relpath(bundle: dict[str, Any]) -> str | None:
    artifacts = bundle.get("artifacts") or []
    relpaths = [str(a.get("relpath")) for a in artifacts if a.get("relpath")]
    relpath_set = set(relpaths)
    for candidate in _DEFAULT_ARTIFACT_PRIORITY:
        if candidate in relpath_set:
            return candidate
    return relpaths[0] if relpaths else None


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


def _load_tool_trace(artifact_root: Path, ref: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Parse analysis/tool_trace.jsonl for compact bundle rendering."""
    if ref is None:
        return []
    path = _resolve_artifact_path(artifact_root, ref)
    if path is None or not path.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if isinstance(ev, dict):
                events.append(ev)
    except OSError:
        return []
    return events


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
        job_id: str | None = None,
        since_id: int = Query(default=0, ge=0),
        stage_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Activity-log query.

        - ``job_id`` set → per-job rows. With ``since_id > 0`` returns
          new rows oldest-first (polling shape for the job detail
          page's live refresh — Step 9c). Optional ``stage_filter``
          narrows to ``llm_turn`` or ``tool`` (Step 9b).
        - ``job_id`` unset → global recent (newest-first), optionally
          filtered by target.
        """
        with _conn() as conn:
            if job_id:
                return activity_for_job(
                    conn, job_id, limit=limit, since_id=since_id,
                    stage_filter=stage_filter,
                )
            return recent_activity(conn, limit=limit, target=target)

    @app.get("/api/runner-status")
    def api_runner_status() -> dict[str, Any]:
        with _conn() as conn:
            return runner_status(conn)

    @app.get("/api/env-health")
    def api_env_health() -> list[dict[str, Any]]:
        with _conn() as conn:
            return env_health_statuses(conn)

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

    @app.post("/api/jobs/{job_id}/abandon")
    def api_job_abandon(job_id: str) -> dict[str, Any]:
        """Operator-triggered kill. Transitions a QUEUED or in-flight
        job to DEAD with ``retire_reason='abandoned'``. Rejects calls
        against terminal states (DONE/DEAD/ESCALATED) — the operator
        can't abandon something that's already retired."""
        from dportsv3.agent import lifecycle as _lc  # noqa: PLC0415
        with _conn() as conn:
            row = get_job(conn, job_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown job: {job_id}",
            )
        # lifecycle.apply runs explicit BEGIN IMMEDIATE / COMMIT and
        # is incompatible with sqlite3's default deferred-transaction
        # wrapper. Use a dedicated autocommit-mode connection so the
        # explicit transaction works as authored.
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            try:
                new_state = _lc.apply(
                    write_conn, job_id, _lc.JobEvent.ABANDON, actor="operator",
                )
            except _lc.IllegalTransition as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot abandon job in state {row.get('state')!r}: "
                        f"{exc}"
                    ),
                )
        finally:
            write_conn.close()
        return {
            "ok": True,
            "job_id": job_id,
            "previous_state": row.get("state"),
            "new_state": new_state.value,
            "retire_reason": "abandoned",
        }

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
                    "env_health": env_health_statuses(conn),
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
    def agentic_bundle_detail(
        request: RequestType,
        bundle_id: str,
        artifact: str | None = None,
    ) -> Any:
        with _conn() as conn:
            bundle = get_bundle(conn, bundle_id)
            tool_trace_ref = get_artifact_ref(conn, bundle_id, "analysis/tool_trace.jsonl")
            selected_relpath = artifact or (_default_artifact_relpath(bundle) if bundle else None)
            selected_ref = (
                get_artifact_ref(conn, bundle_id, selected_relpath)
                if selected_relpath else None
            )
            # Step 9: prior attempts table. Other bundles for the same
            # (origin, target) so the operator can see the agent's
            # history at a glance from any bundle page.
            prior_attempts = (
                [b for b in list_port_bundles(
                    conn, origin=bundle.get("origin"),
                    target=bundle.get("target"), limit=10,
                ) if b["bundle_id"] != bundle_id]
                if bundle is not None else []
            )
            # Step 9: lifetime token usage for this port, across
            # every job (triage + each patch attempt).
            port_token_usage = (
                token_usage_for_port(
                    conn, origin=bundle.get("origin"),
                    target=bundle.get("target"),
                )
                if bundle is not None and bundle.get("origin") else None
            )
            # Step 20f: dops conversion state for the port. Surfaces
            # whether this failure is on an already-converted port,
            # one waiting for conversion, etc.
            dops_state = None
            if bundle is not None and bundle.get("origin"):
                try:
                    from dportsv3.agent.dops import classify as _classify_dops
                    import os as _os
                    _repo = (
                        _os.environ.get("DP_HARNESS_REPO_ROOT")
                        or _os.environ.get("DPORTSV3_REPO_ROOT")
                        or "."
                    )
                    dops_state = _classify_dops(
                        bundle["origin"], _repo,
                    )
                except Exception:
                    dops_state = None
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        if selected_relpath and selected_ref is None:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        selected_artifact = (
            _artifact_view_data(app.state.artifact_root, bundle_id, selected_relpath, selected_ref)
            if selected_relpath and selected_ref else None
        )
        if selected_relpath and selected_artifact is None:
            raise HTTPException(status_code=404, detail="Artifact file missing")
        tool_trace = _load_tool_trace(app.state.artifact_root, tool_trace_ref)
        return templates.TemplateResponse(
            request,
            "agentic_bundle.html",
            {
                "title": bundle_id,
                "bundle": bundle,
                "tool_trace": tool_trace,
                "selected_artifact": selected_artifact,
                "selected_artifact_relpath": selected_relpath,
                "prior_attempts": prior_attempts,
                "port_token_usage": port_token_usage,
                "dops_state": dops_state,
            },
        )

    @app.get("/agentic/bundles/{bundle_id}/artifacts/{relpath:path}", response_class=HTMLResponse)
    def agentic_bundle_artifact_view(
        request: RequestType,
        bundle_id: str,
        relpath: str,
    ) -> Any:
        with _conn() as conn:
            bundle = get_bundle(conn, bundle_id)
            ref = get_artifact_ref(conn, bundle_id, relpath)
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        if ref is None:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        artifact = _artifact_view_data(app.state.artifact_root, bundle_id, relpath, ref)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact file missing")
        return templates.TemplateResponse(
            request,
            "agentic_artifact.html",
            {"title": relpath, "bundle": bundle, "artifact": artifact},
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
        stage_filter: str | None = None,
    ) -> Any:
        limit = max(10, min(int(limit), 5000))
        # Normalize the filter. Step 9b — three pills: all/llm_turn/tool.
        sf = stage_filter if stage_filter in ("llm_turn", "tool") else None
        with _conn() as conn:
            job = get_job(conn, job_id)
            activity = (activity_for_job(conn, job_id, limit=limit,
                                          stage_filter=sf)
                        if job is not None else [])
            transitions = (
                job_events_for_job(conn, job_id, limit=limit)
                if job is not None else []
            )
            attempt_summary = (
                port_attempt_summary(
                    conn,
                    target=job.get("target"),
                    origin=job.get("origin"),
                    window_hours=int(os.environ.get("DP_HARNESS_ATTEMPT_WINDOW_HOURS", "2")),
                    max_attempts=int(os.environ.get("DP_HARNESS_MAX_PATCH_ATTEMPTS", "3")),
                ) if job is not None else None
            )
            token_usage = (
                token_usage_for_job(conn, job_id)
                if job is not None else None
            )
            # Step 9: prior-attempts table — recent bundles for the
            # same (origin, target) so the operator can see history.
            prior_attempts = (
                list_port_bundles(
                    conn, origin=job.get("origin"),
                    target=job.get("target"), limit=10,
                )
                if job is not None and job.get("origin") else []
            )
            # Step 9: when a job ends in 'escalated', operators
            # currently have to bounce out to /agentic/manual to read
            # the handoff. Inline it: pull the most recent bundle for
            # this (origin, target) that has manual_handoff.md and
            # render it next to the activity timeline.
            handoff = None
            if job is not None and job.get("state") == "escalated" and prior_attempts:
                for cand in prior_attempts:
                    ref = get_artifact_ref(
                        conn, cand["bundle_id"], "analysis/manual_handoff.md",
                    )
                    if ref is not None:
                        handoff = _artifact_view_data(
                            app.state.artifact_root,
                            cand["bundle_id"],
                            "analysis/manual_handoff.md",
                            ref,
                        )
                        handoff = dict(handoff or {})
                        handoff["bundle_id"] = cand["bundle_id"]
                        handoff["run_id"] = cand.get("run_id")
                        break
        if job is None:
            raise HTTPException(status_code=404, detail=f"Unknown job: {job_id}")
        # Activity rows come back newest-first from the query; flip for
        # chronological reading (attempt 1 tools → attempt 2 tools → ...).
        activity = list(reversed(activity))
        # Cursor for the live-refresh polling — the client polls
        # /api/activity?job_id=X&since_id=N for new rows.
        max_id = max((a.get("id") or 0) for a in activity) if activity else 0
        return templates.TemplateResponse(
            request,
            "agentic_job.html",
            {
                "title": job_id,
                "job": job,
                "activity": activity,
                "transitions": transitions,
                "attempt_summary": attempt_summary,
                "token_usage": token_usage,
                "max_activity_id": max_id,
                "limit": limit,
                "limit_options": [50, 200, 500, 2000, 5000],
                "stage_filter": sf,
                "prior_attempts": prior_attempts,
                "handoff": handoff,
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
    # Manual escalation queue (post-impl plan, Step 4).
    # Operators land here when a job escalates to MANUAL — read the
    # handoff artifact, type context, hit "Try again with this
    # context." POST writes the context_text + bumps context_rev; the
    # runner's existing process_user_context_updates loop picks it up
    # and re-enqueues a triage job.
    # ------------------------------------------------------------------

    @app.get("/agentic/manual", response_class=HTMLResponse)
    def agentic_manual_list(
        request: RequestType,
        open_only: bool = True,
    ) -> Any:
        with _conn() as conn:
            return templates.TemplateResponse(
                request,
                "agentic_manual_list.html",
                {
                    "title": "Manual Queue",
                    "requests": list_manual_requests(conn, open_only=open_only),
                    "open_only": open_only,
                },
            )

    @app.get("/agentic/manual/{run_id}/{origin:path}", response_class=HTMLResponse)
    def agentic_manual_detail(
        request: RequestType,
        run_id: str,
        origin: str,
    ) -> Any:
        with _conn() as conn:
            mr = get_manual_request(conn, run_id, origin)
            handoff = None
            blocking_job = (
                active_job_for_port(
                    conn, origin=origin, target=mr.get("target"),
                )
                if mr is not None else None
            )
            if mr is not None and mr.get("bundle_id"):
                ref = get_artifact_ref(
                    conn, mr["bundle_id"], "analysis/manual_handoff.md",
                )
                if ref is not None:
                    handoff = _artifact_view_data(
                        app.state.artifact_root,
                        mr["bundle_id"],
                        "analysis/manual_handoff.md",
                        ref,
                    )
        if mr is None:
            raise HTTPException(
                status_code=404,
                detail=f"No manual request for run={run_id} origin={origin}",
            )
        return templates.TemplateResponse(
            request,
            "agentic_manual_detail.html",
            {
                "title": f"Manual: {origin}",
                "request_row": mr,
                "handoff": handoff,
                "blocking_job": blocking_job,
            },
        )

    @app.get("/api/manual-requests")
    def api_manual_requests(open_only: bool = True) -> dict[str, Any]:
        with _conn() as conn:
            rows = list_manual_requests(conn, open_only=open_only)
        return {"requests": rows}

    @app.post(
        "/api/manual-requests/{run_id}/{origin:path}/context",
        response_model=ManualContextResponse,
    )
    def api_manual_submit_context(
        run_id: str,
        origin: str,
        payload: ManualContextRequest,
    ) -> dict[str, Any]:
        text = (payload.context_text or "").strip()
        if not text:
            raise HTTPException(
                status_code=400, detail="context_text cannot be empty",
            )
        if len(text) > 8000:
            raise HTTPException(
                status_code=400,
                detail="context_text too long (max 8000 chars)",
            )
        with _conn() as conn:
            mr = get_manual_request(conn, run_id, origin)
            if mr is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No manual request for run={run_id} origin={origin}",
                )
            new_rev = upsert_user_context_text(conn, run_id, origin, text)
        return {"ok": True, "context_rev": new_rev}

    @app.post(
        "/api/manual-requests/{run_id}/{origin:path}/discard",
        response_model=ManualDiscardResponse,
    )
    def api_manual_discard(
        run_id: str,
        origin: str,
        payload: ManualDiscardRequest | None = None,
    ) -> dict[str, Any]:
        with _conn() as conn:
            mr = get_manual_request(conn, run_id, origin)
            if mr is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"No manual request for run={run_id} origin={origin}",
                )
            reason = (payload.reason if payload else "") or ""
            discarded = discard_manual_request(conn, run_id, origin, reason)
        return {"ok": True, "discarded": discarded}

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
