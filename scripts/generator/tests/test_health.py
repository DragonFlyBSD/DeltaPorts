"""Unit tests for dportsv3.agent.health.

Each concrete check is exercised under mocked subprocess outcomes
(no real chroot, no real dportsv3 CLI). The aggregate ``check()``
function gets coverage for selection (``only=``), per-check
exception handling, and status aggregation.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from dportsv3.agent import health


# --- helpers ------------------------------------------------------------------


@dataclass
class _FakeCP:
    """Stub for subprocess.CompletedProcess."""
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _patch_run(monkeypatch, fn):
    """Replace health._run_in_env with fn(env, *argv, timeout=...)."""
    monkeypatch.setattr(health, "_run_in_env", fn)


# --- HealthCheck / EnvHealth shape -------------------------------------------


def test_envhealth_ready_with_no_broken_checks():
    eh = health.EnvHealth(
        env="x", status="ready",
        checks=[health.HealthCheck("a", "ok"), health.HealthCheck("b", "ok")],
    )
    assert eh.is_ready()
    assert eh.to_dict()["status"] == "ready"


def test_envhealth_to_json_roundtrip():
    eh = health.EnvHealth(
        env="x", status="broken",
        checks=[health.HealthCheck("a", "broken", "boom", "do x")],
        operator_action="do x",
        probed_at="2026-05-21T00:00:00Z",
    )
    data = json.loads(eh.to_json())
    assert data["status"] == "broken"
    assert data["checks"][0]["operator_action"] == "do x"


def test_aggregate_broken_wins():
    s = health._aggregate([
        health.HealthCheck("a", "ok"),
        health.HealthCheck("b", "broken"),
        health.HealthCheck("c", "warn"),
    ])
    assert s == "broken"


def test_aggregate_warn_when_no_broken():
    s = health._aggregate([
        health.HealthCheck("a", "ok"),
        health.HealthCheck("b", "warn"),
    ])
    assert s == "degraded"


def test_aggregate_ready_all_ok():
    s = health._aggregate([
        health.HealthCheck("a", "ok"),
        health.HealthCheck("b", "ok"),
    ])
    assert s == "ready"


# --- python_runtime ----------------------------------------------------------


def test_python_runtime_all_present(monkeypatch):
    _patch_run(monkeypatch, lambda *a, **kw: _FakeCP(returncode=0))
    c = health._check_python_runtime("env")
    assert c.status == "ok"
    assert "packages present" in c.detail
    assert "runtime profile" in c.detail


def test_python_runtime_missing_some(monkeypatch):
    """Aggregate fails, per-pkg loop identifies which are missing."""
    calls = {"n": 0}
    missing = {"py311-sqlite3", "py311-pydantic2"}

    def fake(env, *argv, **kw):
        calls["n"] += 1
        # First call is the bulk `pkg info -e <all>` — return rc=70.
        if calls["n"] == 1:
            return _FakeCP(returncode=70, stderr="pkg: not installed: ...")
        # Subsequent calls are per-pkg. The argument after "info -e" is the pkg name.
        pkg = argv[-1]
        return _FakeCP(returncode=1 if pkg in missing else 0)

    _patch_run(monkeypatch, fake)
    c = health._check_python_runtime("env")
    assert c.status == "broken"
    assert "py311-sqlite3" in c.detail
    assert "py311-pydantic2" in c.detail
    assert c.operator_action == "recreate the env"


def test_python_runtime_timeout(monkeypatch):
    def fake(env, *argv, **kw):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kw.get("timeout", 0))
    _patch_run(monkeypatch, fake)
    c = health._check_python_runtime("env")
    assert c.status == "broken"
    assert "timed out" in c.detail


def test_python_runtime_cli_missing(monkeypatch):
    def fake(env, *argv, **kw):
        raise FileNotFoundError("dportsv3 not on PATH")
    _patch_run(monkeypatch, fake)
    c = health._check_python_runtime("env")
    assert c.status == "broken"
    assert "DPORTSV3_CMD" in (c.operator_action or "")


# --- writable_overlay --------------------------------------------------------


def test_writable_overlay_touches_sentinel(tmp_path, monkeypatch):
    @dataclass
    class _Paths:
        env_dir: Path
        writable: Path

    # Make the env_paths import resolve to a tmp path
    monkeypatch.setattr(
        "dportsv3.agent.worker.env_paths",
        lambda env: _Paths(env_dir=tmp_path, writable=tmp_path / "writable"),
    )
    (tmp_path / "writable").mkdir()
    c = health._check_writable_overlay("env")
    assert c.status == "ok"
    # Sentinel cleaned up
    assert not (tmp_path / "writable" / "work" / ".health" / "probe").exists()


def test_writable_overlay_paths_resolution_failure(monkeypatch):
    def boom(env):
        raise RuntimeError("env not mounted")
    monkeypatch.setattr("dportsv3.agent.worker.env_paths", boom)
    c = health._check_writable_overlay("env")
    assert c.status == "broken"
    assert "could not resolve env paths" in c.detail


def test_writable_overlay_readonly(tmp_path, monkeypatch):
    @dataclass
    class _Paths:
        env_dir: Path
        writable: Path

    ro_writable = tmp_path / "writable"
    ro_writable.mkdir()
    # Make the writable dir read-only so mkdir of sentinel fails.
    (ro_writable / "work").mkdir(mode=0o555)
    monkeypatch.setattr(
        "dportsv3.agent.worker.env_paths",
        lambda env: _Paths(env_dir=tmp_path, writable=ro_writable),
    )
    c = health._check_writable_overlay("env")
    assert c.status == "broken"
    assert "sentinel touch failed" in c.detail
    # Cleanup for tmp_path teardown
    (ro_writable / "work").chmod(0o755)


# --- dports_compose ----------------------------------------------------------


def test_dports_compose_ok(monkeypatch):
    _patch_run(monkeypatch,
               lambda *a, **kw: _FakeCP(returncode=0, stdout="dportsv3 0.4.2\n"))
    c = health._check_dports_compose("env")
    assert c.status == "ok"
    assert "dportsv3 0.4.2" in c.detail


def test_dports_compose_broken(monkeypatch):
    _patch_run(monkeypatch,
               lambda *a, **kw: _FakeCP(
                   returncode=1,
                   stderr="dportsv3: missing DragonFly packages required\n"))
    c = health._check_dports_compose("env")
    assert c.status == "broken"
    assert "missing DragonFly packages" in c.detail


# --- aggregate check() -------------------------------------------------------


def test_check_runs_all_three(monkeypatch, tmp_path):
    @dataclass
    class _Paths:
        env_dir: Path
        writable: Path

    (tmp_path / "writable").mkdir()
    monkeypatch.setattr(
        "dportsv3.agent.worker.env_paths",
        lambda env: _Paths(env_dir=tmp_path, writable=tmp_path / "writable"),
    )
    _patch_run(monkeypatch, lambda *a, **kw: _FakeCP(returncode=0))

    eh = health.check("env-x")
    assert eh.env == "env-x"
    assert eh.status == "ready"
    assert {c.name for c in eh.checks} == {
        "python_runtime", "writable_overlay", "dports_compose",
    }
    assert eh.operator_action is None
    assert eh.probed_at  # ISO timestamp set


def test_check_only_subset(monkeypatch):
    _patch_run(monkeypatch, lambda *a, **kw: _FakeCP(returncode=0))
    eh = health.check("env-x", only=["python_runtime"])
    assert [c.name for c in eh.checks] == ["python_runtime"]


def test_check_unknown_only_silently_ignored(monkeypatch):
    _patch_run(monkeypatch, lambda *a, **kw: _FakeCP(returncode=0))
    eh = health.check("env-x", only=["nope", "python_runtime"])
    assert [c.name for c in eh.checks] == ["python_runtime"]


def test_check_propagates_broken_to_aggregate(monkeypatch, tmp_path):
    @dataclass
    class _Paths:
        env_dir: Path
        writable: Path

    (tmp_path / "writable").mkdir()
    monkeypatch.setattr(
        "dportsv3.agent.worker.env_paths",
        lambda env: _Paths(env_dir=tmp_path, writable=tmp_path / "writable"),
    )

    def fake(env, *argv, **kw):
        # python_runtime bulk check fails; per-pkg loop says all missing.
        if argv[0] == "pkg":
            return _FakeCP(returncode=1)
        return _FakeCP(returncode=0)

    _patch_run(monkeypatch, fake)
    eh = health.check("env-x")
    assert eh.status == "broken"
    # operator_action populated from the first broken check
    assert eh.operator_action is not None
    assert eh.operator_action == "recreate the env"


def test_check_swallows_per_check_exceptions(monkeypatch):
    """A buggy check shouldn't crash the probe; it should land as a
    broken HealthCheck with the exception type + message in detail."""
    def boom(env):
        raise ValueError("synthetic")
    # Patch the dispatch-table entry directly. The dict captured the
    # function reference at import time, so module-level setattr
    # wouldn't propagate.
    monkeypatch.setitem(health._CHECKS, "python_runtime", boom)

    eh = health.check("env-x", only=["python_runtime"])
    assert eh.status == "broken"
    assert eh.checks[0].name == "python_runtime"
    assert "ValueError" in eh.checks[0].detail
    assert "synthetic" in eh.checks[0].detail
