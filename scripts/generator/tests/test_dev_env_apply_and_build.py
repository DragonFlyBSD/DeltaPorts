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
    # reapply + dtest, then post-build cleanup (substrate reset +
    # WRKDIR clean) which now runs for every path.
    assert len(fake_env.calls) == 4
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
    # apply, reapply, dtest, then post-build cleanup (2 calls).
    assert len(fake_env.calls) == 5
    apply_argv = " ".join(fake_env.calls[0]["argv"])
    assert "git apply --3way" in apply_argv
    assert "/work/.apply-and-build.diff" in apply_argv
    # Staged file should have been cleaned up after the apply.
    assert not (fake_env.writable / "work" / ".apply-and-build.diff").exists()


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
    # apply failed → reapply + dtest skipped. Post-build cleanup
    # still runs in the finally, but the build stages don't.
    all_argv = [" ".join(c["argv"]) for c in fake_env.calls]
    assert any("git apply --3way" in a for a in all_argv)
    assert not any("reapply devel/foo" in a for a in all_argv)
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


def _write_intent_log(tmp_path: Path, origin: str = "devel/foo") -> Path:
    """Minimal valid intent-log file. Replay validates
    ``doc["origin"] == origin``; an empty ``baseline_commit``
    bypasses the env-HEAD drift check (which would otherwise
    require a real git repo in the fake env). Zero intents
    means the replay loop runs once with nothing to apply and
    returns ok=0 — the substrate is unchanged but cleanup still
    fires."""
    p = tmp_path / "intent_log.json"
    p.write_text(
        '{"origin": "' + origin + '", '
        '"baseline_commit": "", '
        '"mode_at_apply": "dops", '
        '"intents": []}\n'
    )
    return p


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


def test_intent_log_path_runs_substrate_reset_then_make_clean(
    fake_env, monkeypatch, tmp_path,
) -> None:
    """Q1 follow-up: post-build cleanup for the intent-log path
    runs the substrate reset AND ``make clean``. Without the
    second stage, /work/obj/<origin>/ carries stale extracted
    source into the next verify run on the same env."""
    from dports_dev_env.cli import apply_and_build

    intent_log = _write_intent_log(tmp_path)

    # Both stages succeed; runner.outcomes is empty so the default
    # rc=0 is returned for every runner.run call.
    apply_and_build(
        fake_env.env_name, "devel/foo",
        intent_log_path=str(intent_log),
    )

    shell_calls = _post_build_calls(fake_env.calls)
    # Find the cleanup commands by content. The substrate reset
    # carries `git reset --hard`; the WRKDIR clean carries
    # `make` + `WRKDIRPREFIX`.
    substrate = [
        c[2] for c in shell_calls if "git reset --hard" in c[2]
    ]
    wrkdir = [
        c[2] for c in shell_calls
        if "WRKDIRPREFIX=/work/obj" in c[2] and "clean" in c[2]
    ]
    assert substrate, "substrate reset command not observed"
    assert wrkdir, "WRKDIR make clean command not observed"
    # Substrate reset comes before the WRKDIR wipe — if substrate
    # fails the WRKDIR wipe is skipped, so this ordering matters.
    assert (
        fake_env.calls.index(
            next(c for c in fake_env.calls
                 if "git reset --hard" in (c["argv"][2] if len(c["argv"]) > 2 else ""))
        )
        < fake_env.calls.index(
            next(c for c in fake_env.calls
                 if "WRKDIRPREFIX=/work/obj" in (c["argv"][2] if len(c["argv"]) > 2 else ""))
        )
    )


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
    assert any("git reset --hard" in c[2] for c in shell_calls)
    assert any(
        "WRKDIRPREFIX=/work/obj" in c[2] and "clean" in c[2]
        for c in shell_calls
    )


def test_make_clean_skipped_when_substrate_reset_fails(
    fake_env, monkeypatch, tmp_path,
) -> None:
    """Substrate reset failure should not cascade — skip the
    WRKDIR wipe and surface the substrate-reset stderr to the
    operator. Matches worker.reset_port's ordering."""
    from dports_dev_env.cli import apply_and_build
    import subprocess as _sp

    intent_log = _write_intent_log(tmp_path)

    # Queue outcomes so the substrate reset fails. The runner
    # consumes outcomes from a list, in order — earlier reapply +
    # dtest stages consume the first two, then the substrate
    # cleanup gets rc=1.
    fake_env.runner.outcomes = [
        _sp.CompletedProcess([], 0, "", ""),     # reapply
        _sp.CompletedProcess([], 0, "", ""),     # dsynth (dtest)
        _sp.CompletedProcess(                    # substrate reset → fails
            [], 1, "", "fatal: not a git repository",
        ),
    ]

    apply_and_build(
        fake_env.env_name, "devel/foo",
        intent_log_path=str(intent_log),
    )

    shell_calls = _post_build_calls(fake_env.calls)
    assert any("git reset --hard" in c[2] for c in shell_calls)
    # WRKDIR wipe must not fire when substrate reset failed.
    assert not any(
        "WRKDIRPREFIX=/work/obj" in c[2] and "clean" in c[2]
        for c in shell_calls
    )
