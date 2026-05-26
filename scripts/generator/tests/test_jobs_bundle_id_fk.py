"""Tests for the normalized jobs.bundle_id FK + list_jobs_for_bundle.

Regression for multimedia/v4l_compat (analysis 2026-05-26): the
prior LIKE-on-bundle_dir join silently missed patch / verify jobs
because those paths only set bundle_id, never bundle_dir.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest

from dportsv3.db.schema import init_db
from dportsv3.tracker.agentic_queries import list_jobs_for_bundle


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def _insert_job(conn, job_id, *, bundle_id=None, bundle_dir=None,
                type_="triage", state="queued"):
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO jobs
           (job_id, state, type, bundle_id, bundle_dir, created_ts_utc)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (job_id, state, type_, bundle_id, bundle_dir, now),
    )
    conn.commit()


def test_finds_jobs_with_bundle_id_set(db):
    """Modern enqueue paths set jobs.bundle_id directly. The FK
    query is the primary match."""
    _insert_job(db, "triage-1", bundle_id="b-1", type_="triage")
    _insert_job(db, "patch-1", bundle_id="b-1", type_="patch")
    _insert_job(db, "verify-1", bundle_id="b-1", type_="verify")
    # Sibling bundle's job must not leak in.
    _insert_job(db, "patch-2", bundle_id="b-2", type_="patch")

    rows = list_jobs_for_bundle(db, "b-1")
    ids = sorted(r["job_id"] for r in rows)
    assert ids == ["patch-1", "triage-1", "verify-1"]


def test_finds_legacy_jobs_via_bundle_dir_fallback(db):
    """Pre-migration rows have bundle_dir but no bundle_id (NULL).
    The query still finds them so old data isn't dropped."""
    _insert_job(db, "legacy-1", bundle_id=None,
                bundle_dir="/logs/bundles/b-1", type_="triage")
    _insert_job(db, "legacy-2", bundle_id=None,
                bundle_dir="/logs/bundles/b-1/", type_="patch")
    rows = list_jobs_for_bundle(db, "b-1")
    assert sorted(r["job_id"] for r in rows) == ["legacy-1", "legacy-2"]


def test_mixed_modern_and_legacy_rows(db):
    """A bundle with both modern (bundle_id set) and legacy
    (bundle_dir only) jobs returns all of them — no double-count
    when both are present on the same row."""
    _insert_job(db, "modern-1", bundle_id="b-1", type_="patch")
    _insert_job(db, "legacy-1", bundle_id=None,
                bundle_dir="/logs/bundles/b-1", type_="triage")
    _insert_job(db, "both-set", bundle_id="b-1",
                bundle_dir="/logs/bundles/b-1", type_="convert")
    rows = list_jobs_for_bundle(db, "b-1")
    ids = sorted(r["job_id"] for r in rows)
    assert ids == ["both-set", "legacy-1", "modern-1"]
    # Critically, both-set appears exactly once even though it
    # would match by both bundle_id and bundle_dir.
    assert len(rows) == 3


def test_returns_empty_for_unknown_bundle(db):
    _insert_job(db, "patch-1", bundle_id="b-1")
    assert list_jobs_for_bundle(db, "ghost-bundle") == []


def test_ordered_newest_first(db):
    """Newest-first ordering by created_ts_utc."""
    db.execute(
        """INSERT INTO jobs (job_id, state, type, bundle_id, created_ts_utc)
           VALUES ('old',   'done',   'triage',  'b-1', '2026-01-01T00:00:00Z'),
                  ('mid',   'done',   'convert', 'b-1', '2026-02-01T00:00:00Z'),
                  ('newer', 'queued', 'patch',   'b-1', '2026-03-01T00:00:00Z')""",
    )
    db.commit()
    rows = list_jobs_for_bundle(db, "b-1")
    assert [r["job_id"] for r in rows] == ["newer", "mid", "old"]


def test_other_bundles_excluded(db):
    """Cross-bundle isolation — bundle_id filter is strict."""
    _insert_job(db, "a", bundle_id="b-1", type_="patch")
    _insert_job(db, "b", bundle_id="b-2", type_="patch")
    _insert_job(db, "c", bundle_id="b-1-similar", type_="patch")
    rows = list_jobs_for_bundle(db, "b-1")
    assert [r["job_id"] for r in rows] == ["a"]
