"""SQLite-backed build tracker database helpers."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

from dportsv3.common.validation import is_compose_target

VALID_BUILD_RESULTS = frozenset({"success", "failure", "skipped", "ignored"})
DEFAULT_BUILD_TYPES = ("test", "release")


def open_db(db_path: str | Path) -> sqlite3.Connection:
    """Open one configured SQLite connection for tracker operations."""
    path_text = str(db_path)
    conn = sqlite3.connect(path_text, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if path_text != ":memory:":
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


class ActiveBuildError(RuntimeError):
    """Raised when a target/build_type already has an active run."""

    def __init__(self, active_run: dict[str, Any]) -> None:
        self.active_run = active_run
        run_id = active_run.get("id")
        started_at = active_run.get("started_at")
        target = active_run.get("target")
        build_type = active_run.get("build_type")
        super().__init__(
            f"Active build already exists for {target} {build_type}: run {run_id}"
            f" (started_at={started_at})"
        )


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Initialize the tracker schema and return one configured connection."""
    path_text = str(db_path)
    conn = open_db(path_text)

    with conn:
        conn.executescript(
            """
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
        )
        conn.executemany(
            "INSERT OR IGNORE INTO build_types(name) VALUES (?)",
            [(name,) for name in DEFAULT_BUILD_TYPES],
        )

    # Idempotent schema migrations for queue tracking
    for stmt in (
        "ALTER TABLE build_results ADD COLUMN status TEXT NOT NULL DEFAULT 'recorded'",
        "ALTER TABLE build_runs ADD COLUMN total_expected INTEGER",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # column already exists

    return conn


def get_active_run(
    conn: sqlite3.Connection,
    target: str,
    build_type: str,
) -> dict[str, Any] | None:
    """Return the active run for one target/build_type, if present."""
    row = conn.execute(
        """
        SELECT *
        FROM build_runs
        WHERE target = ? AND build_type = ? AND finished_at IS NULL
        ORDER BY started_at DESC, id DESC
        LIMIT 1
        """,
        (target, build_type),
    ).fetchone()
    return _row_to_dict(row)


def create_build_run(
    conn: sqlite3.Connection,
    target: str,
    build_type: str,
    started_at: str | None,
) -> int:
    """Create a new build run and return its numeric ID."""
    _validate_target(target)
    _validate_build_type(conn, build_type)
    started_value = started_at or _utc_now()
    active_run = get_active_run(conn, target, build_type)
    if active_run is not None:
        raise ActiveBuildError(active_run)

    try:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO build_runs(target, build_type, started_at)
                VALUES (?, ?, ?)
                """,
                (target, build_type, started_value),
            )
    except sqlite3.IntegrityError as exc:
        active_run = get_active_run(conn, target, build_type)
        if active_run is not None:
            raise ActiveBuildError(active_run) from exc
        raise
    lastrowid = cursor.lastrowid
    if lastrowid is None:
        raise RuntimeError("Failed to create build run")
    return int(cast(int, lastrowid))


def finish_build_run(
    conn: sqlite3.Connection,
    run_id: int,
    finished_at: str | None,
    commit_sha: str | None = None,
    commit_branch: str | None = None,
    commit_pushed_at: str | None = None,
) -> None:
    """Mark one build run finished and optionally store commit metadata."""
    _require_build_run(conn, run_id)
    finished_value = finished_at or _utc_now()
    with conn:
        cursor = conn.execute(
            """
            UPDATE build_runs
            SET finished_at = ?,
                commit_sha = ?,
                commit_branch = ?,
                commit_pushed_at = ?
            WHERE id = ?
            """,
            (finished_value, commit_sha, commit_branch, commit_pushed_at, run_id),
        )
    if cursor.rowcount == 0:
        raise ValueError(f"Unknown build run: {run_id}")


def record_results(
    conn: sqlite3.Connection,
    run_id: int,
    target: str,
    results: list[dict[str, Any]],
) -> int:
    """Record results for one build run and update current per-port status."""
    run = _require_build_run(conn, run_id)
    if str(run["target"]) != target:
        raise ValueError(
            f"Build run {run_id} belongs to target {run['target']}, not {target}"
        )

    with conn:
        for result in results:
            origin = str(result.get("origin", "")).strip()
            version = str(result.get("version", "")).strip()
            outcome = str(result.get("result", "")).strip()
            log_url = result.get("log_url")
            if not origin:
                raise ValueError("Result origin must be non-empty")
            if not version:
                raise ValueError("Result version must be non-empty")
            _validate_build_result(outcome)

            recorded_at = str(result.get("recorded_at") or _utc_now())
            conn.execute(
                """
                INSERT INTO build_results(
                    build_run_id,
                    origin,
                    version,
                    result,
                    log_url,
                    recorded_at,
                    status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(build_run_id, origin) DO UPDATE SET
                    version = excluded.version,
                    result = excluded.result,
                    log_url = excluded.log_url,
                    recorded_at = excluded.recorded_at,
                    status = excluded.status
                """,
                (run_id, origin, version, outcome, log_url, recorded_at, outcome),
            )

            success_version = version if outcome == "success" else None
            success_at = recorded_at if outcome == "success" else None
            success_run_id = run_id if outcome == "success" else None
            conn.execute(
                """
                INSERT INTO port_status(
                    target,
                    origin,
                    last_attempt_version,
                    last_attempt_result,
                    last_attempt_at,
                    last_attempt_run_id,
                    last_success_version,
                    last_success_at,
                    last_success_run_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(target, origin) DO UPDATE SET
                    last_attempt_version = excluded.last_attempt_version,
                    last_attempt_result = excluded.last_attempt_result,
                    last_attempt_at = excluded.last_attempt_at,
                    last_attempt_run_id = excluded.last_attempt_run_id,
                    last_success_version = CASE
                        WHEN excluded.last_success_version IS NOT NULL
                            THEN excluded.last_success_version
                        ELSE port_status.last_success_version
                    END,
                    last_success_at = CASE
                        WHEN excluded.last_success_at IS NOT NULL
                            THEN excluded.last_success_at
                        ELSE port_status.last_success_at
                    END,
                    last_success_run_id = CASE
                        WHEN excluded.last_success_run_id IS NOT NULL
                            THEN excluded.last_success_run_id
                        ELSE port_status.last_success_run_id
                    END
                """,
                (
                    target,
                    origin,
                    version,
                    outcome,
                    recorded_at,
                    run_id,
                    success_version,
                    success_at,
                    success_run_id,
                ),
            )
    return len(results)


def enqueue_ports(
    conn: sqlite3.Connection,
    run_id: int,
    ports: list[dict[str, Any]],
    total_expected: int | None = None,
) -> int:
    """Bulk-insert queued ports for a build run. Returns count inserted."""
    _require_build_run(conn, run_id)
    inserted = 0
    with conn:
        for port in ports:
            origin = str(port.get("origin", "")).strip()
            version = str(port.get("version", "")).strip()
            if not origin or not version:
                continue
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO build_results(
                    build_run_id, origin, version, result, log_url, recorded_at, status
                ) VALUES (?, ?, ?, '', NULL, '', 'queued')
                """,
                (run_id, origin, version),
            )
            inserted += cursor.rowcount
        if total_expected is not None:
            conn.execute(
                "UPDATE build_runs SET total_expected = ? WHERE id = ?",
                (total_expected, run_id),
            )
    return inserted


def update_port_status(
    conn: sqlite3.Connection,
    run_id: int,
    origin: str,
    status: str,
) -> None:
    """Update the status of one port in a build run (e.g. queued -> building)."""
    _require_build_run(conn, run_id)
    with conn:
        cursor = conn.execute(
            """
            UPDATE build_results SET status = ?
            WHERE build_run_id = ? AND origin = ?
            """,
            (status, run_id, origin),
        )
    if cursor.rowcount == 0:
        raise ValueError(f"No result row for run {run_id}, origin {origin}")


def get_active_builds_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return summary info for all active (unfinished) builds."""
    rows = conn.execute(
        """
        SELECT
            build_runs.id,
            build_runs.target,
            build_runs.build_type,
            build_runs.started_at,
            build_runs.total_expected,
            COALESCE(SUM(CASE WHEN br.status = 'queued' THEN 1 ELSE 0 END), 0) AS queued_count,
            COALESCE(SUM(CASE WHEN br.status = 'building' THEN 1 ELSE 0 END), 0) AS building_count,
            COALESCE(SUM(CASE WHEN br.status IN ('success', 'failure', 'skipped', 'ignored', 'recorded') THEN 1 ELSE 0 END), 0) AS done_count,
            COALESCE(SUM(CASE WHEN br.status = 'success' OR (br.status = 'recorded' AND br.result = 'success') THEN 1 ELSE 0 END), 0) AS success_count,
            COALESCE(SUM(CASE WHEN br.status = 'failure' OR (br.status = 'recorded' AND br.result = 'failure') THEN 1 ELSE 0 END), 0) AS failure_count
        FROM build_runs
        LEFT JOIN build_results br ON br.build_run_id = build_runs.id
        WHERE build_runs.finished_at IS NULL
        GROUP BY build_runs.id
        ORDER BY build_runs.started_at DESC
        """
    ).fetchall()
    return [_row_dict_required(row) for row in rows]


def get_build_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """Return one build run with aggregate result counts."""
    row = conn.execute(
        """
        SELECT
            build_runs.id,
            build_runs.target,
            build_runs.build_type,
            build_runs.started_at,
            build_runs.finished_at,
            build_runs.commit_sha,
            build_runs.commit_branch,
            build_runs.commit_pushed_at,
            build_runs.total_expected,
            COUNT(build_results.origin) AS result_count,
            COALESCE(SUM(CASE WHEN build_results.result = 'success' THEN 1 ELSE 0 END), 0) AS success_count,
            COALESCE(SUM(CASE WHEN build_results.result = 'failure' THEN 1 ELSE 0 END), 0) AS failure_count,
            COALESCE(SUM(CASE WHEN build_results.result = 'skipped' THEN 1 ELSE 0 END), 0) AS skipped_count,
            COALESCE(SUM(CASE WHEN build_results.result = 'ignored' THEN 1 ELSE 0 END), 0) AS ignored_count,
            COALESCE(SUM(CASE WHEN build_results.status = 'queued' THEN 1 ELSE 0 END), 0) AS queued_count,
            COALESCE(SUM(CASE WHEN build_results.status = 'building' THEN 1 ELSE 0 END), 0) AS building_count
        FROM build_runs
        LEFT JOIN build_results ON build_results.build_run_id = build_runs.id
        WHERE build_runs.id = ?
        GROUP BY build_runs.id
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown build run: {run_id}")
    return _row_dict_required(row)


def list_build_runs(
    conn: sqlite3.Connection,
    target: str | None = None,
    build_type: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List build runs, newest first."""
    clauses: list[str] = []
    params: list[Any] = []
    if target is not None:
        clauses.append("build_runs.target = ?")
        params.append(target)
    if build_type is not None:
        clauses.append("build_runs.build_type = ?")
        params.append(build_type)
    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)

    rows = conn.execute(
        f"""
        SELECT
            build_runs.id,
            build_runs.target,
            build_runs.build_type,
            build_runs.started_at,
            build_runs.finished_at,
            build_runs.commit_sha,
            build_runs.commit_branch,
            build_runs.commit_pushed_at,
            build_runs.total_expected,
            COUNT(build_results.origin) AS result_count,
            COALESCE(SUM(CASE WHEN build_results.result = 'success' THEN 1 ELSE 0 END), 0) AS success_count,
            COALESCE(SUM(CASE WHEN build_results.result = 'failure' THEN 1 ELSE 0 END), 0) AS failure_count,
            COALESCE(SUM(CASE WHEN build_results.result = 'skipped' THEN 1 ELSE 0 END), 0) AS skipped_count,
            COALESCE(SUM(CASE WHEN build_results.result = 'ignored' THEN 1 ELSE 0 END), 0) AS ignored_count,
            COALESCE(SUM(CASE WHEN build_results.status = 'queued' THEN 1 ELSE 0 END), 0) AS queued_count,
            COALESCE(SUM(CASE WHEN build_results.status = 'building' THEN 1 ELSE 0 END), 0) AS building_count
        FROM build_runs
        LEFT JOIN build_results ON build_results.build_run_id = build_runs.id
        {where_sql}
        GROUP BY build_runs.id
        ORDER BY build_runs.started_at DESC, build_runs.id DESC
        LIMIT ?
        """,
        (*params, max(1, int(limit))),
    ).fetchall()
    return [_row_dict_required(row) for row in rows]


def get_build_results(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    """Return all recorded results for one build run."""
    _require_build_run(conn, run_id)
    rows = conn.execute(
        """
        SELECT build_run_id, origin, version, result, log_url, recorded_at, status
        FROM build_results
        WHERE build_run_id = ?
        ORDER BY
            CASE status
                WHEN 'building' THEN 0
                WHEN 'queued' THEN 1
                ELSE 2
            END,
            origin ASC
        """,
        (run_id,),
    ).fetchall()
    return [_row_dict_required(row) for row in rows]


def get_port_history(
    conn: sqlite3.Connection,
    target: str,
    origin: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Return recent build history for one origin on one target."""
    rows = conn.execute(
        """
        SELECT
            build_runs.id AS build_run_id,
            build_runs.target,
            build_runs.build_type,
            build_runs.started_at,
            build_runs.finished_at,
            build_results.origin,
            build_results.version,
            build_results.result,
            build_results.log_url,
            build_results.recorded_at
        FROM build_results
        JOIN build_runs ON build_runs.id = build_results.build_run_id
        WHERE build_runs.target = ? AND build_results.origin = ?
        ORDER BY build_runs.started_at DESC, build_runs.id DESC
        LIMIT ?
        """,
        (target, origin, max(1, int(limit))),
    ).fetchall()
    return [_row_dict_required(row) for row in rows]


def get_port_status(
    conn: sqlite3.Connection,
    target: str | None = None,
    origin: str | None = None,
) -> list[dict[str, Any]]:
    """Return current status rows filtered by target and/or origin."""
    clauses: list[str] = []
    params: list[Any] = []
    if target is not None:
        clauses.append("target = ?")
        params.append(target)
    if origin is not None:
        clauses.append("origin = ?")
        params.append(origin)
    where_sql = ""
    if clauses:
        where_sql = "WHERE " + " AND ".join(clauses)
    rows = conn.execute(
        f"""
        SELECT *
        FROM port_status
        {where_sql}
        ORDER BY target ASC, origin ASC
        """,
        params,
    ).fetchall()
    return [_row_dict_required(row) for row in rows]


def get_failures(conn: sqlite3.Connection, target: str) -> list[dict[str, Any]]:
    """Return current failures for one target."""
    rows = conn.execute(
        """
        SELECT *
        FROM port_status
        WHERE target = ? AND last_attempt_result = 'failure'
        ORDER BY origin ASC
        """,
        (target,),
    ).fetchall()
    return [_row_dict_required(row) for row in rows]


def get_diff(
    conn: sqlite3.Connection,
    target_a: str,
    target_b: str,
) -> dict[str, list[dict[str, Any]]]:
    """Return current per-port differences between two targets."""
    statuses_a = {row["origin"]: row for row in get_port_status(conn, target=target_a)}
    statuses_b = {row["origin"]: row for row in get_port_status(conn, target=target_b)}

    only_a: list[dict[str, Any]] = []
    only_b: list[dict[str, Any]] = []
    differ: list[dict[str, Any]] = []

    for origin in sorted(set(statuses_a) | set(statuses_b)):
        row_a = statuses_a.get(origin)
        row_b = statuses_b.get(origin)
        if row_a is None:
            assert row_b is not None
            row_b_required = row_b
            only_b.append(
                {
                    "origin": origin,
                    "target": target_b,
                    "version": row_b_required["last_attempt_version"],
                    "result": row_b_required["last_attempt_result"],
                }
            )
            continue
        if row_b is None:
            assert row_a is not None
            row_a_required = row_a
            only_a.append(
                {
                    "origin": origin,
                    "target": target_a,
                    "version": row_a_required["last_attempt_version"],
                    "result": row_a_required["last_attempt_result"],
                }
            )
            continue
        if (
            row_a["last_attempt_version"] != row_b["last_attempt_version"]
            or row_a["last_attempt_result"] != row_b["last_attempt_result"]
        ):
            differ.append(
                {
                    "origin": origin,
                    "version_a": row_a["last_attempt_version"],
                    "result_a": row_a["last_attempt_result"],
                    "version_b": row_b["last_attempt_version"],
                    "result_b": row_b["last_attempt_result"],
                }
            )

    return {"only_a": only_a, "only_b": only_b, "differ": differ}


def get_target_summary(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return per-target current summary rows for the dashboard index."""
    targets = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT target FROM port_status
            UNION
            SELECT target FROM build_runs
            """
        ).fetchall()
    }

    summaries: list[dict[str, Any]] = []
    for target in sorted(targets):
        counts = conn.execute(
            """
            SELECT
                COUNT(*) AS total_ports,
                COALESCE(SUM(CASE WHEN last_attempt_result = 'success' THEN 1 ELSE 0 END), 0) AS successes,
                COALESCE(SUM(CASE WHEN last_attempt_result = 'failure' THEN 1 ELSE 0 END), 0) AS failures,
                COALESCE(SUM(CASE WHEN last_attempt_result = 'skipped' THEN 1 ELSE 0 END), 0) AS skipped,
                COALESCE(SUM(CASE WHEN last_attempt_result = 'ignored' THEN 1 ELSE 0 END), 0) AS ignored
            FROM port_status
            WHERE target = ?
            """,
            (target,),
        ).fetchone()
        last_run = conn.execute(
            """
            SELECT id, build_type, started_at, finished_at
            FROM build_runs
            WHERE target = ?
            ORDER BY started_at DESC, id DESC
            LIMIT 1
            """,
            (target,),
        ).fetchone()
        count_dict = _row_dict_required(counts) if counts is not None else {}
        last_run_dict = _row_to_dict(last_run) or {}
        summaries.append(
            {
                "target": target,
                "total_ports": count_dict.get("total_ports", 0),
                "successes": count_dict.get("successes", 0),
                "failures": count_dict.get("failures", 0),
                "skipped": count_dict.get("skipped", 0),
                "ignored": count_dict.get("ignored", 0),
                "last_build_id": last_run_dict.get("id"),
                "last_build_type": last_run_dict.get("build_type"),
                "last_build_started_at": last_run_dict.get("started_at"),
                "last_build_finished_at": last_run_dict.get("finished_at"),
                "last_build_at": last_run_dict.get("finished_at")
                or last_run_dict.get("started_at"),
            }
        )
    return summaries


def compare_builds(
    conn: sqlite3.Connection,
    run_id_a: int,
    run_id_b: int,
) -> dict[str, Any]:
    """Compare two build runs and categorize origin deltas."""
    run_a = get_build_run(conn, run_id_a)
    run_b = get_build_run(conn, run_id_b)
    results_a = {row["origin"]: row for row in get_build_results(conn, run_id_a)}
    results_b = {row["origin"]: row for row in get_build_results(conn, run_id_b)}

    new_successes: list[dict[str, Any]] = []
    new_failures: list[dict[str, Any]] = []
    still_failing: list[dict[str, Any]] = []
    added: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    version_changes: list[dict[str, Any]] = []
    still_succeeding = 0

    for origin in sorted(set(results_a) | set(results_b)):
        row_a = results_a.get(origin)
        row_b = results_b.get(origin)
        if row_a is None:
            assert row_b is not None
            added.append(
                {
                    "origin": origin,
                    "version_b": row_b["version"],
                    "result_b": row_b["result"],
                }
            )
            continue
        if row_b is None:
            removed.append(
                {
                    "origin": origin,
                    "version_a": row_a["version"],
                    "result_a": row_a["result"],
                }
            )
            continue

        if row_a["version"] != row_b["version"]:
            version_changes.append(
                {
                    "origin": origin,
                    "version_a": row_a["version"],
                    "result_a": row_a["result"],
                    "version_b": row_b["version"],
                    "result_b": row_b["result"],
                }
            )

        result_a = row_a["result"]
        result_b = row_b["result"]
        if result_a == "failure" and result_b == "success":
            new_successes.append(
                {
                    "origin": origin,
                    "version_a": row_a["version"],
                    "result_a": result_a,
                    "version_b": row_b["version"],
                    "result_b": result_b,
                }
            )
        elif result_a == "success" and result_b == "failure":
            new_failures.append(
                {
                    "origin": origin,
                    "version_a": row_a["version"],
                    "result_a": result_a,
                    "version_b": row_b["version"],
                    "result_b": result_b,
                }
            )
        elif result_a == "failure" and result_b == "failure":
            still_failing.append(
                {
                    "origin": origin,
                    "version_a": row_a["version"],
                    "result_a": result_a,
                    "version_b": row_b["version"],
                    "result_b": result_b,
                }
            )
        elif result_a == "success" and result_b == "success":
            still_succeeding += 1

    return {
        "run_a": run_a,
        "run_b": run_b,
        "summary": {
            "new_successes": len(new_successes),
            "new_failures": len(new_failures),
            "still_failing": len(still_failing),
            "still_succeeding": still_succeeding,
            "added": len(added),
            "removed": len(removed),
            "version_changes": len(version_changes),
        },
        "new_successes": new_successes,
        "new_failures": new_failures,
        "still_failing": still_failing,
        "added": added,
        "removed": removed,
        "version_changes": version_changes,
    }


def _require_build_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM build_runs WHERE id = ?",
        (run_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown build run: {run_id}")
    return _row_dict_required(row)


def _validate_target(target: str) -> None:
    if not is_compose_target(target):
        raise ValueError(f"Invalid build target: {target}")


def _validate_build_type(conn: sqlite3.Connection, build_type: str) -> None:
    row = conn.execute(
        "SELECT name FROM build_types WHERE name = ?",
        (build_type,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown build type: {build_type}")


def _validate_build_result(result: str) -> None:
    if result not in VALID_BUILD_RESULTS:
        raise ValueError(f"Invalid build result: {result}")


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {str(key): row[key] for key in row.keys()}


def _row_dict_required(row: sqlite3.Row) -> dict[str, Any]:
    return {str(key): row[key] for key in row.keys()}


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
