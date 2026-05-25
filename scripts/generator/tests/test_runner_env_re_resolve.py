"""Tests for the load-bearing UX promise of the env-active feature:
a tracker UI change to the active env takes effect on the runner
without restart.

The runner's gate calls :func:`runner.resolve_env_for_gate` every
poll. That helper caches resolve_env(None) for a short TTL so we
don't hammer the DB; UI changes must still propagate within the
cache window (1 s) and across the cache boundary (after expiry).
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from dportsv3.agent import runner
from dportsv3.db.schema import init_db
from dportsv3.tracker.agentic_queries import set_active_env


@pytest.fixture
def runner_db(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    monkeypatch.setattr(runner, "_state_db_conn", conn)
    # Reset cache between tests.
    monkeypatch.setattr(runner, "_GATE_RESOLVE_CACHE", None)
    yield conn
    conn.close()


def test_gate_resolve_picks_up_ui_change_after_ttl(runner_db, monkeypatch):
    """UI sets active env → next gate resolve (after TTL) returns
    the new value. Exercises the cache-expiry path explicitly."""
    # Make TTL tiny so the test doesn't sleep long.
    monkeypatch.setattr(runner, "_GATE_RESOLVE_TTL_SECONDS", 0.05)

    set_active_env(runner_db, "first-env")
    assert runner.resolve_env_for_gate() == "first-env"

    # Operator picks a different env in the UI.
    set_active_env(runner_db, "second-env")
    # Within the cache window: still the cached value.
    assert runner.resolve_env_for_gate() == "first-env"
    # After TTL: re-reads from DB, returns the new value.
    time.sleep(0.15)  # 3x TTL — survives CI scheduling jitter
    assert runner.resolve_env_for_gate() == "second-env"


def test_gate_resolve_picks_up_clear_after_ttl(runner_db, monkeypatch):
    """Setting the active env to None (clear) must also propagate."""
    monkeypatch.setattr(runner, "_GATE_RESOLVE_TTL_SECONDS", 0.05)

    set_active_env(runner_db, "alpha")
    assert runner.resolve_env_for_gate() == "alpha"

    set_active_env(runner_db, None)
    time.sleep(0.15)  # 3x TTL — survives CI scheduling jitter
    assert runner.resolve_env_for_gate() is None


def test_uncached_resolve_env_sees_change_immediately(runner_db):
    """The non-gate resolve_env (used in job dispatch) has no
    cache layer — UI changes are reflected on the very next call.
    This is the contract for per-job env resolution."""
    set_active_env(runner_db, "alpha")
    assert runner.resolve_env({}) == "alpha"
    set_active_env(runner_db, "beta")
    assert runner.resolve_env({}) == "beta"  # no TTL wait


def test_gate_cache_hit_does_not_hit_db(runner_db, monkeypatch):
    """Within the TTL window, resolve_env_for_gate must not re-read
    the DB. Asserted by stubbing resolve_env to count calls."""
    monkeypatch.setattr(runner, "_GATE_RESOLVE_TTL_SECONDS", 60.0)
    calls = {"n": 0}
    def counting_resolve(job):
        calls["n"] += 1
        return "cached-env"
    monkeypatch.setattr(runner, "resolve_env", counting_resolve)
    # Force cache reset.
    monkeypatch.setattr(runner, "_GATE_RESOLVE_CACHE", None)
    for _ in range(10):
        assert runner.resolve_env_for_gate() == "cached-env"
    assert calls["n"] == 1  # first call populated; rest hit cache
