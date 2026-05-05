from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


CHROOT_PATH = "/usr/local/bin:/usr/local/sbin:/bin:/sbin:/usr/bin:/usr/sbin"


def chroot_env() -> dict[str, str]:
    return {
        "HOME": "/root",
        "TERM": os.environ.get("TERM", "xterm"),
        "PATH": CHROOT_PATH,
    }


def run_in_chroot(
    root_dir: Path,
    script: str,
    *args: str,
    check: bool = False,
    capture_output: bool = False,
) -> subprocess.CompletedProcess[str]:
    command = ["chroot", str(root_dir), "/usr/bin/env", "-i", *[f"{key}={value}" for key, value in chroot_env().items()], "/bin/sh", "-c", script, "_", *args]
    return subprocess.run(command, text=True, check=check, capture_output=capture_output)


def command_exists(root_dir: Path, command: str) -> bool:
    result = run_in_chroot(root_dir, 'command -v "$1" >/dev/null 2>&1', command)
    return result.returncode == 0


def exec_shell(root_dir: Path) -> None:
    env_args = [f"{key}={value}" for key, value in chroot_env().items()]
    chroot_bin = shutil.which("chroot") or "/usr/sbin/chroot"
    if command_exists(root_dir, "bash"):
        os.execv(chroot_bin, ["chroot", str(root_dir), "/usr/bin/env", "-i", *env_args, "bash", "--noprofile", "--rcfile", "/root/.dports-dev-env.sh", "-i"])
    print("WARN: bash is unavailable in the environment; falling back to /bin/sh")
    os.execv(chroot_bin, ["chroot", str(root_dir), "/usr/bin/env", "-i", *env_args, "/bin/sh", "-c", ". /root/.dports-dev-env.sh; exec /bin/sh -i"])
