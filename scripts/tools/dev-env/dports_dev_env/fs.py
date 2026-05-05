from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .config import DevEnvConfig, validate_cache_root
from .errors import StateError
from .log import error, info, warn
from .mounts import mounts_under, unmount_under


def is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def clear_immutable_flags(path: Path) -> None:
    if not path.exists():
        return
    if shutil.which("chflags") is None:
        return
    info(f"clearing immutable flags under {path}")
    result = subprocess.run(["chflags", "-R", "noschg,nouchg", str(path)], text=True, capture_output=True)
    if result.returncode != 0:
        warn(f"failed to clear immutable flags under {path}")


def safe_remove_tree(config: DevEnvConfig, path: Path) -> None:
    validate_cache_root(config.cache_root)
    if not str(path):
        raise StateError("safe_remove_tree: empty path")
    cache_root = config.cache_root.resolve(strict=False)
    target = path.resolve(strict=False)
    if not is_relative_to(target, cache_root):
        raise StateError(f"safe_remove_tree: refusing to remove outside cache root: {path}")

    unmount_under(target)
    survivors = mounts_under(target)
    if survivors:
        error(f"refusing to remove {path}; mounts are still present:")
        for mount in survivors:
            print(str(mount.target))
        raise StateError("unmount the listed paths and re-run")

    clear_immutable_flags(target)
    shutil.rmtree(target)


def copy_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        raise StateError(f"destination already exists: {destination}")
    destination.mkdir(parents=True)
    producer = subprocess.Popen(["tar", "-C", str(source), "-cpf", "-", "."], stdout=subprocess.PIPE)
    assert producer.stdout is not None
    consumer = subprocess.run(["tar", "-C", str(destination), "-xpf", "-"], stdin=producer.stdout)
    producer.stdout.close()
    producer_status = producer.wait()
    if producer_status != 0 or consumer.returncode != 0:
        raise StateError(
            f"copy_tree failed (producer={producer_status} consumer={consumer.returncode} "
            f"src={source} dst={destination})"
        )
