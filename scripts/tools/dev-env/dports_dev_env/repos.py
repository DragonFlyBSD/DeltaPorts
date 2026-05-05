from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import DevEnvConfig
from .errors import CommandError
from .locks import CacheLock
from .log import info, step_timer


@dataclass(frozen=True)
class RepoMirrors:
    deltaports: Path
    freebsd_ports: Path
    dports: Path


class RepoCache:
    def __init__(self, config: DevEnvConfig) -> None:
        self.config = config

    def refresh_all(self, delta_root: Path) -> RepoMirrors:
        deltaports = self.refresh_local_mirror("deltaports", delta_root)
        freebsd_ports = self.refresh_remote_mirror("freebsd-ports", self.config.freebsd_ports_url)
        dports = self.refresh_remote_mirror("dports", self.config.dports_url)
        return RepoMirrors(deltaports=deltaports, freebsd_ports=freebsd_ports, dports=dports)

    def refresh_remote_mirror(self, name: str, url: str) -> Path:
        mirror = self.config.repos_dir / f"{name}.git"
        with CacheLock(self.config.locks_dir, f"repo-{name}"):
            with step_timer(f"refresh {name} mirror"):
                if not mirror.is_dir():
                    info(f"creating {name} mirror at {mirror}")
                    self.run(["git", "clone", "--mirror", url, str(mirror)])
                else:
                    info(f"updating {name} mirror {mirror}")
                    self.run(["git", "--git-dir", str(mirror), "fetch", "--prune", "origin"])
        return mirror

    def refresh_local_mirror(self, name: str, source_repo: Path) -> Path:
        mirror = self.config.repos_dir / f"{name}.git"
        with CacheLock(self.config.locks_dir, f"repo-{name}"):
            with step_timer(f"refresh {name} mirror"):
                if not mirror.is_dir():
                    info(f"creating {name} mirror from {source_repo}")
                    self.run(["git", "clone", "--mirror", str(source_repo), str(mirror)])
                else:
                    info(f"updating {name} mirror from {source_repo}")
                    self.run(["git", "--git-dir", str(mirror), "remote", "set-url", "origin", str(source_repo)])
                    self.run(["git", "--git-dir", str(mirror), "fetch", "--prune", "origin", "+refs/*:refs/*"])
        return mirror

    def clone_branch(self, label: str, mirror: Path, branch: str, destination: Path) -> None:
        info(f"cloning {label} branch {branch} into {destination}")
        with step_timer(f"clone {label}"):
            self.run(["git", "clone", "--single-branch", "--branch", branch, str(mirror), str(destination)])

    def export_branch(self, label: str, mirror: Path, branch: str, destination: Path) -> None:
        ref = self.resolve_ref(label, mirror, branch)
        info(f"exporting {label} branch {branch} into {destination}")
        destination.mkdir(parents=True, exist_ok=True)
        archive = subprocess.Popen(["git", "--git-dir", str(mirror), "archive", ref], stdout=subprocess.PIPE)
        assert archive.stdout is not None
        extract = subprocess.run(["tar", "-C", str(destination), "-xpf", "-"], stdin=archive.stdout)
        archive.stdout.close()
        archive_status = archive.wait()
        if archive_status != 0 or extract.returncode != 0:
            raise CommandError(f"failed to export {label} branch {branch}")

    def resolve_ref(self, label: str, mirror: Path, branch: str) -> str:
        for ref in [f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"]:
            result = subprocess.run(["git", "--git-dir", str(mirror), "rev-parse", "--verify", ref], text=True, capture_output=True)
            if result.returncode == 0:
                return ref
        raise CommandError(f"branch not found in {label} mirror: {branch}")

    def run(self, command: list[str]) -> None:
        result = subprocess.run(command, text=True)
        if result.returncode != 0:
            raise CommandError(f"command failed: {' '.join(command)}")
