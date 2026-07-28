"""Lazy, on-render reconciliation of a bundle's upstream PR state.

A delivered bundle's PR lives on GitHub; the tracker only learns it
merged if someone tells it. This closes that gap without a daemon: when
a page renders a bundle that still has an *open* GitHub delivery row, we
ask GitHub whether the PR merged (throttled by ``last_synced_at`` so a
busy page doesn't hammer the API). On a merge we flip the bundle to the
terminal ``merged`` resolution — which, through the existing state
machine, removes it from the worklist, hides Accept/Reject, and blocks a
duplicate re-Accept. Upstream reality is authoritative: a merged PR means
the code already changed, so whatever the local resolution was
(``agent_fixed`` after a reopen, ``accepted``, even ``rejected``) loses.

Only the GitHub provider can auto-detect — ``local-patch`` has no
upstream to poll, so merge there stays a manual ``delivery/status`` call
(which routes through :func:`set_bundle_merged_resolution` too, so both
paths converge on the same terminal state).

Best-effort throughout: no delivery config, a transport error, or a
missing token all resolve to "couldn't check, leave it be" — a page
render must never fail because GitHub was unreachable.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from dportsv3.tracker.agentic_queries import (
    latest_review_request_for_bundle,
    update_review_request_status,
)

_LOG = logging.getLogger(__name__)

# How long a delivery row's recorded upstream state is trusted before we
# re-poll GitHub. Bounds API calls to at most one per open PR per window,
# regardless of how often the worklist / detail page is rendered.
DEFAULT_MIN_INTERVAL_S = 300

# Statuses that are still worth polling — a terminal row (closed / merged
# / create_failed) is never re-checked.
_OPEN_STATUSES = ("created", "updated")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_stale(last_synced_at: str | None, now_iso: str, min_interval_s: int) -> bool:
    """True when the row hasn't been synced within the throttle window
    (or was never synced). Unparseable timestamps count as stale — we'd
    rather re-poll than get wedged never checking."""
    if not last_synced_at:
        return True
    try:
        last = datetime.fromisoformat(last_synced_at)
        now = datetime.fromisoformat(now_iso)
    except ValueError:
        return True
    return (now - last) >= timedelta(seconds=min_interval_s)


def set_bundle_merged_resolution(
    write_conn: sqlite3.Connection, bundle_id: str, *,
    now_iso: str, source: str,
) -> str | None:
    """Move a bundle to the terminal ``merged`` resolution.

    Snapshots the prior resolution into ``pre_terminal_resolution`` so a
    later reopen can restore it (mirrors accept's own snapshot). Idempotent:
    a bundle already ``merged`` is left untouched. Emits ``bundle_merged``.
    Returns the prior resolution, or None when nothing changed.

    Shared by the lazy reconciler and the manual ``delivery/status``
    endpoint so both merge paths land in exactly one place.

    Runs its statements on the caller's connection; the caller owns the
    surrounding transaction.
    """
    row = write_conn.execute(
        "SELECT resolution FROM bundles WHERE bundle_id = ?",
        (bundle_id,),
    ).fetchone()
    if row is None:
        return None
    prior = row["resolution"] if isinstance(row, sqlite3.Row) else row[0]
    if prior == "merged":
        return None
    write_conn.execute(
        """UPDATE bundles SET
               resolution = 'merged',
               pre_terminal_resolution = ?,
               last_seen_at = ?
           WHERE bundle_id = ?""",
        (prior, now_iso, bundle_id),
    )
    from dportsv3.artifact_store import emit_event  # noqa: PLC0415
    emit_event(write_conn, "bundle_merged", {
        "bundle_id": bundle_id,
        "prior_resolution": prior,
        "source": source,
    })
    # A merged fix is exactly what resolves the issue: the problem shipped.
    # Both merge paths (lazy poll + manual delivery/status) funnel through
    # here, so the issue-level resolve lands once, in one place.
    resolve_issue_for_bundle(write_conn, bundle_id, now_iso=now_iso, source=source)
    return prior


def resolve_issue_for_bundle(
    write_conn: sqlite3.Connection, bundle_id: str, *,
    now_iso: str, source: str,
) -> str | None:
    """Move the issue this occurrence belongs to into ``resolved``.

    A merged occurrence closes its fingerprinted problem. Idempotent and
    defensive: no-op when the bundle has no ``issue_key`` (degenerate /
    pre-issue occurrence) or the issue is already ``resolved``. A later
    genuinely-new occurrence re-opens it via the WS3 regression path, so
    resolving even a ``muted`` issue here is safe — the problem really is
    gone until it recurs. Emits ``issue_resolved``. Returns the issue_key
    when it transitioned, else None.

    Runs on the caller's connection inside the caller's transaction — both
    merge callers (the lazy poll and the manual delivery/status endpoint)
    wrap the bundle-merge + issue-resolve in one ``BEGIN IMMEDIATE`` so a
    crash between them can't strand a merged bundle with an open issue.
    """
    row = write_conn.execute(
        "SELECT issue_key FROM bundles WHERE bundle_id = ?", (bundle_id,),
    ).fetchone()
    if row is None:
        return None
    issue_key = row["issue_key"] if isinstance(row, sqlite3.Row) else row[0]
    if not issue_key:
        return None
    irow = write_conn.execute(
        "SELECT state FROM issues WHERE issue_key = ?", (issue_key,),
    ).fetchone()
    if irow is None:
        return None
    state = irow["state"] if isinstance(irow, sqlite3.Row) else irow[0]
    if state == "resolved":
        return None
    write_conn.execute(
        "UPDATE issues SET state = 'resolved', resolved_at = ?, updated_at = ? "
        "WHERE issue_key = ?",
        (now_iso, now_iso, issue_key),
    )
    from dportsv3.artifact_store import emit_event  # noqa: PLC0415
    emit_event(write_conn, "issue_resolved", {
        "issue_key": issue_key,
        "bundle_id": bundle_id,
        "source": source,
    })
    return issue_key


def _resolve_merge_probe(target: str | None) -> Callable[[str], dict[str, Any]] | None:
    """Build a ``pr_id -> {merged, state, url}`` probe from delivery
    config, or None when GitHub delivery isn't configured/available.

    Import-local so the tracker doesn't hard-depend on the delivery
    package at module load and so a broken config degrades to no-op.
    """
    try:
        from dportsv3.delivery.orchestrator import (  # noqa: PLC0415
            resolve_config, build_provider,
        )
        cfg = resolve_config(target=target)
        if cfg is None or cfg.provider_type != "github":
            return None
        provider = build_provider(cfg)
    except Exception:  # config/build failure → can't poll, that's fine
        return None
    probe = getattr(provider, "pull_request_merge_state", None)
    if probe is None:
        return None
    return probe  # type: ignore[return-value]


def reconcile_bundle_delivery(
    *, db_path: str, bundle_id: str, target: str | None = None,
    min_interval_s: int = DEFAULT_MIN_INTERVAL_S,
    now_iso: str | None = None,
    merge_state_fn: Callable[[str], dict[str, Any]] | None = None,
) -> str | None:
    """Poll GitHub for one bundle's PR merge state and persist a merge.

    Returns the new delivery status when it advanced (``"merged"`` /
    ``"closed"``), else None (nothing to do, throttled, still open, or
    the check failed). Self-contained connection management so both page
    routes can call it without threading a connection through.

    ``merge_state_fn`` is the test seam: pass a fake ``pr_id -> {...}``
    to avoid real config resolution + network I/O.
    """
    now_iso = now_iso or _now_iso()
    read = sqlite3.connect(db_path, check_same_thread=False)
    read.row_factory = sqlite3.Row
    read.execute("PRAGMA busy_timeout=5000")
    try:
        latest = latest_review_request_for_bundle(read, bundle_id)
    finally:
        read.close()

    if latest is None or latest.get("provider") != "github":
        return None
    if latest.get("status") not in _OPEN_STATUSES:
        return None
    pr_id = latest.get("provider_pr_id")
    if not pr_id:
        return None
    if not _is_stale(latest.get("last_synced_at"), now_iso, min_interval_s):
        return None

    if merge_state_fn is None:
        merge_state_fn = _resolve_merge_probe(target)
        if merge_state_fn is None:
            return None

    request_id = int(latest["id"])
    prior_status = str(latest["status"])
    write = sqlite3.connect(db_path, check_same_thread=False, isolation_level=None)
    write.row_factory = sqlite3.Row
    write.execute("PRAGMA busy_timeout=5000")
    try:
        try:
            state = merge_state_fn(str(pr_id))
        except Exception as exc:  # transport/auth — bump sync, try later
            _LOG.info(
                "delivery poll for %s (PR %s) failed: %s",
                bundle_id, pr_id, exc,
            )
            # Touch last_synced_at (re-writes same status) so a persistently
            # failing PR doesn't get re-polled on every single render.
            update_review_request_status(
                write, request_id=request_id, status=prior_status,
            )
            return None

        merged = bool(state.get("merged"))
        upstream_state = state.get("state")

        write.execute("BEGIN IMMEDIATE")
        try:
            if merged:
                update_review_request_status(
                    write, request_id=request_id, status="merged",
                    url=state.get("url"),
                )
                set_bundle_merged_resolution(
                    write, bundle_id, now_iso=now_iso, source="poll",
                )
                from dportsv3.artifact_store import emit_event  # noqa: PLC0415
                emit_event(write, "bundle_delivery_status_changed", {
                    "bundle_id": bundle_id,
                    "request_id": request_id,
                    "prior_status": prior_status,
                    "new_status": "merged",
                    "source": "poll",
                })
                write.execute("COMMIT")
                _LOG.info("bundle %s PR %s detected merged upstream", bundle_id, pr_id)
                return "merged"
            if upstream_state == "closed":
                # Closed without merging — stop polling, but the fix
                # didn't ship, so the bundle's resolution is left alone
                # for the operator to decide.
                update_review_request_status(
                    write, request_id=request_id, status="closed",
                )
                write.execute("COMMIT")
                _LOG.info("bundle %s PR %s detected closed (unmerged)", bundle_id, pr_id)
                return "closed"
            # Still open: just record that we checked.
            update_review_request_status(
                write, request_id=request_id, status=prior_status,
            )
            write.execute("COMMIT")
            return None
        except Exception:
            write.execute("ROLLBACK")
            raise
    finally:
        write.close()
