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
from datetime import datetime, timedelta, timezone
from enum import StrEnum


class JobState(StrEnum):
    QUEUED     = "queued"
    CLAIMED    = "claimed"
    TRIAGING   = "triaging"
    TRIAGED    = "triaged"
    PATCHING   = "patching"
    VERIFYING  = "verifying"
    # Step 20: dops-conversion in progress. Parallel to TRIAGING /
    # PATCHING — a convert job is its own type that lives entirely
    # in this state until done/dead/escalated.
    CONVERTING = "converting"
    DONE       = "done"
    ESCALATED  = "escalated"
    DEAD       = "dead"


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
    # Step 10b: operator-triggered job kill. Distinct from REAP_ORPHAN
    # so retire_reason ('abandoned') can be filled differently and
    # audit history can tell "operator killed this" apart from
    # "runner restart reaped this".
    ABANDON          = "abandon"
    # Step 20: dops-conversion job lifecycle. CONVERT_OK lands a
    # converted port at DONE; CONVERT_GAVE_UP lands at DEAD with
    # retire_reason='convert_failed'. The "needs_llm" sub-case (the
    # deterministic converter bailed and 20b's LLM tool loop isn't
    # implemented yet) reuses CONVERT_GAVE_UP with a distinct detail
    # field; once 20b lands those will instead exercise the loop.
    CONVERT_START    = "convert_start"
    CONVERT_OK       = "convert_ok"
    CONVERT_GAVE_UP  = "convert_gave_up"


# (from_state, event) -> to_state. ``None`` as from_state means
# "new job" — only valid for HOOK_ENQUEUED.
TRANSITIONS: dict[tuple[JobState | None, JobEvent], JobState] = {
    # Entry
    (None,                 JobEvent.HOOK_ENQUEUED):    JobState.QUEUED,

    # Happy path
    (JobState.QUEUED,      JobEvent.CLAIM):            JobState.CLAIMED,
    (JobState.CLAIMED,     JobEvent.TRIAGE_START):     JobState.TRIAGING,
    (JobState.TRIAGING,    JobEvent.TRIAGE_OK):        JobState.TRIAGED,
    # Patch jobs (created by enqueue_patch_job after a triage decides
    # auto_patch) arrive at CLAIMED with no preceding triage in their
    # own lifecycle. PATCH_START transitions them straight to PATCHING.
    (JobState.CLAIMED,     JobEvent.PATCH_START):      JobState.PATCHING,
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

    # Step 20: convert-job happy path + failure.
    (JobState.CLAIMED,     JobEvent.CONVERT_START):    JobState.CONVERTING,
    (JobState.CONVERTING,  JobEvent.CONVERT_OK):       JobState.DONE,
    (JobState.CONVERTING,  JobEvent.CONVERT_GAVE_UP):  JobState.DEAD,
    (JobState.CONVERTING,  JobEvent.ESCALATE_MANUAL):  JobState.ESCALATED,

    # env_broken can interrupt any active state
    (JobState.CLAIMED,     JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.TRIAGING,    JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.PATCHING,    JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.ENV_BROKEN):       JobState.DEAD,
    (JobState.CONVERTING,  JobEvent.ENV_BROKEN):       JobState.DEAD,

    # Startup orphan reap — same shape as env_broken but a distinct
    # event so retire_reason can be filled differently.
    (JobState.CLAIMED,     JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.TRIAGING,    JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.PATCHING,    JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.REAP_ORPHAN):      JobState.DEAD,
    (JobState.CONVERTING,  JobEvent.REAP_ORPHAN):      JobState.DEAD,
    # Step 10a: QUEUED jobs can be reaped too, but ONLY by the
    # stricter ``reap_stale_queued`` helper — never by ``reap_orphans``
    # (which would kill brand-new claimable work). The state-machine
    # entry is permissive; the safety lives in the caller.
    (JobState.QUEUED,      JobEvent.REAP_ORPHAN):      JobState.DEAD,

    # Step 10b: operator-triggered ABANDON. Permitted from QUEUED and
    # every in-flight state. Terminal states (DONE/DEAD/ESCALATED)
    # remain off-limits — the operator can't "abandon" something
    # that's already terminal.
    (JobState.QUEUED,      JobEvent.ABANDON):          JobState.DEAD,
    (JobState.CLAIMED,     JobEvent.ABANDON):          JobState.DEAD,
    (JobState.TRIAGING,    JobEvent.ABANDON):          JobState.DEAD,
    (JobState.TRIAGED,     JobEvent.ABANDON):          JobState.DEAD,
    (JobState.PATCHING,    JobEvent.ABANDON):          JobState.DEAD,
    (JobState.VERIFYING,   JobEvent.ABANDON):          JobState.DEAD,
    (JobState.CONVERTING,  JobEvent.ABANDON):          JobState.DEAD,
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
    JobEvent.ABANDON:          "abandoned",
    JobEvent.CONVERT_GAVE_UP:  "convert_failed",
}


# Events that close the agent's investigation of a bundle. Propagated
# to ``bundles.resolution`` so the UI can show "this failure was
# resolved by the agent" without spelunking analysis/patch_audit.json.
# ``bundles.result`` stays unchanged (the bundle WAS a failure at
# ingest); ``resolution`` carries the post-ingest verdict.
_EVENT_TO_RESOLUTION: dict[JobEvent, str] = {
    JobEvent.PATCH_OK:         "agent_fixed",
    JobEvent.PATCH_GAVE_UP:    "agent_gave_up",
    JobEvent.PATCH_BUDGET_OUT: "agent_budget_exhausted",
    JobEvent.ESCALATE_MANUAL:  "escalated_manual",
}

_INFLIGHT_STATES: tuple[JobState, ...] = (
    JobState.CLAIMED,
    JobState.TRIAGING,
    JobState.TRIAGED,
    JobState.PATCHING,
    JobState.VERIFYING,
    JobState.CONVERTING,
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

        # Resolution propagation: if this event closes the agent's
        # investigation and ``detail`` carries the originating
        # bundle_id, write the verdict onto the bundle. Best-effort:
        # a missing bundles row (older bundle, or detail lacking the
        # field) just silently skips. Idempotent — running the same
        # transition twice writes the same resolution.
        resolution = _EVENT_TO_RESOLUTION.get(event)
        if resolution and detail and detail.get("bundle_id"):
            conn.execute(
                """UPDATE bundles
                   SET resolution = ?, last_seen_at = ?
                   WHERE bundle_id = ?""",
                (resolution, ts, str(detail["bundle_id"])),
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


def reap_stale_queued(
    conn: sqlite3.Connection,
    queue_root,                 # Path: host's queue dir; pending/ underneath
    *,
    max_age_seconds: int = 3600,
    actor: str = "runner",
) -> list[str]:
    """Reap QUEUED rows whose ``.job`` file is missing from
    ``queue_root/pending/`` AND whose last transition is older than
    ``max_age_seconds``.

    Both conditions are required:

    - **File missing.** A queued row whose `.job` file is still on
      disk in pending/ is legitimate work waiting to be claimed; do
      not touch it. Missing file = the row references a job that can
      never advance (the file was swept up, or written into a path the
      runner doesn't scan — see the chroot-path bug surfaced during
      first smoke).
    - **Old.** ``last_transition_at`` (or ``last_seen_at`` as
      fallback) older than the threshold. Guards against racing with
      a brand-new HOOK_ENQUEUED whose .job file is *about* to be
      written.

    Returns the list of reaped ``job_id`` values for logging.
    """
    from pathlib import Path  # local import: lifecycle is otherwise stdlib-only
    pending_dir = Path(queue_root) / "pending"

    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    ).isoformat()

    rows = conn.execute(
        """SELECT job_id, last_transition_at, last_seen_at
           FROM jobs
           WHERE state = ?
             AND COALESCE(last_transition_at, last_seen_at, '') < ?""",
        (JobState.QUEUED.value, cutoff_iso),
    ).fetchall()

    reaped: list[str] = []
    for row in rows:
        job_id = row[0] if not hasattr(row, "keys") else row["job_id"]
        if (pending_dir / job_id).exists():
            # File is still on disk — legitimate work, leave alone.
            continue
        try:
            apply(conn, job_id, JobEvent.REAP_ORPHAN, actor=actor)
            reaped.append(job_id)
        except IllegalTransition:
            continue
    return reaped
