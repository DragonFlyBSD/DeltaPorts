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
    changed_paths,
    commit_diff,
    prepare_clean_branch,
    push_branch,
    restore_to_base,
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


def test_apply_diff_with_new_file_lands_in_commit(clone):
    """Finding 1 (11d-3 review): apply_diff must stage newly-
    created files so they make it into the commit. Pre-fix the
    `--index` flag was missing and commit_diff's `git add -u`
    only caught modifications, silently dropping new files (the
    load-bearing case for the agent's edits producing a fresh
    dragonfly/patch-* file)."""
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/newfile",
    )
    new_file_diff = (
        "diff --git a/dragonfly/patch-src_foo.c b/dragonfly/patch-src_foo.c\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/dragonfly/patch-src_foo.c\n"
        "@@ -0,0 +1,3 @@\n"
        "+--- src/foo.c.orig\n"
        "+++ src/foo.c\n"
        "+@@ ...\n"
    )
    apply_diff(clone, new_file_diff)
    commit_diff(clone, title="add new patch", body="x")
    # Verify the new file is part of the commit, not lurking
    # untracked.
    out = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        cwd=str(clone), capture_output=True, text=True,
    )
    assert "dragonfly/patch-src_foo.c" in out.stdout
    # And the file is no longer untracked.
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(clone), capture_output=True, text=True,
    )
    assert "dragonfly/patch-src_foo.c" not in status.stdout


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


def test_commit_diff_uses_configured_identity(clone):
    """committer_name/email override the clone's git identity for
    this commit only (and feed the Signed-off-by trailer), without
    touching the clone's git config."""
    prepare_clean_branch(
        clone, base_branch="master", branch_name="feature/ident",
    )
    apply_diff(clone, _SAMPLE_DIFF)
    commit_diff(
        clone, title="t: fix x", body="b", signoff=True,
        committer_name="Fred [bot]",
        committer_email="github@dragonflybsd.org",
    )
    out = subprocess.run(
        ["git", "log", "-1", "--format=%an <%ae>%n%cn <%ce>%n%B"],
        cwd=str(clone), capture_output=True, text=True,
    )
    assert "Fred [bot] <github@dragonflybsd.org>" in out.stdout
    assert "Signed-off-by: Fred [bot] <github@dragonflybsd.org>" in out.stdout
    # The clone's persisted config is untouched (still the fixture's
    # "t" identity).
    cfg = subprocess.run(
        ["git", "config", "user.name"],
        cwd=str(clone), capture_output=True, text=True,
    )
    assert cfg.stdout.strip() == "t"


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


def test_subprocess_timeout_surfaces_as_git_error(clone, monkeypatch):
    """Finding 3 (11d-3 review): a git subprocess that hangs longer
    than _GIT_DEFAULT_TIMEOUT must surface as GitError, not bubble
    up as raw subprocess.TimeoutExpired."""
    from dportsv3.delivery import _git as gitmod
    import subprocess as sp

    def _fake_run(*a, **kw):
        raise sp.TimeoutExpired(cmd=a[0] if a else "git", timeout=1.0)

    monkeypatch.setattr(sp, "run", _fake_run)
    with pytest.raises(gitmod.GitError, match="timed out"):
        gitmod._run(["git", "status"], cwd=clone)


def test_push_branch_injects_auth_via_env_not_argv(clone, monkeypatch):
    """The token must reach git as an HTTP auth header (origin is an
    anonymous HTTPS URL), but never via argv — argv shows up in `ps`
    and in the timeout error message. It's passed through GIT_CONFIG_*
    env instead."""
    import base64
    from dportsv3.delivery import _git as gitmod

    captured: dict = {}

    def _fake_run(args, *, cwd, env_extra=None, **kw):
        captured["args"] = list(args)
        captured["env_extra"] = env_extra
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gitmod, "_run", _fake_run)
    push_branch(clone, branch_name="feature/x", token="secret-tok-123")

    # Token is nowhere in argv.
    assert all("secret-tok-123" not in a for a in captured["args"])
    # Token rides in the GIT_CONFIG_* env, base64'd as Basic auth.
    env = captured["env_extra"]
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
    header = env["GIT_CONFIG_VALUE_0"]
    assert header.startswith("Authorization: Basic ")
    decoded = base64.b64decode(header.split("Basic ", 1)[1]).decode()
    assert decoded == "x-access-token:secret-tok-123"


def test_push_branch_no_token_sends_no_auth_env(clone, monkeypatch):
    """Without a token, no auth env is injected (push stays
    anonymous — matches pre-fix behavior for the local-remote tests)."""
    from dportsv3.delivery import _git as gitmod
    captured: dict = {}

    def _fake_run(args, *, cwd, env_extra=None, **kw):
        captured["env_extra"] = env_extra
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(gitmod, "_run", _fake_run)
    push_branch(clone, branch_name="feature/x")
    assert captured["env_extra"] is None


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


# ---------------------------------------------------------------------
# changed_paths / restore_to_base
# ---------------------------------------------------------------------


def test_changed_paths_extracts_b_side():
    diff = (
        "diff --git a/devel/foo/Makefile b/devel/foo/Makefile\n"
        "--- a/devel/foo/Makefile\n"
        "+++ b/devel/foo/Makefile\n"
        "@@ -1 +1 @@\n-x\n+y\n"
        "diff --git a/devel/foo/files/patch-x b/devel/foo/files/patch-x\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/devel/foo/files/patch-x\n"
        "@@ -0,0 +1 @@\n+z\n"
    )
    paths = changed_paths(diff)
    assert paths == [
        "devel/foo/Makefile", "devel/foo/files/patch-x",
    ]


def test_restore_to_base_after_failed_push_returns_to_clean_base(clone):
    """The real-world wedge: commit lands locally, push fails, clone
    left on the feature branch. restore_to_base must put it back on
    a clean master so the next Accept's precondition holds."""
    prepare_clean_branch(
        clone, base_branch="master", branch_name="agentic/x",
    )
    apply_diff(clone, _SAMPLE_DIFF)
    commit_diff(clone, title="t", body="b")
    # Simulate push failure: we just don't push. Clone is now on
    # agentic/x with a commit.
    on_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(clone), capture_output=True, text=True,
    ).stdout.strip()
    assert on_branch == "agentic/x"

    ok = restore_to_base(
        clone, base_branch="master",
        scope_paths=changed_paths(_SAMPLE_DIFF),
    )
    assert ok is True
    # Back on master, clean.
    back = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(clone), capture_output=True, text=True,
    ).stdout.strip()
    assert back == "master"
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(clone), capture_output=True, text=True,
    ).stdout.strip()
    assert status == ""


def test_restore_to_base_removes_only_scoped_untracked(clone):
    """A new file the delivery staged (apply --index) must be cleaned
    on restore, but an operator's unrelated untracked file outside the
    scope must survive."""
    prepare_clean_branch(
        clone, base_branch="master", branch_name="agentic/y",
    )
    # Delivery adds a new file under the port path.
    new_diff = (
        "diff --git a/devel/foo/files/patch-new b/devel/foo/files/patch-new\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        "+++ b/devel/foo/files/patch-new\n"
        "@@ -0,0 +1 @@\n+content\n"
    )
    (clone / "devel" / "foo" / "files").mkdir(parents=True)
    apply_diff(clone, new_diff)
    # Operator has an unrelated untracked file elsewhere.
    (clone / "MY-SCRATCH").write_text("operator work\n")

    restore_to_base(
        clone, base_branch="master",
        scope_paths=changed_paths(new_diff),
    )
    # Scoped new file gone; operator's scratch file survives.
    assert not (clone / "devel" / "foo" / "files" / "patch-new").exists()
    assert (clone / "MY-SCRATCH").exists()


def test_restore_to_base_never_raises_on_bad_clone(tmp_path):
    """Best-effort: a non-clone dir returns False, doesn't raise (it
    runs in a finally and must not mask the delivery exception)."""
    assert restore_to_base(
        tmp_path / "not-a-clone", base_branch="master",
    ) is False
