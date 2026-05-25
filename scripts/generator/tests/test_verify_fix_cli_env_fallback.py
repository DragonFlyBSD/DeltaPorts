"""Tests for the verify-fix CLI's --env fallback to tracker's
active env."""

from __future__ import annotations

import argparse
from types import SimpleNamespace

import pytest

from dportsv3 import verify_fix as vf


def _make_args(env=None, tracker_url=None):
    return SimpleNamespace(
        bundle_id="b-1", env=env, tracker_url=tracker_url,
        keep_log=False, json=False,
    )


def test_resolve_env_returns_name_from_tracker(monkeypatch):
    def fake_get(url, timeout=5):
        assert url.endswith("/api/config/active-env")
        return {"name": "2026Q2"}
    monkeypatch.setattr(vf, "_get_json", fake_get)
    assert vf._resolve_env_from_tracker("http://t:1") == "2026Q2"


def test_resolve_env_returns_none_when_unset(monkeypatch):
    monkeypatch.setattr(vf, "_get_json", lambda url, timeout=5: {"name": None})
    assert vf._resolve_env_from_tracker("http://t:1") is None


def test_resolve_env_returns_none_when_empty_string(monkeypatch):
    monkeypatch.setattr(vf, "_get_json", lambda url, timeout=5: {"name": ""})
    assert vf._resolve_env_from_tracker("http://t:1") is None


def test_resolve_env_returns_none_when_tracker_unreachable(monkeypatch):
    def boom(url, timeout=5):
        raise OSError("connection refused")
    monkeypatch.setattr(vf, "_get_json", boom)
    assert vf._resolve_env_from_tracker("http://t:1") is None


def test_cli_uses_explicit_env_arg(monkeypatch):
    """When --env is provided, no tracker fallback fires."""
    calls = []
    monkeypatch.setattr(
        vf, "_resolve_env_from_tracker",
        lambda url: calls.append(url) or "should-not-be-used",
    )
    monkeypatch.setattr(
        vf, "run_verify_fix",
        lambda **kw: SimpleNamespace(
            bundle_id="b-1", origin="x/y", env=kw["env"],
            ok=True, dsynth_exit=0, posted=True, log_path=None,
            __dict__={"ok": True, "env": kw["env"]},
        ),
    )
    vf.cmd_verify_fix(_make_args(env="explicit-env"))
    assert calls == []  # fallback never consulted


def test_cli_falls_back_when_env_omitted(monkeypatch):
    monkeypatch.setattr(
        vf, "_resolve_env_from_tracker",
        lambda url: "from-tracker",
    )
    captured = {}
    def fake_run(**kw):
        captured["env"] = kw["env"]
        return SimpleNamespace(
            bundle_id="b-1", origin="x/y", env=kw["env"],
            ok=True, dsynth_exit=0, posted=True, log_path=None,
            __dict__={"ok": True, "env": kw["env"]},
        )
    monkeypatch.setattr(vf, "run_verify_fix", fake_run)
    vf.cmd_verify_fix(_make_args(env=None))
    assert captured["env"] == "from-tracker"


def test_cli_errors_when_neither_arg_nor_tracker_has_env(monkeypatch):
    monkeypatch.setattr(
        vf, "_resolve_env_from_tracker", lambda url: None,
    )
    with pytest.raises(SystemExit) as exc:
        vf.cmd_verify_fix(_make_args(env=None))
    msg = str(exc.value)
    assert "--env" in msg
    assert "active env" in msg
