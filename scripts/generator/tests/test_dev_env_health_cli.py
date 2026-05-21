"""Smoke test for the ``dportsv3 dev-env health NAME`` subcommand.

Phase 2 Step 3. The handler lives in the dev-env tool's package
(scripts/tools/dev-env/dports_dev_env/cli.py), which has its own
venv; we add it to sys.path on demand to test it from the
generator suite. The handler under test imports
``dportsv3.agent.health`` lazily, so monkeypatching that import is
how we drive the test.

What we cover:
- Status "ready" → exit 0, JSON contains the expected shape.
- Status "broken" → exit 1, operator_action surfaces.
- Status "degraded" → exit 2.
- The ``--no-indent`` path emits one-line JSON.
- ``--only`` is propagated to health.check.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

# The dev-env package isn't installed in the generator venv; reach
# it via sys.path. Cached at module load so each test sees the same path.
_DEV_ENV_PKG = (
    Path(__file__).resolve().parents[2] / "tools" / "dev-env"
)
if _DEV_ENV_PKG.is_dir() and str(_DEV_ENV_PKG) not in sys.path:
    sys.path.insert(0, str(_DEV_ENV_PKG))


@pytest.fixture
def cli_module():
    """Late import after sys.path is patched."""
    from dports_dev_env import cli  # noqa: WPS433 — intentional late import
    return cli


def _ns(**fields) -> argparse.Namespace:
    defaults = {"name": "env-x", "only": None, "no_indent": False}
    defaults.update(fields)
    return argparse.Namespace(**defaults)


def _stub_check(status, *, checks=None, operator_action=None):
    """Build a callable that mimics health.check(env, only=...)."""
    from dportsv3.agent import health as h
    eh = h.EnvHealth(
        env="env-x",
        status=status,
        checks=checks or [],
        operator_action=operator_action,
        probed_at="2026-05-21T00:00:00Z",
    )

    def _call(env, *, only=None):
        # Record the env + only-filter for the cmd_health test that
        # checks propagation.
        _call.last_env = env
        _call.last_only = only
        return eh
    _call.last_env = None
    _call.last_only = None
    return _call


# --- Tests --------------------------------------------------------------------


def test_ready_returns_exit_0(cli_module, monkeypatch, capsys):
    from dportsv3.agent import health
    monkeypatch.setattr(health, "check", _stub_check("ready"))

    rc = cli_module.cmd_health(_ns())
    assert rc == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["env"] == "env-x"
    assert data["status"] == "ready"
    assert data["operator_action"] is None


def test_broken_returns_exit_1_surfaces_action(cli_module, monkeypatch, capsys):
    from dportsv3.agent import health
    monkeypatch.setattr(
        health, "check",
        _stub_check(
            "broken",
            checks=[health.HealthCheck(
                name="python_runtime", status="broken",
                detail="missing: py311-sqlite3",
                operator_action="recreate the env",
            )],
            operator_action="recreate the env",
        ),
    )

    rc = cli_module.cmd_health(_ns())
    assert rc == 1

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["status"] == "broken"
    assert data["operator_action"] == "recreate the env"
    assert data["checks"][0]["name"] == "python_runtime"


def test_degraded_returns_exit_2(cli_module, monkeypatch, capsys):
    from dportsv3.agent import health
    monkeypatch.setattr(health, "check", _stub_check("degraded"))

    rc = cli_module.cmd_health(_ns())
    assert rc == 2


def test_no_indent_emits_one_line(cli_module, monkeypatch, capsys):
    from dportsv3.agent import health
    monkeypatch.setattr(health, "check", _stub_check("ready"))

    cli_module.cmd_health(_ns(no_indent=True))
    out = capsys.readouterr().out.strip()
    assert "\n" not in out
    # Still valid JSON.
    json.loads(out)


def test_only_filter_propagates(cli_module, monkeypatch, capsys):
    from dportsv3.agent import health
    stub = _stub_check("ready")
    monkeypatch.setattr(health, "check", stub)

    cli_module.cmd_health(_ns(only=["python_runtime"]))
    assert stub.last_only == ["python_runtime"]
    assert stub.last_env == "env-x"
