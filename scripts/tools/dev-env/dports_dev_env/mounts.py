from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


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
    print(f"INFO: unmounting {target}")
    for _ in range(3):
        result = subprocess.run(["umount", str(target)], text=True, capture_output=True)
        if result.returncode == 0:
            return True
    print(f"ERROR: umount could not release {target}")
    if result.stderr:
        print(result.stderr.rstrip())
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
        print(f"INFO: mount already present at {target}")
        return
    if read_only:
        print(f"INFO: mounting {source} read-only at {target}")
        result = subprocess.run(["mount_null", "-o", "ro", str(source), str(target)])
        if result.returncode != 0:
            result = subprocess.run(["mount", "-t", "null", "-o", "ro", str(source), str(target)])
    else:
        print(f"INFO: mounting {source} at {target}")
        result = subprocess.run(["mount_null", str(source), str(target)])
    if result.returncode != 0:
        raise SystemExit(f"ERROR: failed to mount {source} at {target}")


def mount_procfs(target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    if is_mounted(target):
        return
    print(f"INFO: mounting procfs at {target}")
    result = subprocess.run(["mount_procfs", "proc", str(target)])
    if result.returncode != 0:
        raise SystemExit(f"ERROR: failed to mount procfs at {target}")
