from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from typing import Iterator


def _quiet() -> bool:
    """Suppress INFO output when running non-interactively (agent harness).

    Set DPORTS_DEV_ENV_QUIET=1 in the environment to silence INFO lines.
    WARN and ERROR are never silenced.
    """
    return os.environ.get("DPORTS_DEV_ENV_QUIET") == "1"


def info(message: str) -> None:
    if _quiet():
        return
    print(f"INFO: {message}", file=sys.stderr)


def warn(message: str) -> None:
    print(f"WARN: {message}", file=sys.stderr)


def error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)


@contextmanager
def step_timer(label: str) -> Iterator[None]:
    started_at = time.monotonic()
    try:
        yield
    finally:
        elapsed = int(time.monotonic() - started_at)
        info(f"timing: {label} completed in {elapsed}s")
