"""Agentic read API + bundle detail/chat/verify routes."""

from __future__ import annotations

import sqlite3
from typing import Any

from dportsv3.tracker import (
    fix_state,
    render,
)
from dportsv3.tracker.agentic_queries import (
    activity_for_job,
    agentic_status,
    env_health_statuses,
    get_active_env,
    get_bundle,
    get_job,
    get_run,
    list_bundles,
    list_jobs,
    list_jobs_for_bundle,
    list_runs,
    recent_activity,
    runner_status,
    set_active_env,
)
from dportsv3.tracker.routes._common import (
    HTTPException,
    Query,
    _LOG,
    _chat_llm_config,
    _pick_default_session_relpath,
)


def register(app, ctx):
    _conn = ctx.conn
    templates = ctx.templates

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

    @app.get("/api/jobs/{job_id}/activity-fragment")
    def api_job_activity_fragment(
        job_id: str,
        since_id: int = Query(default=0, ge=0),
        stage_filter: str | None = None,
    ) -> dict[str, Any]:
        """Server-rendered activity rows since a cursor, for the live feed.

        Returns the same ``_activity_row.html`` markup the initial page
        render uses — one render path, no client-side row duplication.
        ``html`` is oldest-first (the caller inserts each at the top so the
        newest lands highest). ``job_state`` lets the client stop polling
        when the job reaches a terminal state without a second request.
        """
        row_tmpl = templates.env.get_template("_activity_row.html")
        with _conn() as conn:
            rows = activity_for_job(
                conn, job_id, limit=200, since_id=since_id,
                stage_filter=stage_filter,
            )
            job = get_job(conn, job_id)
        html = "".join(row_tmpl.render(a=row) for row in rows)
        max_id = max((int(r["id"]) for r in rows if r.get("id")), default=since_id)
        return {
            "html": html,
            "since_id": max_id,
            "job_state": (job or {}).get("state"),
            "count": len(rows),
        }

    @app.get("/api/runner-status")
    def api_runner_status() -> dict[str, Any]:
        with _conn() as conn:
            return runner_status(conn)

    @app.get("/api/env-health")
    def api_env_health() -> list[dict[str, Any]]:
        with _conn() as conn:
            return env_health_statuses(conn)

    @app.get("/api/config/active-env")
    def api_get_active_env() -> dict[str, Any]:
        with _conn() as conn:
            return {"name": get_active_env(conn)}

    @app.put("/api/config/active-env")
    def api_put_active_env(payload: dict[str, Any]) -> dict[str, Any]:
        name = payload.get("name")
        if name is not None and not isinstance(name, str):
            raise HTTPException(
                status_code=400,
                detail="name must be a string or null",
            )
        # Empty string normalizes to None (clear).
        if isinstance(name, str) and not name.strip():
            name = None
        with _conn() as conn:
            set_active_env(conn, name)
            return {"name": get_active_env(conn)}

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
    def api_bundle_detail(
        bundle_id: str,
        include: str = "",
    ) -> dict[str, Any]:
        """Return the bundle row + its artifacts. With ``include=jobs``
        also attaches the list of jobs that touched this bundle
        (linked via jobs.bundle_dir basename → bundle_id) so the
        analyzer subagent doesn't need a separate list-jobs join."""
        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
            if row is None:
                raise HTTPException(
                    status_code=404, detail=f"Unknown bundle: {bundle_id}",
                )
            includes = {t.strip() for t in include.split(",") if t.strip()}
            if "jobs" in includes:
                row["jobs"] = list_jobs_for_bundle(conn, bundle_id)
        return row

    @app.post("/api/bundles/{bundle_id}/chat")
    def api_bundle_chat(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator Q&A about a completed fix (tools-off).

        Seeds a fresh LLM call with this bundle's **frozen artifacts** —
        the diff, triage, proposed_fix, errors, and the agent's session
        dump — assembled by ``fix_chat.build_chat_messages``, then carries
        the operator's chat turns. The original agent process is gone;
        this "chats with" a fresh model given the record the job produced.
        No tools are passed — pure explanation, nothing re-run or re-read
        from a live tree.

        Body::

            {
              "messages": [{"role":"user"|"assistant","content":str}, ...],
              "session_relpath": "<optional override>"
            }

        ``messages`` is the full client-held chat history ending with the
        operator's newest question; nothing is persisted server-side
        (v1 is ephemeral). Returns ``{ok, reply, session_relpath,
        artifacts_included, session_truncated, usage}``.

        Gated by ``DP_HARNESS_CHAT_MODEL``: 503 when unset.
        """
        cfg = _chat_llm_config()
        if cfg is None:
            raise HTTPException(
                status_code=503,
                detail="chat is disabled: set DP_HARNESS_CHAT_MODEL on the "
                       "tracker process to enable fix-review chat",
            )

        raw = body.get("messages")
        if not isinstance(raw, list) or not raw:
            raise HTTPException(
                status_code=400,
                detail="body must include a non-empty 'messages' list",
            )
        chat_turns: list[dict[str, str]] = []
        for m in raw:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            content = m.get("content")
            if (role in ("user", "assistant")
                    and isinstance(content, str) and content.strip()):
                chat_turns.append({"role": role, "content": content})
        if not chat_turns or chat_turns[-1]["role"] != "user":
            raise HTTPException(
                status_code=400,
                detail="'messages' must be user/assistant turns ending "
                       "with a user turn",
            )

        with _conn() as conn:
            bundle = get_bundle(conn, bundle_id)
        if bundle is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        override = body.get("session_relpath")
        session_relpath = (
            str(override).strip() if override else None
        ) or _pick_default_session_relpath(bundle)
        if session_relpath and not render.is_session_relpath(session_relpath):
            raise HTTPException(
                status_code=400,
                detail=f"not a session artifact: {session_relpath}",
            )
        if not session_relpath and not (bundle.get("artifacts") or []):
            raise HTTPException(
                status_code=404,
                detail="this bundle has no artifacts to chat about",
            )

        # Reader over THIS bundle's artifacts only: the bundle row
        # carries each artifact's ref fields (backend/sha256/fs_path), so
        # we resolve + read from the store with no extra DB round-trips
        # and no path escape (an unknown relpath simply returns None).
        artifacts_by_relpath = {
            str(a.get("relpath")): a
            for a in (bundle.get("artifacts") or [])
            if a.get("relpath")
        }

        def _read_artifact_text(relpath: str) -> str | None:
            ref = artifacts_by_relpath.get(relpath)
            if ref is None:
                return None
            p = render.resolve_artifact_path(app.state.artifact_root, ref)
            if p is None or not p.exists():
                return None
            gz = relpath.endswith(".gz") or ref.get("kind") == "gzip"
            try:
                if gz:
                    import gzip as _gzip  # noqa: PLC0415
                    with _gzip.open(p, "rt", encoding="utf-8",
                                    errors="replace") as fh:
                        return fh.read()
                return p.read_text(errors="replace")
            except OSError:
                return None

        from dportsv3.agent import fix_chat  # noqa: PLC0415
        messages, assembled = fix_chat.build_chat_messages(
            bundle_meta=bundle,
            read_artifact=_read_artifact_text,
            session_relpath=session_relpath,
            chat_turns=chat_turns,
            cap=cfg["context_cap"],
        )

        try:
            from dportsv3.agent import llm  # noqa: PLC0415
            resp = llm.complete(
                messages,
                model=cfg["model"],
                api_base=cfg["api_base"],
                api_key=cfg["api_key"],
                custom_llm_provider=cfg["custom_llm_provider"],
                timeout=cfg["timeout"],
            )
        except Exception as exc:  # noqa: BLE001 — surface as 502
            _LOG.warning(
                "bundle chat: llm.complete failed (bundle=%s): %s",
                bundle_id, exc,
            )
            raise HTTPException(
                status_code=502, detail=f"chat model error: {exc}",
            )

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "session_relpath": session_relpath,
            "artifacts_included": assembled["artifacts_included"],
            "session_truncated": assembled["session_truncated"],
            "reply": resp.text or "",
            # Server-rendered so the panel reuses the same Markdown subset
            # (headings/lists/code/tables) the artifact previews use,
            # rather than shipping a JS renderer. render.render_markdown escapes
            # all content, so this is innerHTML-safe.
            "reply_html": render.render_markdown(resp.text or ""),
            "usage": {
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            },
        }

    @app.post("/api/bundles/{bundle_id}/verification")
    def api_bundle_verification(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Record an independent-verification outcome for a bundle
        (plan Step 11b Slice 2).

        Body shape (validated minimally on purpose — the orchestrator
        in Slice 3 owns the schema):

            {
              "ok": bool,                          # required
              "applied_diff_sha256": "<hex>"|null, # required (forensics)
              "verified_at": "<iso>"|null,         # optional; server fills
              "dsynth_exit": int|null,             # optional, kept for audit
            }

        Updates three columns on bundles: verification_status (set to
        'verified' or 'verification_failed'), verification_at,
        verification_applied_diff_sha256. Emits a bundle_verified
        event so the SSE stream picks it up. Idempotent: re-POSTing
        with a different applied_diff_sha256 overwrites — the column
        records the *last* verification attempt, not a history.

        The dsynth log itself is not stored here — Slice 3 may upload
        it as a bundle artifact (e.g. analysis/verification.log) via
        the artifact-store endpoint independently.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        if "ok" not in body or not isinstance(body["ok"], bool):
            raise HTTPException(
                status_code=400,
                detail="body must include boolean 'ok'",
            )
        if "applied_diff_sha256" not in body:
            raise HTTPException(
                status_code=400,
                detail="body must include 'applied_diff_sha256' "
                       "(null acceptable if no diff was applied)",
            )

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )

        status = "verified" if body["ok"] else "verification_failed"
        verified_at = body.get("verified_at") or (
            datetime.now(timezone.utc).isoformat()
        )
        applied_diff_sha = body.get("applied_diff_sha256")

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            write_conn.execute(
                """UPDATE bundles SET
                       verification_status = ?,
                       verification_at = ?,
                       verification_applied_diff_sha256 = ?,
                       last_seen_at = ?
                   WHERE bundle_id = ?""",
                (status, verified_at, applied_diff_sha,
                 verified_at, bundle_id),
            )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_verified", {
                "bundle_id": bundle_id,
                "verification_status": status,
                "verification_at": verified_at,
                "applied_diff_sha256": applied_diff_sha,
                "dsynth_exit": body.get("dsynth_exit"),
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "verification_status": status,
            "verification_at": verified_at,
            "applied_diff_sha256": applied_diff_sha,
        }

    @app.post("/api/bundles/{bundle_id}/verify")
    def api_bundle_verify(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator-triggered verify (Step 11c). Writes a row to
        ``verify_requests``; the runner's poll loop picks it up,
        calls ``dportsv3.verify_fix.run_verify_fix`` in-process, and
        the result POSTs back to ``/verification`` (Slice 2) when
        done.

        Body: ``{"env": "<dev-env-name>"}``. Operator-chosen env;
        auto-provisioning is a follow-up.

        The tracker doesn't import the runner or touch the queue
        filesystem any more (layer-violation cleanup). The
        ``verify_requests`` table mirrors the
        ``user_context_requests`` pattern: the tracker records
        intent, the runner reconciles.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        env = (body or {}).get("env")
        if not env or not isinstance(env, str):
            raise HTTPException(
                status_code=400,
                detail="body must include 'env' (dev-env name)",
            )
        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        if not fix_state.action_allowed(
            "verify", row.get("resolution"), row.get("verification_status")
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot verify bundle in terminal state "
                    f"{row.get('resolution')!r}"
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            cur = write_conn.execute(
                """INSERT INTO verify_requests
                       (bundle_id, env, requested_by, requested_at, status)
                   VALUES (?, ?, 'operator', ?, 'pending')""",
                (bundle_id, env, now),
            )
            request_id = cur.lastrowid
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "verify_requested", {
                "bundle_id": bundle_id,
                "request_id": request_id,
                "env": env,
                "requested_at": now,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "request_id": request_id,
            "status": "pending",
            "env": env,
        }

