"""Smoke test for two-writer access to state.db under SQLite WAL.

Phase 4 step 4 puts both artifact-store and the tracker on the same
state.db file. SQLite WAL allows one writer + N readers concurrently;
writers serialize via the WAL lock with a busy_timeout window. This
test stresses that contract by hammering both writers in parallel
threads — if WAL or busy_timeout weren't set, we'd see ``database is
locked`` errors here.

Run with: pytest scripts/generator/tests/test_state_db_concurrency.py
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dportsv3.db.schema import init_db as init_state_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _artifact_store_writes(db_path: Path, n: int, stop_after_first_error: bool = True) -> list[str]:
    """Simulate artifact-store: insert bundles + events."""
    errors: list[str] = []
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        for i in range(n):
            try:
                with conn:
                    conn.execute(
                        """INSERT INTO bundles (bundle_id, run_id, origin, ts_utc, result, last_seen_at)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (f"as-bundle-{i}", "as-run-1", "devel/x", _now(), "fail", _now()),
                    )
                    conn.execute(
                        "INSERT INTO events (ts, type, data_json) VALUES (?, ?, ?)",
                        (_now(), "bundle_upserted", "{}"),
                    )
            except sqlite3.Error as exc:
                errors.append(f"as[{i}]: {exc}")
                if stop_after_first_error:
                    break
    finally:
        conn.close()
    return errors


def _tracker_writes(db_path: Path, n: int, stop_after_first_error: bool = True) -> list[str]:
    """Simulate tracker: create a build_run, write build_results."""
    errors: list[str] = []
    conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=10)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    try:
        # Open one run
        with conn:
            cur = conn.execute(
                "INSERT INTO build_runs (target, build_type, started_at) VALUES (?, ?, ?)",
                ("@concurrency", "test", _now()),
            )
            run_id = cur.lastrowid
        # Now hammer build_results
        for i in range(n):
            try:
                with conn:
                    conn.execute(
                        """INSERT INTO build_results
                           (build_run_id, origin, version, result, recorded_at, status)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (run_id, f"devel/p{i:03d}", "1.0", "success", _now(), "recorded"),
                    )
            except sqlite3.Error as exc:
                errors.append(f"tk[{i}]: {exc}")
                if stop_after_first_error:
                    break
    finally:
        conn.close()
    return errors


def test_two_writers_no_lock_errors(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    init_state_db(conn)
    conn.close()

    n_per_writer = 60
    as_errors: list[str] = []
    tk_errors: list[str] = []

    def run_as() -> None:
        as_errors.extend(_artifact_store_writes(db_path, n_per_writer))

    def run_tk() -> None:
        tk_errors.extend(_tracker_writes(db_path, n_per_writer))

    t_as = threading.Thread(target=run_as)
    t_tk = threading.Thread(target=run_tk)
    t0 = time.monotonic()
    t_as.start()
    t_tk.start()
    t_as.join(timeout=30)
    t_tk.join(timeout=30)
    elapsed = time.monotonic() - t0

    assert not as_errors, f"artifact-store writes had errors: {as_errors[:3]}"
    assert not tk_errors, f"tracker writes had errors: {tk_errors[:3]}"

    # Verify all writes landed.
    reader = sqlite3.connect(str(db_path))
    n_bundles = reader.execute("SELECT count(*) FROM bundles").fetchone()[0]
    n_events = reader.execute(
        "SELECT count(*) FROM events WHERE type='bundle_upserted'"
    ).fetchone()[0]
    n_runs = reader.execute("SELECT count(*) FROM build_runs").fetchone()[0]
    n_results = reader.execute("SELECT count(*) FROM build_results").fetchone()[0]
    reader.close()

    assert n_bundles == n_per_writer, f"expected {n_per_writer} bundles, got {n_bundles}"
    assert n_events == n_per_writer, f"expected {n_per_writer} events, got {n_events}"
    assert n_runs == 1, f"expected 1 build_run, got {n_runs}"
    assert n_results == n_per_writer, f"expected {n_per_writer} build_results, got {n_results}"

    # Sanity bound: 60 writes per side × ~5ms each + WAL contention <<< 30s
    # If we're anywhere near 30s we have a serialization disaster
    assert elapsed < 15, f"two-writer load took {elapsed:.1f}s — investigate"


def test_fk_enforcement_on_tracker_write(tmp_path: Path) -> None:
    """Tracker connections must respect the FK constraint introduced by
    the build_results -> build_runs reference. If foreign_keys=ON gets
    skipped (per-connection pragma), this insert would succeed and the
    bug would only surface much later."""
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    init_state_db(conn)
    conn.close()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON")
    with pytest.raises(sqlite3.IntegrityError):
        with conn:
            conn.execute(
                """INSERT INTO build_results
                   (build_run_id, origin, version, result, recorded_at, status)
                   VALUES (99999, 'devel/x', '1.0', 'success', ?, 'recorded')""",
                (_now(),),
            )
    conn.close()
