"""Hook install / uninstall / status for dsynth integration.

Copies ``scripts/dsynth-hooks/`` into the dsynth hooks dir (usually
``/etc/dsynth``). Idempotent — re-running ``install`` replaces hook
scripts but never overwrites ``dportsv3-hooks.conf`` unless ``--force``
is passed. ``uninstall`` removes the files we shipped; the operator's
``dportsv3-hooks.conf`` is preserved unless ``--purge`` is given.
"""

from __future__ import annotations

import os
import shutil
import stat
from argparse import Namespace
from pathlib import Path

DEFAULT_PREFIX = Path("/etc/dsynth")

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

# The example config — written as ``dportsv3-hooks.conf`` if no operator
# config exists yet. Never overwritten without --force.
CONF_EXAMPLE = "dportsv3-hooks.conf.example"
CONF_TARGET = "dportsv3-hooks.conf"


def _source_dir() -> Path:
    """Resolve the source dir for hooks bundled with the dportsv3 install.

    For an editable install ``Path(__file__)`` points into the repo,
    so we can walk up to ``scripts/dsynth-hooks``. If layout ever
    changes this raises FileNotFoundError on first read.
    """
    return Path(__file__).resolve().parents[3] / "dsynth-hooks"


def cmd_hooks(args: Namespace) -> int:
    action = getattr(args, "hooks_action", None)
    if action == "install":
        return _cmd_install(args)
    if action == "uninstall":
        return _cmd_uninstall(args)
    if action == "status":
        return _cmd_status(args)
    print(f"Unknown hooks action: {action}", flush=True)
    return 1


def _cmd_install(args: Namespace) -> int:
    src = Path(getattr(args, "source", None) or _source_dir())
    dst = Path(getattr(args, "prefix", None) or DEFAULT_PREFIX)
    force = bool(getattr(args, "force", False))

    if not src.is_dir():
        print(f"Source dir not found: {src}")
        return 1
    dst.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    skipped: list[str] = []
    for name in HOOK_SCRIPTS:
        sfile = src / name
        if not sfile.is_file():
            print(f"Missing hook in source: {sfile}")
            return 1
        dfile = dst / name
        shutil.copy2(sfile, dfile)
        dfile.chmod(dfile.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(name)

    # Config: only if missing or --force, and source it from .example.
    src_conf = src / CONF_EXAMPLE
    dst_conf = dst / CONF_TARGET
    if src_conf.is_file():
        if dst_conf.exists() and not force:
            skipped.append(f"{CONF_TARGET} (exists; --force to overwrite)")
        else:
            shutil.copy2(src_conf, dst_conf)
            written.append(CONF_TARGET)

    print(f"Installed {len(written)} files into {dst}:")
    for name in written:
        print(f"  - {name}")
    for note in skipped:
        print(f"  skipped: {note}")

    print()
    print("Next steps:")
    print(f"  1. Edit {dst}/{CONF_TARGET} — set ARTIFACT_STORE_URL,")
    print("     DPORTSV3_TRACKER_URL, DPORTSV3_TRACKER_TARGET as needed.")
    print("  2. Make sure your dsynth profile is configured to run hooks")
    print(f"     from {dst} (the dsynth default).")
    print("  3. Start artifact-store + tracker before kicking off a build.")
    return 0


def _cmd_uninstall(args: Namespace) -> int:
    dst = Path(getattr(args, "prefix", None) or DEFAULT_PREFIX)
    purge = bool(getattr(args, "purge", False))

    if not dst.is_dir():
        print(f"Nothing to remove: {dst} does not exist")
        return 0

    removed: list[str] = []
    for name in HOOK_SCRIPTS:
        path = dst / name
        if path.exists():
            path.unlink()
            removed.append(name)

    conf_path = dst / CONF_TARGET
    if conf_path.exists():
        if purge:
            conf_path.unlink()
            removed.append(CONF_TARGET)
        else:
            print(
                f"Preserved {conf_path} (pass --purge to remove the operator config too)"
            )

    if not removed:
        print(f"No dportsv3-installed files found in {dst}")
        return 0

    print(f"Removed {len(removed)} files from {dst}:")
    for name in removed:
        print(f"  - {name}")
    return 0


def _cmd_status(args: Namespace) -> int:
    dst = Path(getattr(args, "prefix", None) or DEFAULT_PREFIX)
    src = Path(getattr(args, "source", None) or _source_dir())

    if not dst.is_dir():
        print(f"{dst}: missing (hooks not installed)")
        return 1

    src_mtimes = {
        name: (src / name).stat().st_mtime
        for name in HOOK_SCRIPTS
        if (src / name).is_file()
    }
    present = 0
    stale = 0
    missing = 0
    for name in HOOK_SCRIPTS:
        path = dst / name
        if not path.exists():
            missing += 1
            print(f"  missing: {name}")
            continue
        present += 1
        st = path.stat()
        executable = bool(st.st_mode & 0o111)
        src_mtime = src_mtimes.get(name)
        marker = " (stale: source is newer)" if (
            src_mtime is not None and st.st_mtime < src_mtime
        ) else ""
        perm = "x" if executable else "-"
        print(f"  {perm}  {name}{marker}")
        if marker:
            stale += 1
    conf_path = dst / CONF_TARGET
    if conf_path.exists():
        print(f"  ✓ {CONF_TARGET}")
    else:
        print(f"  missing: {CONF_TARGET}")

    print()
    print(
        f"{dst}: {present} hook(s) installed, {missing} missing, {stale} stale"
    )
    return 0 if missing == 0 else 1
