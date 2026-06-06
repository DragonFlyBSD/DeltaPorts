"""Tests for `dportsv3 dev-env apply-and-build` (plan Step 11b
Slice 1).

The command is a substrate primitive — no bundles, no tracker, no
artifact-store. It optionally applies a diff to the env's
DeltaPorts overlay, runs `reapply ORIGIN`, then `dtest ORIGIN`
(dsynth test), capturing combined output to a log file under writable.

We can't run a real DragonFly chroot in CI, so we mock
``ChrootRunner`` + ``EnvironmentSession.prepare`` and assert on the
command surface, return JSON, exit codes, and the diff staging
mechanics.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


_DEV_ENV_PKG = Path(__file__).resolve().parents[2] / "tools" / "dev-env"
if _DEV_ENV_PKG.is_dir() and str(_DEV_ENV_PKG) not in sys.path:
    sys.path.insert(0, str(_DEV_ENV_PKG))


@pytest.fixture
def fake_env(tmp_path: Path, monkeypatch):
    """Mount a fake env layout + monkeypatch every chroot/session hop
    so `cmd_apply_and_build` runs end-to-end against the filesystem
    without needing a real DragonFly env."""
    from dports_dev_env import cli

    env_name = "verify-test"
    env_dir = tmp_path / "envs" / env_name
    writable = env_dir / "writable"
    (writable / "work" / "DeltaPorts").mkdir(parents=True)
    (writable / "work" / "artifacts").mkdir(parents=True)

    monkeypatch.setattr(cli, "require_root", lambda: None)
    monkeypatch.setattr(cli, "load_config", lambda: SimpleNamespace(cache_root=tmp_path))
    monkeypatch.setattr(cli, "validate_cache_root", lambda _root: None)

    class _Store:
        def __init__(self, _config):
            pass

        def env_dir(self, _name):
            return env_dir

        def writable_dir(self, _name):
            return writable

    monkeypatch.setattr(cli, "EnvironmentStore", _Store)

    state = SimpleNamespace(
        name=env_name,
        root_dir=env_dir / "root",
        target="@main",
        origin=None,
        backend="chroot",
        status="ready",
    )

    class _Session:
        def __init__(self, _config, _store):
            pass

        def prepare(self, _name):
            return state

    monkeypatch.setattr(cli, "EnvironmentSession", _Session)

    # Capture ChrootRunner invocations so tests can inspect what
    # would have run in the chroot.
    calls: list[dict] = []

    class _Runner:
        def __init__(self, root_dir):
            self._root_dir = root_dir
            self.outcomes: list[subprocess.CompletedProcess] = []

        def run(self, argv, env=None, capture_output=False, check=False):
            calls.append({
                "argv": list(argv),
                "capture": capture_output,
                "env_keys": sorted((env or {}).keys()),
            })
            if self.outcomes:
                return self.outcomes.pop(0)
            return subprocess.CompletedProcess(argv, 0, "", "")

    runner = _Runner(state.root_dir)

    def _make_runner(_root_dir):
        return runner

    # The cmd does `from .chroot import ChrootRunner, chroot_env` and
    # `from .helpers import build_env_dict` inside the function. Patch
    # via the source modules so the local imports pick up the fakes.
    from dports_dev_env import chroot as _chroot_mod
    from dports_dev_env import helpers as _helpers_mod
    monkeypatch.setattr(_chroot_mod, "ChrootRunner", _make_runner)
    monkeypatch.setattr(_chroot_mod, "chroot_env", lambda: {"PATH": "/usr/local/bin:/usr/bin:/bin"})
    monkeypatch.setattr(_helpers_mod, "build_env_dict", lambda _state: {"DPORTS_TARGET": "@main"})

    return SimpleNamespace(
        env_dir=env_dir,
        writable=writable,
        calls=calls,
        runner=runner,
        env_name=env_name,
    )


def _args(name: str, origin: str, **kw) -> argparse.Namespace:
    return argparse.Namespace(
        name=name, origin=origin,
        diff=kw.get("diff"),
        json=kw.get("json", False),
    )


def _capture_stdout(monkeypatch) -> io.StringIO:
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    return buf


def test_no_diff_happy_path_returns_zero(fake_env, monkeypatch) -> None:
    from dports_dev_env.cli import cmd_apply_and_build
    out = _capture_stdout(monkeypatch)

    rc = cmd_apply_and_build(_args(fake_env.env_name, "devel/foo", json=True))

    assert rc == 0
    result = json.loads(out.getvalue())
    assert result["ok"] is True
    assert result["apply_exit"] is None
    assert result["reapply_exit"] == 0
    assert result["dsynth_exit"] == 0
    assert result["applied_diff_sha256"] is None
    assert "apply-and-build-devel_foo.log" in result["log_path"]
    # reapply + dtest, then post-build cleanup (WRKDIR clean +
    # substrate reset + baseline reapply) which now runs for every
    # path.
    assert len(fake_env.calls) == 5
    reapply_argv = " ".join(fake_env.calls[0]["argv"])
    dtest_argv = " ".join(fake_env.calls[1]["argv"])
    assert "reapply devel/foo" in reapply_argv
    assert "dtest devel/foo" in dtest_argv
    # The verify build suppresses the dsynth failure hooks (sentinel
    # file + trap) so a failed verify doesn't upload a new bundle and
    # re-trigger triage for an origin the loop is already handling.
    assert ".dports-agent-hooks-disabled" in dtest_argv
    assert "trap" in dtest_argv and "EXIT" in dtest_argv


def test_diff_path_is_staged_into_writable_and_applied(fake_env, monkeypatch, tmp_path) -> None:
    from dports_dev_env.cli import cmd_apply_and_build
    _capture_stdout(monkeypatch)

    diff = tmp_path / "fix.diff"
    diff.write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+2\n")

    rc = cmd_apply_and_build(_args(fake_env.env_name, "devel/foo",
                                   diff=str(diff), json=True))

    assert rc == 0
    # apply, reapply, dtest, then post-build cleanup (3 calls:
    # WRKDIR wipe + substrate reset + baseline reapply).
    assert len(fake_env.calls) == 6
    apply_argv = " ".join(fake_env.calls[0]["argv"])
    assert "git apply --3way" in apply_argv
    assert "/work/.apply-and-build.diff" in apply_argv
    # Staged file should have been cleaned up after the apply.
    assert not (fake_env.writable / "work" / ".apply-and-build.diff").exists()


def test_diff_mode_refuses_dirty_port_tree(fake_env, monkeypatch, tmp_path) -> None:
    """Diff replay must refuse when ports/<origin>/ is dirty —
    applying onto stale state would make the verdict meaningless. The
    refusal happens BEFORE any chroot work, so no apply/reapply/dtest
    AND no cleanup runs (cleanup would reset the operator's state)."""
    from dports_dev_env import cli
    from dports_dev_env.cli import cmd_apply_and_build
    out = _capture_stdout(monkeypatch)

    monkeypatch.setattr(
        cli, "_port_dirty_paths",
        lambda workspace, origin: [" M ports/devel/foo/Makefile"],
    )
    diff = tmp_path / "fix.diff"
    diff.write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+2\n")

    rc = cmd_apply_and_build(_args(fake_env.env_name, "devel/foo",
                                   diff=str(diff), json=True))

    assert rc == 1
    result = json.loads(out.getvalue())
    assert result["apply_exit"] == 1
    assert result["ok"] is False
    assert result["dirty_paths"] == [" M ports/devel/foo/Makefile"]
    # Refused before touching the chroot: no apply, no cleanup.
    all_argv = [" ".join(c["argv"]) for c in fake_env.calls]
    assert not any("git apply --3way" in a for a in all_argv)
    assert not any("git reset" in a for a in all_argv)


def test_diff_apply_failure_short_circuits(fake_env, monkeypatch, tmp_path) -> None:
    from dports_dev_env.cli import cmd_apply_and_build
    out = _capture_stdout(monkeypatch)

    # First call (git apply) returns non-zero with an error.
    fake_env.runner.outcomes = [
        subprocess.CompletedProcess([], 1, "", "error: patch failed"),
    ]
    diff = tmp_path / "bad.diff"
    diff.write_bytes(b"not actually a diff\n")

    rc = cmd_apply_and_build(_args(fake_env.env_name, "devel/foo",
                                   diff=str(diff), json=True))

    assert rc == 1
    result = json.loads(out.getvalue())
    assert result["apply_exit"] == 1
    assert result["reapply_exit"] is None
    assert result["dsynth_exit"] is None
    assert result["ok"] is False
    # apply failed → build-stage reapply + dtest skipped. Post-build
    # cleanup still runs in the finally; its baseline reapply IS
    # expected, so we count occurrences rather than asserting absence
    # (the build-stage and cleanup-stage reapplies produce identical
    # argv).
    all_argv = [" ".join(c["argv"]) for c in fake_env.calls]
    assert any("git apply --3way" in a for a in all_argv)
    reapply_calls = [a for a in all_argv if "reapply devel/foo" in a]
    assert len(reapply_calls) == 1  # only the cleanup-stage reapply
    assert not any("dtest devel/foo" in a for a in all_argv)
    # sha256 still recorded so the orchestrator can dedupe.
    assert result["applied_diff_sha256"] is not None


def test_reapply_failure_short_circuits_dsynth(fake_env, monkeypatch) -> None:
    from dports_dev_env.cli import cmd_apply_and_build
    out = _capture_stdout(monkeypatch)

    # Reapply (first call here, no --diff) returns non-zero.
    fake_env.runner.outcomes = [
        subprocess.CompletedProcess([], 2, "", "compose failed"),
    ]
    rc = cmd_apply_and_build(_args(fake_env.env_name, "devel/foo", json=True))

    # CLI exit is binary (0 if ok, 1 otherwise) so shell pipelines
    # behave consistently. The per-stage exit code lives in the
    # JSON for callers who want it.
    assert rc == 1
    result = json.loads(out.getvalue())
    assert result["reapply_exit"] == 2
    assert result["dsynth_exit"] is None
    assert result["ok"] is False
    # reapply failed → dtest skipped (cleanup still runs in finally).
    all_argv = [" ".join(c["argv"]) for c in fake_env.calls]
    assert any("reapply devel/foo" in a for a in all_argv)
    assert not any("dtest devel/foo" in a for a in all_argv)


def test_dsynth_failure_reports_ok_false_but_returns_one(fake_env, monkeypatch) -> None:
    from dports_dev_env.cli import cmd_apply_and_build
    out = _capture_stdout(monkeypatch)

    # reapply ok, dtest fails with 1.
    fake_env.runner.outcomes = [
        subprocess.CompletedProcess([], 0, "", ""),
        subprocess.CompletedProcess([], 1, "", ""),
    ]
    rc = cmd_apply_and_build(_args(fake_env.env_name, "devel/foo", json=True))

    assert rc == 1
    result = json.loads(out.getvalue())
    assert result["reapply_exit"] == 0
    assert result["dsynth_exit"] == 1
    assert result["ok"] is False
    assert result["log_path"] is not None


def test_missing_diff_raises_usage_error(fake_env, monkeypatch) -> None:
    from dports_dev_env.cli import cmd_apply_and_build
    from dports_dev_env.errors import UsageError
    _capture_stdout(monkeypatch)

    with pytest.raises(UsageError, match="--diff: file not found"):
        cmd_apply_and_build(_args(fake_env.env_name, "devel/foo",
                                  diff="/nonexistent/path.diff",
                                  json=True))


def test_non_json_output_is_one_line_summary(fake_env, monkeypatch) -> None:
    from dports_dev_env.cli import cmd_apply_and_build
    out = _capture_stdout(monkeypatch)

    rc = cmd_apply_and_build(_args(fake_env.env_name, "devel/foo", json=False))

    assert rc == 0
    text = out.getvalue().strip()
    assert "\n" not in text  # one line
    assert "ok=True" in text
    assert "dsynth=0" in text
    assert "reapply=0" in text
    assert "log=" in text


# --- Q1 follow-up: post-build cleanup wipes substrate + WRKDIR -------------


def _post_build_calls(calls: list[dict]) -> list[list[str]]:
    """Pick out the runner.run invocations that look like the
    post-build cleanup shell commands. Both stages route through
    ``/bin/sh -c <cmd> _``."""
    out: list[list[str]] = []
    for c in calls:
        argv = c["argv"]
        if (
            len(argv) >= 3
            and argv[0] == "/bin/sh"
            and argv[1] == "-c"
        ):
            out.append(argv)
    return out


def test_diff_path_runs_post_build_cleanup(
    fake_env, monkeypatch, tmp_path,
) -> None:
    """Post-build cleanup runs for the diff path too. Pre-slice-5
    the diff path was exempted, but ``--diff`` is now the only
    verify path, so skipping cleanup left ports/<origin>/ + the
    WRKDIR dirty after every verify (and blocked the runner's
    verify-branch drop, which needs a clean tree to switch off
    the throwaway branch)."""
    from dports_dev_env.cli import apply_and_build

    diff = tmp_path / "fix.diff"
    diff.write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+2\n")
    apply_and_build(fake_env.env_name, "devel/foo", diff_path=str(diff))

    shell_calls = _post_build_calls(fake_env.calls)
    assert any("git reset -q --" in c[2] for c in shell_calls)
    assert any(
        "WRKDIRPREFIX=/work/obj" in c[2] and "clean" in c[2]
        for c in shell_calls
    )


def test_baseline_reapply_skipped_when_substrate_reset_fails(
    fake_env, monkeypatch, tmp_path,
) -> None:
    """Substrate reset failure should not cascade — skip the
    baseline reapply (would compose against a half-reset substrate)
    and surface the substrate-reset stderr to the operator. Matches
    worker.reset_port's ordering."""
    from dports_dev_env.cli import apply_and_build
    import subprocess as _sp

    diff = tmp_path / "fix.diff"
    diff.write_text("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-1\n+2\n")

    # Queue outcomes so the substrate reset fails. The runner
    # consumes outcomes from a list, in order — earlier apply +
    # reapply + dtest + WRKDIR-wipe stages consume the first ones,
    # then the substrate reset gets rc=1.
    fake_env.runner.outcomes = [
        _sp.CompletedProcess([], 0, "", ""),     # diff apply
        _sp.CompletedProcess([], 0, "", ""),     # reapply
        _sp.CompletedProcess([], 0, "", ""),     # dsynth (dtest)
        _sp.CompletedProcess([], 0, "", ""),     # WRKDIR wipe
        _sp.CompletedProcess(                    # substrate reset → fails
            [], 1, "", "fatal: not a git repository",
        ),
    ]

    apply_and_build(
        fake_env.env_name, "devel/foo",
        diff_path=str(diff),
    )

    shell_calls = _post_build_calls(fake_env.calls)
    assert any("git reset -q --" in c[2] for c in shell_calls)
    # WRKDIR wipe ran (before substrate reset).
    assert any(
        "WRKDIRPREFIX=/work/obj" in c[2] and "clean" in c[2]
        for c in shell_calls
    )
    # Baseline reapply must NOT fire when substrate reset failed —
    # otherwise we'd compose against a half-reset substrate. Look
    # for the cleanup-stage reapply specifically (the build-stage
    # `reapply devel/foo` ran before the failure).
    cleanup_reapplies = [
        c for c in shell_calls
        if "reapply devel/foo" in c[2]
        and "git reset" not in c[2]
        and "git checkout" not in c[2]
    ]
    # The first match is the build-stage reapply; the cleanup-stage
    # reapply would be a second one. Substrate-reset failure → only
    # the build-stage occurrence is present.
    assert len(cleanup_reapplies) == 1


def test_cleanup_reapply_failure_does_not_flip_ok(
    fake_env, monkeypatch,
) -> None:
    """Cleanup-stage ``reapply`` failure means baseline HEAD itself
    doesn't compose — that was the state when verify started, so it
    isn't a regression we should mask as a cleanup failure. Surface
    it as a warning in ``stderr_tail`` but don't flip ``ok``."""
    from dports_dev_env.cli import apply_and_build
    import subprocess as _sp

    # No-diff path: 5 chroot calls — reapply (build), dtest, WRKDIR
    # wipe, substrate reset, cleanup reapply. Make the last one fail
    # while everything before it succeeds.
    fake_env.runner.outcomes = [
        _sp.CompletedProcess([], 0, "", ""),     # reapply (build)
        _sp.CompletedProcess([], 0, "", ""),     # dtest
        _sp.CompletedProcess([], 0, "", ""),     # WRKDIR wipe
        _sp.CompletedProcess([], 0, "", ""),     # substrate reset
        _sp.CompletedProcess(                    # cleanup reapply → fails
            [], 2, "", "compose: E_COMPOSE_APPLY_FAILED on ports/devel/foo",
        ),
    ]

    result = apply_and_build(fake_env.env_name, "devel/foo")

    # Build phases all succeeded → ok stays True. Cleanup-reapply
    # failure is informational.
    assert result["ok"] is True
    assert result["dsynth_exit"] == 0
    # Warning surfaced in stderr_tail with the failure-mode tag and
    # the underlying reapply stderr.
    tail = result.get("stderr_tail") or ""
    assert "post-build reapply failed" in tail
    assert "E_COMPOSE_APPLY_FAILED" in tail
