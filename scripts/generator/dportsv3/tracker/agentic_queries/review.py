"""Delivery review-request reads/writes for the tracker's agentic endpoints."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from dportsv3.agent.lifecycle import ACTIVE_WORK_STATE_VALUES
from dportsv3.tracker.agentic_queries._util import (
    _row_dict,
    _maybe,
    _decode_extra_json,
)


def insert_review_request(
    conn: sqlite3.Connection, *,
    bundle_id: str,
    provider: str,
    status: str = "created",
    provider_pr_id: str | None = None,
    url: str | None = None,
    branch: str | None = None,
    title: str | None = None,
    error: str | None = None,
    operator: str | None = None,
    error_signature: str | None = None,
    diff_sha256: str | None = None,
) -> int:
    """Append one ``bundle_review_requests`` row. Returns row id.

    Raises ``sqlite3.IntegrityError`` if the partial-unique index
    ``uq_brr_open_branch`` blocks a duplicate open delivery for the
    same ``(provider, branch)`` — caller (``deliver`` in
    ``delivery.orchestrator``) catches this and reconciles to the
    existing row rather than orphaning the upstream PR.
    """
    ts = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """INSERT INTO bundle_review_requests
           (bundle_id, provider, provider_pr_id, url, branch, title,
            status, created_at, error, operator, error_signature,
            diff_sha256)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (bundle_id, provider, provider_pr_id, url, branch, title,
         status, ts, error, operator, error_signature, diff_sha256),
    )
    return int(cur.lastrowid or 0)


def latest_review_request_for_bundle(
    conn: sqlite3.Connection, bundle_id: str,
) -> dict[str, Any] | None:
    """Most-recent ``bundle_review_requests`` row for one bundle,
    or None. Drives the bundle detail page's "Delivery" card."""
    row = conn.execute(
        """SELECT id, bundle_id, provider, provider_pr_id, url, branch,
                  title, status, created_at, last_synced_at, error,
                  operator, error_signature, note, diff_sha256
           FROM bundle_review_requests
           WHERE bundle_id = ?
           ORDER BY id DESC LIMIT 1""",
        (bundle_id,),
    ).fetchone()
    return _maybe(row)


def find_open_review_request(
    conn: sqlite3.Connection, *,
    provider: str,
    branch: str,
) -> dict[str, Any] | None:
    """Idempotency lookup: return the open delivery row for
    ``(provider, branch)`` if one exists, else None.

    "Open" matches the partial-unique index condition: status NOT
    IN ('closed', 'merged', 'create_failed'). Caller uses this to
    decide between create-new and patch-existing-body.

    Keyed on branch (not error_signature) because the branch is what
    the provider keys on for find-or-create — matching that key means
    "provider returned updated" ↔ "we have an open row" stays in
    lockstep. The default branch template encodes (origin, target,
    signature_short) so genuine same-port re-deliveries still
    converge.
    """
    row = conn.execute(
        """SELECT id, bundle_id, provider, provider_pr_id, url, branch,
                  title, status, created_at, last_synced_at, error,
                  operator, error_signature, note, diff_sha256
           FROM bundle_review_requests
           WHERE provider = ? AND branch = ?
             AND status NOT IN ('closed', 'merged', 'create_failed')
           ORDER BY id DESC LIMIT 1""",
        (provider, branch),
    ).fetchone()
    return _maybe(row)


def update_review_request_status(
    conn: sqlite3.Connection, *,
    request_id: int,
    status: str,
    error: str | None = None,
    note: str | None = None,
    provider_pr_id: str | None = None,
    url: str | None = None,
    branch: str | None = None,
    diff_sha256: str | None = None,
) -> bool:
    """Move a delivery row's status. Used for transitions like
    ``created`` → ``closed``/``merged`` (operator action), or
    ``created`` → ``updated`` on idempotency hits, or to attach
    PR-side data when the provider-create returns asynchronously.

    Returns True if a row was updated, False if no row matched
    ``request_id``. Always bumps ``last_synced_at``.

    ``note`` is the operator-supplied annotation for manual
    status updates (11d-5 / Finding 7 of the review). Lives in
    its own column rather than being co-located with ``error`` —
    the latter is for create-time failures only.
    """
    ts = datetime.now(timezone.utc).isoformat()
    # Build the SET clause dynamically so we don't blow away
    # fields the caller didn't pass.
    sets = ["status = ?", "last_synced_at = ?"]
    args: list[object] = [status, ts]
    if error is not None:
        sets.append("error = ?")
        args.append(error)
    if note is not None:
        sets.append("note = ?")
        args.append(note)
    if provider_pr_id is not None:
        sets.append("provider_pr_id = ?")
        args.append(provider_pr_id)
    if url is not None:
        sets.append("url = ?")
        args.append(url)
    if branch is not None:
        sets.append("branch = ?")
        args.append(branch)
    if diff_sha256 is not None:
        sets.append("diff_sha256 = ?")
        args.append(diff_sha256)
    args.append(request_id)
    cur = conn.execute(
        f"UPDATE bundle_review_requests SET {', '.join(sets)} "
        f"WHERE id = ?",
        tuple(args),
    )
    return cur.rowcount > 0
