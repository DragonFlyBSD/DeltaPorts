from __future__ import annotations

import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .errors import MountError
from .log import error, info, warn


@dataclass(frozen=True)
class Mount:
    source: str
    target: Path
    fstype: str | None = None
    options: str | None = None


def parse_mount_line(line: str) -> Mount | None:
    marker = " on "
    if marker not in line:
        return None
    source, rest = line.split(marker, 1)
    if " (" in rest:
        target, options = rest.split(" (", 1)
        options = options.rstrip(")")
    else:
        target, options = rest, None
    return Mount(source=source, target=Path(target), options=options)


def list_mounts() -> list[Mount]:
    result = subprocess.run(["mount"], check=True, text=True, capture_output=True)
    mounts: list[Mount] = []
    for line in result.stdout.splitlines():
        mount = parse_mount_line(line)
        if mount is not None:
            mounts.append(mount)
    return mounts


def is_mounted(target: Path) -> bool:
    resolved = target.resolve(strict=False)
    return any(mount.target.resolve(strict=False) == resolved for mount in list_mounts())


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def mounts_under(root: Path) -> list[Mount]:
    root = root.resolve(strict=False)
    return [mount for mount in list_mounts() if mount.target == root or is_relative_to(mount.target, root)]


def unmount(target: Path) -> bool:
    info(f"unmounting {target}")
    last_stderr = ""
    for attempt in range(1, 4):
        result = subprocess.run(["umount", str(target)], text=True, capture_output=True)
        if result.returncode == 0:
            return True
        last_stderr = result.stderr
        if attempt < 3:
            warn(f"umount {target} failed (attempt {attempt}); retrying")
            time.sleep(1)
    error(f"umount could not release {target}")
    if last_stderr:
        print(last_stderr.rstrip(), file=sys.stderr)
    holder_cmd: list[str] | None = None
    if shutil.which("fstat"):
        holder_cmd = ["fstat", "-f", str(target)]
    elif shutil.which("lsof"):
        holder_cmd = ["lsof", str(target)]
    if holder_cmd:
        info(f"current holders (best-effort via {holder_cmd[0]}):")
        subprocess.run(holder_cmd, check=False)
    return False


def unmount_under(root: Path) -> bool:
    mounts = sorted(mounts_under(root), key=lambda mount: len(str(mount.target)), reverse=True)
    ok = True
    for mount in mounts:
        if not unmount(mount.target):
            ok = False
    return ok


def mount_null(source: Path, target: Path, *, read_only: bool = False) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if is_mounted(target):
        info(f"mount already present at {target}")
        return
    if read_only:
        info(f"mounting {source} read-only at {target}")
        result = subprocess.run(["mount_null", "-o", "ro", str(source), str(target)])
        if result.returncode != 0:
            result = subprocess.run(["mount", "-t", "null", "-o", "ro", str(source), str(target)])
    else:
        info(f"mounting {source} at {target}")
        result = subprocess.run(["mount_null", str(source), str(target)])
    if result.returncode != 0:
        raise MountError(f"failed to mount {source} at {target}")


def mount_procfs(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if is_mounted(target):
        return
    info(f"mounting procfs at {target}")
    result = subprocess.run(["mount_procfs", "proc", str(target)])
    if result.returncode != 0:
        raise MountError(f"failed to mount procfs at {target}")
