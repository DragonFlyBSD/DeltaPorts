"""Shared filesystem helpers for dportsv3 runtime modules."""

from __future__ import annotations

import shutil
from collections.abc import Callable
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


def diff_tree(
    left: Path,
    right: Path,
    *,
    normalize: Callable[[str, str], str] | None = None,
) -> list[tuple[str, str]]:
    """Content-aware recursive comparison of two trees.

    Returns ``(classification, relpath)`` pairs for every entry that is
    not byte-identical between ``left`` and ``right``. An empty list
    means the trees are equal. Classifications:

    - ``only_left`` — present in ``left``, absent in ``right``.
    - ``only_right`` — present in ``right``, absent in ``left``.
    - ``content`` — present on both sides, content differs (compared
      with ``shallow=False``, so mtime/size alone never count).
    - ``funny`` — type mismatch or unreadable on one side.

    ``normalize(relpath, text) -> text`` optionally relaxes the content
    comparison: a file flagged byte-different is re-checked after
    normalizing both sides, and dropped if equal. Used by the Makefile
    absorption phase to accept whitespace-only divergence in the
    ``Makefile`` while staying byte-exact for every other file. A file
    that can't be read as text (or normalize raises) stays a difference.

    Reuses ``_DeepDircmp`` so classification is content-based, matching
    ``reconcile``. Used by the compose-parity oracle (one side composed
    from the baseline tree, the other from the candidate tree)."""
    left, right = Path(left), Path(right)
    out: list[tuple[str, str]] = []
    if not left.is_dir() or not right.is_dir():
        if left.is_dir() != right.is_dir():
            out.append(("only_left" if left.is_dir() else "only_right", "."))
        return out
    _diff_pair(_DeepDircmp(str(left), str(right)), Path(), left, right, normalize, out)
    return out


def _content_equal_after_normalize(
    rel: str,
    left_file: Path,
    right_file: Path,
    normalize: Callable[[str, str], str],
) -> bool:
    try:
        lt = left_file.read_text()
        rt = right_file.read_text()
    except (OSError, UnicodeDecodeError):
        return False
    return normalize(rel, lt) == normalize(rel, rt)


def _diff_pair(
    cmp: dircmp,
    rel: Path,
    left: Path,
    right: Path,
    normalize: Callable[[str, str], str] | None,
    out: list[tuple[str, str]],
) -> None:
    for name in sorted(cmp.left_only):
        out.append(("only_left", str(rel / name)))
    for name in sorted(cmp.right_only):
        out.append(("only_right", str(rel / name)))
    for name in sorted(cmp.diff_files):
        relpath = str(rel / name)
        if normalize is not None and _content_equal_after_normalize(
            relpath, left / name, right / name, normalize
        ):
            continue
        out.append(("content", relpath))
    for name in sorted({*cmp.common_funny, *cmp.funny_files}):
        out.append(("funny", str(rel / name)))
    for name, sub in sorted(cmp.subdirs.items()):
        _diff_pair(sub, rel / name, left / name, right / name, normalize, out)


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
    # `cmpfiles(shallow=False)` resolves both sides via `open(...)`,
    # so symlinks land in `diff_files` whenever the resolved content
    # differs — including (a) symlink-vs-file, (b) file-vs-symlink,
    # and (c) symlink-vs-symlink with different targets. All three
    # break `copy2(follow_symlinks=False)`: case (a)/(c) fail
    # `os.symlink(... existing dst)` with FileExistsError; case (b)
    # silently follows dst's symlink and corrupts whatever it
    # pointed at. Unlink dst first whenever either side is a
    # symlink — for the plain file-vs-file hot path the check is
    # cheap and avoids the bug surface entirely.
    if src.is_symlink() or dst.is_symlink():
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
