"""Tests for ``dportsv3.fsutils.reconcile``.

The function exists to keep dst mtimes stable when content matches —
dsynth's port-change detector folds mtime into the per-port CRC and
will force-rebuild on any mtime bump even when the bytes didn't
change. These tests pin the no-op property explicitly (same content
in scratch and live → live's mtime unchanged) alongside the usual
"makes dst look like src" property.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from dportsv3.fsutils import reconcile


def _write(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(mode)


def _mtime_ns(path: Path) -> int:
    return path.stat(follow_symlinks=False).st_mtime_ns


# --- The dsynth-relevant invariant ---


def test_identical_trees_preserve_dst_mtime(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "Makefile", "PORTNAME=foo\n")
    _write(src / "files" / "patch-a", "diff content\n")
    _write(dst / "Makefile", "PORTNAME=foo\n")
    _write(dst / "files" / "patch-a", "diff content\n")

    # Stamp dst with a known-old mtime so any rewrite would be visible.
    old = time.time() - 86400
    os.utime(dst / "Makefile", (old, old))
    os.utime(dst / "files" / "patch-a", (old, old))
    pre_mk = _mtime_ns(dst / "Makefile")
    pre_pa = _mtime_ns(dst / "files" / "patch-a")

    reconcile(src, dst)

    assert _mtime_ns(dst / "Makefile") == pre_mk
    assert _mtime_ns(dst / "files" / "patch-a") == pre_pa


def test_content_change_updates_dst(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "Makefile", "PORTNAME=new\n")
    _write(dst / "Makefile", "PORTNAME=old\n")
    pre = _mtime_ns(dst / "Makefile")

    reconcile(src, dst)

    assert (dst / "Makefile").read_text() == "PORTNAME=new\n"
    assert _mtime_ns(dst / "Makefile") != pre  # rewrite expected


# --- left_only / right_only ---


def test_files_only_in_src_are_copied(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "Makefile", "x\n")
    _write(src / "new_file", "added\n")
    _write(dst / "Makefile", "x\n")

    reconcile(src, dst)

    assert (dst / "new_file").read_text() == "added\n"


def test_files_only_in_dst_are_pruned(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "Makefile", "x\n")
    _write(dst / "Makefile", "x\n")
    _write(dst / "stale_file", "delete me\n")

    reconcile(src, dst)

    assert not (dst / "stale_file").exists()


def test_directory_only_in_src_is_copied(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "files" / "patch-1", "1\n")
    _write(src / "files" / "patch-2", "2\n")
    dst.mkdir()

    reconcile(src, dst)

    assert (dst / "files" / "patch-1").read_text() == "1\n"
    assert (dst / "files" / "patch-2").read_text() == "2\n"


def test_directory_only_in_dst_is_pruned(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "Makefile", "x\n")
    _write(dst / "Makefile", "x\n")
    _write(dst / "old_subdir" / "f1", "stale\n")
    _write(dst / "old_subdir" / "deeper" / "f2", "stale\n")

    reconcile(src, dst)

    assert not (dst / "old_subdir").exists()


# --- mode drift (same content, different mode) ---


def test_mode_drift_is_corrected_without_mtime_bump(tmp_path: Path) -> None:
    # Same content, different mode → chmod (not rewrite), mtime stable.
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "do-fetch.sh", "#!/bin/sh\n", mode=0o755)
    _write(dst / "do-fetch.sh", "#!/bin/sh\n", mode=0o644)
    old = time.time() - 3600
    os.utime(dst / "do-fetch.sh", (old, old))
    pre = _mtime_ns(dst / "do-fetch.sh")

    reconcile(src, dst)

    assert (dst / "do-fetch.sh").stat().st_mode & 0o7777 == 0o755
    assert _mtime_ns(dst / "do-fetch.sh") == pre


# --- type changes (file ↔ dir, symlink ↔ file) ---


def test_file_becomes_directory(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "thing" / "inner", "inner content\n")
    _write(dst / "thing", "was a file\n")

    reconcile(src, dst)

    assert (dst / "thing").is_dir()
    assert (dst / "thing" / "inner").read_text() == "inner content\n"


def test_directory_becomes_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "thing", "now a file\n")
    _write(dst / "thing" / "inner", "was a dir\n")

    reconcile(src, dst)

    assert (dst / "thing").is_file()
    assert (dst / "thing").read_text() == "now a file\n"


def test_symlink_replaced_with_file(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "alias", "real content\n")
    dst.mkdir()
    _write(dst / "target_outside", "external\n")
    (dst / "alias").symlink_to("target_outside")

    reconcile(src, dst)

    assert not (dst / "alias").is_symlink()
    assert (dst / "alias").read_text() == "real content\n"


def test_file_replaced_with_symlink(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "target", "real\n")
    (src / "alias").symlink_to("target")
    _write(dst / "target", "real\n")
    _write(dst / "alias", "stale regular file\n")

    reconcile(src, dst)

    assert (dst / "alias").is_symlink()
    assert os.readlink(dst / "alias") == "target"


# --- symlink content compare ---


def test_symlinks_with_same_target_left_alone(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    _write(src / "target", "x\n")
    (src / "alias").symlink_to("target")
    _write(dst / "target", "x\n")
    (dst / "alias").symlink_to("target")
    old = time.time() - 3600
    # lchown / lutimes equivalent — best effort; mtime check is the
    # signal that matters here.
    try:
        os.utime(dst / "alias", (old, old), follow_symlinks=False)
    except (NotImplementedError, OSError):
        pytest.skip("platform doesn't support utime on symlink")
    pre = _mtime_ns(dst / "alias")

    reconcile(src, dst)

    assert _mtime_ns(dst / "alias") == pre


# --- recursion ---


def test_nested_no_op_preserves_all_mtimes(tmp_path: Path) -> None:
    # Realistic ports-shape: nested files, no changes between runs.
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    layout = {
        "Makefile": "PORTNAME=pkg\n",
        "distinfo": "TIMESTAMP=0\n",
        "files/patch-a": "diff a\n",
        "files/patch-b": "diff b\n",
        "files/sub/patch-c": "diff c\n",
        "pkg-descr": "a port\n",
    }
    for rel, content in layout.items():
        _write(src / rel, content)
        _write(dst / rel, content)

    old = time.time() - 3600
    pre: dict[str, int] = {}
    for rel in layout:
        os.utime(dst / rel, (old, old))
        pre[rel] = _mtime_ns(dst / rel)

    reconcile(src, dst)

    for rel in layout:
        assert _mtime_ns(dst / rel) == pre[rel], rel


# --- error cases ---


def test_src_must_be_directory(tmp_path: Path) -> None:
    src = tmp_path / "not_a_dir"
    src.write_text("x")
    dst = tmp_path / "dst"
    with pytest.raises(ValueError):
        reconcile(src, dst)


def test_dst_created_when_missing(tmp_path: Path) -> None:
    src = tmp_path / "src"
    _write(src / "Makefile", "x\n")
    dst = tmp_path / "fresh"

    reconcile(src, dst)

    assert (dst / "Makefile").read_text() == "x\n"
