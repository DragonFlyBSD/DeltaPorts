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
    last_seen_at TEXT
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

CREATE INDEX IF NOT EXISTS idx_events_id ON events(id);
CREATE INDEX IF NOT EXISTS idx_activity_log_ts ON activity_log(ts);
CREATE INDEX IF NOT EXISTS idx_user_context_updated ON user_context(updated_at);
CREATE INDEX IF NOT EXISTS idx_user_context_requests_pending ON user_context_requests(status, requested_at);
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
    "CREATE INDEX IF NOT EXISTS idx_jobs_target ON jobs(target)",
    "CREATE INDEX IF NOT EXISTS idx_runs_target ON runs(target)",
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
