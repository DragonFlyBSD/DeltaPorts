"""Shared prelude for the tracker route modules.

This module has no dependency on ``server`` (it is imported *by* both
``server`` and every ``routes/*`` module), so it is the cycle-free home for:

- the optional-FastAPI import shim + the ``cast(Any, ...)`` aliases the route
  bodies reference by bare name (``HTTPException``, ``Query``, ``HTMLResponse``,
  …);
- the fix-review chat helpers (``_chat_llm_config`` /
  ``_pick_default_session_relpath``) the bundle routes call;
- ``RouteContext`` — the tiny struct ``create_app`` fills once and hands to each
  ``register(app, ctx)``.

Route modules do ``from dportsv3.tracker.routes._common import *`` so their
verbatim bodies keep resolving every name they used inside ``create_app``.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import sqlite3
from dataclasses import dataclass
from importlib import util as importlib_util
from pathlib import Path
from typing import Any, Callable, cast

from dportsv3.tracker import fix_state
from dportsv3.tracker import render
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
    get_active_env,
    set_active_env,
    get_artifact_ref,
    get_bundle,
    get_job,
    get_manual_request,
    get_run,
    clear_origin_skip,
    is_origin_skipped,
    job_events_for_job,
    latest_review_request_for_bundle,
    list_bundles,
    update_review_request_status,
    list_jobs,
    list_jobs_for_bundle,
    list_manual_requests,
    list_port_bundles,
    list_runs,
    port_attempt_summary,
    recent_activity,
    recent_activity_for_bundle,
    runner_status,
    set_origin_skip,
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

_LOG = logging.getLogger("dportsv3.tracker.server")

# ---------------------------------------------------------------------------
# Optional-FastAPI import shim. The tracker is an optional extra; when its
# deps are absent these resolve to loud placeholders and create_app raises a
# helpful error before any route is hit.
# ---------------------------------------------------------------------------

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
    RedirectResponseType = _responses.RedirectResponse
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
    RedirectResponseType = _MissingHTMLResponse
    StaticFilesType = _MissingStaticFiles
    Jinja2TemplatesType = _MissingTemplates
    FileResponseType = _MissingHTMLResponse
    StreamingResponseType = _MissingHTMLResponse


# The bare names the route bodies use (they were cast once inside create_app).
FastAPI = cast(Any, FastAPIType)
HTTPException = cast(Any, HTTPExceptionType)
Query = cast(Any, QueryType)
HTMLResponse = cast(Any, HTMLResponseType)
RedirectResponse = cast(Any, RedirectResponseType)
StaticFiles = cast(Any, StaticFilesType)
Jinja2Templates = cast(Any, Jinja2TemplatesType)
FileResponse = cast(Any, FileResponseType)
StreamingResponse = cast(Any, StreamingResponseType)


@dataclass
class RouteContext:
    """Runtime deps a route group needs from ``create_app`` that cannot be
    plain module globals: the per-request connection factory, the shared
    HTTP-error translator, and the Jinja templates instance."""

    conn: Callable[[], Any]
    raise_http_error: Callable[[Exception], None]
    templates: Any


# ---------------------------------------------------------------------------
# Fix-review chat helpers (operator Q&A about a completed fix).
#
# A completed fix is reviewed from its **frozen bundle artifacts** — the diff,
# triage, proposed_fix, errors, and the agent's session dump — not a live env
# (the agent's tools read a shared quarterly chroot that has moved on from this
# fix). Message assembly lives in ``dportsv3.agent.fix_chat`` (pure,
# unit-testable); this layer only resolves creds and picks the seed session.
# ---------------------------------------------------------------------------


def _chat_llm_config() -> dict[str, Any] | None:
    """Resolve the chat model config from ``DP_HARNESS_CHAT_*`` env.

    Returns ``None`` when ``DP_HARNESS_CHAT_MODEL`` is unset — this is the
    feature gate. Callers treat ``None`` as "chat disabled" (503 on the
    endpoint, hidden panel in the UI). The other three vars mirror the
    runner's per-flow config: ``*_API_KEY``, ``*_API_BASE`` (custom
    endpoint), ``*_PROVIDER`` (force litellm's provider code path).
    """
    model = os.environ.get("DP_HARNESS_CHAT_MODEL", "").strip()
    if not model:
        return None

    def _clean(name: str) -> str | None:
        v = os.environ.get(name, "").strip()
        return v or None

    try:
        timeout = int(os.environ.get("DP_HARNESS_CHAT_TIMEOUT", "120") or "120")
    except ValueError:
        timeout = 120
    # Bound the assembled artifact+transcript context. Default suits a
    # modern 128K-context model; operators on a smaller-context chat
    # model can shrink it. Assembly + the default live in fix_chat.
    from dportsv3.agent import fix_chat  # noqa: PLC0415
    try:
        context_cap = int(
            os.environ.get("DP_HARNESS_CHAT_CONTEXT_CAP", "")
            or fix_chat.DEFAULT_CONTEXT_CAP
        )
    except ValueError:
        context_cap = fix_chat.DEFAULT_CONTEXT_CAP
    return {
        "model": model,
        "api_key": _clean("DP_HARNESS_CHAT_API_KEY"),
        "api_base": _clean("DP_HARNESS_CHAT_API_BASE"),
        "custom_llm_provider": _clean("DP_HARNESS_CHAT_PROVIDER"),
        "timeout": timeout,
        "context_cap": max(8 * 1024, context_cap),
    }


def _pick_default_session_relpath(bundle: dict[str, Any]) -> str | None:
    """Choose which session dump seeds the chat for ``bundle``.

    Prefers the last (highest-attempt) *patch* session — that's the
    attempt that produced the accepted fix and holds the reasoning an
    operator asks "why" about. Falls back to any session dump (e.g. a
    triage-only bundle) when no patch session exists. Returns ``None``
    when the bundle carries no session dump at all (the run had
    ``DP_HARNESS_DUMP_SESSION`` off).
    """
    sessions = [
        str(a.get("relpath"))
        for a in (bundle.get("artifacts") or [])
        if a.get("relpath") and render.is_session_relpath(str(a.get("relpath")))
    ]
    if not sessions:
        return None

    def _attempt(relpath: str) -> int:
        m = render.SESSION_ATTEMPT_RE.search(Path(relpath).name)
        return int(m.group(1)) if m else 0

    patch = [s for s in sessions if "-patch." in Path(s).name]
    pool = patch or sessions
    return max(pool, key=_attempt)
