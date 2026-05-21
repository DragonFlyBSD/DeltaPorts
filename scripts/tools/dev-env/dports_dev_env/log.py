from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Iterator


# Module-level run-log state. When a ``run_log_context`` is active:
# - ``_log_file`` is the open log file. fd 1 and fd 2 have been
#   redirected to it via dup2, so subprocess output also lands here.
# - ``_user_term`` is a writer wrapping the *saved* original stderr fd.
#   Phase markers and the final summary go here so the operator sees
#   them on their terminal even though the rest of the world prints to
#   the log.
# - ``_log_path`` is the on-disk log path, surfaced in summary messages.
_log_file: IO[str] | None = None
_user_term: IO[str] | None = None
_log_path: Path | None = None


def _quiet() -> bool:
    """Suppress INFO output when running non-interactively (agent harness).

    Set DPORTS_DEV_ENV_QUIET=1 in the environment to silence INFO lines.
    WARN and ERROR are never silenced.
    """
    return os.environ.get("DPORTS_DEV_ENV_QUIET") == "1"


def info(message: str) -> None:
    """Verbose INFO. Goes to the run-log when active; otherwise stderr.

    Under ``run_log_context`` the user terminal sees only ``phase()``
    markers + summary, not every INFO line. Operators tail the log
    file when they want detail.
    """
    if _quiet():
        return
    if _log_file is not None:
        # fd 1/2 are already dup2'd to the log file, so a plain print
        # to sys.stderr also lands in the log. Keep the "INFO:" prefix
        # for grep parity with pre-run-log output.
        print(f"INFO: {message}", file=sys.stderr)
        return
    print(f"INFO: {message}", file=sys.stderr)


def warn(message: str) -> None:
    """WARN goes to both the user terminal and the log file."""
    if _user_term is not None:
        print(f"WARN: {message}", file=_user_term)
    print(f"WARN: {message}", file=sys.stderr)


def error(message: str) -> None:
    """ERROR goes to both the user terminal and the log file."""
    if _user_term is not None:
        print(f"ERROR: {message}", file=_user_term)
    print(f"ERROR: {message}", file=sys.stderr)


def phase(label: str) -> None:
    """Operator-visible phase marker.

    Always prints to the user terminal (or stderr when no run-log is
    active). Also lands in the log via the dup2 redirection of fd 2,
    so the log file has the markers too — handy when grepping for
    "where did we get stuck?"
    """
    if _user_term is not None:
        print(label, file=_user_term, flush=True)
    print(label, file=sys.stderr, flush=True)


def to_user(message: str) -> None:
    """Print a free-form line to the user terminal only.

    Used for the final summary. Falls back to stderr when no run-log
    is active.
    """
    if _user_term is not None:
        print(message, file=_user_term, flush=True)
    else:
        print(message, file=sys.stderr, flush=True)


def current_log_path() -> Path | None:
    """The active run-log path, if any."""
    return _log_path


@contextmanager
def step_timer(label: str) -> Iterator[None]:
    started_at = time.monotonic()
    try:
        yield
    finally:
        elapsed = int(time.monotonic() - started_at)
        info(f"timing: {label} completed in {elapsed}s")


@contextmanager
def run_log_context(log_path: Path) -> Iterator[Path]:
    """Redirect stdout/stderr to ``log_path`` for the duration of a long-
    running command (e.g. ``dportsv3 dev-env create``).

    Behavior:
    - ``log_path`` is opened for write, parent dirs created.
    - The original fd 2 is dup'd before being replaced, so phase markers
      can still reach the operator's terminal via ``_user_term``.
    - fd 1 and fd 2 are dup2'd to the log file. Subprocess output
      inherits these and lands in the log.
    - Python's ``sys.stdout`` and ``sys.stderr`` are also pointed at the
      log file so ``print(...)`` calls in this process go there too.
    - On exit, all fds and Python streams are restored. Stale log
      writers stay closed.

    Idempotent against nested contexts: nests are not supported and
    will raise. (Today's caller is only ``cmd_create``.)
    """
    global _log_file, _user_term, _log_path

    if _log_file is not None:
        raise RuntimeError("run_log_context cannot nest")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_f = open(log_path, "w", buffering=1)  # line-buffered

    # Save originals so we can restore on exit + write summary lines
    # to the user's terminal even after we redirect fds.
    saved_stdout_fd = os.dup(1)
    saved_stderr_fd = os.dup(2)

    # The user-terminal writer wraps a *non-closing* fdopen so closing
    # this file later doesn't close the saved fd (we restore it via dup2
    # in the finally block).
    user_term = os.fdopen(os.dup(saved_stderr_fd), "w", buffering=1)

    sys.stdout.flush()
    sys.stderr.flush()

    os.dup2(log_f.fileno(), 1)
    os.dup2(log_f.fileno(), 2)

    saved_py_stdout = sys.stdout
    saved_py_stderr = sys.stderr
    sys.stdout = log_f
    sys.stderr = log_f

    _log_file = log_f
    _user_term = user_term
    _log_path = log_path

    try:
        yield log_path
    finally:
        try:
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass

        sys.stdout = saved_py_stdout
        sys.stderr = saved_py_stderr

        try:
            os.dup2(saved_stdout_fd, 1)
            os.dup2(saved_stderr_fd, 2)
        finally:
            os.close(saved_stdout_fd)
            os.close(saved_stderr_fd)

        try:
            user_term.close()
        except Exception:
            pass
        try:
            log_f.close()
        except Exception:
            pass

        _log_file = None
        _user_term = None
        _log_path = None
