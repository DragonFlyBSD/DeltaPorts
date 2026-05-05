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


class ChrootRunner:
    def __init__(self, root_dir: Path) -> None:
        self.root_dir = root_dir

    def command(self, argv: list[str], *, env: dict[str, str] | None = None) -> list[str]:
        chroot_environment = chroot_env()
        if env:
            chroot_environment.update(env)
        env_args = [f"{key}={value}" for key, value in chroot_environment.items()]
        return ["chroot", str(self.root_dir), "/usr/bin/env", "-i", *env_args, *argv]

    def run(
        self,
        argv: list[str],
        *,
        env: dict[str, str] | None = None,
        check: bool = False,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(self.command(argv, env=env), text=True, check=check, capture_output=capture_output)

    def output(self, argv: list[str], *, env: dict[str, str] | None = None) -> str:
        result = self.run(argv, env=env, capture_output=True)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, self.command(argv, env=env), result.stdout, result.stderr)
        return result.stdout.strip()

    def run_shell(
        self,
        script: str,
        *args: str,
        env: dict[str, str] | None = None,
        check: bool = False,
        capture_output: bool = False,
    ) -> subprocess.CompletedProcess[str]:
        return self.run(["/bin/sh", "-c", script, "_", *args], env=env, check=check, capture_output=capture_output)


def command_exists(root_dir: Path, command: str) -> bool:
    result = ChrootRunner(root_dir).run(["/bin/sh", "-c", 'command -v "$1" >/dev/null 2>&1', "_", command])
    return result.returncode == 0


def exec_shell(root_dir: Path) -> None:
    env_args = [f"{key}={value}" for key, value in chroot_env().items()]
    chroot_bin = shutil.which("chroot") or "/usr/sbin/chroot"
    if command_exists(root_dir, "bash"):
        os.execv(chroot_bin, ["chroot", str(root_dir), "/usr/bin/env", "-i", *env_args, "bash", "--noprofile", "--rcfile", "/root/.dports-dev-env.sh", "-i"])
    print("WARN: bash is unavailable in the environment; falling back to /bin/sh")
    os.execv(chroot_bin, ["chroot", str(root_dir), "/usr/bin/env", "-i", *env_args, "/bin/sh", "-c", ". /root/.dports-dev-env.sh; exec /bin/sh -i"])
