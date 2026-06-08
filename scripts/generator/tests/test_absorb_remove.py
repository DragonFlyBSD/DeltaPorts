"""Tests for the Step 47 Phase 1 ``diffs/REMOVE`` → ``file remove``
translator (``dportsv3.migration.absorb_remove.absorb_remove``).

Covers the pure translation only — the compose-parity gate
(``absorb_remove_gated``) is exercised against real ports in the
migration run, not here.
"""

from __future__ import annotations

from pathlib import Path

from dportsv3.migration.absorb_remove import _is_safe_relative, absorb_remove


def _make_port(tmp_path: Path, *, remove_lines: str, status: str = "PORT\n") -> Path:
    port = tmp_path / "ports" / "cat" / "name"
    (port / "diffs").mkdir(parents=True)
    (port / "diffs" / "REMOVE").write_text(remove_lines)
    (port / "STATUS").write_text(status)
    return port


def test_bootstrap_creates_overlay_and_removes_remove(tmp_path: Path) -> None:
    port = _make_port(tmp_path, remove_lines="files/patch-a\nfiles/patch-b\n")

    result = absorb_remove(port, origin="cat/name")

    assert result.ok and result.overlay_created
    assert result.entries_absorbed == ["files/patch-a", "files/patch-b"]
    overlay = (port / "overlay.dops").read_text()
    assert "target @any" in overlay
    assert "port cat/name" in overlay
    assert "type port" in overlay
    assert "file remove files/patch-a on-missing noop" in overlay
    assert "file remove files/patch-b on-missing noop" in overlay
    # diffs/ contained only REMOVE → dir is gone.
    assert not (port / "diffs").exists()


def test_type_resolved_from_status(tmp_path: Path) -> None:
    port = _make_port(tmp_path, remove_lines="files/patch-a\n", status="MASK\n")
    absorb_remove(port, origin="cat/name")
    assert "type mask" in (port / "overlay.dops").read_text()


def test_unsafe_entries_are_skipped(tmp_path: Path) -> None:
    port = _make_port(
        tmp_path,
        remove_lines="files/ok\n../escape\n/abs/path\n",
    )

    result = absorb_remove(port, origin="cat/name")

    assert result.entries_absorbed == ["files/ok"]
    assert result.entries_skipped_unsafe == ["../escape", "/abs/path"]
    overlay = (port / "overlay.dops").read_text()
    assert "escape" not in overlay and "/abs/path" not in overlay


def test_no_remove_is_noop(tmp_path: Path) -> None:
    port = tmp_path / "ports" / "cat" / "name"
    port.mkdir(parents=True)

    result = absorb_remove(port, origin="cat/name")

    assert result.ok and not result.overlay_created
    assert not (port / "overlay.dops").exists()


def test_appends_to_existing_overlay(tmp_path: Path) -> None:
    port = _make_port(tmp_path, remove_lines="files/patch-a\n")
    (port / "overlay.dops").write_text(
        "target @any\nport cat/name\ntype port\nmk add CFLAGS -O2\n"
    )

    result = absorb_remove(port, origin="cat/name")

    assert result.ok and not result.overlay_created
    overlay = (port / "overlay.dops").read_text()
    assert "mk add CFLAGS -O2" in overlay  # preserved
    assert "file remove files/patch-a on-missing noop" in overlay


def test_diffs_dir_retained_when_not_empty(tmp_path: Path) -> None:
    port = _make_port(tmp_path, remove_lines="files/patch-a\n")
    (port / "diffs" / "Makefile.diff").write_text("--- a\n+++ b\n")

    absorb_remove(port, origin="cat/name")

    assert (port / "diffs").is_dir()
    assert (port / "diffs" / "Makefile.diff").exists()
    assert not (port / "diffs" / "REMOVE").exists()


def test_is_safe_relative() -> None:
    assert _is_safe_relative("files/patch-a")
    assert _is_safe_relative("a/b/c")
    assert not _is_safe_relative("../escape")
    assert not _is_safe_relative("/abs")
    assert not _is_safe_relative("a/../../escape")
