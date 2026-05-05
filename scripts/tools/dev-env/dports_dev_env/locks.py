from __future__ import annotations

import time
from pathlib import Path
from types import TracebackType

from .errors import DevEnvError
from .log import info


class CacheLock:
    def __init__(self, locks_dir: Path, name: str, *, timeout: int = 600) -> None:
        self.path = locks_dir / name
        self.timeout = timeout
        self.acquired = False

    def __enter__(self) -> "CacheLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        waited = 0
        while True:
            try:
                self.path.mkdir()
            except FileExistsError:
                if waited >= self.timeout:
                    raise DevEnvError(
                        f"could not acquire cache lock {self.path} within {self.timeout}s; "
                        "if no other dports dev-env is running, remove it manually"
                    ) from None
                if waited == 0:
                    info(f"waiting on cache lock {self.path}")
                time.sleep(2)
                waited += 2
                continue
            self.acquired = True
            return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if self.acquired:
            self.path.rmdir()
            self.acquired = False
