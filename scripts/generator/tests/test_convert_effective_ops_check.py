"""Tests for _check_overlay_effective_ops — the post-compose check
that refuses convert success when every op in the overlay would
be filtered out by target-scope mismatch.

Regression for archivers/liblz4 post-Step-C: convert wrote
`target @main` for an unscoped Makefile.DragonFly, env was
`@2026Q2`, every op got I_APPLY_TARGET_MISMATCH at compose time,
composed Makefile lacked the `dfly-patch` target entirely — but
the convert reported success because reapply itself ran without
error.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from dportsv3.agent import runner as runner_mod
from dportsv3.agent import worker
from dportsv3.agent.runner import _check_overlay_effective_ops


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "DeltaPorts"
    (ws / "ports").mkdir(parents=True)
    subprocess.run(["git", "-C", str(ws), "init", "-q", "-b", "main"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(ws), "config", "user.name", "t"],
                   check=True)
    (ws / "README").write_text("baseline\n")
    subprocess.run(["git", "-C", str(ws), "add", "README"], check=True)
    subprocess.run(["git", "-C", str(ws), "commit", "-qm", "init"],
                   check=True)
    return ws


@pytest.fixture
def env_ws(tmp_path, monkeypatch):
    """Workspace + worker.env_paths stub so the helper can find
    overlay.dops without a real env."""
    ws = _make_workspace(tmp_path)
    paths = SimpleNamespace(deltaports=ws, env_dir=tmp_path,
                            writable=tmp_path)
    monkeypatch.setattr(worker, "env_paths", lambda env: paths)
    return ws


def _write_overlay(ws: Path, origin: str, contents: str) -> None:
    p = ws / "ports" / origin / "overlay.dops"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contents)


# --------------------------------------------------------------------
# Effective ops present
# --------------------------------------------------------------------


def test_returns_none_when_op_target_matches_env_target(env_ws):
    _write_overlay(env_ws, "devel/foo",
        "port devel/foo\ntype port\ntarget @2026Q2\n"
        'reason "x"\n\n'
        "mk set USES \"ssl\"\n"
    )
    assert _check_overlay_effective_ops(
        "env", "devel/foo", "@2026Q2",
    ) is None


def test_returns_none_when_op_target_is_any(env_ws):
    """The common case for unscoped legacy artifacts."""
    _write_overlay(env_ws, "devel/foo",
        "port devel/foo\ntype port\ntarget @any\n"
        'reason "x"\n\n'
        "mk set USES \"ssl\"\n"
    )
    assert _check_overlay_effective_ops(
        "env", "devel/foo", "@2026Q2",
    ) is None


def test_returns_none_when_mix_of_any_and_specific(env_ws):
    """Common-ops on @any + target-specific extras under a scope.
    At least one effective op → ok."""
    _write_overlay(env_ws, "devel/foo",
        "port devel/foo\ntype port\n"
        "target @any\n"
        'reason "x"\n\n'
        "mk set USES \"ssl\"\n"
        "target @main\n"
        "mk set CFLAGS \"-O0\"\n"
    )
    assert _check_overlay_effective_ops(
        "env", "devel/foo", "@2026Q2",
    ) is None


# --------------------------------------------------------------------
# Dead overlay refusals
# --------------------------------------------------------------------


def test_refuses_when_every_op_scoped_to_wrong_target(env_ws):
    """The liblz4 2026-05-26 bug: convert wrote `target @main`
    for an unscoped Makefile.DragonFly; env is @2026Q2; every op
    silently skipped at compose."""
    _write_overlay(env_ws, "devel/foo",
        "target @main\nport devel/foo\ntype port\n"
        'reason "test"\n\n'
        "mk target set dfly-patch <<'MK'\n"
        "\t${REINPLACE_CMD} 's|A|B|' x\n"
        "MK\n"
    )
    err = _check_overlay_effective_ops("env", "devel/foo", "@2026Q2")
    assert err is not None
    assert "every op in overlay.dops is scoped to" in err
    assert "@main" in err
    assert "@2026Q2" in err
    assert "Makefile.DragonFly" in err  # explains the likely cause


def test_refuses_when_overlay_has_zero_plan_ops(env_ws):
    """Empty conversion — just directives, no actual ops. Compose
    would have nothing to do; the operator should know."""
    _write_overlay(env_ws, "devel/foo",
        "port devel/foo\ntype port\ntarget @any\n"
        'reason "empty conversion"\n'
    )
    err = _check_overlay_effective_ops("env", "devel/foo", "@2026Q2")
    assert err is not None
    assert "zero plan ops" in err


def test_refuses_when_overlay_fails_to_parse(env_ws):
    """If the overlay reapply accepted but our in-process plan
    rejects, fail safe — don't claim convert succeeded."""
    _write_overlay(env_ws, "devel/foo",
        "port devel/foo\nthis is not valid dops syntax\n"
    )
    err = _check_overlay_effective_ops("env", "devel/foo", "@2026Q2")
    assert err is not None
    assert "failed in-process plan" in err


# --------------------------------------------------------------------
# Best-effort no-ops
# --------------------------------------------------------------------


def test_returns_none_when_no_overlay_file(env_ws):
    """No overlay.dops → reapply already decided what to do.
    Nothing for us to check."""
    assert _check_overlay_effective_ops(
        "env", "devel/foo", "@2026Q2",
    ) is None


def test_returns_none_when_env_target_unknown(env_ws):
    """No env target context → can't filter. Conservative: don't
    refuse. Compose's own diagnostics still surface mismatches."""
    _write_overlay(env_ws, "devel/foo",
        "target @main\nport devel/foo\ntype port\n"
        'reason "x"\n\n'
        "mk set USES \"ssl\"\n"
    )
    assert _check_overlay_effective_ops(
        "env", "devel/foo", "",
    ) is None
