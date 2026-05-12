from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .errors import CommandError, UsageError
from .helpers import TOUCHED_ORIGINS_PATH
from .log import info, step_timer
from .repos import RepoCache
from .store import EnvironmentStore


GENERATOR_VENV_RELATIVE = Path("scripts/generator/.venv")


def extract_touched_origins(changed_paths: list[str]) -> list[str]:
    origins: set[str] = set()
    for raw in changed_paths:
        path = raw.strip().replace("\\", "/")
        if not path:
            continue
        while path.startswith("./"):
            path = path[2:]
        parts = path.split("/")
        if len(parts) < 3 or parts[0] != "ports":
            continue
        category = parts[1].strip()
        port = parts[2].strip()
        if not category or not port:
            continue
        if category.startswith(".") or port.startswith("."):
            continue
        origins.add(f"{category}/{port}")
    return sorted(origins)


class DirtySyncer:
    def __init__(self, config, store: EnvironmentStore) -> None:
        self.config = config
        self.store = store

    def sync(self, name: str) -> list[str]:
        from .session import EnvironmentSession

        state = self.store.load(name)
        env_dir = self.store.env_dir(name)
        EnvironmentSession(self.config, self.store).ensure_root_mounted(env_dir, state)

        host_repo = Path(state.source.delta_root)
        env_repo = state.root_dir / "work/DeltaPorts"
        touched_file = state.root_dir / Path(TOUCHED_ORIGINS_PATH).relative_to("/")

        if not host_repo.is_dir() or not (host_repo / ".git").exists():
            raise UsageError(f"host DeltaPorts checkout is unavailable: {host_repo}")
        if not env_repo.is_dir() or not (env_repo / ".git").exists():
            raise UsageError(f"env DeltaPorts checkout is unavailable: {env_repo}")

        changed_paths = self.collect_changed_paths(host_repo)
        touched_origins = extract_touched_origins(changed_paths)

        info(f"syncing dirty DeltaPorts checkout into env={state.name}")
        with step_timer("sync dirty DeltaPorts checkout"):
            mirror = RepoCache(self.config).refresh_local_mirror("deltaports", host_repo)
            self.reset_env_repo(env_repo, mirror, self.git_output(host_repo, ["rev-parse", "HEAD"]))
            self.apply_unstaged_patch(host_repo, env_repo)
            self.copy_untracked_files(host_repo, env_repo)
            touched_file.parent.mkdir(parents=True, exist_ok=True)
            if touched_origins:
                touched_file.write_text("\n".join(touched_origins) + "\n")
            else:
                touched_file.write_text("")

        if touched_origins:
            info(f"touched origins: {', '.join(touched_origins)}")
        else:
            info("no touched origins under ports/")
        return touched_origins

    def collect_changed_paths(self, host_repo: Path) -> list[str]:
        changed = self.git_output_lines(host_repo, ["diff", "--name-only", "-z"])
        changed.extend(self.git_output_lines(host_repo, ["ls-files", "--others", "--exclude-standard", "-z"]))
        return sorted(set(changed))

    def reset_env_repo(self, env_repo: Path, mirror: Path, host_head: str) -> None:
        info(f"resetting env DeltaPorts checkout at {env_repo}")
        self.git_run(env_repo, ["remote", "set-url", "origin", str(mirror)])
        self.git_run(env_repo, ["fetch", "--prune", "origin"])
        self.git_run(env_repo, ["reset", "--hard", host_head])
        self.git_run(env_repo, ["clean", "-fd", "-e", str(GENERATOR_VENV_RELATIVE)])

    def apply_unstaged_patch(self, host_repo: Path, env_repo: Path) -> None:
        patch = subprocess.run(["git", "-C", str(host_repo), "diff", "--binary"], capture_output=True)
        if patch.returncode != 0:
            raise CommandError("failed to read host unstaged diff")
        if not patch.stdout:
            return
        info("applying host unstaged patch to env DeltaPorts checkout")
        result = subprocess.run(
            ["git", "-C", str(env_repo), "apply", "--whitespace=nowarn", "-"],
            input=patch.stdout,
            capture_output=True,
        )
        if result.returncode != 0:
            raise CommandError("failed to apply host unstaged changes into env checkout")

    def copy_untracked_files(self, host_repo: Path, env_repo: Path) -> None:
        untracked = self.git_output_lines(host_repo, ["ls-files", "--others", "--exclude-standard", "-z"])
        if not untracked:
            return
        info(f"copying {len(untracked)} untracked file(s) into env DeltaPorts checkout")
        for relative_name in untracked:
            relative = Path(relative_name)
            source = host_repo / relative
            destination = env_repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                destination.unlink(missing_ok=True)
                os.symlink(os.readlink(source), destination)
                continue
            if not source.is_file():
                continue
            shutil.copy2(source, destination)

    def git_run(self, repo: Path, args: list[str]) -> None:
        result = subprocess.run(["git", "-C", str(repo), *args], text=True)
        if result.returncode != 0:
            raise CommandError(f"command failed: git -C {repo} {' '.join(args)}")

    def git_output(self, repo: Path, args: list[str]) -> str:
        result = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True)
        if result.returncode != 0:
            raise CommandError(f"command failed: git -C {repo} {' '.join(args)}")
        return result.stdout.strip()

    def git_output_lines(self, repo: Path, args: list[str]) -> list[str]:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
        if result.returncode != 0:
            raise CommandError(f"command failed: git -C {repo} {' '.join(args)}")
        payload = result.stdout.decode(errors="replace")
        if payload.endswith("\0"):
            payload = payload[:-1]
        return [line for line in payload.split("\0") if line]
