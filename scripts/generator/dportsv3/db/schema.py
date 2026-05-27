"""Shared SQLite schema for state.db.

state.db is owned by ``artifact-store`` (the single writer). Tracker
becomes a read-only consumer in Phase 4 step 4. Defining the schema
here keeps both consumers in sync and makes integration tests easy
(import + spin up against an in-memory DB).

The tracker tables (``build_types``, ``build_runs``, ``build_results``,
``port_status``) were folded in by Phase 4 step 1; until tracker.db
retires (step 9) the equivalent definitions in
``scripts/generator/dportsv3/tracker/db.py`` must stay identical.
"""

from __future__ import annotations

import sqlite3

# Default build types seeded into the build_types table on first start.
# Matches DEFAULT_BUILD_TYPES in dportsv3.tracker.db.
DEFAULT_BUILD_TYPES: tuple[str, ...] = ("test", "release")

# All CREATE TABLE / CREATE INDEX statements for state.db. Idempotent —
# safe to re-run on an existing DB.
SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    profile TEXT,
    path TEXT,
    ts_start TEXT,
    ts_end TEXT,
    last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS bundles (
    bundle_id TEXT PRIMARY KEY,
    run_id TEXT,
    origin TEXT,
    flavor TEXT,
    ts_utc TEXT,
    result TEXT,
    path TEXT,
    last_seen_at TEXT,
    resolution TEXT,
    error_signature TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    job_id TEXT PRIMARY KEY,
    state TEXT,
    type TEXT,
    origin TEXT,
    flavor TEXT,
    bundle_dir TEXT,
    created_ts_utc TEXT,
    path TEXT,
    last_error TEXT,
    last_seen_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    bundle_id TEXT,
    relpath TEXT,
    kind TEXT,
    mtime REAL,
    size INTEGER,
    PRIMARY KEY (bundle_id, relpath)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    data_json TEXT
);

CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    job_id TEXT,
    stage TEXT,
    message TEXT,
    duration_ms INTEGER,
    extra_json TEXT
);

CREATE TABLE IF NOT EXISTS runner_status (
    id INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    status TEXT NOT NULL DEFAULT 'unknown',
    job_id TEXT,
    current_stage TEXT,
    started_at TEXT,
    updated_at TEXT,
    extra_json TEXT
);

CREATE TABLE IF NOT EXISTS env_health_status (
    env TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    probed_at TEXT,
    operator_action TEXT,
    detail_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_context (
    run_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    context_text TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    context_rev INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (run_id, origin)
);

CREATE TABLE IF NOT EXISTS user_context_requests (
    run_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    confidence TEXT,
    classification TEXT,
    iteration INTEGER,
    max_iterations INTEGER,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    last_context_rev_handled INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, origin, bundle_id)
);

-- Step 29b: append-only history of every operator-submitted
-- context for a (run_id, origin). user_context above carries
-- only the *current* row (overwritten on every submission);
-- this table preserves each round verbatim so manual_handoff.md
-- can render the full operator-side narrative.
CREATE TABLE IF NOT EXISTS user_context_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    context_rev INTEGER NOT NULL,
    submitted_at TEXT NOT NULL,
    text TEXT NOT NULL,
    submitted_by TEXT
);

CREATE TABLE IF NOT EXISTS blob_objects (
    sha256 TEXT PRIMARY KEY,
    size INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_refs (
    bundle_id TEXT NOT NULL,
    relpath TEXT NOT NULL,
    backend TEXT NOT NULL,
    sha256 TEXT,
    fs_path TEXT,
    kind TEXT,
    size INTEGER,
    created_at TEXT NOT NULL,
    PRIMARY KEY (bundle_id, relpath)
);

-- Phase 1 framework: typed job lifecycle. Every transition writes one row.
-- jobs.state holds the latest JobState value as a denormalized cache;
-- job_events is authoritative.
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    job_id TEXT NOT NULL,
    from_state TEXT,            -- NULL on initial HOOK_ENQUEUED
    to_state TEXT NOT NULL,
    event_name TEXT NOT NULL,   -- one of the JobEvent enum values
    actor TEXT,                 -- free-form label: "hook", "runner",
                                -- "runner-<pid>", "tests", etc.
    detail_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
CREATE INDEX IF NOT EXISTS idx_job_events_job ON job_events(job_id, id);
CREATE INDEX IF NOT EXISTS idx_activity_log_ts ON activity_log(ts);
CREATE INDEX IF NOT EXISTS idx_env_health_status_status ON env_health_status(status);
CREATE INDEX IF NOT EXISTS idx_user_context_updated ON user_context(updated_at);
CREATE INDEX IF NOT EXISTS idx_user_context_requests_pending ON user_context_requests(status, requested_at);
CREATE INDEX IF NOT EXISTS idx_user_context_history_lookup ON user_context_history(run_id, origin, context_rev);
CREATE INDEX IF NOT EXISTS idx_artifact_refs_bundle ON artifact_refs(bundle_id);
CREATE INDEX IF NOT EXISTS idx_artifact_refs_sha ON artifact_refs(sha256);

-- Phase 4 step 1: tracker schema folded into state.db.
CREATE TABLE IF NOT EXISTS build_types (
    name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS build_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    build_type TEXT NOT NULL REFERENCES build_types(name),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    commit_sha TEXT,
    commit_branch TEXT,
    commit_pushed_at TEXT
);

CREATE TABLE IF NOT EXISTS build_results (
    build_run_id INTEGER NOT NULL REFERENCES build_runs(id),
    origin TEXT NOT NULL,
    version TEXT NOT NULL,
    result TEXT NOT NULL,
    log_url TEXT,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (build_run_id, origin)
);

CREATE TABLE IF NOT EXISTS port_status (
    target TEXT NOT NULL,
    origin TEXT NOT NULL,
    last_attempt_version TEXT,
    last_attempt_result TEXT,
    last_attempt_at TEXT,
    last_attempt_run_id INTEGER REFERENCES build_runs(id),
    last_success_version TEXT,
    last_success_at TEXT,
    last_success_run_id INTEGER REFERENCES build_runs(id),
    PRIMARY KEY (target, origin)
);

CREATE TABLE IF NOT EXISTS tracker_active_env (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    env_name  TEXT,
    set_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_build_runs_target ON build_runs(target);
CREATE INDEX IF NOT EXISTS idx_build_results_origin ON build_results(origin);
CREATE INDEX IF NOT EXISTS idx_port_status_target ON port_status(target);
CREATE INDEX IF NOT EXISTS idx_port_status_failures
    ON port_status(target, last_attempt_result);
CREATE INDEX IF NOT EXISTS idx_build_runs_target_type_started
    ON build_runs(target, build_type, started_at DESC, id DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_build_runs_active
    ON build_runs(target, build_type)
    WHERE finished_at IS NULL;
"""

# Idempotent ADD COLUMN migrations. Wrapped at call time because SQLite
# raises OperationalError when the column already exists. Order matters
# only insofar as they target their own tables — no cross-deps.
MIGRATIONS: tuple[str, ...] = (
    "ALTER TABLE build_results ADD COLUMN status TEXT NOT NULL DEFAULT 'recorded'",
    "ALTER TABLE build_runs ADD COLUMN total_expected INTEGER",
    "ALTER TABLE runs ADD COLUMN build_run_id INTEGER",
    # Phase 4 step 5: target awareness on the agentic side. Nullable
    # because pre-step-5 rows exist; new writes carry target.
    "ALTER TABLE bundles ADD COLUMN target TEXT",
    "ALTER TABLE jobs ADD COLUMN target TEXT",
    "ALTER TABLE runs ADD COLUMN target TEXT",
    "CREATE INDEX IF NOT EXISTS idx_bundles_target ON bundles(target)",
    "CREATE INDEX IF NOT EXISTS idx_bundles_origin_target_seen "
    "ON bundles(origin, target, last_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_jobs_target ON jobs(target)",
    "CREATE INDEX IF NOT EXISTS idx_runs_target ON runs(target)",
    # Phase 1 framework: per-job transition forensics.
    "ALTER TABLE jobs ADD COLUMN last_transition_at TEXT",
    "ALTER TABLE jobs ADD COLUMN retire_reason TEXT",
    # bundle_id FK: the canonical relation between jobs and bundles.
    # Pre-2026-05-26 the relation was expressed via jobs.bundle_dir
    # (a filesystem-path string), which list_jobs_for_bundle joined
    # on with LIKE matching. Patch / verify enqueue paths never set
    # bundle_dir, so any query "what jobs touched this bundle" was
    # silently incomplete. Normalized FK + index now.
    "ALTER TABLE jobs ADD COLUMN bundle_id TEXT",
    "CREATE INDEX IF NOT EXISTS idx_jobs_bundle_id ON jobs(bundle_id)",
    # Post-impl plan: agent-driven resolution propagation. The hook
    # writes bundles.result='failure' at ingest; that never changes,
    # even after the patch agent fixes the build. Resolution carries
    # the agent's verdict: 'agent_fixed' on PATCH_OK,
    # 'agent_gave_up' / 'agent_budget_exhausted' on terminal patch
    # failures, 'escalated_manual' when triage routes to MANUAL.
    # NULL = no agent disposition yet (typical for fresh bundles).
    "ALTER TABLE bundles ADD COLUMN resolution TEXT",
    "CREATE INDEX IF NOT EXISTS idx_bundles_resolution ON bundles(resolution)",
    # Step 6: cached hash of the bundle's first error-line. Lazy-
    # computed by the runner the first time the retry-cap query needs
    # it; the hook itself doesn't write this so old bundles can still
    # contribute. Stored as a short hex digest. NULL = not computed
    # yet OR no errors.txt artifact available.
    "ALTER TABLE bundles ADD COLUMN error_signature TEXT",
    "CREATE INDEX IF NOT EXISTS idx_bundles_signature_origin "
    "ON bundles(origin, target, error_signature)",
    # Step 11b Slice 2: independent fix verification. The orchestrator
    # (`dportsv3 verify-fix BUNDLE_ID`, Slice 3) provisions a fresh
    # env, replays the bundle's analysis/changes.diff, runs
    # dsynth_build, and POSTs the result back here. Three columns:
    # - verification_status: 'verified' | 'verification_failed' | NULL
    # - verification_at: ISO timestamp of last verification attempt
    # - verification_applied_diff_sha256: sha256 of the diff that was
    #   replayed; lets the UI dedupe re-verifications of the same fix
    "ALTER TABLE bundles ADD COLUMN verification_status TEXT",
    "ALTER TABLE bundles ADD COLUMN verification_at TEXT",
    "ALTER TABLE bundles ADD COLUMN verification_applied_diff_sha256 TEXT",
    "CREATE INDEX IF NOT EXISTS idx_bundles_verification_status "
    "ON bundles(verification_status)",
    # Step 11c: operator accept/reject decisions on agent-fixed
    # bundles. Acceptance is gated on verification_status='verified'.
    # accepted_by is intentionally NULL today (auth lands in Step 18).
    "ALTER TABLE bundles ADD COLUMN accepted_at TEXT",
    "ALTER TABLE bundles ADD COLUMN accepted_by TEXT",
    "ALTER TABLE bundles ADD COLUMN rejected_at TEXT",
    "ALTER TABLE bundles ADD COLUMN rejection_reason TEXT",
    # Step 11c layer-violation cleanup: tracker used to call
    # dops.classify() live on every bundle detail render, which
    # required host-side DP_HARNESS_REPO_ROOT access — defeating
    # the "tracker reads state.db, not the host filesystem" rule.
    # The runner now writes dops_state at triage time (it has
    # chroot access via worker.assess_dops); the tracker just reads
    # the column. NULL for legacy rows where no triage ran post-
    # this-change.
    "ALTER TABLE bundles ADD COLUMN dops_state TEXT",
    # Step 11c layer-violation cleanup: the operator-triggered
    # /verify endpoint used to import dportsv3.agent.runner and
    # write .job files directly into the queue. That couples the
    # tracker to runner colocation (breaks Step 17 remote runners)
    # and crosses the read-only boundary. Mirror the existing
    # user_context_requests pattern instead: the tracker INSERTs a
    # row here, the runner polls and enqueues the verify job.
    # status: 'pending' | 'enqueued' | 'failed'.
    """CREATE TABLE IF NOT EXISTS verify_requests (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        bundle_id       TEXT NOT NULL,
        env             TEXT NOT NULL,
        requested_by    TEXT NOT NULL DEFAULT 'operator',
        requested_at    TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'pending',
        job_id          TEXT,
        error           TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_verify_requests_status "
    "ON verify_requests(status, requested_at)",
    "CREATE INDEX IF NOT EXISTS idx_verify_requests_bundle "
    "ON verify_requests(bundle_id, requested_at)",
    # Step 28a: operator take-over on failed bundles. The operator
    # stakes a (target, origin) pair via POST /api/bundles/{id}/
    # take-over; the bundle's resolution moves to 'operator_owned'
    # (non-terminal — Verify/Accept from Step 11c can still fire)
    # and a row lands in origin_skip_flags so subsequent dsynth
    # hooks for the same (target, origin) produce a tombstone
    # bundle instead of fresh triage. taken_over_by is freeform
    # today; integrating with the auth model is Step 17 territory.
    "ALTER TABLE bundles ADD COLUMN taken_over_at TEXT",
    "ALTER TABLE bundles ADD COLUMN taken_over_by TEXT",
    """CREATE TABLE IF NOT EXISTS origin_skip_flags (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        target          TEXT NOT NULL,
        origin          TEXT NOT NULL,
        set_by          TEXT NOT NULL DEFAULT 'operator',
        set_at          TEXT NOT NULL,
        reason          TEXT NOT NULL,
        bundle_id       TEXT,
        cleared_at      TEXT,
        cleared_by      TEXT
    )""",
    # Partial-unique index: at most one OPEN lock per (target, origin).
    # Cleared rows accumulate as forensics and don't block re-locking.
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_origin_skip_flags_open "
    "ON origin_skip_flags(target, origin) WHERE cleared_at IS NULL",
    "CREATE INDEX IF NOT EXISTS idx_origin_skip_flags_lookup "
    "ON origin_skip_flags(target, origin, cleared_at)",
    # Step 28b: operator discard on failed (or operator-owned)
    # bundles. resolution='discarded' is terminal — the operator
    # has decided the bundle's underlying port isn't worth pursuing
    # right now. discard_reason is required (an unexplained discard
    # is uninformative); per-(target, origin) skip flag optional via
    # the endpoint's skip_origin body field.
    "ALTER TABLE bundles ADD COLUMN discarded_at TEXT",
    "ALTER TABLE bundles ADD COLUMN discard_reason TEXT",
    # Step 28d: terminal-state reopen override. Operator clears a
    # terminal resolution (accepted/rejected/discarded) back to NULL
    # so the bundle can be re-actioned. Rare — guarded behind a
    # confirmation modal in the UI. Forensics columns are populated
    # only on reopen; the prior terminal columns
    # (accepted_at, rejected_at, discarded_*, taken_over_*) are
    # preserved as historical record. reopened_from records which
    # terminal state was being undone so the audit trail is
    # self-explanatory without joining job_events.
    "ALTER TABLE bundles ADD COLUMN reopened_at TEXT",
    "ALTER TABLE bundles ADD COLUMN reopened_by TEXT",
    "ALTER TABLE bundles ADD COLUMN reopened_from TEXT",
    # Step 11d-1: per-bundle review-request tracking. Append-only;
    # every delivery attempt writes a row. The bundle UI shows the
    # most-recent row's status. Partial-unique index enforces "at
    # most one open delivery per (provider, error_signature)" at
    # the DB layer — a double-clicked Accept can't produce two
    # open PRs. provider_pr_id is the upstream's identifier
    # (PR number / MR iid / outbox filename). status moves
    # 'created' → 'closed'/'merged' via operator action; future
    # PR-status polling (out of scope for 11d) will keep it in
    # sync with the upstream platform.
    """CREATE TABLE IF NOT EXISTS bundle_review_requests (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        bundle_id       TEXT NOT NULL,
        provider        TEXT NOT NULL,
        provider_pr_id  TEXT,
        url             TEXT,
        branch          TEXT,
        title           TEXT,
        status          TEXT NOT NULL DEFAULT 'created',
        created_at      TEXT NOT NULL,
        last_synced_at  TEXT,
        error           TEXT,
        operator        TEXT,
        error_signature TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_brr_bundle "
    "ON bundle_review_requests(bundle_id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_brr_open_signature "
    "ON bundle_review_requests(provider, error_signature) "
    "WHERE status NOT IN ('closed', 'merged', 'create_failed')",
)


def init_db(conn: sqlite3.Connection) -> None:
    """Run schema + seeds + migrations on an open connection.

    Called by artifact-store at startup. Tracker (read-only) doesn't
    need this — it just opens the DB and queries. Sets PRAGMAs first
    so the rest of the call inherits them.
    """
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    # Enforce FK constraints introduced by the tracker tables
    # (build_results.build_run_id -> build_runs.id, etc.). The original
    # artifact-store tables have no FKs, so the only impact is on
    # writes to the folded-in tracker tables.
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    conn.executemany(
        "INSERT OR IGNORE INTO build_types(name) VALUES (?)",
        [(name,) for name in DEFAULT_BUILD_TYPES],
    )
    for stmt in MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.commit()
