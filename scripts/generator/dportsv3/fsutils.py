"""Shared filesystem helpers for dportsv3 runtime modules."""

from __future__ import annotations

import shutil
from filecmp import cmpfiles, dircmp
from pathlib import Path


class _DeepDircmp(dircmp):
    """``filecmp.dircmp`` uses ``cmpfiles`` with ``shallow=True`` by
    default, which classifies files by ``stat()`` (mtime + size).
    For reconcile we need content-based classification — same content
    with different mtimes must land in ``same_files`` (so we preserve
    dst's mtime), not ``diff_files`` (which would trigger a rewrite).

    ``phase4`` in Python's dircmp uses ``self.__class__`` when
    creating subdir dircmps, so this override propagates through
    recursion without further plumbing.
    """

    def phase3(self) -> None:
        same, diff, funny = cmpfiles(
            self.left, self.right, self.common_files, shallow=False
        )
        self.same_files, self.diff_files, self.funny_files = same, diff, funny


def copy_tree(src: Path, dst: Path) -> None:
    """Copy one directory tree to destination path."""
    if dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst, symlinks=True)


def reconcile(src: Path, dst: Path) -> None:
    """Mirror ``src`` into ``dst`` preserving ``dst`` mtimes when
    content matches.

    Intended use: compose builds a port subtree into a scratch dir,
    then reconciles the scratch onto the live tree. dsynth's
    port-change detector (``subs.c::crcDirTree`` in DragonFly) folds
    each file's ``mtime + size + path`` into a per-port CRC; an
    unconditional rewrite bumps mtime and force-rebuilds the package
    even when the content didn't actually change. ``reconcile`` keeps
    mtime stable for files whose content matches what's already on
    disk — so a no-change recompose is a true filesystem no-op and
    dsynth sees an unchanged CRC.

    Semantics (mirrors what cpdup -VV / rsync --checksum would do):

    - **Same content + same mode** → leave dst alone (mtime preserved).
    - **Same content + different mode** → ``chmod`` only (mtime
      preserved; src's mode wins).
    - **Different content** → atomic replace via ``shutil.copy2``
      (mtime + mode adopted from src).
    - **Only in src** → copy fresh (recursive for dirs).
    - **Only in dst** → remove (recursive for dirs, ``unlink`` for
      files and symlinks).
    - **Type mismatch** (one side file, other side dir, etc.) → clear
      dst then copy from src. ``filecmp.dircmp`` surfaces these as
      ``common_funny``.
    - **Permission / decode failures during compare** → conservative
      re-copy. ``dircmp`` surfaces these as ``funny_files``.

    Symlinks are mirrored (``shutil.copy2(follow_symlinks=False)``,
    ``shutil.copytree(symlinks=True)``); broken symlinks in src are
    propagated as broken symlinks in dst.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.is_dir():
        raise ValueError(f"reconcile src is not a directory: {src}")
    dst.mkdir(parents=True, exist_ok=True)
    _reconcile_pair(_DeepDircmp(str(src), str(dst)), src, dst)


def _reconcile_pair(cmp: dircmp, src: Path, dst: Path) -> None:
    # Entries present only in src — materialize fresh into dst.
    for name in cmp.left_only:
        _copy_fresh(src / name, dst / name)

    # Entries present only in dst — prune.
    for name in cmp.right_only:
        _remove(dst / name)

    # Same name on both sides, but content (or shape) differs.
    for name in cmp.diff_files:
        _replace_file(src / name, dst / name)

    # Type-mismatched or unreadable on one side: conservative replace.
    for name in (*cmp.common_funny, *cmp.funny_files):
        target = dst / name
        if target.exists() or target.is_symlink():
            _remove(target)
        _copy_fresh(src / name, target)

    # Same content on both sides — leave dst's mtime alone. Mode may
    # still drift (dircmp shallow=False compares content but not mode
    # bits); reconcile mode without rewriting content.
    for name in cmp.same_files:
        _sync_mode(src / name, dst / name)

    # Recurse into common subdirs. dircmp already cached the
    # subdir comparison in `subdirs`; no second os.scandir needed.
    for name, sub in cmp.subdirs.items():
        _reconcile_pair(sub, src / name, dst / name)


def _copy_fresh(src: Path, dst: Path) -> None:
    if src.is_symlink() or not src.is_dir():
        shutil.copy2(src, dst, follow_symlinks=False)
    else:
        shutil.copytree(src, dst, symlinks=True)


def _replace_file(src: Path, dst: Path) -> None:
    # `cmpfiles` classifies symlink-vs-file pairs into `diff_files`
    # (their resolved content differs), so we land here for type
    # mismatches too — `copy2(follow_symlinks=False)` can't overwrite
    # a regular file with a symlink without an explicit unlink first.
    # For plain file-vs-file the unlink is redundant but cheap.
    if src.is_symlink() != dst.is_symlink():
        try:
            dst.unlink()
        except FileNotFoundError:
            pass
    shutil.copy2(src, dst, follow_symlinks=False)


def _sync_mode(src: Path, dst: Path) -> None:
    try:
        src_mode = src.stat().st_mode
        dst_mode = dst.stat().st_mode
    except OSError:
        return
    if (src_mode & 0o7777) != (dst_mode & 0o7777):
        try:
            dst.chmod(src_mode & 0o7777)
        except OSError:
            pass


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        shutil.rmtree(path)
