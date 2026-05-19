"""Refresh the env's git repos from the host's mirror cache.

Two-phase operation:
  1. Refresh the bare mirrors under ``config.repos_dir`` from the host's
     working trees (``RepoCache.refresh_all`` — same logic the builder
     uses at env create time).
  2. From the host, run ``git fetch`` + ``git pull --ff-only`` against
     each repo's host-side checkout under ``env_dir/writable/work/<repo>``.

The bind-mount of ``repos_dir`` into the chroot (added in runtime.py)
lets ``git pull`` work from inside the env shell too — but the
operator-facing ``dportsv3 dev-env update`` command does the work from
the host, so it doesn't depend on the chroot being mounted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .builder import default_delta_root
from .config import DevEnvConfig
from .errors import CommandError, UsageError
from .log import info, step_timer
from .repos import RepoCache
from .state import EnvironmentState
from .store import EnvironmentStore


# Repos in the env to fast-forward. Each tuple is (label,
# writable-relative checkout path). DPorts is deliberately omitted —
# it's compose-generated, not a git checkout.
ENV_REPOS: list[tuple[str, str]] = [
    ("DeltaPorts", "work/DeltaPorts"),
    ("freebsd-ports", "work/freebsd-ports"),
]


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        raise CommandError(
            f"command failed: {' '.join(cmd)}\nstderr: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", str(repo)] + list(args))


def _is_dirty(repo: Path) -> bool:
    """True when the working tree has any uncommitted changes."""
    out = _run(["git", "-C", str(repo), "status", "--porcelain"])
    return bool(out.strip())


def _current_branch(repo: Path) -> str:
    try:
        out = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        return out.strip() or "HEAD"
    except CommandError:
        return "HEAD"


def update_env(
    config: DevEnvConfig,
    store: EnvironmentStore,
    name: str,
    *,
    force: bool = False,
) -> None:
    """Refresh mirrors + fast-forward the env's git checkouts."""
    state: EnvironmentState = store.load(name)
    delta_root = (
        Path(state.source.delta_root) if state.source.delta_root else default_delta_root()
    )
    if not delta_root.is_dir():
        raise UsageError(f"delta_root not found: {delta_root}")

    # 1. Refresh mirrors from the host's working trees.
    info(f"[1/2] Refreshing repo mirrors from {delta_root}")
    with step_timer("refresh mirrors"):
        RepoCache(config).refresh_all(delta_root)

    # 2. Fast-forward each repo checkout in the env.
    writable = store.writable_dir(name)
    info("[2/2] Fast-forwarding env repos")
    for label, rel in ENV_REPOS:
        repo_path = writable / rel
        if not (repo_path / ".git").exists():
            info(f"  {label}: skipped (no .git at {repo_path})")
            continue
        branch = _current_branch(repo_path)
        if _is_dirty(repo_path):
            if not force:
                raise UsageError(
                    f"{label} has uncommitted changes at {repo_path} "
                    f"(branch {branch}); rerun with --force to override"
                )
            info(f"  {label}: dirty (branch {branch}); --force given, proceeding")
        before = _git(repo_path, "rev-parse", "HEAD")
        _git(repo_path, "fetch", "--prune", "origin")
        try:
            _git(repo_path, "pull", "--ff-only", "origin", branch)
        except CommandError as exc:
            raise UsageError(
                f"{label} branch {branch} cannot fast-forward — "
                f"divergent history or wrong branch. Resolve manually: {exc}"
            )
        after = _git(repo_path, "rev-parse", "HEAD")
        if before == after:
            info(f"  {label}: already at {before[:12]} (branch {branch})")
        else:
            info(f"  {label}: {before[:12]} -> {after[:12]} (branch {branch})")
    info(f"environment {name} updated")
