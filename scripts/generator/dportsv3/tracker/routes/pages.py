"""HTML page routes + manual-request and progress JSON endpoints."""

from __future__ import annotations

from dportsv3.tracker.routes._common import *  # noqa: F401,F403
from dportsv3.tracker.routes._common import (  # noqa: F401
    _LOG, _chat_llm_config, _pick_default_session_relpath,
)


def register(app, ctx):
    _conn = ctx.conn
    _raise_http_error = ctx.raise_http_error
    templates = ctx.templates


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
                    "active_env": get_active_env(conn),
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
            selected_relpath = artifact or (render.default_artifact_relpath(bundle) if bundle else None)
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
            # Step 20f / Step 11c layer-violation cleanup: the dops
            # state is now persisted to bundles.dops_state at triage
            # time by the runner (which has chroot access). The
            # tracker no longer reaches into the host filesystem to
            # compute it live. NULL on legacy rows where no triage
            # ran post-this-change — the template hides the pill in
            # that case.
            dops_state = bundle.get("dops_state") if bundle is not None else None
            # Step 11d-2: most-recent delivery attempt for the Delivery
            # card. None on bundles that haven't been delivered yet
            # (the template hides the card in that case).
            delivery_request = latest_review_request_for_bundle(
                conn, bundle_id,
            )
            # Visibility plan: tracker-side activity rows
            # (bundle_accepted, delivery_complete) live with
            # bundle_id set but job_id=NULL. Surface them on the
            # bundle page directly so accept-and-deliver outcomes
            # are visible without dropping out to the CLI.
            bundle_activity = recent_activity_for_bundle(
                conn, bundle_id, limit=20,
            )
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        if selected_relpath and selected_ref is None:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        selected_artifact = (
            render.artifact_view_data(app.state.artifact_root, bundle_id, selected_relpath, selected_ref)
            if selected_relpath and selected_ref else None
        )
        if selected_relpath and selected_artifact is None:
            raise HTTPException(status_code=404, detail="Artifact file missing")
        tool_trace = render.load_tool_trace(app.state.artifact_root, tool_trace_ref)
        # Operator-action surface: which buttons this page shows/enables.
        # The policy (and the authoritative endpoint gate) lives in
        # fix_state — one place, tested, instead of the former inline
        # matrix. See that module for the allowed-vs-surface split.
        acts = fix_state.bundle_actions(bundle)
        # Env picker for the Verify button — a live DB read, so it stays
        # here rather than in the pure policy. Populate only when Verify
        # is eligible; default-select the active env, falling back to the
        # first known env when the active one is cleared/decommissioned.
        verify_envs: list[str] = []
        verify_default_env: str | None = None
        if acts["can_verify"]:
            with _conn() as _envs_conn:
                verify_envs = [
                    str(r.get("env"))
                    for r in env_health_statuses(_envs_conn)
                    if r.get("env")
                ]
                verify_default_env = get_active_env(_envs_conn)
            if (
                verify_default_env is not None
                and verify_default_env not in verify_envs
            ):
                verify_default_env = verify_envs[0] if verify_envs else None
            elif verify_default_env is None and verify_envs:
                verify_default_env = verify_envs[0]

        operator_actions = {
            **acts,
            "verify_envs": verify_envs,
            "verify_default_env": verify_default_env,
        }
        # Fix-review chat: only offer the panel when the tracker has a
        # chat model configured (DP_HARNESS_CHAT_MODEL) AND this bundle
        # carries a session dump to seed it. Both must hold or the panel
        # is hidden — no dead UI.
        chat_session_relpath = _pick_default_session_relpath(bundle)
        chat_enabled = (
            _chat_llm_config() is not None and chat_session_relpath is not None
        )
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
                "operator_actions": operator_actions,
                "delivery_request": delivery_request,
                "bundle_activity": bundle_activity,
                "chat_enabled": chat_enabled,
                "chat_session_relpath": chat_session_relpath,
            },
        )

    @app.get("/agentic/bundles/{bundle_id}/artifacts/{relpath:path}", response_class=HTMLResponse)
    def agentic_bundle_artifact_view(
        request: RequestType,
        bundle_id: str,
        relpath: str,
    ) -> Any:
        # Session dumps under analysis/sessions/ get the structured
        # viewer instead of the default text/octet-stream renderer.
        # Redirect rather than re-route so the canonical URL for a
        # session is /sessions/<filename>, not /artifacts/...jsonl.gz.
        if render.is_session_relpath(relpath):
            filename = Path(relpath).name
            return RedirectResponse(
                url=str(request.url_for(
                    "agentic_bundle_session_view",
                    bundle_id=bundle_id,
                    filename=filename,
                )),
                status_code=302,
            )
        with _conn() as conn:
            bundle = get_bundle(conn, bundle_id)
            ref = get_artifact_ref(conn, bundle_id, relpath)
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        if ref is None:
            raise HTTPException(status_code=404, detail="Unknown artifact")
        artifact = render.artifact_view_data(app.state.artifact_root, bundle_id, relpath, ref)
        if artifact is None:
            raise HTTPException(status_code=404, detail="Artifact file missing")
        return templates.TemplateResponse(
            request,
            "agentic_artifact.html",
            {"title": relpath, "bundle": bundle, "artifact": artifact},
        )

    @app.get(
        "/agentic/bundles/{bundle_id}/sessions/{filename}",
        response_class=HTMLResponse,
        name="agentic_bundle_session_view",
    )
    def agentic_bundle_session_view(
        request: RequestType,
        bundle_id: str,
        filename: str,
    ) -> Any:
        """Structured per-turn viewer for analysis/sessions/*.jsonl[.gz]
        — replaces the gzip-octet-stream download with a per-message
        rendering: collapsible system + user prompts (with section
        breakdown), chronological assistant-turn cards with
        reasoning_content + tool_calls + tool results, and a right-rail
        TOC. The relpath is always under analysis/sessions/ — we accept
        only the filename in the URL to keep links short."""
        relpath = f"analysis/sessions/{filename}"
        with _conn() as conn:
            bundle = get_bundle(conn, bundle_id)
            ref = get_artifact_ref(conn, bundle_id, relpath)
            # tool_trace.jsonl is what carries per-turn token counts;
            # join the session's assistant turns to its llm_turn events.
            tool_trace_ref = get_artifact_ref(
                conn, bundle_id, "analysis/tool_trace.jsonl",
            )
        if bundle is None:
            raise HTTPException(status_code=404, detail=f"Unknown bundle: {bundle_id}")
        if ref is None:
            raise HTTPException(status_code=404, detail="Unknown session artifact")
        session = render.session_view_data(
            app.state.artifact_root, bundle_id, relpath, ref,
            tool_trace_ref=tool_trace_ref,
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session file missing")
        return templates.TemplateResponse(
            request,
            "agentic_session.html",
            {"title": filename, "bundle": bundle, "session": session},
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
                        handoff = render.artifact_view_data(
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
        # Activity rows stay newest-first (the query default) so the
        # autorefresh JS — which prepends new rows to the top — keeps
        # the table consistent. Mixing ASC initial + prepended new
        # rows produced a chaotic sort (gperf/liblz4 2026-05-26):
        # live rows piled up at the top above an ASC-sorted body.
        # Cursor for the live-refresh polling — the client polls
        # /api/activity?job_id=X&since_id=N for new rows.
        max_id = max((a.get("id") or 0) for a in activity) if activity else 0
        # Whether the job is still doing its own work — drives the
        # live-poll indicator. Computed server-side from the single
        # canonical set (lifecycle.ACTIVE_WORK_STATES) rather than an
        # inline template literal, so it can't drift from the runner's
        # retriage guard and the dashboard count. Notably: a `triaged`
        # job is NOT active (it handed off to a spawned patch/convert
        # job and rests there), and `verifying_fix` IS active.
        from dportsv3.agent.lifecycle import (  # noqa: PLC0415
            ACTIVE_WORK_STATE_VALUES,
        )
        job_is_active = job.get("state") in ACTIVE_WORK_STATE_VALUES
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
                "job_is_active": job_is_active,
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
                    handoff = render.artifact_view_data(
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
            operator = (payload.operator or "").strip() or None
            new_rev = upsert_user_context_text(
                conn, run_id, origin, text, submitted_by=operator,
            )
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
