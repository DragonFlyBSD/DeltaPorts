"""
Utility functions for DPorts v2.

Provides common functionality used across the package:
- Logging setup
- External command execution (cpdup, patch)
- File operations
- Error handling
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class DPortsError(Exception):
    """Base exception for DPorts errors."""

    pass


# Module-level logger cache
_loggers: dict[str, logging.Logger] = {}
_log_level = logging.INFO


def setup_logging(verbose: int = 0, quiet: bool = False) -> None:
    """
    Configure logging for dports.

    Args:
        verbose: Verbosity level (0=INFO, 1=DEBUG, 2+=TRACE)
        quiet: If True, only show errors
    """
    global _log_level

    if quiet:
        _log_level = logging.ERROR
    elif verbose >= 2:
        _log_level = logging.DEBUG - 5  # TRACE level
    elif verbose >= 1:
        _log_level = logging.DEBUG
    else:
        _log_level = logging.INFO

    # Configure root logger
    logging.basicConfig(
        level=_log_level,
        format="%(levelname)s: %(message)s",
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger for a module.

    Args:
        name: Module name (usually __name__)

    Returns:
        Configured logger
    """
    if name not in _loggers:
        logger = logging.getLogger(name)
        logger.setLevel(_log_level)
        _loggers[name] = logger
    return _loggers[name]


def cpdup(src: Path, dst: Path, flags: list[str] | None = None) -> bool:
    """
    Copy directory using cpdup.

    cpdup is DragonFly's efficient copy utility that preserves
    metadata and handles hard links properly.

    Args:
        src: Source directory
        dst: Destination directory
        flags: Additional flags for cpdup

    Returns:
        True if successful

    Raises:
        DPortsError: If cpdup fails
    """
    log = get_logger(__name__)

    # Fall back to shutil if cpdup not available
    cpdup_path = shutil.which("cpdup")

    if cpdup_path:
        cmd = [cpdup_path]
        if flags:
            cmd.extend(flags)
        cmd.extend([str(src), str(dst)])

        log.debug(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
            )

            if result.returncode != 0:
                raise DPortsError(f"cpdup failed: {result.stderr}")

            return True

        except subprocess.TimeoutExpired:
            raise DPortsError("cpdup timed out")
    else:
        # Fall back to Python's shutil
        log.debug(f"cpdup not found, using shutil.copytree")
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True)
        return True


def apply_patch(
    patch_file: Path,
    target_dir: Path,
    strip: int = 0,
    filename: str | None = None,
) -> bool:
    """
    Apply a patch file.

    Args:
        patch_file: Path to the patch/diff file
        target_dir: Directory to apply patch in
        strip: Number of leading path components to strip (-p flag)
        filename: Specific file to patch (for single-file patches)

    Returns:
        True if patch applied successfully
    """
    log = get_logger(__name__)

    cmd = ["patch", f"-p{strip}", "-i", str(patch_file)]

    if filename:
        cmd.extend(["--", filename])

    log.debug(f"Running: {' '.join(cmd)} in {target_dir}")

    try:
        result = subprocess.run(
            cmd,
            cwd=target_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            log.warning(f"Patch failed: {result.stderr or result.stdout}")
            return False

        return True

    except subprocess.TimeoutExpired:
        log.error("Patch command timed out")
        return False
    except Exception as e:
        log.error(f"Patch error: {e}")
        return False


def cleanup_patch_artifacts(path: Path) -> int:
    """
    Remove patch artifacts (.orig, .rej files).

    Args:
        path: Directory to clean

    Returns:
        Number of files removed
    """
    log = get_logger(__name__)
    count = 0

    for pattern in ["*.orig", "*.rej"]:
        for artifact in path.rglob(pattern):
            try:
                artifact.unlink()
                count += 1
            except OSError as e:
                log.warning(f"Failed to remove {artifact}: {e}")

    if count > 0:
        log.debug(f"Removed {count} patch artifacts from {path}")

    return count


def filter_moved_entries(entries: list[str], moved_ports: set[str]) -> list[str]:
    """
    Filter out entries for ports that have been moved/deleted.

    Args:
        entries: List of port entries
        moved_ports: Set of moved port origins

    Returns:
        Filtered list
    """
    return [e for e in entries if e not in moved_ports]


def list_ports(path: Path, categories: list[str] | None = None) -> list[str]:
    """
    List all ports in a directory.

    Args:
        path: Base ports directory
        categories: Optional list of categories to search

    Returns:
        List of port origins (category/name)
    """
    ports = []

    if categories is None:
        # Find all categories
        categories = [
            d.name
            for d in path.iterdir()
            if d.is_dir()
            and not d.name.startswith(".")
            and d.name not in {"distfiles", "packages", "Mk", "Templates"}
        ]

    for category in categories:
        cat_dir = path / category
        if not cat_dir.exists():
            continue

        for port_dir in cat_dir.iterdir():
            if port_dir.is_dir() and not port_dir.name.startswith("."):
                if (port_dir / "Makefile").exists():
                    ports.append(f"{category}/{port_dir.name}")

    return sorted(ports)


def list_delta_ports(path: Path) -> list[str]:
    """
    List all ports with customizations in DeltaPorts.

    Args:
        path: Path to DeltaPorts/ports directory

    Returns:
        List of port origins with overlays
    """
    ports = []

    if not path.exists() or not path.is_dir():
        return ports

    for category_dir in path.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue

        for port_dir in category_dir.iterdir():
            if not port_dir.is_dir() or port_dir.name.startswith("."):
                continue

            has_manifest = (port_dir / "overlay.toml").exists()

            # Makefile indicators
            has_makefile_root = (port_dir / "Makefile.DragonFly").exists()
            has_makefile_targets = any(port_dir.glob("Makefile.DragonFly.@*"))

            # diffs/ indicators (target-scoped and legacy root files)
            has_diffs_root_files = False
            has_diffs_targets = False
            diffs_dir = port_dir / "diffs"
            if diffs_dir.is_dir():
                for entry in diffs_dir.iterdir():
                    if entry.is_file() and entry.suffix in {".diff", ".patch"}:
                        has_diffs_root_files = True
                    elif entry.is_dir() and entry.name.startswith("@"):
                        has_diffs_targets = True

            # dragonfly/ indicators (target-scoped and legacy root content)
            has_dragonfly_root_content = False
            has_dragonfly_targets = False
            dragonfly_dir = port_dir / "dragonfly"
            if dragonfly_dir.is_dir():
                for entry in dragonfly_dir.iterdir():
                    if entry.is_dir() and entry.name.startswith("@"):
                        has_dragonfly_targets = True
                    elif entry.name.startswith("."):
                        continue
                    else:
                        has_dragonfly_root_content = True

            # Check for any customization indicator (strict v2 + legacy layouts)
            if (
                has_manifest
                or has_makefile_root
                or has_makefile_targets
                or has_diffs_root_files
                or has_diffs_targets
                or has_dragonfly_root_content
                or has_dragonfly_targets
            ):
                ports.append(f"{category_dir.name}/{port_dir.name}")

    return sorted(ports)


def list_overlay_dirs(path: Path) -> list[str]:
    """
    List all category/port directories under a DeltaPorts ports tree.

    This is a discovery helper for full-tree scans that should not require
    Makefile presence or overlay markers.
    """
    ports: list[str] = []

    if not path.exists() or not path.is_dir():
        return ports

    for category_dir in path.iterdir():
        if not category_dir.is_dir() or category_dir.name.startswith("."):
            continue

        for port_dir in category_dir.iterdir():
            if port_dir.is_dir() and not port_dir.name.startswith("."):
                ports.append(f"{category_dir.name}/{port_dir.name}")

    return sorted(ports)


def _run_git(
    repo_path: Path, args: list[str], timeout: int = 60
) -> subprocess.CompletedProcess:
    """Run a git command in a repository and return the completed process."""
    cmd = ["git", *args]
    try:
        return subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise DPortsError("git executable not found") from e


def ensure_git_repo(path: Path) -> None:
    """Raise if the path is not a git repository."""
    if not path.exists():
        raise DPortsError(f"Path does not exist: {path}")

    result = _run_git(path, ["rev-parse", "--is-inside-work-tree"])
    if result.returncode != 0 or result.stdout.strip() != "true":
        raise DPortsError(f"Not a git repository: {path}")


def is_git_clean(path: Path) -> bool:
    """Return True when git working tree is clean."""
    ensure_git_repo(path)
    result = _run_git(path, ["status", "--porcelain"])
    if result.returncode != 0:
        raise DPortsError(result.stderr.strip() or "Failed to check git status")
    return result.stdout.strip() == ""


def ensure_git_clean(path: Path) -> None:
    """Raise if git working tree has local changes."""
    if not is_git_clean(path):
        raise DPortsError(f"Git tree is dirty: {path}")


def get_git_branch(path: Path) -> str:
    """Return current branch name (or HEAD in detached mode)."""
    ensure_git_repo(path)
    result = _run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if result.returncode != 0:
        raise DPortsError(result.stderr.strip() or "Failed to get current git branch")
    return result.stdout.strip()


def ensure_git_branch(path: Path, branch: str) -> None:
    """Raise if current branch is not the expected branch."""
    current = get_git_branch(path)
    if current != branch:
        raise DPortsError(
            f"FreeBSD ports tree branch mismatch: expected {branch}, current {current}"
        )


def sync_git_branch(path: Path, branch: str, remote: str = "origin") -> None:
    """
    Sync repository to a target branch using fetch + checkout + ff-only.

    Fails if tree is dirty.
    """
    ensure_git_clean(path)

    fetch = _run_git(path, ["fetch", remote], timeout=180)
    if fetch.returncode != 0:
        raise DPortsError(fetch.stderr.strip() or "git fetch failed")

    # If branch exists locally, checkout it. Otherwise create from remote branch.
    local_exists = _run_git(path, ["show-ref", "--verify", f"refs/heads/{branch}"])
    if local_exists.returncode == 0:
        checkout = _run_git(path, ["checkout", branch])
    else:
        remote_ref = _run_git(
            path, ["show-ref", "--verify", f"refs/remotes/{remote}/{branch}"]
        )
        if remote_ref.returncode != 0:
            raise DPortsError(f"Remote branch not found: {remote}/{branch}")
        checkout = _run_git(
            path, ["checkout", "-b", branch, "--track", f"{remote}/{branch}"]
        )

    if checkout.returncode != 0:
        raise DPortsError(checkout.stderr.strip() or f"git checkout {branch} failed")

    ff = _run_git(path, ["merge", "--ff-only", f"{remote}/{branch}"])
    if ff.returncode != 0:
        raise DPortsError(ff.stderr.strip() or "git merge --ff-only failed")
