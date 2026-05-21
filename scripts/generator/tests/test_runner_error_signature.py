"""Step 6 — runner-side ``error_signature`` backfill.

The hook doesn't compute the signature. The runner backfills lazily
the first time ``PortHistory.load`` runs for an origin: it reads
``logs/errors.txt`` for each recent failure bundle that's missing a
signature, hashes the first non-empty line, and UPDATEs.

Without this, ``sticky_signature`` would never fire because every
signature would be NULL.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from dportsv3.agent import runner
from dportsv3.db.schema import init_db as init_state_db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def runner_db(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_state_db(conn)
    monkeypatch.setattr(runner, "_state_db_conn", conn, raising=False)
    yield conn
    conn.close()


def _seed_bundle(conn, bundle_id, origin, target, errors_text):
    conn.execute(
        """INSERT INTO bundles
           (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at)
           VALUES (?, 'r1', ?, '', ?, 'failure', ?, ?)""",
        (bundle_id, origin, _now(), target, _now()),
    )
    conn.commit()
    return errors_text


def test_compute_error_signature_first_nonempty_line():
    assert runner._compute_error_signature(None) is None
    assert runner._compute_error_signature("") is None
    assert runner._compute_error_signature("   \n\n\n") is None

    sig_a = runner._compute_error_signature("cc: error: foo\nrest\n")
    sig_b = runner._compute_error_signature("\n\ncc: error: foo\nelse\n")
    sig_c = runner._compute_error_signature("cc: error: bar\n")
    assert sig_a == sig_b               # leading blanks ignored
    assert sig_a != sig_c               # different first line → different hash
    assert len(sig_a) == 16             # 16-hex-char digest


def test_ensure_recent_signatures_populates_nulls(runner_db, monkeypatch):
    _seed_bundle(runner_db, "b1", "devel/foo", "@2026Q2", "cc: error A\n")
    _seed_bundle(runner_db, "b2", "devel/foo", "@2026Q2", "cc: error B\n")

    # Fake the artifact-store read so the helper doesn't need network.
    def fake_read(bundle_dir, bundle_id, relpath):
        assert relpath == "logs/errors.txt"
        return {"b1": "cc: error A\n", "b2": "cc: error B\n"}.get(bundle_id)

    monkeypatch.setattr(runner, "read_bundle_text", fake_read)

    runner._ensure_recent_signatures("@2026Q2", "devel/foo", window_hours=2)

    rows = runner_db.execute(
        "SELECT bundle_id, error_signature FROM bundles ORDER BY bundle_id"
    ).fetchall()
    sigs = {r["bundle_id"]: r["error_signature"] for r in rows}
    assert sigs["b1"] is not None
    assert sigs["b2"] is not None
    assert sigs["b1"] != sigs["b2"]


def test_ensure_recent_signatures_idempotent(runner_db, monkeypatch):
    """A second pass must not overwrite already-populated signatures
    (only NULL rows are scanned)."""
    _seed_bundle(runner_db, "b1", "devel/foo", "@2026Q2", "cc: error A\n")
    runner_db.execute(
        "UPDATE bundles SET error_signature = 'pinned' WHERE bundle_id = 'b1'"
    )
    runner_db.commit()

    calls = {"n": 0}

    def fake_read(bundle_dir, bundle_id, relpath):
        calls["n"] += 1
        return "different\n"

    monkeypatch.setattr(runner, "read_bundle_text", fake_read)
    runner._ensure_recent_signatures("@2026Q2", "devel/foo", window_hours=2)

    assert calls["n"] == 0  # nothing to backfill
    row = runner_db.execute(
        "SELECT error_signature FROM bundles WHERE bundle_id = 'b1'"
    ).fetchone()
    assert row["error_signature"] == "pinned"


def test_ensure_recent_signatures_skips_unreadable_artifact(runner_db, monkeypatch):
    """Artifact-store unreachable / file missing → signature stays NULL;
    helper doesn't crash."""
    _seed_bundle(runner_db, "b-missing", "devel/foo", "@2026Q2", "")
    monkeypatch.setattr(runner, "read_bundle_text",
                        lambda *_a, **_kw: None)

    runner._ensure_recent_signatures("@2026Q2", "devel/foo", window_hours=2)

    row = runner_db.execute(
        "SELECT error_signature FROM bundles WHERE bundle_id = 'b-missing'"
    ).fetchone()
    assert row["error_signature"] is None


def test_ensure_recent_signatures_respects_window(runner_db, monkeypatch):
    """Old bundles (outside the window) shouldn't get backfilled —
    they're not eligible for the sticky-signature check anyway."""
    from datetime import timedelta
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    runner_db.execute(
        """INSERT INTO bundles
           (bundle_id, run_id, origin, flavor, ts_utc, result, target, last_seen_at)
           VALUES (?, 'r1', ?, '', ?, 'failure', ?, ?)""",
        ("old", "devel/foo", long_ago, "@2026Q2", long_ago),
    )
    runner_db.commit()

    monkeypatch.setattr(runner, "read_bundle_text",
                        lambda *_a, **_kw: "cc: error\n")
    runner._ensure_recent_signatures("@2026Q2", "devel/foo", window_hours=2)

    row = runner_db.execute(
        "SELECT error_signature FROM bundles WHERE bundle_id = 'old'"
    ).fetchone()
    assert row["error_signature"] is None  # outside window, untouched
