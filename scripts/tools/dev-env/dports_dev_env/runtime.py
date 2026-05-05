from __future__ import annotations

import shutil
from pathlib import Path

from .config import Config
from .mounts import mount_null, mount_procfs


WRITABLE_DIRS = [
    ("work", "work", 0o755),
    ("root", "root", 0o700),
    ("tmp", "tmp", 0o1777),
    ("var_tmp", "var/tmp", 0o1777),
    ("etc_dsynth", "etc/dsynth", 0o755),
    ("construction", "construction", 0o755),
]


def ensure_resolv_conf(root_dir: Path, *, force: bool = False) -> None:
    source = Path("/etc/resolv.conf")
    target = root_dir / "etc/resolv.conf"
    if not source.is_file():
        return
    if target.is_file() and not force:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def prepare_env_writable_dirs(env_dir: Path) -> None:
    writable_dir = env_dir / "writable"
    for source_name, _target_name, mode in WRITABLE_DIRS:
        path = writable_dir / source_name
        path.mkdir(parents=True, exist_ok=True)
        path.chmod(mode)


def mount_env_writable_dirs(env_dir: Path, root_dir: Path) -> None:
    writable_dir = env_dir / "writable"
    for source_name, target_name, _mode in WRITABLE_DIRS:
        mount_null(writable_dir / source_name, root_dir / target_name)


def mount_env_root(provisioned_root: Path, env_dir: Path, root_dir: Path) -> None:
    root_dir.mkdir(parents=True, exist_ok=True)
    mount_null(provisioned_root, root_dir, read_only=True)
    prepare_env_writable_dirs(env_dir)
    mount_env_writable_dirs(env_dir, root_dir)


def prepare_root_runtime(config: Config, root_dir: Path) -> None:
    for name in ["dev", "proc", "work"]:
        (root_dir / name).mkdir(parents=True, exist_ok=True)
    ensure_resolv_conf(root_dir)
    mount_null(Path("/dev"), root_dir / "dev")
    mount_procfs(root_dir / "proc")
    if str(config.host_distdir) and config.host_distdir.is_dir():
        mount_null(config.host_distdir, root_dir / "usr/distfiles")
