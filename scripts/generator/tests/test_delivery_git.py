"""Tests for delivery._git — the local-clone git driver.

Uses a real temp git repo + a fake "remote" (also a local repo)
to exercise prepare_clean_branch / apply_diff / commit_diff /
push_branch end-to-end. No network.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dportsv3.delivery._git import (
    GitApplyConflict,
    GitApplyError,
    GitCommitError,
    GitDirtyClone,
    GitError,
    GitPushError,
    GitWrongBranch,
    apply_diff,
    commit_diff,
    prepare_clean_branch,
    push_branch,
)


def _sh(args, cwd):
    subprocess.run(args, cwd=str(cwd), check=True, capture_output=True)


@pytest.fixture
def remote(tmp_path):
    """A bare git repo acting as the "remote" — pushes target it."""
    remote_dir = tmp_path / "remote.git"
    _sh(["git", "init", "--bare", "-b", "master", str(remote_dir)], tmp_path)
    return remote_dir


@pytest.fixture
def clone(tmp_path, remote):
    """A working clone of `remote` with one baseline commit so
    `master` has a tip to fetch."""
    clone_dir = tmp_path / "clone"
    _sh(["git", "clone", str(remote), str(clone_dir)], tmp_path)
    _sh(["git", "config", "user.email", "t@t"], clone_dir)
    _sh(["git", "config", "user.name", "t"], clone_dir)
    (clone_dir / "README").write_text("baseline\n")
    _sh(["git", "add", "README"], clone_dir)
    _sh(["git", "commit", "-qm", "baseline"], clone_dir)
    _sh(["git", "push", "-u", "origin", "master"], clone_dir)
    return clone_dir


_SAMPLE_DIFF = (
    "--- a/README\n"
    "+++ b/README\n"
    "@@ -1 +1,2 @@\n"
    " baseline\n"
    "+new line\n"
)


# ---------------------------------------------------------------------
# prepare_clean_branch
# ---------------------------------------------------------------------


def test_prepare_clean_branch_happy_path(clone):
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/x",
    )
    out = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(clone), capture_output=True, text=True,
    )
    assert out.stdout.strip() == "feature/x"


def test_prepare_clean_branch_refuses_dirty(clone):
    (clone / "README").write_text("dirty\n")
    with pytest.raises(GitDirtyClone, match="uncommitted"):
        prepare_clean_branch(
            clone, base_branch="master", branch_name="feature/x",
        )


def test_prepare_clean_branch_refuses_off_base(clone):
    _sh(["git", "checkout", "-b", "wrong"], clone)
    with pytest.raises(GitWrongBranch, match="wrong"):
        prepare_clean_branch(
            clone, base_branch="master", branch_name="feature/x",
        )


def test_prepare_clean_branch_resets_existing_branch(clone):
    """checkout -B reuses the local branch name. Useful for the
    re-Accept idempotency case: the same operator re-Accepts the
    bundle, the same local branch gets refreshed to remote tip."""
    # First run creates the branch.
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/x",
    )
    _sh(["git", "commit", "--allow-empty", "-qm", "stray"], clone)
    _sh(["git", "checkout", "master"], clone)
    # Second run resets it (without erroring on "branch exists").
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/x",
    )
    # The stray commit is gone.
    out = subprocess.run(
        ["git", "log", "--oneline", "feature/x"],
        cwd=str(clone), capture_output=True, text=True,
    )
    assert "stray" not in out.stdout


def test_prepare_clean_branch_missing_clone_errors(tmp_path):
    with pytest.raises(GitError, match="doesn't exist"):
        prepare_clean_branch(
            tmp_path / "nope",
            base_branch="master", branch_name="x",
        )


def test_prepare_clean_branch_not_a_git_repo_errors(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    with pytest.raises(GitError, match="isn't a git working tree"):
        prepare_clean_branch(
            d, base_branch="master", branch_name="x",
        )


# ---------------------------------------------------------------------
# apply_diff
# ---------------------------------------------------------------------


def test_apply_diff_happy_path(clone):
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/y",
    )
    apply_diff(clone, _SAMPLE_DIFF)
    assert (clone / "README").read_text() == "baseline\nnew line\n"


def test_apply_diff_conflict_raises(clone):
    """A diff that doesn't apply cleanly with --3way raises
    GitApplyConflict."""
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/z",
    )
    # Diff against content that doesn't exist in the file.
    bad_diff = (
        "--- a/README\n"
        "+++ b/README\n"
        "@@ -1 +1 @@\n"
        "-totally different baseline\n"
        "+new\n"
    )
    with pytest.raises((GitApplyConflict, GitApplyError)):
        apply_diff(clone, bad_diff)


def test_apply_diff_malformed_raises_apply_error(clone):
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/m",
    )
    with pytest.raises(GitApplyError):
        apply_diff(clone, "not a diff at all\n")


# ---------------------------------------------------------------------
# commit_diff
# ---------------------------------------------------------------------


def test_commit_diff_happy_path(clone):
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/c",
    )
    apply_diff(clone, _SAMPLE_DIFF)
    commit_diff(
        clone, title="t: fix x", body="Verified by xyz.\n",
        signoff=True,
    )
    out = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=str(clone), capture_output=True, text=True,
    )
    assert "t: fix x" in out.stdout
    assert "Verified by xyz." in out.stdout
    assert "Signed-off-by:" in out.stdout


def test_commit_diff_nothing_to_commit_refuses(clone):
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/empty",
    )
    # Skip apply_diff — there are no staged changes.
    with pytest.raises(GitCommitError, match="nothing to commit"):
        commit_diff(clone, title="t", body="b")


def test_commit_diff_without_signoff(clone):
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/no-s",
    )
    apply_diff(clone, _SAMPLE_DIFF)
    commit_diff(clone, title="t", body="b", signoff=False)
    out = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=str(clone), capture_output=True, text=True,
    )
    assert "Signed-off-by:" not in out.stdout


# ---------------------------------------------------------------------
# push_branch
# ---------------------------------------------------------------------


def test_push_branch_happy_path(clone, remote):
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/push-me",
    )
    apply_diff(clone, _SAMPLE_DIFF)
    commit_diff(clone, title="t", body="b")
    push_branch(clone, branch_name="feature/push-me")
    # The remote has the branch now.
    out = subprocess.run(
        ["git", "branch", "--list"],
        cwd=str(remote), capture_output=True, text=True,
    )
    assert "feature/push-me" in out.stdout


def test_push_branch_failure_to_invalid_remote(clone, tmp_path):
    """Point origin at a non-existent path → push fails."""
    # Sabotage origin's URL.
    subprocess.run(
        ["git", "remote", "set-url", "origin", str(tmp_path / "nope")],
        cwd=str(clone), check=True, capture_output=True,
    )
    prepare_clean_branch.__wrapped__ if False else None  # silence linter
    # Make a branch and commit so there's something to push.
    _sh(["git", "checkout", "-b", "broken-push"], clone)
    (clone / "x").write_text("x\n")
    _sh(["git", "add", "x"], clone)
    _sh(["git", "commit", "-qm", "x"], clone)
    with pytest.raises(GitPushError, match="push"):
        push_branch(clone, branch_name="broken-push")
