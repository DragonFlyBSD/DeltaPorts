"""Adapter that exposes tracker data in dsynth-progress' JSON shape.

The dsynth-progress UI (``www/example/progress.{html,js,css}``) consumes
two endpoints:

- ``summary.json``      — profile + kickoff + stats + active builders
- ``<NN>_history.json`` — array of build entries, paginated into chunks

This module maps the tracker's ``state.db`` rows (``build_runs``,
``build_results``, ``port_status``) into that shape so the lifted UI
runs against tracker data without modification.

Result vocabulary mapping:
- tracker ``success``  → dsynth ``built``
- tracker ``failure``  → dsynth ``failed``
- tracker ``skipped``  → dsynth ``skipped``
- tracker ``ignored``  → dsynth ``ignored``
- (no tracker analog)  → dsynth ``meta``  — left at 0

Chunk size is fixed at 1000 entries per ``<NN>_history.json``, matching
dsynth-progress' own chunking. ``kfiles`` in summary.json is the count
of chunks the UI should fetch.
"""

from __future__ import annotations

import sqlite3
from typing import Any

CHUNK_SIZE = 1000

_RESULT_TO_DSYNTH = {
    "success": "built",
    "failure": "failed",
    "skipped": "skipped",
    "ignored": "ignored",
}


def _latest_run_id(conn: sqlite3.Connection, target: str) -> int | None:
    row = conn.execute(
        """SELECT id FROM build_runs
           WHERE target = ?
           ORDER BY started_at DESC, id DESC LIMIT 1""",
        (target,),
    ).fetchone()
    return int(row[0]) if row else None


def target_summary(conn: sqlite3.Connection, target: str) -> dict[str, Any]:
    """Return the summary.json shape for one target."""
    run_id = _latest_run_id(conn, target)
    if run_id is None:
        return _empty_summary(target)

    run = conn.execute(
        """SELECT id, target, build_type, started_at, finished_at, total_expected
           FROM build_runs WHERE id = ?""",
        (run_id,),
    ).fetchone()
    assert run is not None

    counts = conn.execute(
        """SELECT
             COUNT(*) AS total,
             COALESCE(SUM(CASE WHEN result = 'success' THEN 1 ELSE 0 END), 0) AS built,
             COALESCE(SUM(CASE WHEN result = 'failure' THEN 1 ELSE 0 END), 0) AS failed,
             COALESCE(SUM(CASE WHEN result = 'skipped' THEN 1 ELSE 0 END), 0) AS skipped,
             COALESCE(SUM(CASE WHEN result = 'ignored' THEN 1 ELSE 0 END), 0) AS ignored,
             COALESCE(SUM(CASE WHEN status = 'building' THEN 1 ELSE 0 END), 0) AS building,
             COALESCE(SUM(CASE WHEN status = 'queued' THEN 1 ELSE 0 END), 0) AS queued
           FROM build_results WHERE build_run_id = ?""",
        (run_id,),
    ).fetchone()

    total_recorded = int(counts["total"])
    total_expected = int(run["total_expected"] or total_recorded)
    remains = max(0, total_expected - total_recorded)

    elapsed = _elapsed_str(str(run["started_at"]), run["finished_at"])

    builders = _active_builders(conn, run_id)

    # kfiles counts chunks of *historical* rows the UI will fetch —
    # building/queued rows live in `builders`, not in NN_history.json,
    # so exclude them from the chunk math.
    historical = total_recorded - int(counts["building"]) - int(counts["queued"])
    kfiles = max(1, (historical + CHUNK_SIZE - 1) // CHUNK_SIZE) if historical else 0

    return {
        "profile": str(run["target"]),
        "kickoff": _format_kickoff(str(run["started_at"])),
        "kfiles": kfiles,
        "active": 1 if run["finished_at"] is None else 0,
        "stats": {
            "queued": total_expected,
            "built": int(counts["built"]),
            "failed": int(counts["failed"]),
            "ignored": int(counts["ignored"]),
            "skipped": int(counts["skipped"]),
            "remains": remains,
            "meta": 0,
            "elapsed": elapsed,
            "pkghour": 0,
            "impulse": 0,
            "swapinfo": "  -",
            "load": "  -",
        },
        "builders": builders,
    }


def target_history_chunk(
    conn: sqlite3.Connection,
    target: str,
    chunk_index: int,
) -> list[dict[str, Any]]:
    """Return one chunk of build entries for the latest run on ``target``.

    Chunks are 1-indexed to match dsynth-progress (``01_history.json`` =
    chunk 1). Returns an empty list past the last chunk.
    """
    if chunk_index < 1:
        return []
    run_id = _latest_run_id(conn, target)
    if run_id is None:
        return []

    offset = (chunk_index - 1) * CHUNK_SIZE
    # 'building' and 'queued' rows are in-flight — they belong in
    # summary.builders, not in the historical record.
    rows = conn.execute(
        """SELECT origin, version, result, recorded_at, status
           FROM build_results
           WHERE build_run_id = ?
             AND status NOT IN ('building', 'queued')
           ORDER BY recorded_at ASC, origin ASC
           LIMIT ? OFFSET ?""",
        (run_id, CHUNK_SIZE, offset),
    ).fetchall()

    entries: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        entries.append(
            {
                "entry": offset + i + 1,
                "elapsed": "",
                "ID": "00",
                "result": _RESULT_TO_DSYNTH.get(
                    str(row["result"] or ""), str(row["result"] or "")
                ),
                "origin": str(row["origin"]),
                "info": str(row["version"] or ""),
                "duration": "",
            }
        )
    return entries


def _empty_summary(target: str) -> dict[str, Any]:
    return {
        "profile": target,
        "kickoff": "",
        "kfiles": 0,
        "active": 0,
        "stats": {
            "queued": 0,
            "built": 0,
            "failed": 0,
            "ignored": 0,
            "skipped": 0,
            "remains": 0,
            "meta": 0,
            "elapsed": "",
            "pkghour": 0,
            "impulse": 0,
            "swapinfo": "  -",
            "load": "  -",
        },
        "builders": [],
    }


def _active_builders(
    conn: sqlite3.Connection, run_id: int
) -> list[dict[str, Any]]:
    """One row per port currently in 'building' state.

    Tracker has no per-builder-slot model — every in-progress port maps
    to one virtual slot ID (zero-padded index). Matches dsynth-progress'
    table shape without claiming we have N physical builder slots.
    """
    rows = conn.execute(
        """SELECT origin, recorded_at
           FROM build_results
           WHERE build_run_id = ? AND status = 'building'
           ORDER BY recorded_at ASC""",
        (run_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        out.append(
            {
                "ID": _two_digit(i),
                "elapsed": " --:--:--",
                "phase": "build",
                "origin": str(row["origin"]),
                "lines": "",
            }
        )
    return out


def _two_digit(n: int) -> str:
    return f"{n:02d}" if n < 100 else str(n)


def _elapsed_str(started_at: str, finished_at: str | None) -> str:
    """HH:MM:SS between two ISO timestamps (best-effort).

    dsynth-progress' format is space-padded HH:MM:SS. If timestamps
    don't parse, returns empty.
    """
    from datetime import datetime

    try:
        start = datetime.fromisoformat(started_at)
    except (ValueError, TypeError):
        return ""
    if finished_at:
        try:
            end = datetime.fromisoformat(finished_at)
        except (ValueError, TypeError):
            return ""
    else:
        end = datetime.now(start.tzinfo) if start.tzinfo else datetime.now()
    delta = end - start
    secs = max(0, int(delta.total_seconds()))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _format_kickoff(started_at: str) -> str:
    """Best-effort match to dsynth's ' DD-Mon-YYYY HH:MM:SS UTC' format."""
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(started_at)
    except (ValueError, TypeError):
        return started_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime(" %d-%b-%Y %H:%M:%S UTC")
