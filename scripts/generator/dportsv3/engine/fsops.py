"""Filesystem helpers for safe apply-stage writes."""

from __future__ import annotations

import tempfile
from pathlib import Path


class FileTransaction:
    """Collect staged file writes/removals and commit atomically per file."""

    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run
        self._writes: dict[Path, str] = {}
        # Verbatim byte writes (file.materialize) — the staged file may
        # not be valid UTF-8 (e.g. a Latin-1 patch with a 0xa0 byte), so
        # it can't go through the text path.
        self._writes_bytes: dict[Path, bytes] = {}
        self._removes: set[Path] = set()

    def read_text(self, path: Path) -> str:
        if path in self._writes:
            return self._writes[path]
        if path in self._removes:
            raise FileNotFoundError(path)
        return path.read_text()

    def stage_write(self, path: Path, content: str) -> None:
        self._writes[path] = content
        self._writes_bytes.pop(path, None)
        self._removes.discard(path)

    def stage_write_bytes(self, path: Path, data: bytes) -> None:
        self._writes_bytes[path] = data
        self._writes.pop(path, None)
        self._removes.discard(path)

    def stage_remove(self, path: Path) -> None:
        self._removes.add(path)
        self._writes.pop(path, None)
        self._writes_bytes.pop(path, None)

    def staged_paths(self) -> list[Path]:
        paths = set(self._writes) | set(self._writes_bytes) | set(self._removes)
        return sorted(paths, key=lambda path: str(path))

    def staged_writes(self) -> dict[Path, str]:
        return dict(self._writes)

    def staged_removes(self) -> set[Path]:
        return set(self._removes)

    def staged_change_snapshot(self, path: Path) -> tuple[str | None, str | None]:
        before: str | None
        try:
            before = path.read_text()
        except FileNotFoundError:
            before = None

        if path in self._writes:
            after: str | None = self._writes[path]
        elif path in self._removes:
            after = None
        else:
            after = before
        return before, after

    def commit(self) -> None:
        if self.dry_run:
            return

        for path, content in self._writes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(path.parent),
                delete=False,
            ) as temp:
                temp.write(content)
                temp_path = Path(temp.name)
            temp_path.replace(path)

        for path, data in self._writes_bytes.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=str(path.parent), delete=False
            ) as temp:
                temp.write(data)
                temp_path = Path(temp.name)
            temp_path.replace(path)

        for path in self._removes:
            if path.exists():
                path.unlink()

    def rollback(self) -> None:
        self._writes.clear()
        self._writes_bytes.clear()
        self._removes.clear()
