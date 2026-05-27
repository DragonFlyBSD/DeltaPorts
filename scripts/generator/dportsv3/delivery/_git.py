"""Local-clone git driver for the network delivery providers
(Step 11d-3 base; reused by GitLab + Gitea).

Network providers need to push a branch to the upstream remote
before opening a review request. This module drives those git
operations against the operator's clone (``$DPORTSV3_OPERATOR_CLONE``)
via subprocess — we don't import a Python git library; ``git``
is the source of truth for git semantics and subprocess is enough.

Pre-state invariants the driver enforces:
- The clone exists and is a git working tree.
- The working tree is clean (no staged or unstaged changes).
- The current branch is ``base_branch`` (the configured target
  branch in delivery.toml).

Operations:
- ``prepare_clean_branch`` — fetch + checkout a fresh feature
  branch from ``origin/base_branch``.
- ``apply_diff`` — ``git apply --3way`` the bundle's diff.
- ``commit_diff`` — ``git add -A`` + ``git commit -s`` with the
  templated message.
- ``push_branch`` — ``git push --set-upstream origin <branch>``.

Each operation raises a structured exception (subclass of
``DeliveryError``) on failure. The orchestrator catches at the
``deliver`` boundary so the bundle's create_failed row carries
a meaningful error.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import DeliveryError


# Default timeout (seconds) for individual git subprocesses.
# A hung remote on `git push` would otherwise block the Accept
# request thread indefinitely. Overridable via the env var for
# operators on slow networks / huge histories.
_GIT_DEFAULT_TIMEOUT = float(
    os.environ.get("DPORTSV3_GIT_TIMEOUT", "60.0")
)


__all__ = [
    "GitError",
    "GitDirtyClone",
    "GitWrongBranch",
    "GitApplyConflict",
    "GitApplyError",
    "GitCommitError",
    "GitPushError",
    "prepare_clean_branch",
    "apply_diff",
    "commit_diff",
    "push_branch",
]


class GitError(DeliveryError):
    """Base class for git operation failures."""


class GitDirtyClone(GitError):
    """The clone has uncommitted changes; refusing to operate."""


class GitWrongBranch(GitError):
    """The clone is not on the expected base branch."""


class GitApplyConflict(GitError):
    """git apply --3way produced conflicts."""


class GitApplyError(GitError):
    """git apply failed for a reason other than conflicts
    (malformed diff, missing files, etc.)."""


class GitCommitError(GitError):
    """git commit failed (nothing to commit, hook rejection, etc.)."""


class GitPushError(GitError):
    """git push to origin failed (network, permissions, non-fast-forward)."""


def _run(
    args: list[str],
    *,
    cwd: Path,
    stdin_text: str | None = None,
    check: bool = False,
    timeout: float | None = None,
) -> subprocess.CompletedProcess:
    """Thin wrapper around subprocess.run with capture + UTF-8 text.

    ``check=False`` by default — callers inspect ``returncode`` and
    raise structured exceptions rather than letting CalledProcessError
    propagate. Set ``check=True`` only for invariants that should
    never fail given the prior validation steps.

    A timeout (default ``$DPORTSV3_GIT_TIMEOUT`` or 60s) bounds the
    subprocess so a hung remote can't block the request thread.
    On timeout, raises ``GitError`` with a clear message rather
    than propagating subprocess.TimeoutExpired (callers don't
    know about subprocess internals).
    """
    effective_timeout = (
        timeout if timeout is not None else _GIT_DEFAULT_TIMEOUT
    )
    try:
        return subprocess.run(
            args, cwd=str(cwd),
            capture_output=True, text=True,
            input=stdin_text, check=check,
            timeout=effective_timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            f"{' '.join(args[:3])}… timed out after "
            f"{effective_timeout}s (set $DPORTSV3_GIT_TIMEOUT "
            f"if the remote is legitimately slow)"
        ) from exc


def _assert_clone_dir(clone_dir: Path) -> None:
    if not clone_dir.is_dir():
        raise GitError(
            f"clone_dir {clone_dir!s} doesn't exist; set "
            f"$DPORTSV3_OPERATOR_CLONE to your local DeltaPorts "
            f"working tree"
        )
    if not (clone_dir / ".git").exists():
        raise GitError(
            f"clone_dir {clone_dir!s} isn't a git working tree "
            f"(no .git/ found)"
        )


def _current_branch(clone_dir: Path) -> str:
    p = _run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=clone_dir,
    )
    if p.returncode != 0:
        raise GitError(
            f"git rev-parse --abbrev-ref HEAD failed: "
            f"{(p.stderr or '').strip()[:200]}"
        )
    return p.stdout.strip()


def _is_dirty(clone_dir: Path) -> bool:
    """True iff the working tree has staged or unstaged changes,
    or untracked files git would consider commit-worthy."""
    p = _run(
        ["git", "status", "--porcelain"],
        cwd=clone_dir,
    )
    if p.returncode != 0:
        raise GitError(
            f"git status failed: {(p.stderr or '').strip()[:200]}"
        )
    return bool(p.stdout.strip())


def prepare_clean_branch(
    clone_dir: Path,
    *,
    base_branch: str,
    branch_name: str,
    remote: str = "origin",
) -> None:
    """Fetch origin, refuse if dirty/off-base, checkout a fresh
    branch from ``origin/<base_branch>``.

    Uses ``checkout -B`` (capital B) so a re-Accept on the same
    bundle reuses the same local branch name without manual cleanup
    — the remote-side state is what matters for idempotency.
    """
    _assert_clone_dir(clone_dir)
    if _is_dirty(clone_dir):
        raise GitDirtyClone(
            f"clone {clone_dir!s} has uncommitted changes; "
            f"commit, stash, or revert before retrying"
        )
    current = _current_branch(clone_dir)
    if current != base_branch:
        raise GitWrongBranch(
            f"clone is on branch {current!r}, expected base_branch "
            f"{base_branch!r}; checkout the correct branch before "
            f"retrying"
        )
    # Fetch the remote so our base ref is fresh.
    fetch = _run(
        ["git", "fetch", remote, base_branch],
        cwd=clone_dir,
    )
    if fetch.returncode != 0:
        raise GitError(
            f"git fetch {remote} {base_branch} failed: "
            f"{(fetch.stderr or '').strip()[:200]}"
        )
    # Checkout -B creates or resets the branch to remote's HEAD.
    co = _run(
        ["git", "checkout", "-B", branch_name,
         f"{remote}/{base_branch}"],
        cwd=clone_dir,
    )
    if co.returncode != 0:
        raise GitError(
            f"git checkout -B {branch_name} failed: "
            f"{(co.stderr or '').strip()[:200]}"
        )


def apply_diff(clone_dir: Path, diff_text: str) -> None:
    """Apply ``diff_text`` to ``clone_dir`` via ``git apply --3way
    --index``. The ``--index`` flag stages the result as it
    applies, including registering newly-created files. Without
    it the downstream ``commit_diff`` would only catch
    modifications to already-tracked files via ``git add -u`` and
    silently drop NEW files (e.g. dragonfly/patch-* created by an
    add_patch intent).

    Raises ``GitApplyConflict`` if the diff doesn't apply cleanly
    (probably the operator's clone has drifted from the bundle's
    baseline). Other failures raise ``GitApplyError``.
    """
    _assert_clone_dir(clone_dir)
    p = _run(
        ["git", "apply", "--3way", "--index", "--whitespace=nowarn"],
        cwd=clone_dir,
        stdin_text=diff_text,
    )
    if p.returncode == 0:
        return
    stderr = (p.stderr or "").strip()
    # `git apply --3way` reports conflict markers in stderr as
    # "with conflicts" or similar. The exact phrasing varies
    # across git versions; we treat any non-zero exit with
    # "conflict" in the output as a 3-way conflict.
    if "conflict" in stderr.lower():
        raise GitApplyConflict(
            f"git apply --3way produced conflicts: {stderr[:300]}"
        )
    raise GitApplyError(
        f"git apply failed: {stderr[:300] or '(no stderr)'}"
    )


def commit_diff(
    clone_dir: Path,
    *,
    title: str,
    body: str,
    signoff: bool = True,
) -> None:
    """Commit the staged changes (already in the index after
    ``apply_diff``, which uses ``--index``) with the templated
    message.

    Refuses if there's nothing to commit (would produce an empty
    commit, which the upstream review platform would reject). The
    message is built as ``<title>\\n\\n<body>``.

    Pre-11d-3-fix this ran ``git add -u`` to stage tracked-file
    modifications, but that pattern silently dropped new files
    (the load-bearing case for add_patch / add_file intents).
    Now relies on apply_diff's ``--index`` to populate the index
    correctly, so add isn't needed.
    """
    _assert_clone_dir(clone_dir)
    if not _is_dirty_after_add(clone_dir):
        raise GitCommitError(
            "nothing to commit — apply_diff produced no changes "
            "(diff may have already been applied or be empty)"
        )
    args = ["git", "commit"]
    if signoff:
        args.append("-s")
    args += ["-m", title, "-m", body]
    p = _run(args, cwd=clone_dir)
    if p.returncode != 0:
        stderr = (p.stderr or "").strip()
        raise GitCommitError(
            f"git commit failed: {stderr[:300] or '(no stderr)'}"
        )


def _is_dirty_after_add(clone_dir: Path) -> bool:
    """Are there staged changes ready to commit? Used by
    ``commit_diff`` to distinguish "nothing to commit" from real
    failures. Different from _is_dirty which includes untracked
    + unstaged."""
    p = _run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=clone_dir,
    )
    # `--quiet` returns 0 if no changes, 1 if there are changes.
    return p.returncode != 0


def push_branch(
    clone_dir: Path,
    *,
    branch_name: str,
    remote: str = "origin",
) -> None:
    """``git push --force-with-lease`` the feature branch.

    --force-with-lease (not plain --force) lets the re-Accept
    workflow update an existing branch safely: if the operator
    pushed to the same branch from elsewhere meanwhile, the lease
    check fails and we don't clobber.
    """
    _assert_clone_dir(clone_dir)
    p = _run(
        ["git", "push", "--force-with-lease",
         "--set-upstream", remote, branch_name],
        cwd=clone_dir,
    )
    if p.returncode != 0:
        stderr = (p.stderr or "").strip()
        raise GitPushError(
            f"git push to {remote}/{branch_name} failed: "
            f"{stderr[:300] or '(no stderr)'}"
        )
