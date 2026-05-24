"""Empty-diff regression: agents that create new files (e.g. a fresh
``overlay.dops`` on a compat-mode port) used to produce an empty
``changes.diff`` because plain ``git diff`` is silent on untracked
files. Confirmed in production bundles ``devel_gperf-20260523-094119Z``
and ``multimedia_v4l_compat-20260523-101601Z``: ``put_file`` returned
a changed sha256, ``rebuild_ok`` was True, yet ``emit_diff`` returned
``""`` — the operator had no diff to land.

Fix: ``_git_diff_with_untracked`` runs ``git add --intent-to-add``
before ``git diff`` so new files show up as additions, then ``git
reset`` after to leave the index clean.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


_GEN = Path(__file__).resolve().parents[1]
if str(_GEN) not in sys.path:
    sys.path.insert(0, str(_GEN))

from dportsv3.agent.worker import _git_diff_with_untracked  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return p.stdout


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "ports").mkdir()
    (r / "ports" / "tracked.txt").write_text("original\n")
    _git(r, "add", "ports/tracked.txt")
    _git(r, "commit", "-qm", "init")
    return r


def test_new_file_appears_in_diff(repo: Path) -> None:
    """A brand-new untracked file shows up as an addition."""
    (repo / "ports" / "fresh.txt").write_text("hello\n")

    p = _git_diff_with_untracked(repo, "ports/fresh.txt")

    assert p.returncode == 0, p.stderr
    assert "diff --git" in p.stdout
    assert "+hello" in p.stdout
    assert "new file" in p.stdout


def test_modified_tracked_file_appears_in_diff(repo: Path) -> None:
    """Existing tracked-modified files still show up (no regression)."""
    (repo / "ports" / "tracked.txt").write_text("changed\n")

    p = _git_diff_with_untracked(repo, "ports/tracked.txt")

    assert p.returncode == 0, p.stderr
    assert "diff --git" in p.stdout
    assert "-original" in p.stdout
    assert "+changed" in p.stdout


def test_no_changes_returns_empty(repo: Path) -> None:
    """When nothing has changed for the path, the diff is empty."""
    p = _git_diff_with_untracked(repo, "ports/tracked.txt")

    assert p.returncode == 0, p.stderr
    assert p.stdout == ""


def test_index_is_clean_after_call(repo: Path) -> None:
    """The intent-to-add must be reset; we leave no staged residue."""
    (repo / "ports" / "fresh.txt").write_text("hello\n")

    _git_diff_with_untracked(repo, "ports/fresh.txt")

    # ports/fresh.txt should still be untracked, not staged.
    status = _git(repo, "status", "--porcelain", "--", "ports/fresh.txt")
    assert status.startswith("??"), f"expected untracked, got: {status!r}"


def test_directory_scope_catches_new_file(repo: Path) -> None:
    """Scoping to a directory (the runner's pattern) catches new files
    inside it. Mirrors ``runner._write_changes_diff(env, origin)``
    which passes ``rel = f'ports/{origin}'``."""
    sub = repo / "ports" / "devel" / "gperf"
    sub.mkdir(parents=True)
    (sub / "overlay.dops").write_text("# new dops overlay\n")

    p = _git_diff_with_untracked(repo, "ports/devel/gperf")

    assert p.returncode == 0, p.stderr
    assert "overlay.dops" in p.stdout
    assert "+# new dops overlay" in p.stdout
