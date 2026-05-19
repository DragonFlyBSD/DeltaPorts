"""dsynth hook install/uninstall/status for a dev-env.

The dev-env's writable overlay carries ``etc_dsynth`` (see
``runtime.WRITABLE_DIRS``) which is bind-mounted onto ``/etc/dsynth``
inside the chroot. Installing hooks into that writable dir persists
across remounts and is visible to dsynth runs inside the env.

This module operates on the host-side path
``${env_dir}/writable/etc_dsynth``. The chroot does not need to be
mounted to install — files written here will be picked up at next
mount.
"""

from __future__ import annotations

import shutil
import stat
from argparse import Namespace
from pathlib import Path

# Files we ship as executable hook scripts (chmod 0755 on install).
HOOK_SCRIPTS: tuple[str, ...] = (
    "hook_common.sh",
    "hook_pkg_failure",
    "hook_pkg_ignored",
    "hook_pkg_skipped",
    "hook_pkg_start",
    "hook_pkg_started",
    "hook_pkg_success",
    "hook_run_end",
    "hook_run_start",
)

# Example config — copied as ``dportsv3-hooks.conf`` only if no
# operator-edited config exists yet.
CONF_EXAMPLE = "dportsv3-hooks.conf.example"
CONF_TARGET = "dportsv3-hooks.conf"


def repo_hook_source() -> Path:
    """Path to the repo's scripts/dsynth-hooks/ directory.

    Walks up from this module: parents[0]=dports_dev_env,
    parents[1]=dev-env, parents[2]=tools, parents[3]=scripts.
    """
    return Path(__file__).resolve().parents[3] / "dsynth-hooks"


def install_hooks(
    target_dir: Path,
    source_dir: Path | None = None,
    *,
    force: bool = False,
) -> tuple[list[str], list[str]]:
    """Copy hook scripts + (optionally) a default conf into ``target_dir``.

    Returns (written_files, skipped_notes). ``dportsv3-hooks.conf`` is
    written from the example only if it doesn't exist or ``force`` is
    set. Hook scripts are always replaced (they're code, not config).
    """
    src = source_dir or repo_hook_source()
    if not src.is_dir():
        raise FileNotFoundError(f"source dir not found: {src}")
    target_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped: list[str] = []

    for name in HOOK_SCRIPTS:
        sfile = src / name
        if not sfile.is_file():
            raise FileNotFoundError(f"missing hook in source: {sfile}")
        dfile = target_dir / name
        shutil.copy2(sfile, dfile)
        dfile.chmod(
            dfile.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        written.append(name)

    src_conf = src / CONF_EXAMPLE
    dst_conf = target_dir / CONF_TARGET
    if src_conf.is_file():
        if dst_conf.exists() and not force:
            skipped.append(f"{CONF_TARGET} (exists; --force to overwrite)")
        else:
            shutil.copy2(src_conf, dst_conf)
            written.append(CONF_TARGET)

    return written, skipped


def uninstall_hooks(
    target_dir: Path, *, purge: bool = False
) -> list[str]:
    """Remove hook scripts (and config if ``purge``). Returns removed names."""
    if not target_dir.is_dir():
        return []
    removed: list[str] = []
    for name in HOOK_SCRIPTS:
        path = target_dir / name
        if path.exists():
            path.unlink()
            removed.append(name)
    conf = target_dir / CONF_TARGET
    if conf.exists() and purge:
        conf.unlink()
        removed.append(CONF_TARGET)
    return removed


def status_hooks(
    target_dir: Path, source_dir: Path | None = None
) -> dict[str, object]:
    """Return a dict describing what's installed vs. the source.

    Keys: present, missing, stale, conf_present.
    """
    if not target_dir.is_dir():
        return {
            "present": [],
            "missing": list(HOOK_SCRIPTS),
            "stale": [],
            "conf_present": False,
            "exists": False,
        }
    src = source_dir or repo_hook_source()
    src_mtimes: dict[str, float] = {}
    if src.is_dir():
        for name in HOOK_SCRIPTS:
            sfile = src / name
            if sfile.is_file():
                src_mtimes[name] = sfile.stat().st_mtime

    present: list[str] = []
    missing: list[str] = []
    stale: list[str] = []
    for name in HOOK_SCRIPTS:
        path = target_dir / name
        if not path.exists():
            missing.append(name)
            continue
        present.append(name)
        src_mtime = src_mtimes.get(name)
        if src_mtime is not None and path.stat().st_mtime < src_mtime:
            stale.append(name)

    return {
        "present": present,
        "missing": missing,
        "stale": stale,
        "conf_present": (target_dir / CONF_TARGET).exists(),
        "exists": True,
    }


# ---- CLI argparse handlers ----


def _env_hooks_dir(env_dir: Path) -> Path:
    """Resolve the per-env writable etc_dsynth dir.

    Matches ``runtime.WRITABLE_DIRS`` source name ``etc_dsynth``.
    """
    return env_dir / "writable" / "etc_dsynth"


def cmd_hooks_install(args: Namespace, env_dir: Path) -> int:
    target = _env_hooks_dir(env_dir)
    source = Path(args.source) if getattr(args, "source", None) else None
    try:
        written, skipped = install_hooks(
            target, source_dir=source, force=bool(args.force)
        )
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    print(f"Installed {len(written)} files into {target}:")
    for name in written:
        print(f"  - {name}")
    for note in skipped:
        print(f"  skipped: {note}")
    print()
    print("Next steps:")
    print(f"  1. Edit {target}/{CONF_TARGET} — set ARTIFACT_STORE_URL,")
    print("     DPORTSV3_TRACKER_URL, DPORTSV3_TRACKER_TARGET.")
    print("  2. Hooks become visible at /etc/dsynth inside the chroot")
    print("     once the env is mounted (or on next 'dportsv3 dev-env shell').")
    return 0


def cmd_hooks_uninstall(args: Namespace, env_dir: Path) -> int:
    target = _env_hooks_dir(env_dir)
    if not target.is_dir():
        print(f"Nothing to remove: {target} does not exist")
        return 0
    removed = uninstall_hooks(target, purge=bool(args.purge))
    if not removed:
        print(f"No dportsv3-installed hooks found in {target}")
        return 0
    print(f"Removed {len(removed)} files from {target}:")
    for name in removed:
        print(f"  - {name}")
    if not args.purge and (target / CONF_TARGET).exists():
        print(f"Preserved {target}/{CONF_TARGET} (pass --purge to remove it too)")
    return 0


def cmd_hooks_status(args: Namespace, env_dir: Path) -> int:
    target = _env_hooks_dir(env_dir)
    source = Path(args.source) if getattr(args, "source", None) else None
    info = status_hooks(target, source_dir=source)

    if not info["exists"]:
        print(f"{target}: missing (hooks not installed)")
        return 1

    for name in info["present"]:
        marker = " (stale: source is newer)" if name in info["stale"] else ""
        print(f"  x  {name}{marker}")
    for name in info["missing"]:
        print(f"  missing: {name}")
    print(f"  {'✓' if info['conf_present'] else 'missing:'} {CONF_TARGET}")
    print()
    present = len(info["present"])
    missing = len(info["missing"])
    stale = len(info["stale"])
    print(
        f"{target}: {present} hook(s) installed, {missing} missing, {stale} stale"
    )
    return 0 if missing == 0 else 1
