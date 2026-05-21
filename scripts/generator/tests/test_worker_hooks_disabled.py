"""Agent-driven ``dsynth_build`` must set DPORTSV3_HOOKS_DISABLED=1.

There is one dev-env per target — the same env the operator uses for
production dsynth runs has the dsynth hooks installed. Without this
guard, every failed dsynth_build the patch agent runs would fire
hook_pkg_failure, upload a new bundle, and the runner would enqueue
another triage job for an origin the agent is already actively
patching. That loop is unbounded in the worst case.

hook_common.sh checks DPORTSV3_HOOKS_DISABLED at the top and exits 0
if set, so all hook variants short-circuit when the agent sets the var.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


_GEN = Path(__file__).resolve().parents[1]
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))


def test_dsynth_build_sets_hooks_disabled(monkeypatch):
    """worker.dsynth_build must prefix its in-chroot shell command
    with ``DPORTSV3_HOOKS_DISABLED=1`` so dsynth's hook scripts no-op."""
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

    worker.dsynth_build("test-env", "devel/foo")

    # The sh -c payload is argv[2] (argv = ('/bin/sh', '-c', PAYLOAD, '_', origin)).
    assert captured["argv"][0] == "/bin/sh"
    assert captured["argv"][1] == "-c"
    payload = captured["argv"][2]
    assert "DPORTSV3_HOOKS_DISABLED=1" in payload, (
        "dsynth_build must disable hooks to prevent the agent's "
        "failing builds from looping back into a new triage job"
    )
    # Sanity: env var must come BEFORE the dsynth invocation so the
    # child process inherits it.
    assert payload.find("DPORTSV3_HOOKS_DISABLED=1") < payload.find("dsynth")


def test_hook_common_short_circuits_when_disabled(tmp_path):
    """Sourcing hook_common.sh with DPORTSV3_HOOKS_DISABLED=1 must exit
    immediately so the sourcing hook script never reaches its body."""
    repo_root = Path(__file__).resolve().parents[3]
    hook_common = repo_root / "scripts" / "dsynth-hooks" / "hook_common.sh"
    assert hook_common.is_file(), f"missing hook_common.sh at {hook_common}"

    # A tiny driver that sources hook_common.sh then prints "REACHED".
    # If the early-exit guard fires, "REACHED" must not appear.
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

    # With the guard set, hook_common.sh exits 0 — driver never prints.
    result = subprocess.run(
        ["/bin/sh", str(driver)],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "DPORTSV3_HOOKS_DISABLED": "1"},
    )
    assert result.returncode == 0
    assert "REACHED" not in result.stdout

    # Without the guard, hook_common.sh proceeds normally — driver
    # reaches "REACHED". (May print other things from hook_common.sh
    # setup; we only assert REACHED is in stdout.)
    result = subprocess.run(
        ["/bin/sh", str(driver)],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "REACHED" in result.stdout
