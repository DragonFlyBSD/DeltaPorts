"""Agent-driven ``dsynth_build`` must short-circuit dsynth-hooks.

There is one dev-env per target — the same env the operator uses for
production dsynth runs has the dsynth hooks installed. Without this
guard, every failed dsynth_build the patch agent runs would fire
hook_pkg_failure, upload a new bundle, and the runner would enqueue
another triage job for an origin the agent is already actively
patching. That loop is unbounded in the worst case.

The first attempt at this used a ``DPORTSV3_HOOKS_DISABLED=1`` env
var, but dsynth strips arbitrary env vars before invoking hooks
(it passes only PROFILE / DIR_LOGS / etc.). The current mechanism
is a flag file at ``/work/.dports-agent-hooks-disabled`` that the
agent creates before ``dsynth build`` and removes via shell trap.
dsynth has no business stripping filesystem state.

``DPORTSV3_HOOKS_FLAG_FILE`` overrides the path; production hooks
see the env var stripped and fall back to the default, but tests
(which source hook_common.sh from a regular shell) can point it at
a tmpdir.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


_GEN = Path(__file__).resolve().parents[1]
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))


def test_dsynth_build_creates_and_removes_flag_file(monkeypatch):
    """worker.dsynth_build must wrap its in-chroot shell command in a
    flag-file create + trap-on-EXIT cleanup so dsynth's hook scripts
    no-op for the duration of the build."""
    from dportsv3.agent import worker

    captured: dict = {}

    def fake_exec(env, *argv, cwd="/work/DeltaPorts",
                  input_text=None, timeout=None):
        captured["env"] = env
        captured["argv"] = argv
        return subprocess.CompletedProcess(args=argv, returncode=0,
                                            stdout="", stderr="")

    monkeypatch.setattr(worker, "_exec", fake_exec)
    monkeypatch.setattr(worker, "_dsynth_log_path", lambda origin: "/tmp/log")
    # Stale-compose guard requires a prior successful materialize.
    # Pre-seed the baseline so this test exercises the dsynth invocation
    # path, not the new freshness refusal.
    monkeypatch.setattr(worker, "_port_subtree_hash",
                        lambda env, origin: "deadbeef")
    monkeypatch.setitem(worker._MATERIALIZE_STATE,
                        ("test-env", "devel/foo"), "deadbeef")

    worker.dsynth_build("test-env", "devel/foo")

    # The sh -c payload is argv[2] (argv = ('/bin/sh', '-c', PAYLOAD, '_', origin)).
    assert captured["argv"][0] == "/bin/sh"
    assert captured["argv"][1] == "-c"
    payload = captured["argv"][2]
    assert "/work/.dports-agent-hooks-disabled" in payload, (
        "dsynth_build must touch the flag file so hooks no-op during "
        "the agent's own dsynth invocation"
    )
    # Trap-on-EXIT cleans up even if dsynth exits non-zero.
    assert "trap" in payload
    assert "rm -f" in payload
    # Flag creation must come BEFORE the dsynth invocation.
    assert payload.find(".dports-agent-hooks-disabled") < payload.find("dsynth")


def test_hook_common_short_circuits_when_flag_present(tmp_path):
    """Sourcing hook_common.sh with the flag file present must exit 0
    so the sourcing hook script never reaches its body."""
    repo_root = Path(__file__).resolve().parents[3]
    hook_common = repo_root / "scripts" / "dsynth-hooks" / "hook_common.sh"
    assert hook_common.is_file(), f"missing hook_common.sh at {hook_common}"

    # Tiny driver: source hook_common.sh, then echo REACHED.
    driver = tmp_path / "driver.sh"
    driver.write_text(
        f"""#!/bin/sh
set -eu
. {hook_common}
echo REACHED
""",
        encoding="utf-8",
    )
    driver.chmod(0o755)

    flag = tmp_path / "flag"

    # No flag → hook_common.sh proceeds normally → driver reaches REACHED.
    result = subprocess.run(
        ["/bin/sh", str(driver)],
        capture_output=True, text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DPORTSV3_HOOKS_FLAG_FILE": str(flag),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "REACHED" in result.stdout

    # Flag created → hook_common.sh exits 0 immediately, never reaches body.
    flag.write_text("")
    result = subprocess.run(
        ["/bin/sh", str(driver)],
        capture_output=True, text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DPORTSV3_HOOKS_FLAG_FILE": str(flag),
        },
    )
    assert result.returncode == 0
    assert "REACHED" not in result.stdout

    # Flag removed → back to normal.
    flag.unlink()
    result = subprocess.run(
        ["/bin/sh", str(driver)],
        capture_output=True, text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "DPORTSV3_HOOKS_FLAG_FILE": str(flag),
        },
    )
    assert result.returncode == 0
    assert "REACHED" in result.stdout


def test_dsynth_build_payload_is_resilient_to_dsynth_failure(monkeypatch):
    """If dsynth exits non-zero, the trap must still rm the flag —
    otherwise a left-over flag would gag every subsequent legitimate
    operator dsynth on the same env. The shell pattern guarantees
    cleanup regardless of dsynth's exit code."""
    from dportsv3.agent import worker

    captured: dict = {}

    def fake_exec(env, *argv, **kw):
        captured["argv"] = argv
        return subprocess.CompletedProcess(args=argv, returncode=1,
                                            stdout="", stderr="boom")

    monkeypatch.setattr(worker, "_exec", fake_exec)
    monkeypatch.setattr(worker, "_dsynth_log_path", lambda origin: "/tmp/log")
    monkeypatch.setattr(worker, "_port_subtree_hash",
                        lambda env, origin: "deadbeef")
    monkeypatch.setitem(worker._MATERIALIZE_STATE,
                        ("test-env", "devel/foo"), "deadbeef")

    result = worker.dsynth_build("test-env", "devel/foo")
    assert result["rebuild_ok"] is False
    # The payload includes the trap regardless of dsynth's outcome.
    payload = captured["argv"][2]
    assert 'trap "rm -f' in payload
    assert "EXIT" in payload
