"""Tests for the per-job env resolver."""

from __future__ import annotations

import sqlite3

import pytest

from dportsv3.agent.env_resolver import (
    EnvResolution,
    resolve_env_for_job,
)


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from dportsv3.db.schema import init_db
    init_db(conn)
    return conn


def _set_tracker_active(db: sqlite3.Connection, name: str | None) -> None:
    from dportsv3.tracker.agentic_queries import set_active_env
    set_active_env(db, name)


# --------------------------------------------------------------------
# Precedence
# --------------------------------------------------------------------


def test_job_dev_env_wins_over_everything(db):
    _set_tracker_active(db, "tracker-env")
    r = resolve_env_for_job(
        {"dev_env": "from-job"}, db,
        cli_env="cli-env", available_envs=["only-env"],
    )
    assert r == EnvResolution(env="from-job", source="job")


def test_tracker_active_env_wins_over_cli_and_auto(db):
    _set_tracker_active(db, "tracker-env")
    r = resolve_env_for_job(
        {}, db, cli_env="cli-env", available_envs=["a", "b"],
    )
    assert r.env == "tracker-env"
    assert r.source == "tracker"


def test_cli_flag_wins_over_auto(db):
    r = resolve_env_for_job(
        {}, db, cli_env="cli-env", available_envs=["a", "b", "c"],
    )
    assert r.env == "cli-env"
    assert r.source == "cli_flag"


def test_auto_pick_when_single_env(db):
    r = resolve_env_for_job(
        {}, db, cli_env=None, available_envs=["only-env"],
    )
    assert r.env == "only-env"
    assert r.source == "auto"


def test_refuses_when_zero_envs(db):
    r = resolve_env_for_job({}, db, cli_env=None, available_envs=[])
    assert r.env is None
    assert r.source == "none"
    assert "no dev-envs exist" in r.refusal_reason


def test_refuses_when_multiple_envs_and_no_selection(db):
    r = resolve_env_for_job(
        {}, db, cli_env=None, available_envs=["alpha", "beta"],
    )
    assert r.env is None
    assert r.source == "none"
    assert "2 dev-envs exist" in r.refusal_reason
    assert "alpha" in r.refusal_reason
    assert "beta" in r.refusal_reason
    assert r.available_envs == ("alpha", "beta")


# --------------------------------------------------------------------
# Edge cases
# --------------------------------------------------------------------


def test_empty_string_in_job_dev_env_treated_as_unset(db):
    r = resolve_env_for_job(
        {"dev_env": ""}, db, cli_env="cli-env",
        available_envs=["a", "b"],
    )
    assert r.env == "cli-env"  # falls through to CLI flag
    assert r.source == "cli_flag"


def test_none_job_falls_through(db):
    r = resolve_env_for_job(
        None, db, cli_env="cli-env", available_envs=["a"],
    )
    assert r.env == "cli-env"


def test_no_db_conn_skips_tracker_step(db):
    r = resolve_env_for_job(
        {}, None, cli_env=None, available_envs=["only-env"],
    )
    # Should still auto-pick without crashing.
    assert r.env == "only-env"
    assert r.source == "auto"


def test_cleared_tracker_value_does_not_match(db):
    _set_tracker_active(db, "tracker-env")
    _set_tracker_active(db, None)
    r = resolve_env_for_job(
        {}, db, cli_env=None, available_envs=["only-env"],
    )
    assert r.env == "only-env"
    assert r.source == "auto"


def test_tracker_pointing_at_missing_env_still_returned(db):
    """Resolver doesn't validate the env exists on disk — caller's
    job to surface "env not found". Keeps the resolver pure."""
    _set_tracker_active(db, "ghost-env")
    r = resolve_env_for_job(
        {}, db, cli_env=None, available_envs=["real-env"],
    )
    assert r.env == "ghost-env"
    assert r.source == "tracker"
