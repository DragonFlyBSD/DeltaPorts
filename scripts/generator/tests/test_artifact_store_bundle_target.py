"""Tests for ArtifactStore.apply_transition's bundle-target fallback
(server-side counterpart to runner._lookup_bundle_target).

Hooks run with a possibly-empty DPORTSV3_TRACKER_TARGET env var, and
the artifact-store-client strips empty strings out of the detail
dict. Without a fallback, HOOK_ENQUEUED leaves jobs.target NULL even
when the bundle has a real target, and the tracker UI's
``token_usage_for_port`` JOIN excludes the job's llm_turn rows.

The hook now passes ``--bundle-id``; apply_transition consults
``bundles.target`` when ``detail.target`` is empty.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.artifact_store import ArtifactStore


def _insert_bundle(store: ArtifactStore, bundle_id: str, target: str) -> None:
    store.conn.execute(
        """INSERT INTO bundles (bundle_id, run_id, origin, flavor, ts_utc,
                                result, target, path, last_seen_at)
           VALUES (?, '', 'archivers/liblz4', '', '', 'failure', ?, '', '')""",
        (bundle_id, target),
    )
    store.conn.commit()


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "logs")


def test_hook_enqueued_inherits_target_from_bundle(store: ArtifactStore) -> None:
    """The liblz4 case: hook posts empty target, but bundle_id resolves
    to a bundle with @2026Q2."""
    _insert_bundle(store, "archivers_liblz4-20260524-183031Z", "@2026Q2")

    result = store.apply_transition({
        "job_id": "job-1.job",
        "event": "hook_enqueued",
        "actor": "hook",
        "detail": {
            "type": "triage",
            "origin": "archivers/liblz4",
            "bundle_id": "archivers_liblz4-20260524-183031Z",
            # target intentionally absent
        },
    })
    assert result["ok"]

    row = store.conn.execute(
        "SELECT target FROM jobs WHERE job_id = ?", ("job-1.job",),
    ).fetchone()
    assert row[0] == "@2026Q2"


def test_hook_enqueued_explicit_target_wins(store: ArtifactStore) -> None:
    _insert_bundle(store, "b-2", "@2026Q2")

    store.apply_transition({
        "job_id": "job-2.job",
        "event": "hook_enqueued",
        "actor": "hook",
        "detail": {
            "type": "triage",
            "origin": "archivers/liblz4",
            "bundle_id": "b-2",
            "target": "@main",
        },
    })

    row = store.conn.execute(
        "SELECT target FROM jobs WHERE job_id = ?", ("job-2.job",),
    ).fetchone()
    assert row[0] == "@main"


def test_hook_enqueued_no_bundle_no_target_leaves_null(
    store: ArtifactStore,
) -> None:
    store.apply_transition({
        "job_id": "job-3.job",
        "event": "hook_enqueued",
        "actor": "hook",
        "detail": {"type": "convert", "origin": "devel/foo"},
    })

    row = store.conn.execute(
        "SELECT target FROM jobs WHERE job_id = ?", ("job-3.job",),
    ).fetchone()
    assert row[0] in (None, "")
