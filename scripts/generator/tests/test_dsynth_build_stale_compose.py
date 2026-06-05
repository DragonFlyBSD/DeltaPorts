"""Tests for the stale-compose guard on worker.dsynth_build.

Regression for devel_gperf-20260526-064013Z: dsynth_build claimed
rebuild_ok=true against a pre-corruption compose tree even though
materialize_dports had failed 3× mid-attempt. The guard refuses
when the substrate's port-subtree hash doesn't match what the
last successful materialize_dports recorded.
"""

from __future__ import annotations

import subprocess

import pytest

from dportsv3.agent import worker


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Each test starts with an empty _MATERIALIZE_STATE so prior
    test runs don't leak baselines into this one."""
    monkeypatch.setattr(worker, "_MATERIALIZE_STATE", {})


def _fake_exec_returning(rc: int, stdout: str = "", stderr: str = ""):
    def _exec(env, *argv, **kw):
        return subprocess.CompletedProcess(
            args=argv, returncode=rc, stdout=stdout, stderr=stderr,
        )
    return _exec


def test_dsynth_refused_without_prior_materialize(monkeypatch):
    """No baseline → no proof the compose tree matches the substrate.
    Conservative refusal; agent must call materialize_dports first."""
    monkeypatch.setattr(worker, "_exec", _fake_exec_returning(0))
    monkeypatch.setattr(worker, "_dsynth_log_path", lambda o: "/tmp/log")

    result = worker.dsynth_build("test-env", "devel/foo")
    assert result["ok"] is False
    assert result["rebuild_ok"] is False
    assert result["blocked_by"] == "stale_compose"
    assert "materialize_dports" in result["error"]


def test_dsynth_refused_when_substrate_changed_since_materialize(monkeypatch):
    """Baseline recorded but the port subtree's content hash has
    changed since (agent edited overlay.dops etc.).
    Compose tree on disk is stale → refuse, ask for re-materialize."""
    monkeypatch.setattr(worker, "_exec", _fake_exec_returning(0))
    monkeypatch.setattr(worker, "_dsynth_log_path", lambda o: "/tmp/log")
    # Pretend materialize succeeded with hash A; now substrate is B.
    monkeypatch.setitem(worker._MATERIALIZE_STATE,
                        ("test-env", "devel/foo"), "a" * 64)
    monkeypatch.setattr(worker, "_port_subtree_hash",
                        lambda env, origin: "b" * 64)

    result = worker.dsynth_build("test-env", "devel/foo")
    assert result["ok"] is False
    assert result["rebuild_ok"] is False
    assert result["blocked_by"] == "stale_compose"
    assert "has changed" in result["error"]


def test_dsynth_allowed_when_substrate_matches_baseline(monkeypatch):
    """Baseline recorded AND current hash matches → compose tree is
    fresh; dsynth proceeds normally."""
    monkeypatch.setattr(worker, "_exec", _fake_exec_returning(0))
    monkeypatch.setattr(worker, "_dsynth_log_path", lambda o: "/tmp/log")
    monkeypatch.setitem(worker._MATERIALIZE_STATE,
                        ("test-env", "devel/foo"), "match")
    monkeypatch.setattr(worker, "_port_subtree_hash",
                        lambda env, origin: "match")

    result = worker.dsynth_build("test-env", "devel/foo")
    assert result["rebuild_ok"] is True
    assert result.get("blocked_by") is None


def test_materialize_success_records_baseline(monkeypatch):
    """Successful materialize_dports stamps a baseline hash so the
    very next dsynth_build can proceed."""
    monkeypatch.setattr(worker, "_exec", _fake_exec_returning(0))
    monkeypatch.setattr(worker, "_port_subtree_hash",
                        lambda env, origin: "freshhash")

    result = worker.materialize_dports("test-env", "devel/foo")
    assert result["ok"] is True
    assert worker._MATERIALIZE_STATE[("test-env", "devel/foo")] == "freshhash"


def test_materialize_failure_clears_baseline(monkeypatch):
    """A failed materialize invalidates any prior baseline — the
    substrate might be in an intermediate state and the compose
    tree shouldn't be trusted for a subsequent dsynth_build."""
    monkeypatch.setitem(worker._MATERIALIZE_STATE,
                        ("test-env", "devel/foo"), "old-baseline")
    monkeypatch.setattr(worker, "_exec",
                        _fake_exec_returning(1, stderr="reapply failed"))

    result = worker.materialize_dports("test-env", "devel/foo")
    assert result["ok"] is False
    assert ("test-env", "devel/foo") not in worker._MATERIALIZE_STATE


def test_stale_compose_full_flow(monkeypatch):
    """End-to-end: materialize ok (record baseline) → substrate
    changes (simulated) → dsynth refuses → operator re-materializes
    → dsynth allowed."""
    # Phase 1: materialize records baseline 'v1'.
    monkeypatch.setattr(worker, "_exec", _fake_exec_returning(0))
    hash_state = {"value": "v1"}
    monkeypatch.setattr(worker, "_port_subtree_hash",
                        lambda env, origin: hash_state["value"])
    monkeypatch.setattr(worker, "_dsynth_log_path", lambda o: "/tmp/log")

    r1 = worker.materialize_dports("test-env", "devel/foo")
    assert r1["ok"] is True

    # Phase 2: substrate changes (hash flips to 'v2'); dsynth refuses.
    hash_state["value"] = "v2"
    r2 = worker.dsynth_build("test-env", "devel/foo")
    assert r2["rebuild_ok"] is False
    assert r2["blocked_by"] == "stale_compose"

    # Phase 3: agent re-materializes; baseline updates to 'v2'.
    r3 = worker.materialize_dports("test-env", "devel/foo")
    assert r3["ok"] is True
    assert worker._MATERIALIZE_STATE[("test-env", "devel/foo")] == "v2"

    # Phase 4: dsynth now allowed.
    r4 = worker.dsynth_build("test-env", "devel/foo")
    assert r4["rebuild_ok"] is True


def test_port_subtree_hash_uses_real_filesystem(monkeypatch, tmp_path):
    """Regression: the prior shell-pipeline implementation used
    `sort -z` + `xargs -0`, both GNU-only flags. On DragonFly's
    BSD `sort` the pipeline silently produced no hash, so every
    dsynth_build refused with `no successful materialize_dports`
    (archivers/liblz4 2026-05-26). The host-side Python
    implementation has no platform-specific tool dependencies.

    This test exercises the REAL _port_subtree_hash against a
    real filesystem (no monkeypatch), so a future regression that
    reintroduces shell-tool dependencies fails here."""
    # Build a tiny port subtree in tmp_path and point env_paths at it.
    deltaports = tmp_path / "DeltaPorts"
    port_dir = deltaports / "ports" / "devel" / "foo"
    port_dir.mkdir(parents=True)
    (port_dir / "overlay.dops").write_text(
        "port devel/foo\ntype port\ntarget @any\n"
        'reason "x"\n'
    )
    (port_dir / "dragonfly").mkdir()
    (port_dir / "dragonfly" / "patch-x.c").write_text("--- a\n+++ b\n")

    from types import SimpleNamespace
    paths = SimpleNamespace(deltaports=deltaports, env_dir=tmp_path,
                            writable=tmp_path)
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)

    # Sanity: the hash function (no monkeypatch) returns a truthy
    # hex digest. A regression that broke shell-tool portability
    # used to return "" here.
    h1 = worker._port_subtree_hash("env", "devel/foo")
    assert h1
    assert len(h1) == 64  # sha256 hex digest

    # Different content → different hash.
    (port_dir / "overlay.dops").write_text(
        "port devel/foo\ntype port\ntarget @any\n"
        'reason "x"\n\n'
        "mk add USES \"ssl\"\n"
    )
    h2 = worker._port_subtree_hash("env", "devel/foo")
    assert h2
    assert h2 != h1

    # Same content again → same hash (stable, not timestamp-y).
    h3 = worker._port_subtree_hash("env", "devel/foo")
    assert h3 == h2


def test_port_subtree_hash_returns_empty_for_missing_port(monkeypatch, tmp_path):
    """No port subtree on disk → empty hash. Caller treats this
    as `no valid baseline` and refuses dsynth_build."""
    deltaports = tmp_path / "DeltaPorts"
    deltaports.mkdir()
    from types import SimpleNamespace
    paths = SimpleNamespace(deltaports=deltaports, env_dir=tmp_path,
                            writable=tmp_path)
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)
    assert worker._port_subtree_hash("env", "devel/nonexistent") == ""


def test_baseline_isolated_per_env_origin(monkeypatch):
    """Two parallel jobs in different envs (or different origins)
    don't share baselines."""
    monkeypatch.setattr(worker, "_exec", _fake_exec_returning(0))
    hashes = {("env-a", "devel/foo"): "ha", ("env-b", "devel/bar"): "hb"}
    monkeypatch.setattr(worker, "_port_subtree_hash",
                        lambda env, origin: hashes.get((env, origin), "?"))

    worker.materialize_dports("env-a", "devel/foo")
    worker.materialize_dports("env-b", "devel/bar")
    assert worker._MATERIALIZE_STATE[("env-a", "devel/foo")] == "ha"
    assert worker._MATERIALIZE_STATE[("env-b", "devel/bar")] == "hb"
