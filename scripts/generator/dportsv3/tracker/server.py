"""FastAPI application factory for the build tracker.

``create_app`` is app assembly + wiring only: it builds the FastAPI app,
opens the shared setup (db connection factory, templates, static mount),
then hands one ``RouteContext`` to each ``routes/*.register()``. The route
bodies, the optional-FastAPI import shim, and the fix-review chat helpers
all live under ``routes/`` (see ``routes/_common.py``).
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from dportsv3.tracker import fix_state
from dportsv3.tracker.db import ActiveBuildError, init_db, open_db
from dportsv3.tracker.routes import (
    _common,
    agentic_api,
    builds_api,
    bundle_actions,
    pages,
)
from dportsv3.tracker.routes._common import (
    FastAPI,
    HTTPException,
    Jinja2Templates,
    StaticFiles,
    _fastapi,
    _responses,
    _staticfiles,
    _templating,
)

_LOG = logging.getLogger(__name__)


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

    # Content-hash cache-buster for progress.css. Without this, when
    # we change rules in the file (e.g. extracting the diff renderer's
    # styles in 2.5a), browsers keep serving the previous version from
    # cache — so the new HTML structure looks unstyled until the
    # operator hard-refreshes. Compute the hash once at startup
    # (template responses can read it as ``static_v`` then). Falls back
    # to "0" if the file isn't present so dev / packaging variants
    # don't break.
    import hashlib as _hashlib  # noqa: PLC0415
    _static_v = "0"
    _css_path = static_dir / "progress.css"
    if _css_path.is_file():
        _static_v = _hashlib.sha256(_css_path.read_bytes()).hexdigest()[:10]
    templates.env.globals["static_v"] = _static_v
    # The single operator-facing status projection. Templates call it to
    # render one status pill instead of reconciling resolution +
    # verification_status + job.state by eye.
    templates.env.globals["fix_status"] = fix_state.fix_status

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

    ctx = _common.RouteContext(
        conn=_conn,
        raise_http_error=_raise_http_error,
        templates=templates,
    )
    builds_api.register(app, ctx)
    agentic_api.register(app, ctx)
    bundle_actions.register(app, ctx)
    pages.register(app, ctx)

    return app
