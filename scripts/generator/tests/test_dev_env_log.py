"""Tests for the dev-env run-log context.

Covers the ``run_log_context`` redirector that ``cmd_create`` uses to
beautify output: phase markers + WARN/ERROR reach the user terminal;
INFO + subprocess output land in the per-invocation log file.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Reach into the dev-env package the same way test_dev_env_health_cli does.
_DEV_ENV_PKG = (
    Path(__file__).resolve().parents[2] / "tools" / "dev-env"
)
if _DEV_ENV_PKG.is_dir() and str(_DEV_ENV_PKG) not in sys.path:
    sys.path.insert(0, str(_DEV_ENV_PKG))


@pytest.fixture
def log_mod():
    from dports_dev_env import log
    return log


def _read_log(path: Path) -> str:
    return path.read_text(errors="replace")


# --- redirection -------------------------------------------------------------


def test_run_log_context_captures_print_to_log(tmp_path, log_mod, capsys):
    log_path = tmp_path / "create.log"

    with log_mod.run_log_context(log_path):
        print("captured stdout")
        print("captured stderr", file=sys.stderr)

    contents = _read_log(log_path)
    assert "captured stdout" in contents
    assert "captured stderr" in contents


def test_run_log_context_captures_subprocess_output(tmp_path, log_mod):
    log_path = tmp_path / "create.log"

    with log_mod.run_log_context(log_path):
        # Subprocess inherits the dup2'd fds; output should land in the log.
        subprocess.run([sys.executable, "-c",
                        "import sys; print('out-from-subp'); "
                        "print('err-from-subp', file=sys.stderr)"],
                       check=True)

    contents = _read_log(log_path)
    assert "out-from-subp" in contents
    assert "err-from-subp" in contents


def test_run_log_context_restores_streams(tmp_path, log_mod):
    log_path = tmp_path / "create.log"
    saved_stdout = sys.stdout
    saved_stderr = sys.stderr

    with log_mod.run_log_context(log_path):
        assert sys.stdout is not saved_stdout
        assert sys.stderr is not saved_stderr

    assert sys.stdout is saved_stdout
    assert sys.stderr is saved_stderr
    assert log_mod.current_log_path() is None


def test_nested_run_log_context_raises(tmp_path, log_mod):
    log_path = tmp_path / "create.log"
    with log_mod.run_log_context(log_path):
        with pytest.raises(RuntimeError, match="cannot nest"):
            with log_mod.run_log_context(tmp_path / "inner.log"):
                pass


def test_current_log_path_set_inside_context(tmp_path, log_mod):
    log_path = tmp_path / "create.log"
    assert log_mod.current_log_path() is None
    with log_mod.run_log_context(log_path):
        assert log_mod.current_log_path() == log_path


# --- phase / info / warn routing --------------------------------------------


def test_phase_writes_to_user_terminal_and_log(tmp_path, log_mod, capfd):
    """phase() should hit both the user terminal (original stderr) and
    the log file (via the dup2'd fd 2)."""
    log_path = tmp_path / "create.log"

    with log_mod.run_log_context(log_path):
        log_mod.phase("[1/7] resolving")

    captured = capfd.readouterr()
    # capfd captures the original fds, which is what _user_term wraps.
    assert "[1/7] resolving" in captured.err
    # Log file also has it (fd 2 was dup2'd).
    assert "[1/7] resolving" in _read_log(log_path)


def test_info_lands_in_log_only(tmp_path, log_mod, capfd):
    """info() under run-log should go to the log file but NOT to the
    user terminal."""
    log_path = tmp_path / "create.log"

    with log_mod.run_log_context(log_path):
        log_mod.info("noisy detail")

    captured = capfd.readouterr()
    assert "noisy detail" not in captured.err  # user didn't see it
    assert "noisy detail" in _read_log(log_path)


def test_warn_lands_in_both(tmp_path, log_mod, capfd):
    log_path = tmp_path / "create.log"

    with log_mod.run_log_context(log_path):
        log_mod.warn("something off")

    captured = capfd.readouterr()
    assert "something off" in captured.err
    assert "something off" in _read_log(log_path)


def test_error_lands_in_both(tmp_path, log_mod, capfd):
    log_path = tmp_path / "create.log"

    with log_mod.run_log_context(log_path):
        log_mod.error("kaboom")

    captured = capfd.readouterr()
    assert "kaboom" in captured.err
    assert "kaboom" in _read_log(log_path)


def test_to_user_writes_only_to_terminal(tmp_path, log_mod, capfd):
    log_path = tmp_path / "create.log"

    with log_mod.run_log_context(log_path):
        log_mod.to_user("==> summary line")

    captured = capfd.readouterr()
    assert "==> summary line" in captured.err
    # to_user() does not write to the log file by design.
    assert "==> summary line" not in _read_log(log_path)


# --- behavior outside the context (back-compat) ------------------------------


def test_info_outside_context_goes_to_stderr(log_mod, capsys, monkeypatch):
    monkeypatch.delenv("DPORTS_DEV_ENV_QUIET", raising=False)
    log_mod.info("plain detail")
    out = capsys.readouterr()
    assert "INFO: plain detail" in out.err


def test_phase_outside_context_falls_back_to_stderr(log_mod, capsys):
    log_mod.phase("[N/M] standalone")
    out = capsys.readouterr()
    assert "[N/M] standalone" in out.err


# --- exception propagation ---------------------------------------------------


def test_exception_inside_context_still_restores_streams(tmp_path, log_mod):
    log_path = tmp_path / "create.log"
    saved_stdout = sys.stdout
    saved_stderr = sys.stderr

    with pytest.raises(RuntimeError):
        with log_mod.run_log_context(log_path):
            raise RuntimeError("boom")

    assert sys.stdout is saved_stdout
    assert sys.stderr is saved_stderr
    # Log was created + closed cleanly even on exception.
    assert log_path.exists()


def test_exception_message_lands_in_log(tmp_path, log_mod):
    """A `print(traceback)` style write before the exception escapes
    should still be flushed to the log."""
    log_path = tmp_path / "create.log"
    with pytest.raises(RuntimeError):
        with log_mod.run_log_context(log_path):
            print("before-boom")
            raise RuntimeError("boom")
    assert "before-boom" in _read_log(log_path)
