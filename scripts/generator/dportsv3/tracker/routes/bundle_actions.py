"""Bundle operator-action routes (accept/reject/take-over/discard/...) + reads."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from dportsv3.tracker import (
    fix_state,
    render,
)
from dportsv3.tracker.agentic_queries import (
    clear_origin_skip,
    events_since,
    get_artifact_ref,
    get_bundle,
    is_origin_skipped,
    latest_review_request_for_bundle,
    list_port_bundles,
    set_origin_skip,
    update_review_request_status,
    upsert_user_context_text,
)
from dportsv3.tracker.routes._common import (
    FileResponse,
    HTTPException,
    Query,
    StreamingResponse,
    _LOG,
)


def register(app, ctx):
    _conn = ctx.conn

    def _activity_log(
        write_conn: sqlite3.Connection,
        stage: str,
        message: str,
        extra: dict[str, Any] | None = None,
        bundle_id: str | None = None,
    ) -> None:
        """Tracker-side activity_log writer. Mirrors the runner's
        ``activity_log()`` helper but uses the connection the caller
        already holds (the runner's is bound to a module global).

        Visibility plan: every accept and every delivery outcome
        emits one row so operators can see what happened on the
        bundle detail page's activity ribbon AND via
        ``dportsv3 tracker get-activity``. Pairs with a daemon-log
        line for tail-the-log workflows. ``bundle_id`` lands in
        its own column so the bundle page can render
        bundle-scoped rows without parsing extra_json.
        """
        from datetime import datetime, timezone  # noqa: PLC0415
        ts = datetime.now(timezone.utc).isoformat()
        try:
            write_conn.execute(
                """INSERT INTO activity_log
                       (ts, job_id, bundle_id, stage, message,
                        duration_ms, extra_json)
                       VALUES (?, NULL, ?, ?, ?, NULL, ?)""",
                (ts, bundle_id, stage, message,
                 json.dumps(extra) if extra is not None else None),
            )
        except sqlite3.Error as exc:
            # Activity logging is best-effort — never break an
            # accept because the activity table couldn't take a row.
            _LOG.warning("activity_log insert failed: %s", exc)

    def _accept_delivery_step(
        *,
        bundle: dict[str, Any],
        request_body: dict[str, Any],
        write_conn: sqlite3.Connection,
    ) -> dict[str, Any]:
        """Step 11d-2: optional delivery side-effect on Accept.

        Always returns a dict carrying the outcome for the accept
        response. ``status`` is one of:
          - ``created`` / ``updated`` — provider succeeded.
          - ``create_failed`` — provider raised; a create_failed row
            is also written for operator visibility.
          - ``skipped`` — delivery wasn't attempted; ``skip_reason``
            names which gate fired (``operator_optout``,
            ``no_config``, ``no_changes_diff``,
            ``changes_diff_empty``, ``changes_diff_unreadable``).

        Step 30 slice 5: reads ``analysis/changes.diff`` — now the
        single canonical diff (branch-vs-base, includes convert
        commits). Pre-slice-5 bundles had a HEAD-relative
        changes.diff that silently lost convert work on converted
        ports; the slice-5 cutover makes changes.diff the
        full-chain artifact.

        The bundle accept itself never depends on delivery — any
        exception in here writes a create_failed row and returns a
        descriptive dict but does not propagate.
        """
        if request_body.get("deliver") is False:
            return {"status": "skipped", "skip_reason": "operator_optout"}

        from dportsv3.delivery.orchestrator import (  # noqa: PLC0415
            resolve_config, deliver, DeliveryOutcome,
        )
        from dportsv3.delivery import DeliveryConfigError  # noqa: PLC0415

        try:
            cfg = resolve_config(target=bundle.get("target") or None)
        except DeliveryConfigError as exc:
            # Config exists but is malformed — surface as a delivery
            # error rather than silently skipping.
            return {
                "status": "create_failed",
                "error": f"DeliveryConfigError: {exc}",
            }
        if cfg is None:
            return {"status": "skipped", "skip_reason": "no_config"}

        bundle_id = bundle.get("bundle_id") or ""
        diff_ref = get_artifact_ref(
            write_conn, bundle_id, "analysis/changes.diff",
        )
        if diff_ref is None:
            return {
                "status": "skipped",
                "skip_reason": "no_changes_diff",
            }
        diff_path = render.resolve_artifact_path(app.state.artifact_root, diff_ref)
        if diff_path is None or not diff_path.is_file():
            return {
                "status": "skipped",
                "skip_reason": "changes_diff_unreadable",
            }
        try:
            diff_text = diff_path.read_text()
        except OSError as exc:
            return {
                "status": "create_failed",
                "error": f"changes.diff read failed: {exc}",
            }
        if not diff_text.strip():
            return {
                "status": "skipped",
                "skip_reason": "changes_diff_empty",
            }

        operator = (
            (request_body.get("operator") or "operator").strip()
            or "operator"
        )

        # Read artifact text best-effort for the PR body. triage.md
        # supplies the Problem section (Root Cause / Evidence /
        # classification); patch.md supplies the Fix rationale;
        # patch_audit.json supplies model / attempts / tokens.
        def _read_artifact_text(relpath: str) -> str | None:
            ref = get_artifact_ref(write_conn, bundle_id, relpath)
            if ref is None:
                return None
            path = render.resolve_artifact_path(app.state.artifact_root, ref)
            if path is None or not path.is_file():
                return None
            try:
                return path.read_text()
            except OSError:
                return None

        model = attempts = tokens = None
        audit_text = _read_artifact_text("analysis/patch_audit.json")
        if audit_text:
            try:
                import json as _json  # noqa: PLC0415
                audit_data = _json.loads(audit_text)
                model = audit_data.get("model")
                raw_attempts = audit_data.get("attempts")
                if isinstance(raw_attempts, list):
                    attempts = len(raw_attempts)
                tu = audit_data.get("tokens_used") or {}
                tokens = tu.get("total") if isinstance(tu, dict) else None
            except Exception:
                pass

        triage_md = _read_artifact_text("analysis/triage.md")
        patch_md = _read_artifact_text("analysis/patch.md")

        try:
            outcome: DeliveryOutcome = deliver(
                bundle=bundle,
                diff_text=diff_text,
                cfg=cfg,
                operator=operator,
                triage_md=triage_md,
                patch_md=patch_md,
                model=model, attempts=attempts, tokens=tokens,
                write_conn=write_conn,
            )
        except Exception as exc:
            # The orchestrator already catches provider failures and
            # writes a create_failed row. Any exception that escapes
            # here is a bug in orchestrator.deliver itself; surface
            # without crashing the accept.
            return {
                "status": "create_failed",
                "error": f"deliver() raised: {type(exc).__name__}: {exc}",
            }

        from dportsv3.artifact_store import emit_event  # noqa: PLC0415
        emit_event(write_conn, "bundle_delivered", {
            "bundle_id": bundle_id,
            "provider": outcome.provider,
            "status": outcome.status,
            "url": outcome.url,
            "branch": outcome.branch,
            "operator": operator,
        })
        result: dict[str, Any] = {
            "status": outcome.status,
            "provider": outcome.provider,
        }
        if outcome.url:
            result["url"] = outcome.url
        if outcome.provider_pr_id:
            result["provider_pr_id"] = outcome.provider_pr_id
        if outcome.branch:
            result["branch"] = outcome.branch
        if outcome.error:
            result["error"] = outcome.error
        return result

    @app.post("/api/bundles/{bundle_id}/accept")
    def api_bundle_accept(
        bundle_id: str, body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Operator accept (Step 11c + 28e follow-up).

        Gated on ``verification_status='verified'`` (409 otherwise).
        Sets ``resolution='accepted'`` + ``accepted_at``; emits a
        ``bundle_accepted`` event.

        Step 28e follow-up: when the prior resolution was
        ``operator_owned`` (operator's manual fix being accepted),
        also release the ``origin_skip_flags`` row if this bundle
        owns it. Without this the lock would stay forever — accept
        is terminal, and ``/reopen`` only clears locks on reopen-
        from-discarded. Mirrors release/reopen's own/sibling
        semantics. The ``skip_action`` field on the response and
        the event payload makes the lock disposition observable.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        if not fix_state.action_allowed(
            "accept", row.get("resolution"), row.get("verification_status")
        ):
            # Gate denied — pick the specific reason for the message.
            if row.get("resolution") in fix_state.TERMINAL_RESOLUTIONS:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot accept bundle in terminal state "
                        f"{row.get('resolution')!r}"
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    "Accept requires verification_status='verified'; "
                    f"current: {row.get('verification_status') or 'unverified'!r}"
                ),
            )

        prior_resolution = row.get("resolution")
        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        skip_action = "none"
        try:
            write_conn.execute(
                """UPDATE bundles SET
                       resolution = 'accepted',
                       accepted_at = ?,
                       pre_terminal_resolution = ?,
                       last_seen_at = ?
                   WHERE bundle_id = ?""",
                (now, prior_resolution, now, bundle_id),
            )
            # 28e follow-up: only the operator_owned → accepted
            # path interacts with the skip lock. agent_fixed accepts
            # never opened a lock, so there's nothing to clear.
            if prior_resolution == "operator_owned" and target and origin:
                existing = is_origin_skipped(write_conn, target, origin)
                if existing is not None:
                    if existing.get("bundle_id") == bundle_id:
                        clear_origin_skip(
                            write_conn,
                            target=target, origin=origin,
                            cleared_by="operator-accept",
                        )
                        skip_action = "cleared"
                    else:
                        skip_action = (
                            f"left_intact_owned_by:"
                            f"{existing.get('bundle_id')}"
                        )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_accepted", {
                "bundle_id": bundle_id,
                "accepted_at": now,
                "prior_resolution": prior_resolution,
                "skip_action": skip_action,
                "note": (body or {}).get("note"),
            })

            # Visibility plan: activity row + daemon log for every
            # accept. Pairs with the delivery_complete row below so
            # operators see the full accept-then-deliver story on
            # the bundle's activity ribbon and `get-activity` output.
            accept_msg = (
                f"bundle {bundle_id} accepted (prior_resolution="
                f"{prior_resolution!r}, skip_action={skip_action!r})"
            )
            _activity_log(
                write_conn, "bundle_accepted", accept_msg,
                bundle_id=bundle_id,
                extra={
                    "bundle_id": bundle_id,
                    "prior_resolution": prior_resolution,
                    "skip_action": skip_action,
                    "accepted_at": now,
                },
            )
            _LOG.info(accept_msg)

            # Step 11d-2: Accept-with-delivery. Best-effort — any
            # exception path here writes a create_failed row and
            # logs but the bundle stays accepted. Skipped silently
            # when delivery.toml isn't configured.
            delivery = _accept_delivery_step(
                bundle=row,
                request_body=(body or {}),
                write_conn=write_conn,
            )

            # Visibility plan: one activity row + one daemon log
            # line per delivery outcome, regardless of status. The
            # prior shape only wrote a bundle_review_requests row
            # for provider-stage outcomes (created / updated /
            # provider-raised create_failed); skips and pre-provider
            # config errors went into a black hole. This row covers
            # all of them in one stage so a tail / grep / activity-
            # table render shows the full story.
            d_status = (delivery or {}).get("status", "unknown")
            d_skip = (delivery or {}).get("skip_reason")
            d_url = (delivery or {}).get("url")
            d_err = (delivery or {}).get("error")
            d_provider = (delivery or {}).get("provider")
            d_request = (delivery or {}).get("request_id")
            if d_status == "skipped":
                d_msg = (
                    f"delivery skipped for {bundle_id}: "
                    f"{d_skip or 'unknown'}"
                )
                _LOG.info(d_msg)
            elif d_status in ("created", "updated"):
                d_msg = (
                    f"delivery {d_status} for {bundle_id} "
                    f"(provider={d_provider}, url={d_url or 'n/a'})"
                )
                _LOG.info(d_msg)
            elif d_status == "create_failed":
                d_msg = (
                    f"delivery FAILED for {bundle_id}: "
                    f"{d_err or 'unspecified error'}"
                )
                _LOG.error(d_msg)
            else:
                d_msg = (
                    f"delivery completed for {bundle_id} with "
                    f"unrecognized status={d_status!r}"
                )
                _LOG.warning(d_msg)
            _activity_log(
                write_conn, "delivery_complete", d_msg,
                bundle_id=bundle_id,
                extra={
                    "bundle_id": bundle_id,
                    "status": d_status,
                    "skip_reason": d_skip,
                    "provider": d_provider,
                    "url": d_url,
                    "request_id": d_request,
                    "error": d_err,
                },
            )
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "accepted",
            "accepted_at": now,
            "prior_resolution": prior_resolution,
            "skip_action": skip_action,
            # _accept_delivery_step always returns a dict (skipped /
            # created / updated / create_failed), so `delivery` is
            # always present on the response.
            "delivery": delivery,
        }

    @app.post("/api/bundles/{bundle_id}/reject")
    def api_bundle_reject(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator reject (Step 11c). Terminal "this fix is wrong,
        stop" action: sets resolution='rejected' + rejected_at +
        rejection_reason and stops. The reason is recorded for audit
        only — reject does NOT re-triage. An operator who wants the
        loop to try again with feedback uses /retry instead (allowed
        on agent_fixed), which plants the feedback as user_context
        and re-triages.

        Body: ``{"reason": "<text>"}``. Reason is required (an
        unexplained reject is uninformative)."""
        from datetime import datetime, timezone  # noqa: PLC0415

        reason = (body or {}).get("reason")
        if not reason or not isinstance(reason, str):
            raise HTTPException(
                status_code=400,
                detail="body must include 'reason' (rejection text)",
            )
        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        if not fix_state.action_allowed(
            "reject", row.get("resolution"), row.get("verification_status")
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot reject bundle in terminal state "
                    f"{row.get('resolution')!r}"
                ),
            )

        prior_resolution = row.get("resolution")
        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            write_conn.execute(
                """UPDATE bundles SET
                       resolution = 'rejected',
                       rejected_at = ?,
                       rejection_reason = ?,
                       pre_terminal_resolution = ?,
                       last_seen_at = ?
                   WHERE bundle_id = ?""",
                (now, reason, prior_resolution, now, bundle_id),
            )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_rejected", {
                "bundle_id": bundle_id,
                "rejected_at": now,
                "reason": reason,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "rejected",
            "rejected_at": now,
            "reason": reason,
        }

    @app.post("/api/bundles/{bundle_id}/take-over")
    def api_bundle_take_over(
        bundle_id: str, body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Operator take-over of a failed bundle (Step 28a).

        Stakes the (target, origin) pair so the runner stops competing
        with the operator's manual work. Sets bundle.resolution to
        ``operator_owned`` (non-terminal — Step 11c's Verify/Accept
        path can still fire from this state) and opens a row in
        ``origin_skip_flags`` so subsequent dsynth hooks for the same
        pair produce a tombstone bundle instead of fresh triage.

        Allowed only from failure resolutions
        (``agent_budget_exhausted`` / ``agent_gave_up`` /
        ``escalated_manual``). 409 from already-terminal accept/reject
        or already-operator_owned. 404 if bundle unknown.

        Body (all optional):
          - ``operator``: freeform identifier (defaults to "operator");
            integrating with the auth model is Step 17 territory.
          - ``reason``: short note describing the take-over context;
            defaults to a generic label.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        operator = ((body or {}).get("operator") or "operator").strip()
        reason = ((body or {}).get("reason") or "operator take-over").strip()
        if not operator:
            operator = "operator"
        if not reason:
            reason = "operator take-over"

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        # Take-over is meaningful only on failure-shaped (or fresh)
        # bundles — success-shaped ones use the Accept/Reject surface.
        current_resolution = row.get("resolution")
        if not fix_state.action_allowed(
            "take-over", current_resolution, row.get("verification_status")
        ):
            # Gate denied — specific message per reason.
            if current_resolution in fix_state.TERMINAL_RESOLUTIONS:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot take over bundle in terminal state "
                        f"{current_resolution!r}"
                    ),
                )
            if current_resolution == "operator_owned":
                raise HTTPException(
                    status_code=409,
                    detail="Bundle is already operator_owned",
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Take-over is for failure-shaped bundles; "
                    f"current resolution is {current_resolution!r}"
                ),
            )

        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        if not target or not origin:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Bundle is missing target/origin metadata; cannot "
                    "open a skip lock for the (target, origin) pair"
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            # Pre-check the open-lock invariant against a different
            # bundle for the same (target, origin) pair — could happen
            # if the operator already took over a sibling bundle.
            existing = is_origin_skipped(write_conn, target, origin)
            if existing is not None and existing.get("bundle_id") != bundle_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"(target={target!r}, origin={origin!r}) is "
                        f"already locked by bundle "
                        f"{existing.get('bundle_id')!r}; un-skip it first"
                    ),
                )
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                write_conn.execute(
                    """UPDATE bundles SET
                           resolution = 'operator_owned',
                           taken_over_at = ?,
                           taken_over_by = ?,
                           last_seen_at = ?
                       WHERE bundle_id = ?""",
                    (now, operator, now, bundle_id),
                )
                if existing is None:
                    try:
                        set_origin_skip(
                            write_conn,
                            target=target, origin=origin,
                            set_by=operator,
                            reason=reason,
                            bundle_id=bundle_id,
                        )
                    except sqlite3.IntegrityError:
                        # Race window: another operator opened the lock
                        # between our pre-check and this INSERT. The
                        # partial-unique index correctly rejected the
                        # duplicate; convert to 409 so the caller gets
                        # the same answer as the pre-check would have.
                        write_conn.execute("ROLLBACK")
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"(target={target!r}, origin={origin!r}) "
                                f"was just locked by a concurrent operator "
                                f"action; refresh and try again"
                            ),
                        )
                write_conn.execute("COMMIT")
            except HTTPException:
                raise
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_taken_over", {
                "bundle_id": bundle_id,
                "target": target,
                "origin": origin,
                "taken_over_at": now,
                "taken_over_by": operator,
                "reason": reason,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "operator_owned",
            "taken_over_at": now,
            "taken_over_by": operator,
            "target": target,
            "origin": origin,
        }

    @app.post("/api/bundles/{bundle_id}/discard")
    def api_bundle_discard(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator discard of a failed (or operator-owned) bundle
        (Step 28b).

        Marks the bundle resolved as ``discarded`` (terminal) and,
        when ``skip_origin`` is true (the default), opens a row in
        ``origin_skip_flags`` so the runner stops auto-processing
        the (target, origin) pair until an operator un-skips it.

        Allowed from failure-shaped resolutions
        (``agent_budget_exhausted`` / ``agent_gave_up`` /
        ``escalated_manual`` / ``convert_gave_up``) and from
        ``operator_owned`` (operator gave a manual fix a try, then
        decided to drop it). 409 from already-terminal resolutions
        (``accepted`` / ``rejected`` / ``discarded``) and from
        ``agent_fixed`` (success-side bundles route through Step
        11c's Reject). 404 if bundle unknown.

        Body (``reason`` required; an unexplained discard is
        uninformative forensics):
          - ``reason`` (str, required): why the operator is
            walking away from this bundle.
          - ``skip_origin`` (bool, default true): when true, opens
            a per-``(target, origin)`` lock alongside the discard.
            Pass false for "discard just this bundle; let the loop
            try again on the next dsynth hook."
          - ``operator`` (str, optional): freeform identifier;
            defaults to ``"operator"``. Step 17 territory for real
            auth.

        If a sibling bundle already locked the (target, origin)
        pair, the discard still succeeds — the lock is shared
        forensics across the discard / take-over paths, and the
        existing lock keeps its set_by / bundle_id provenance.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        reason = (body or {}).get("reason")
        if not reason or not isinstance(reason, str) or not reason.strip():
            raise HTTPException(
                status_code=400,
                detail="body must include 'reason' (discard text)",
            )
        reason = reason.strip()
        operator = ((body or {}).get("operator") or "operator").strip()
        if not operator:
            operator = "operator"
        skip_origin = bool((body or {}).get("skip_origin", True))

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        current_resolution = row.get("resolution")
        if not fix_state.action_allowed(
            "discard", current_resolution, row.get("verification_status")
        ):
            # Gate denied — specific message per reason.
            if current_resolution in fix_state.TERMINAL_RESOLUTIONS:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Cannot discard bundle in terminal state "
                        f"{current_resolution!r}"
                    ),
                )
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Discard is for failure-shaped or operator-owned "
                    f"bundles; current resolution is {current_resolution!r}"
                ),
            )

        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        if skip_origin and (not target or not origin):
            raise HTTPException(
                status_code=409,
                detail=(
                    "Bundle is missing target/origin metadata; cannot "
                    "open a skip lock. Re-issue with skip_origin=false "
                    "to discard the bundle without locking."
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        skip_action = "none"
        try:
            existing = (
                is_origin_skipped(write_conn, target, origin)
                if skip_origin and target and origin else None
            )
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                write_conn.execute(
                    """UPDATE bundles SET
                           resolution = 'discarded',
                           discarded_at = ?,
                           discard_reason = ?,
                           pre_terminal_resolution = ?,
                           last_seen_at = ?
                       WHERE bundle_id = ?""",
                    (now, reason, current_resolution, now, bundle_id),
                )
                if skip_origin and target and origin and existing is None:
                    try:
                        set_origin_skip(
                            write_conn,
                            target=target, origin=origin,
                            set_by=operator,
                            reason=f"discard: {reason}",
                            bundle_id=bundle_id,
                        )
                        skip_action = "opened"
                    except sqlite3.IntegrityError:
                        # Race window: a concurrent operator action
                        # opened the lock between our pre-check and
                        # this INSERT. The discard itself still lands
                        # (the bundle is being walked away from
                        # regardless of who locks the origin), but
                        # report skip_action so the operator sees
                        # what happened.
                        skip_action = "race_lost_to_concurrent_lock"
                elif skip_origin and existing is not None:
                    # Sibling already locked the pair; don't duplicate.
                    # The discard still lands; the existing lock keeps
                    # its provenance. Operator visibility comes from
                    # the bundle_discarded event + the existing skip
                    # flag (which is the same forensic record either
                    # take-over or discard would have written).
                    skip_action = (
                        f"already_locked_by:{existing.get('bundle_id')}"
                    )
                write_conn.execute("COMMIT")
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_discarded", {
                "bundle_id": bundle_id,
                "target": target,
                "origin": origin,
                "discarded_at": now,
                "discarded_by": operator,
                "reason": reason,
                "skip_origin": skip_origin,
                "skip_action": skip_action,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "discarded",
            "discarded_at": now,
            "discard_reason": reason,
            "target": target,
            "origin": origin,
            "skip_origin": skip_origin,
            "skip_action": skip_action,
        }

    @app.post("/api/bundles/{bundle_id}/retry")
    def api_bundle_retry(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator retry-with-context on a failed (or operator-owned)
        bundle (Step 28c).

        Plants the same DB rows the manual-queue context-submit path
        plants (``user_context`` text + ``user_context_requests``
        pending row) so the runner's existing
        ``process_user_context_updates`` poll picks the retry up
        without any runner-side changes. The bundle's resolution
        moves to ``retry_requested`` (transient) until the runner
        enqueues the new triage.

        Allowed from failure-shaped resolutions
        (``agent_budget_exhausted`` / ``agent_gave_up`` /
        ``escalated_manual`` / ``convert_gave_up``), from
        ``operator_owned`` (operator gives up on the manual attempt
        and hands back to the loop with context), and from
        ``agent_fixed`` — the "this fix is wrong, try again with my
        feedback" path for a verified-but-rejected fix. (Reject is
        the separate *terminal* "this fix is wrong, stop" action; it
        records a reason but does NOT re-triage.) 409 only from the
        already-terminal states accept / reject / discarded.

        Body:
          - ``context`` (str, required, ≤ 8000 chars): operator's
            note that will land in the next triage's payload via
            the existing ``## User Context`` section.
          - ``operator`` (str, optional): freeform identifier.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        text = ((body or {}).get("context") or "")
        if not isinstance(text, str):
            raise HTTPException(
                status_code=400,
                detail="body 'context' must be a string",
            )
        text = text.strip()
        if not text:
            raise HTTPException(
                status_code=400,
                detail="body 'context' is required (non-empty)",
            )
        if len(text) > 8000:
            raise HTTPException(
                status_code=400,
                detail="body 'context' too long (max 8000 chars)",
            )
        # Two distinct roles for the body's ``operator`` field:
        #   - ``submitted_by`` on the new user_context_history row: NULL
        #     when the operator didn't identify themselves, matching
        #     /api/manual-requests/.../context's NULL-on-empty behavior.
        #   - ``requested_by`` on the response/event payload: keeps the
        #     pre-29b "operator" literal fallback so existing event
        #     consumers see a non-empty string.
        raw_operator = ((body or {}).get("operator") or "").strip()
        submitted_by = raw_operator or None
        operator = raw_operator or "operator"

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        current_resolution = row.get("resolution")
        if not fix_state.action_allowed(
            "retry", current_resolution, row.get("verification_status")
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Cannot retry bundle in terminal state "
                    f"{current_resolution!r}"
                ),
            )
        run_id = (row.get("run_id") or "").strip()
        origin = (row.get("origin") or "").strip()
        if not run_id or not origin:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Bundle is missing run_id/origin metadata; cannot "
                    "plant a retry request"
                ),
            )

        now = datetime.now(timezone.utc).isoformat()
        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            # Atomicity note: BEGIN IMMEDIATE doesn't work cleanly
            # here because upsert_user_context_text commits internally
            # (it's a shared helper used by other paths). Mirroring
            # 11c's accept/reject pattern, the three writes are
            # sequential under autocommit. Partial-failure tradeoff
            # is acceptable: a user_context row that landed without
            # a matching UCR row gets picked up by the next operator
            # /retry call; a UCR row without a bundle resolution
            # update degrades the UI badge but not correctness — the
            # runner's poll loop still acts on the UCR row.
            new_rev = upsert_user_context_text(
                write_conn, run_id, origin, text,
                submitted_by=submitted_by,
            )
            # Default iteration to 1 — operator-driven retry is an
            # explicit override of the automated retry-cap logic.
            # max_iterations defaults to the runner's standard
            # (matches enqueue_triage_job's caller convention).
            ucr_row = write_conn.execute(
                """SELECT 1 FROM user_context_requests
                   WHERE run_id = ? AND origin = ? AND bundle_id = ?""",
                (run_id, origin, bundle_id),
            ).fetchone()
            if ucr_row:
                write_conn.execute(
                    """UPDATE user_context_requests
                       SET requested_at = ?, status = 'pending',
                           iteration = ?, max_iterations = ?
                       WHERE run_id = ? AND origin = ? AND bundle_id = ?""",
                    (now, 1, 3, run_id, origin, bundle_id),
                )
            else:
                write_conn.execute(
                    """INSERT INTO user_context_requests
                       (run_id, origin, bundle_id, classification,
                        confidence, iteration, max_iterations,
                        requested_at, status,
                        last_context_rev_handled)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?,
                               'pending', ?)""",
                    (run_id, origin, bundle_id,
                     row.get("classification") or "",
                     row.get("confidence") or "",
                     1, 3, now, max(0, new_rev - 1)),
                )
            # Move bundle resolution to retry_requested (transient).
            # The runner's enqueue path (process_user_context_updates)
            # clears this back to NULL when it actually plants the
            # new triage job, so a stuck retry_requested is observable.
            write_conn.execute(
                """UPDATE bundles SET
                       resolution = 'retry_requested',
                       last_seen_at = ?
                   WHERE bundle_id = ?""",
                (now, bundle_id),
            )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_retry_requested", {
                "bundle_id": bundle_id,
                "run_id": run_id,
                "origin": origin,
                "requested_at": now,
                "requested_by": operator,
                "context_rev": new_rev,
                "context_chars": len(text),
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": "retry_requested",
            "run_id": run_id,
            "origin": origin,
            "context_rev": new_rev,
            "requested_at": now,
            "requested_by": operator,
        }

    @app.post("/api/bundles/{bundle_id}/release")
    def api_bundle_release(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator release of an ``operator_owned`` bundle (Step 28e).

        The "Hand back to the loop" action: the operator stops
        staking the bundle's (target, origin) pair, without
        terminalizing it via Discard. Bundle moves back to a NULL
        resolution (re-actionable); the skip lock is released iff
        this bundle owns it.

        Allowed only from ``operator_owned``. 409 from any other
        resolution. 404 unknown bundle.

        Body:
          - ``reason`` (str, required): why the stake is being
            released. Audit trail.
          - ``operator`` (str, optional): freeform identifier.

        Side effects:
          - ``resolution`` → NULL. ``taken_over_at`` and
            ``taken_over_by`` are preserved as historical record;
            only the live resolution clears.
          - If this bundle owns the open ``origin_skip_flags`` row
            for its (target, origin), the lock is cleared. If a
            sibling owns it, it's left intact — that's the
            sibling's stake to release.
          - Emits ``bundle_released`` event.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        reason = (body or {}).get("reason")
        if not reason or not isinstance(reason, str) or not reason.strip():
            raise HTTPException(
                status_code=400,
                detail="body must include 'reason' (release text)",
            )
        reason = reason.strip()
        operator = ((body or {}).get("operator") or "operator").strip()
        if not operator:
            operator = "operator"

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        if not fix_state.action_allowed(
            "release", row.get("resolution"), row.get("verification_status")
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Release requires resolution='operator_owned'; "
                    f"current is {row.get('resolution')!r}"
                ),
            )

        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        skip_action = "none"
        try:
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                write_conn.execute(
                    """UPDATE bundles SET
                           resolution = NULL,
                           last_seen_at = ?
                       WHERE bundle_id = ?""",
                    (now, bundle_id),
                )
                if target and origin:
                    existing = is_origin_skipped(write_conn, target, origin)
                    if existing is not None:
                        if existing.get("bundle_id") == bundle_id:
                            clear_origin_skip(
                                write_conn,
                                target=target, origin=origin,
                                cleared_by=operator,
                            )
                            skip_action = "cleared"
                        else:
                            skip_action = (
                                f"left_intact_owned_by:"
                                f"{existing.get('bundle_id')}"
                            )
                write_conn.execute("COMMIT")
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_released", {
                "bundle_id": bundle_id,
                "target": target,
                "origin": origin,
                "released_at": now,
                "released_by": operator,
                "reason": reason,
                "skip_action": skip_action,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": None,
            "released_at": now,
            "released_by": operator,
            "reason": reason,
            "target": target,
            "origin": origin,
            "skip_action": skip_action,
        }

    @app.post("/api/bundles/{bundle_id}/reopen")
    def api_bundle_reopen(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator reopen of a terminally-resolved bundle (Step 28d).

        Clears ``bundle.resolution`` back to NULL so the bundle is
        actionable again (take-over / discard / retry can fire from
        a fresh slate). Rare — undo path for an operator decision
        that turned out to be wrong (accepted a fix that then broke,
        rejected a fix that was actually correct, discarded a port
        that should be retried).

        Allowed only from terminal resolutions (``accepted`` /
        ``rejected`` / ``discarded``). 409 from any non-terminal
        state (no point reopening). 404 unknown bundle.

        Body:
          - ``reason`` (str, required): why the prior terminal
            decision is being undone. Audit trail; freeform.
          - ``operator`` (str, optional): freeform identifier.

        Side effects:
          - ``resolution`` → NULL.
          - ``reopened_at``, ``reopened_by``, ``reopened_from``
            populated. Prior terminal-state columns
            (``accepted_at``, ``rejected_at``, ``discarded_*``,
            ``taken_over_*``) are preserved as historical record.
          - If the prior state was ``discarded`` AND this bundle
            holds the open ``origin_skip_flags`` row (its
            ``bundle_id`` matches), the lock is cleared. If a
            sibling holds the lock, it's left alone — that's the
            sibling's stake to release.
          - Emits ``bundle_reopened`` event with
            ``prior_resolution`` + ``skip_action``.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        reason = (body or {}).get("reason")
        if not reason or not isinstance(reason, str) or not reason.strip():
            raise HTTPException(
                status_code=400,
                detail="body must include 'reason' (reopen text)",
            )
        reason = reason.strip()
        operator = ((body or {}).get("operator") or "operator").strip()
        if not operator:
            operator = "operator"

        with _conn() as conn:
            row = get_bundle(conn, bundle_id)
        if row is None:
            raise HTTPException(
                status_code=404, detail=f"Unknown bundle: {bundle_id}",
            )
        prior = row.get("resolution")
        if not fix_state.action_allowed(
            "reopen", prior, row.get("verification_status")
        ):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Reopen requires a terminal resolution; current "
                    f"is {prior!r} (nothing to undo)"
                ),
            )

        target = (row.get("target") or "").strip()
        origin = (row.get("origin") or "").strip()
        now = datetime.now(timezone.utc).isoformat()

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        skip_action = "none"
        try:
            write_conn.execute("BEGIN IMMEDIATE")
            try:
                # Restore the pre-terminal resolution so the
                # operator-action gates (verify/accept/reject — all
                # keyed on resolution='agent_fixed' or
                # 'operator_owned') light up again. Falls back to
                # NULL for legacy rows where the snapshot wasn't
                # taken; those land in the "actionable from take-
                # over" lane, same as pre-restore behavior.
                restored = row.get("pre_terminal_resolution")
                write_conn.execute(
                    """UPDATE bundles SET
                           resolution = ?,
                           reopened_at = ?,
                           reopened_by = ?,
                           reopened_from = ?,
                           pre_terminal_resolution = NULL,
                           last_seen_at = ?
                       WHERE bundle_id = ?""",
                    (restored, now, operator, prior, now, bundle_id),
                )
                # Clear the origin skip lock if (a) we came from
                # 'discarded' (only path that opens a lock — accept
                # and reject don't), and (b) THIS bundle owns it.
                # Sibling-owned locks are left alone — the sibling
                # made the lock decision and should release it.
                if prior == "discarded" and target and origin:
                    existing = is_origin_skipped(write_conn, target, origin)
                    if existing is not None:
                        if existing.get("bundle_id") == bundle_id:
                            clear_origin_skip(
                                write_conn,
                                target=target, origin=origin,
                                cleared_by=operator,
                            )
                            skip_action = "cleared"
                        else:
                            skip_action = (
                                f"left_intact_owned_by:"
                                f"{existing.get('bundle_id')}"
                            )
                write_conn.execute("COMMIT")
            except Exception:
                write_conn.execute("ROLLBACK")
                raise
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_reopened", {
                "bundle_id": bundle_id,
                "target": target,
                "origin": origin,
                "reopened_at": now,
                "reopened_by": operator,
                "reopened_from": prior,
                "restored_resolution": restored,
                "reason": reason,
                "skip_action": skip_action,
            })
        finally:
            write_conn.close()

        return {
            "ok": True,
            "bundle_id": bundle_id,
            "resolution": restored,
            "reopened_at": now,
            "reopened_by": operator,
            "reopened_from": prior,
            "reason": reason,
            "target": target,
            "origin": origin,
            "skip_action": skip_action,
        }

    @app.post("/api/bundles/{bundle_id}/delivery/status")
    def api_bundle_delivery_status(
        bundle_id: str, body: dict[str, Any],
    ) -> dict[str, Any]:
        """Operator-driven manual status update on the latest
        ``bundle_review_requests`` row (Step 11d-5).

        Bridges to a future PR-status polling step (out of scope
        for 11d): for now the operator tells the tracker "I just
        merged this PR upstream" / "I closed the PR" so the bundle's
        Delivery card reflects reality.

        Body:
          - ``status`` (str, required): one of ``"merged"`` /
            ``"closed"``. Other values (``created`` / ``updated``)
            are reserved for the orchestrator and refused here.
          - ``note`` (str, optional): short freeform context. Stored
            as the row's ``error`` column with a ``note:`` prefix
            (the column is repurposed for both create failures and
            operator-supplied annotations — v1 keeps the schema small).

        Refuses (404) if the bundle has no delivery row. Refuses
        (409) if the latest row is already in a terminal state
        (``closed`` / ``merged``) or in ``create_failed`` — the
        status machine is one-way; an operator who needs to flip
        a terminal back to created can use the standard reopen
        flow (Step 28d) followed by a fresh Accept.
        """
        from datetime import datetime, timezone  # noqa: PLC0415

        status = (body or {}).get("status")
        if status not in ("merged", "closed"):
            raise HTTPException(
                status_code=400,
                detail=(
                    "body 'status' must be 'merged' or 'closed'; "
                    "'created'/'updated' are reserved for the "
                    "orchestrator"
                ),
            )
        note = (body or {}).get("note")
        if note is not None and not isinstance(note, str):
            raise HTTPException(
                status_code=400,
                detail="body 'note', if supplied, must be a string",
            )
        # Cap note length explicitly rather than silently
        # truncating — operators get a clear signal when their
        # note won't fit. Matches the /retry context cap (8000)
        # so operators don't have to remember two limits, but
        # the practical use case is much shorter (one line).
        _NOTE_MAX = 2000
        if note is not None and len(note) > _NOTE_MAX:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"body 'note' too long ({len(note)} chars; "
                    f"max {_NOTE_MAX})"
                ),
            )

        with _conn() as conn:
            latest = latest_review_request_for_bundle(conn, bundle_id)
        if latest is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Bundle {bundle_id!r} has no delivery row "
                    f"(was it ever Accepted?)"
                ),
            )
        prior_status = latest.get("status")
        if prior_status in ("merged", "closed", "create_failed"):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Latest delivery row is in terminal state "
                    f"{prior_status!r}; manual status updates are "
                    f"a one-way street. Reopen the bundle (Step 28d) "
                    f"and re-Accept for a fresh delivery."
                ),
            )

        # 11d-5 Finding 7 follow-up: note lands in its own column.
        # Trim leading/trailing whitespace; empty notes are stored
        # as NULL (the operator skipped the prompt).
        note_text: str | None = None
        if note:
            stripped = note.strip()
            if stripped:
                note_text = stripped

        write_conn = sqlite3.connect(
            str(app.state.db_path), check_same_thread=False,
            isolation_level=None,
        )
        write_conn.row_factory = sqlite3.Row
        try:
            updated = update_review_request_status(
                write_conn,
                request_id=int(latest["id"]),
                status=status,
                note=note_text,
            )
            if not updated:
                # The row vanished between latest_review_request_for_bundle
                # and the UPDATE. Vanishingly rare; surface as 404.
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Bundle {bundle_id!r} delivery row "
                        f"disappeared mid-update"
                    ),
                )
            from dportsv3.artifact_store import emit_event  # noqa: PLC0415
            emit_event(write_conn, "bundle_delivery_status_changed", {
                "bundle_id": bundle_id,
                "request_id": int(latest["id"]),
                "prior_status": prior_status,
                "new_status": status,
                "note": note,
            })
        finally:
            write_conn.close()

        now = datetime.now(timezone.utc).isoformat()
        return {
            "ok": True,
            "bundle_id": bundle_id,
            "request_id": int(latest["id"]),
            "status": status,
            "prior_status": prior_status,
            "last_synced_at": now,
            "note": note,
        }

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
        path = render.resolve_artifact_path(app.state.artifact_root, ref)
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="Artifact file missing")
        media_type, inline = render.artifact_media_type(
            relpath, ref.get("kind"), fs_path=path,
        )
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
