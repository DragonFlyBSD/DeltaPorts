"""Tests for ``dportsv3.fsutils.diff_tree`` — the content-aware tree
comparison that backs the Step 47 compose-parity gate. Classification
is content-based (mtime/size never count), matching ``reconcile``.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from dportsv3.fsutils import diff_tree


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_identical_trees_are_equal(tmp_path: Path) -> None:
    left, right = tmp_path / "l", tmp_path / "r"
    _write(left / "Makefile", "PORTNAME=foo\n")
    _write(left / "files" / "patch-a", "x\n")
    _write(right / "Makefile", "PORTNAME=foo\n")
    _write(right / "files" / "patch-a", "x\n")

    assert diff_tree(left, right) == []


def test_same_content_different_mtime_still_equal(tmp_path: Path) -> None:
    left, right = tmp_path / "l", tmp_path / "r"
    _write(left / "f", "same\n")
    _write(right / "f", "same\n")
    old = time.time() - 86400
    os.utime(right / "f", (old, old))

    assert diff_tree(left, right) == []


def test_only_left_and_only_right(tmp_path: Path) -> None:
    left, right = tmp_path / "l", tmp_path / "r"
    _write(left / "gone", "x\n")
    _write(right / "added", "y\n")

    assert sorted(diff_tree(left, right)) == [
        ("only_left", "gone"),
        ("only_right", "added"),
    ]


def test_content_difference_is_reported(tmp_path: Path) -> None:
    left, right = tmp_path / "l", tmp_path / "r"
    _write(left / "files" / "patch-a", "one\n")
    _write(right / "files" / "patch-a", "two\n")

    assert diff_tree(left, right) == [("content", str(Path("files") / "patch-a"))]


def test_nested_paths_are_relative(tmp_path: Path) -> None:
    left, right = tmp_path / "l", tmp_path / "r"
    _write(left / "a" / "b" / "c", "1\n")
    # right lacks the nested file entirely
    (right / "a" / "b").mkdir(parents=True)

    assert diff_tree(left, right) == [("only_left", str(Path("a") / "b" / "c"))]
