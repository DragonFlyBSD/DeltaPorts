from __future__ import annotations

import errno
import shutil
from pathlib import Path

from .config import Config
from .errors import ProvisionError
from .log import warn
from .mounts import mount_null, mount_procfs


MAX_STATFS_MOUNT_TARGET_LEN = 79


WRITABLE_DIRS = [
    ("work", "work", 0o755),
    ("root", "root", 0o700),
    ("tmp", "tmp", 0o1777),
    ("var_tmp", "var/tmp", 0o1777),
    ("etc_dsynth", "etc/dsynth", 0o755),
    ("construction", "construction", 0o755),
]


def check_mount_target_length(target: Path) -> None:
    target_s = str(target)
    if len(target_s) >= MAX_STATFS_MOUNT_TARGET_LEN:
        raise ProvisionError(
            f"mount target too long ({len(target_s)} >= 79 chars), "
            f"would be truncated by statfs: {target}"
        )


def mount_null_checked(source: Path, target: Path, *, read_only: bool = False) -> bool:
    check_mount_target_length(target)
    return mount_null(source, target, read_only=read_only)


def mount_procfs_checked(target: Path) -> bool:
    check_mount_target_length(target)
    return mount_procfs(target)


def ensure_resolv_conf(root_dir: Path, *, force: bool = False) -> None:
    source = Path("/etc/resolv.conf")
    target = root_dir / "etc/resolv.conf"
    if not source.is_file():
        return
    if target.is_file() and not force:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, target)
    except OSError as exc:
        if exc.errno != errno.EROFS:
            raise
        warn(f"cannot update {target}: read-only filesystem")


def prepare_env_writable_dirs(env_dir: Path) -> None:
    writable_dir = env_dir / "writable"
    for source_name, _target_name, mode in WRITABLE_DIRS:
        path = writable_dir / source_name
        path.mkdir(parents=True, exist_ok=True)
        if (path.stat().st_mode & 0o7777) != mode:
            path.chmod(mode)


def mount_env_writable_dirs(env_dir: Path, root_dir: Path) -> None:
    writable_dir = env_dir / "writable"
    for source_name, target_name, _mode in WRITABLE_DIRS:
        mount_null_checked(writable_dir / source_name, root_dir / target_name)


def mount_env_root(provisioned_root: Path, env_dir: Path, root_dir: Path) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)
    mount_null_checked(provisioned_root, root_dir, read_only=True)
    prepare_env_writable_dirs(env_dir)
    mount_env_writable_dirs(env_dir, root_dir)


def prepare_root_runtime(config: Config, root_dir: Path, *, refresh_resolv_conf: bool = False) -> list[Path]:
    mounted_targets: list[Path] = []
    for name in ["dev", "proc", "work"]:
        (root_dir / name).mkdir(parents=True, exist_ok=True)
    ensure_resolv_conf(root_dir, force=refresh_resolv_conf)
    dev_target = root_dir / "dev"
    if mount_null_checked(Path("/dev"), dev_target):
        mounted_targets.append(dev_target)
    proc_target = root_dir / "proc"
    if mount_procfs_checked(proc_target):
        mounted_targets.append(proc_target)
    if str(config.host_distdir) and config.host_distdir.is_dir():
        distfiles_target = root_dir / "usr/distfiles"
        if mount_null_checked(config.host_distdir, distfiles_target):
            mounted_targets.append(distfiles_target)
    # Bind-mount the repo mirror cache so the env's git origin URLs
    # (recorded at clone time as host paths under config.repos_dir)
    # resolve from inside the chroot. Read-only — operators inside the
    # env shouldn't mutate the shared cache; `dportsv3 dev-env update`
    # is the supported way to refresh it.
    if config.repos_dir.is_dir():
        repos_target = root_dir / "work/repos"
        repos_target.parent.mkdir(parents=True, exist_ok=True)
        if mount_null_checked(config.repos_dir, repos_target, read_only=True):
            mounted_targets.append(repos_target)
    return mounted_targets
