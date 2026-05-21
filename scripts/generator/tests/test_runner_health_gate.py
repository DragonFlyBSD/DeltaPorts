"""Health-gate integration tests for the runner.

Phase 2 Step 4. Exercises the module-level pieces of the runner's
health-aware gate:

- ``probe_health_cached`` honors its TTL: within the window, two
  calls hit the underlying ``health.check`` exactly once.
- After TTL expiry, the next call re-probes.
- ``invalidate_health_cache(env)`` drops a single env's entry; the
  next call re-probes regardless of TTL.
- ``invalidate_health_cache()`` (no arg) drops everything.
- The cache stores per-env entries independently.

The ``_gate_blocked`` closure inside ``main()`` isn't directly
exercised here (it depends on local nonlocals + module-level state
that don't compose cleanly into a unit test). Its behavior is
covered by the existing e2e test in test_runner_e2e_lifecycle.py.
This file is the contract for the cache layer the gate depends on.
"""

from __future__ import annotations

import time
import sqlite3
from dataclasses import dataclass

import pytest

from dportsv3.agent import health, runner
from dportsv3.db.schema import init_db as init_state_db


@dataclass
class _FakeHealth:
    """Minimal stand-in for health.EnvHealth (status is all we read)."""
    env: str
    status: str = "ready"


@pytest.fixture(autouse=True)
def _reset_cache():
    """Each test starts with an empty health cache."""
    runner.invalidate_health_cache()
    yield
    runner.invalidate_health_cache()


def _patch_check(monkeypatch, *, returns=None, counter=None):
    """Replace health.check with a stub.

    ``returns`` is a callable env->FakeHealth, or a single FakeHealth
    used for every call. ``counter`` is a list whose append() tracks
    each invocation.
    """
    calls = counter if counter is not None else []

    def _fake(env, *, only=None):
        calls.append(env)
        if callable(returns):
            return returns(env)
        return returns if returns is not None else _FakeHealth(env=env, status="ready")

    monkeypatch.setattr(health, "check", _fake)
    return calls


def test_first_probe_calls_check_and_caches(monkeypatch):
    calls = _patch_check(monkeypatch)
    eh = runner.probe_health_cached("env-x", ttl_seconds=60)
    assert eh.status == "ready"
    assert calls == ["env-x"]
    # Cache populated
    assert "env-x" in runner._health_cache


def test_within_ttl_no_reprobe(monkeypatch):
    calls = _patch_check(monkeypatch)
    runner.probe_health_cached("env-x", ttl_seconds=60)
    runner.probe_health_cached("env-x", ttl_seconds=60)
    runner.probe_health_cached("env-x", ttl_seconds=60)
    assert calls == ["env-x"]  # one underlying call


def test_after_ttl_reprobes(monkeypatch):
    calls = _patch_check(monkeypatch)
    runner.probe_health_cached("env-x", ttl_seconds=60)
    # Backdate the cache entry past the TTL by editing the timestamp
    # — simpler than waiting wall-clock seconds.
    ts, eh = runner._health_cache["env-x"]
    runner._health_cache["env-x"] = (ts - 120.0, eh)
    runner.probe_health_cached("env-x", ttl_seconds=60)
    assert calls == ["env-x", "env-x"]


def test_invalidate_one_env(monkeypatch):
    calls = _patch_check(monkeypatch)
    runner.probe_health_cached("a", ttl_seconds=60)
    runner.probe_health_cached("b", ttl_seconds=60)
    runner.invalidate_health_cache("a")
    runner.probe_health_cached("a", ttl_seconds=60)  # re-probes
    runner.probe_health_cached("b", ttl_seconds=60)  # still cached
    assert calls == ["a", "b", "a"]


def test_invalidate_all(monkeypatch):
    calls = _patch_check(monkeypatch)
    runner.probe_health_cached("a", ttl_seconds=60)
    runner.probe_health_cached("b", ttl_seconds=60)
    runner.invalidate_health_cache()
    runner.probe_health_cached("a", ttl_seconds=60)
    runner.probe_health_cached("b", ttl_seconds=60)
    assert calls == ["a", "b", "a", "b"]


def test_separate_env_entries(monkeypatch):
    def by_env(env):
        return _FakeHealth(env=env, status="broken" if env == "bad" else "ready")
    calls = _patch_check(monkeypatch, returns=by_env)

    good = runner.probe_health_cached("good", ttl_seconds=60)
    bad = runner.probe_health_cached("bad", ttl_seconds=60)

    assert good.status == "ready"
    assert bad.status == "broken"
    # Each env gets its own cache entry.
    assert set(runner._health_cache.keys()) == {"good", "bad"}


def test_cached_health_broken_reads_cache_only(monkeypatch):
    """_cached_health_broken doesn't trigger a probe."""
    calls = _patch_check(monkeypatch, returns=_FakeHealth(env="x", status="broken"))
    # Empty cache → no broken
    assert runner._cached_health_broken() is False
    assert calls == []

    runner.probe_health_cached("x", ttl_seconds=60)
    assert calls == ["x"]
    assert runner._cached_health_broken() is True
    assert runner._cached_health_broken("x") is True
    assert runner._cached_health_broken("other") is False
    # Still only one underlying call.
    assert calls == ["x"]


def test_broken_status_caches_like_ready(monkeypatch):
    """Broken probes cache the same way ready does — we don't re-probe
    repeatedly while broken (operator fixes it, force-invalidate
    triggers the next probe)."""
    calls = _patch_check(monkeypatch, returns=_FakeHealth(env="x", status="broken"))
    runner.probe_health_cached("x", ttl_seconds=60)
    runner.probe_health_cached("x", ttl_seconds=60)
    runner.probe_health_cached("x", ttl_seconds=60)
    assert calls == ["x"]  # exactly one


def test_force_reprobe_via_invalidate(monkeypatch):
    """The intended workflow when a tool error invalidates the cache:
    next probe sees the freshly-broken (or freshly-ready) env."""
    states = iter(["ready", "broken"])

    def _seq(env, *, only=None):
        return _FakeHealth(env=env, status=next(states))

    monkeypatch.setattr(health, "check", _seq)

    first = runner.probe_health_cached("x", ttl_seconds=60)
    assert first.status == "ready"
    # Operator runs `pkg delete py311-sqlite3`; a tool error trips
    # _looks_env_suspicious → invalidate_health_cache():
    runner.invalidate_health_cache()
    second = runner.probe_health_cached("x", ttl_seconds=60)
    assert second.status == "broken"


def test_probe_persists_env_health_status(tmp_path, monkeypatch):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    init_state_db(conn)
    monkeypatch.setattr(runner, "_state_db_conn", conn, raising=False)
    try:
        calls = _patch_check(monkeypatch, returns=_FakeHealth(env="persist-env", status="broken"))

        runner.probe_health_cached("persist-env", ttl_seconds=60)

        assert calls == ["persist-env"]
        row = conn.execute(
            "SELECT env, status, detail_json FROM env_health_status WHERE env = ?",
            ("persist-env",),
        ).fetchone()
        assert row["env"] == "persist-env"
        assert row["status"] == "broken"
        assert "persist-env" in row["detail_json"]
    finally:
        monkeypatch.setattr(runner, "_state_db_conn", None, raising=False)
        conn.close()
