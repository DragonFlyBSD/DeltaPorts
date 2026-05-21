"""Smoke test for the runner module's import surface.

Phase 1 Step 3.5: ``scripts/agent-queue-runner`` moved into
``dportsv3.agent.runner`` as a proper module so tests can import
internals directly. Lock that in with a tiny import test.
"""

from __future__ import annotations

from pathlib import Path


def test_runner_module_imports():
    """The package import works at all (catches missing deps + syntax)."""
    from dportsv3.agent import runner  # noqa: F401


def test_runner_main_is_callable():
    """main() is the entrypoint; tests + the CLI subcommand both call it."""
    from dportsv3.agent.runner import main

    assert callable(main)


def test_runner_state_db_path_honors_env(monkeypatch, tmp_path):
    from dportsv3.agent.runner import get_state_db_path

    db_path = tmp_path / "custom-state.db"
    monkeypatch.setenv("DPORTSV3_STATE_DB", str(db_path))

    assert get_state_db_path(Path("/build/synth/logs/evidence/queue")) == db_path


def test_runner_state_db_path_falls_back_to_queue_parent(monkeypatch):
    from dportsv3.agent.runner import get_state_db_path

    monkeypatch.delenv("DPORTSV3_STATE_DB", raising=False)

    assert get_state_db_path(Path("/build/synth/logs/evidence/queue")) == Path(
        "/build/synth/logs/evidence/state.db"
    )


def test_runner_orchestration_helpers_importable():
    """Helpers used by the lifecycle integration tests must be
    importable without firing up the LLM, the DB, or the queue.

    Phase 5 Step 4: _completion_events_for retired — event firing
    moved into each Step's StepOutcome. The wrapper helpers
    process_*_job + _finish_orchestrator_run live in their place.
    """
    from dportsv3.agent.runner import (
        _apply_transition,           # lifecycle.apply wrapper
        _finish_orchestrator_run,    # extract events + sibling fan-out
        _register_new_job,           # initial HOOK_ENQUEUED + jobs metadata
        claim_next_job_batch,        # filesystem queue claim
        parse_job_file,              # .job file → dict
        process_job,                 # the orchestration entrypoint
        process_triage_job,          # orchestrator wrapper for TriageStep
        process_patch_job,           # orchestrator wrapper for PatchAttemptStep
    )

    for fn in (_apply_transition, _finish_orchestrator_run,
               _register_new_job, claim_next_job_batch, parse_job_file,
               process_job, process_triage_job, process_patch_job):
        assert callable(fn)


def test_step_outcome_events_populated_by_steps():
    """The Phase 5 cutover moved event mapping inside the Step
    classes. Smoke-check that TriageStep + PatchAttemptStep import
    cleanly — Step-class-specific behavior is covered by
    test_patch_step.py and the e2e suite.
    """
    from dportsv3.agent.steps import TriageStep, PatchAttemptStep
    assert TriageStep().name == "triage"
    assert PatchAttemptStep().name == "patch"


def test_init_state_db_creates_missing_file(tmp_path, monkeypatch):
    """If state.db doesn't exist, the runner must create + schema-init
    it instead of silently disabling writes. Otherwise a first-time
    runner on a clean host emits activity to runner.log while the
    tracker UI sees nothing — the bug surfaced during smoke."""
    from dportsv3.agent import runner

    db_path = tmp_path / "fresh-state.db"
    assert not db_path.exists()
    monkeypatch.setenv("DPORTSV3_STATE_DB", str(db_path))
    monkeypatch.setattr(runner, "_state_db_conn", None, raising=False)

    conn = runner.init_state_db(queue_root=tmp_path / "queue")
    assert conn is not None
    assert db_path.exists()
    # Schema actually applied — the canonical agentic tables exist.
    tables = {
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    for needed in ("bundles", "jobs", "job_events", "activity_log",
                   "user_context_requests"):
        assert needed in tables, f"missing table {needed!r}"


def test_init_state_db_opens_existing_file(tmp_path, monkeypatch):
    """Existing DB must not be re-initialized destructively. Pre-seed
    a row, run init_state_db, confirm the row is still there."""
    from dportsv3.agent import runner
    from dportsv3.db.schema import init_db as init_schema

    db_path = tmp_path / "existing.db"
    import sqlite3
    seed = sqlite3.connect(str(db_path))
    init_schema(seed)
    seed.execute(
        """INSERT INTO bundles (bundle_id, origin, result, last_seen_at)
           VALUES ('keep-me', 'devel/foo', 'failure', '2026-05-22T00:00:00Z')"""
    )
    seed.commit()
    seed.close()

    monkeypatch.setenv("DPORTSV3_STATE_DB", str(db_path))
    monkeypatch.setattr(runner, "_state_db_conn", None, raising=False)
    conn = runner.init_state_db(queue_root=tmp_path / "queue")
    assert conn is not None
    row = conn.execute(
        "SELECT bundle_id FROM bundles WHERE bundle_id = 'keep-me'"
    ).fetchone()
    assert row is not None


def test_init_state_db_returns_none_when_parent_dir_missing(
    tmp_path, monkeypatch,
):
    """Real misconfig (parent dir doesn't exist) should not silently
    swallow — return None so the operator can fix the path."""
    from dportsv3.agent import runner

    bad = tmp_path / "nope" / "really-nope" / "state.db"
    monkeypatch.setenv("DPORTSV3_STATE_DB", str(bad))
    monkeypatch.setattr(runner, "_state_db_conn", None, raising=False)

    conn = runner.init_state_db(queue_root=tmp_path / "queue")
    assert conn is None
    assert not bad.exists()
