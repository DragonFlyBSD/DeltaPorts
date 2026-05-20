"""Typed job lifecycle state machine.

One transition = one row in ``job_events`` + a denormalized cache
update on ``jobs.state`` (+ ``jobs.last_transition_at`` and, for
terminal states, ``jobs.retire_reason``). All under a single
``BEGIN IMMEDIATE`` transaction so concurrent runners can't double-
apply.

Authority order:
    job_events table (truth)
    > jobs.state column (denormalized cache)

``current(conn, job_id)`` falls back to ``job_events`` when the cache
disagrees. Never trust the cache alone for correctness; trust it for
listing-page queries.

Phase 1 only — no Step Protocol, no Decision engine, no Health
contract. Those land in later phases. This module knows nothing
about LLMs, dsynth, bundles, or the filesystem queue. It's pure
state-transition logic on top of sqlite.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from enum import StrEnum


class JobState(StrEnum):
    QUEUED    = "queued"
    CLAIMED   = "claimed"
    TRIAGING  = "triaging"
    TRIAGED   = "triaged"
    PATCHING  = "patching"
    VERIFYING = "verifying"
    DONE      = "done"
    ESCALATED = "escalated"
    DEAD      = "dead"


class JobEvent(StrEnum):
    HOOK_ENQUEUED    = "hook_enqueued"
    CLAIM            = "claim"
    TRIAGE_START     = "triage_start"
    TRIAGE_OK        = "triage_ok"
    TRIAGE_FAIL      = "triage_fail"
    PATCH_START      = "patch_start"
    PATCH_OK         = "patch_ok"
    PATCH_GAVE_UP    = "patch_gave_up"
    PATCH_BUDGET_OUT = "patch_budget_out"
    VERIFY_OK        = "verify_ok"
    VERIFY_FAIL      = "verify_fail"
    ESCALATE_MANUAL  = "escalate_manual"
    ENV_BROKEN       = "env_broken"
    REAP_ORPHAN      = "reap_orphan"


# (from_state, event) -> to_state. ``None`` as from_state means
# "new job" — only valid for HOOK_ENQUEUED.
TRANSITIONS: dict[tuple[JobState | None, JobEvent], JobState] = {
    # Entry
    (None,                 JobEvent.HOOK_ENQUEUED):    JobState.QUEUED,

    # Happy path
    (JobState.QUEUED,      JobEvent.CLAIM):            JobState.CLAIMED,
    (JobState.CLAIMED,     JobEvent.TRIAGE_START):     JobState.TRIAGING,
    (JobState.TRIAGING,    JobEvent.TRIAGE_OK):        JobState.TRIAGED,
    (JobState.TRIAGED,     JobEvent.PATCH_START):      JobState.PATCHING,
    (JobState.PATCHING,    JobEvent.PATCH_OK):         JobState.VERIFYING,
    (JobState.VERIFYING,   JobEvent.VERIFY_OK):        JobState.DONE,

    # Triage failures
    (JobState.TRIAGING,    JobEvent.TRIAGE_FAIL):      JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.ESCALATE_MANUAL):  JobState.ESCALATED,

    # Patch / verify failures
    (JobState.PATCHING,    JobEvent.PATCH_GAVE_UP):    JobState.DEAD,
    (JobState.PATCHING,    JobEvent.PATCH_BUDGET_OUT): JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.VERIFY_FAIL):      JobState.DEAD,

    # env_broken can interrupt any active state
    (JobState.CLAIMED,     JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.TRIAGING,    JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.PATCHING,    JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.ENV_BROKEN):       JobState.DEAD,

    # Startup orphan reap — same shape as env_broken but a distinct
    # event so retire_reason can be filled differently.
    (JobState.CLAIMED,     JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.TRIAGING,    JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.PATCHING,    JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.REAP_ORPHAN):      JobState.DEAD,
}


# Events that terminate a job. Used to fill jobs.retire_reason.
_TERMINAL_REASONS: dict[JobEvent, str] = {
    JobEvent.TRIAGE_FAIL:      "triage_failed",
    JobEvent.PATCH_GAVE_UP:    "patch_gave_up",
    JobEvent.PATCH_BUDGET_OUT: "patch_budget_exhausted",
    JobEvent.VERIFY_FAIL:      "verify_failed",
    JobEvent.ESCALATE_MANUAL:  "escalated_manual",
    JobEvent.ENV_BROKEN:       "env_broken",
    JobEvent.REAP_ORPHAN:      "runner_restart",
}

_INFLIGHT_STATES: tuple[JobState, ...] = (
    JobState.CLAIMED,
    JobState.TRIAGING,
    JobState.TRIAGED,
    JobState.PATCHING,
    JobState.VERIFYING,
)


class IllegalTransition(Exception):
    """Raised when (current_state, event) is not in TRANSITIONS."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_current_locked(conn: sqlite3.Connection, job_id: str) -> JobState | None:
    """Read latest state. Called inside an open transaction.

    Trust the event log first; fall back to jobs.state. If neither
    has any record, the job is new (None — only valid before
    HOOK_ENQUEUED).
    """
    row = conn.execute(
        "SELECT to_state FROM job_events WHERE job_id = ? ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if row is not None:
        return JobState(row[0])
    row = conn.execute(
        "SELECT state FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is not None and row[0]:
        try:
            return JobState(row[0])
        except ValueError:
            # Legacy non-typed value ("pending", "inflight", "done",
            # "failed") — treat as no event history. The runner
            # cutover (Step 3) repurposes jobs.state to typed values
            # going forward; legacy rows aren't migrated.
            return None
    return None


def apply(
    conn: sqlite3.Connection,
    job_id: str,
    event: JobEvent,
    *,
    actor: str = "runner",
    detail: dict | None = None,
) -> JobState:
    """Atomic state transition.

    Returns the new ``JobState`` on success. Raises
    ``IllegalTransition`` if the current state + event isn't in
    ``TRANSITIONS``. Caller is expected to retry or surface the
    error.

    ``actor`` is a short free-form label (``"hook"``, ``"runner"``,
    ``"tests"``) recorded for forensics. Default is ``"runner"``.

    ``detail`` is an optional dict serialized into
    ``job_events.detail_json``.
    """
    if not job_id:
        raise ValueError("job_id required")
    detail_json = json.dumps(detail) if detail else None
    ts = _now()
    try:
        conn.execute("BEGIN IMMEDIATE")
        current_state = _read_current_locked(conn, job_id)
        key = (current_state, event)
        if key not in TRANSITIONS:
            conn.execute("ROLLBACK")
            raise IllegalTransition(
                f"no transition for ({current_state}, {event}) on job {job_id}"
            )
        new_state = TRANSITIONS[key]
        conn.execute(
            """INSERT INTO job_events
               (ts, job_id, from_state, to_state, event_name, actor, detail_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ts, job_id,
             current_state.value if current_state else None,
             new_state.value, event.value, actor, detail_json),
        )
        retire_reason = _TERMINAL_REASONS.get(event)
        # On first transition we may not have a jobs row yet (the
        # runner-side enqueue inserts it; the hook inserts via the
        # artifact-store endpoint). Upsert by job_id so apply() works
        # whether or not the row pre-exists.
        if retire_reason is not None:
            conn.execute(
                """INSERT INTO jobs (job_id, state, last_transition_at, retire_reason)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                     state=excluded.state,
                     last_transition_at=excluded.last_transition_at,
                     retire_reason=excluded.retire_reason""",
                (job_id, new_state.value, ts, retire_reason),
            )
        else:
            conn.execute(
                """INSERT INTO jobs (job_id, state, last_transition_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(job_id) DO UPDATE SET
                     state=excluded.state,
                     last_transition_at=excluded.last_transition_at""",
                (job_id, new_state.value, ts),
            )
        conn.execute("COMMIT")
        return new_state
    except IllegalTransition:
        raise
    except sqlite3.OperationalError:
        # rollback on lock contention; caller decides retry policy
        try:
            conn.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def current(conn: sqlite3.Connection, job_id: str) -> JobState | None:
    """Latest state for a job. ``None`` if no record exists.

    Tries the denormalized ``jobs.state`` cache first; falls back to
    ``job_events`` if the cache is stale or pre-typed-values.
    """
    row = conn.execute(
        "SELECT state FROM jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if row is not None and row[0]:
        try:
            return JobState(row[0])
        except ValueError:
            # Legacy non-typed value; fall through to event log
            pass
    row = conn.execute(
        "SELECT to_state FROM job_events WHERE job_id = ? "
        "ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    if row is None:
        return None
    return JobState(row[0])


def history(conn: sqlite3.Connection, job_id: str) -> list[dict]:
    """All transitions for a job, oldest first."""
    rows = conn.execute(
        """SELECT id, ts, from_state, to_state, event_name, actor, detail_json
           FROM job_events WHERE job_id = ? ORDER BY id ASC""",
        (job_id,),
    ).fetchall()
    return [
        {
            "id": r[0],
            "ts": r[1],
            "from_state": r[2],
            "to_state": r[3],
            "event_name": r[4],
            "actor": r[5],
            "detail": json.loads(r[6]) if r[6] else None,
        }
        for r in rows
    ]


def reap_orphans(conn: sqlite3.Connection, actor: str = "runner") -> int:
    """Transition every job in an inflight-ish state to DEAD.

    Called by the runner at startup. Inflight-ish = any of
    CLAIMED, TRIAGING, TRIAGED, PATCHING, VERIFYING. QUEUED stays
    queued (those are claimable). Terminal states (DONE, ESCALATED,
    DEAD) are untouched.

    Returns the count of reaped jobs.
    """
    placeholders = ",".join("?" * len(_INFLIGHT_STATES))
    rows = conn.execute(
        f"SELECT job_id FROM jobs WHERE state IN ({placeholders})",
        tuple(s.value for s in _INFLIGHT_STATES),
    ).fetchall()
    reaped = 0
    for row in rows:
        try:
            apply(conn, row[0], JobEvent.REAP_ORPHAN, actor=actor)
            reaped += 1
        except IllegalTransition:
            # State changed between SELECT and apply() — fine, skip.
            continue
    return reaped
