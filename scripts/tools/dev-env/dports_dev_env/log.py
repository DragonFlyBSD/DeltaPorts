from __future__ import annotations

import sys
import time
from contextlib import contextmanager
from typing import Iterator


def info(message: str) -> None:
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
