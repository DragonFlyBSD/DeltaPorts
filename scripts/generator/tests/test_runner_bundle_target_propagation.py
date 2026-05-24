"""Tests for the bundle-target inheritance landed to fix the missing
"Lifetime token cost" card. ``token_usage_for_port`` joins
``activity_log`` to ``jobs`` and filters by the bundle's target; if
jobs land with ``target=NULL`` the JOIN returns zero rows and the
card is silently suppressed. Verified against the
``archivers_liblz4-20260524-183031Z`` bundle.

The runner-side enqueue paths now look up ``bundles.target`` via
``_lookup_bundle_target`` and write it into ``jobs.target`` instead
of leaving it NULL whenever ``DPORTSV3_TRACKER_TARGET`` isn't set.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.db.schema import init_db


@pytest.fixture
def state_db(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    monkeypatch.setattr(runner_mod, "_state_db_conn", conn)
    yield conn
    conn.close()


def _insert_bundle(conn, bundle_id: str, target: str, origin: str) -> None:
    conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at)
           VALUES (?, '', ?, '', '', 'failure', ?, '', '')""",
        (bundle_id, origin, target),
    )


def test_lookup_bundle_target_returns_target(state_db) -> None:
    _insert_bundle(state_db, "b-1", "@2026Q2", "archivers/liblz4")
    assert runner_mod._lookup_bundle_target("b-1") == "@2026Q2"


def test_lookup_bundle_target_empty_for_unknown(state_db) -> None:
    assert runner_mod._lookup_bundle_target("nope") == ""
    assert runner_mod._lookup_bundle_target(None) == ""
    assert runner_mod._lookup_bundle_target("") == ""


def test_register_new_job_inherits_target_from_bundle(state_db) -> None:
    """The liblz4 symptom: hook fires HOOK_ENQUEUED with empty
    target; the bundle has it; the job should pick it up."""
    _insert_bundle(state_db, "b-2", "@2026Q2", "devel/foo")

    runner_mod._register_new_job(
        "job-no-target.job",
        metadata={
            "type": "triage",
            "origin": "devel/foo",
            "bundle_id": "b-2",
            # target intentionally absent — simulates hook with
            # DPORTSV3_TRACKER_TARGET unset
        },
    )

    row = state_db.execute(
        "SELECT target FROM jobs WHERE job_id = ?",
        ("job-no-target.job",),
    ).fetchone()
    assert row["target"] == "@2026Q2"


def test_register_new_job_explicit_target_wins(state_db) -> None:
    """Explicit target overrides the bundle lookup."""
    _insert_bundle(state_db, "b-3", "@2026Q2", "devel/foo")

    runner_mod._register_new_job(
        "job-with-target.job",
        metadata={
            "type": "triage",
            "origin": "devel/foo",
            "bundle_id": "b-3",
            "target": "@main",
        },
    )

    row = state_db.execute(
        "SELECT target FROM jobs WHERE job_id = ?",
        ("job-with-target.job",),
    ).fetchone()
    assert row["target"] == "@main"


def test_register_new_job_no_bundle_leaves_target_null(state_db) -> None:
    """No bundle, no target → no fabricated value (NULLIF guard
    preserves prior column state)."""
    runner_mod._register_new_job(
        "job-orphan.job",
        metadata={"type": "convert", "origin": "devel/foo"},
    )

    row = state_db.execute(
        "SELECT target FROM jobs WHERE job_id = ?",
        ("job-orphan.job",),
    ).fetchone()
    assert row["target"] in (None, "")


def test_enqueue_patch_job_writes_target_into_jobfile(
    tmp_path: Path, monkeypatch, state_db,
) -> None:
    """The .job file content for a patch job now carries ``target=``,
    so steps.py finds it and proposed_fix.md no longer renders
    ``Target: (none)`` when the bundle has a real target."""
    monkeypatch.delenv("DPORTSV3_TRACKER_TARGET", raising=False)
    _insert_bundle(state_db, "b-4", "@2026Q2", "devel/foo")

    queue_root = tmp_path / "queue"
    (queue_root / "pending").mkdir(parents=True)

    parent_job = {
        "origin": "devel/foo",
        "profile": "main",
        "flavor": "devel/foo",
        "bundle_id": "b-4",
        "run_id": "r-1",
        # parent triage carried no target either — patch should
        # still resolve via bundle lookup
    }
    patch_path = runner_mod.enqueue_patch_job(queue_root, parent_job)

    content = patch_path.read_text()
    assert "target=@2026Q2" in content
