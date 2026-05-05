from __future__ import annotations

import shutil
from pathlib import Path

from .config import DevEnvConfig
from .errors import StateError
from .state import EnvironmentState, EnvInfo, read_env_info, read_env_state, write_env_state


class EnvironmentStore:
    def __init__(self, config: DevEnvConfig) -> None:
        self.config = config

    def env_dir(self, name: str) -> Path:
        return self.config.envs_dir / name

    def root_dir(self, name: str) -> Path:
        return self.env_dir(name) / "root"

    def writable_dir(self, name: str) -> Path:
        return self.env_dir(name) / "writable"

    def list_infos(self) -> list[tuple[Path, EnvInfo]]:
        if not self.config.envs_dir.is_dir():
            return []
        return [(path, read_env_info(path)) for path in sorted(self.config.envs_dir.iterdir()) if path.is_dir()]

    def load(self, name: str) -> EnvironmentState:
        env_dir = self.env_dir(name)
        if not env_dir.is_dir():
            raise StateError(f"environment not found: {name}")
        try:
            return read_env_state(env_dir)
        except FileNotFoundError as exc:
            raise StateError(f"environment has no env.json: {name}") from exc

    def save(self, state: EnvironmentState) -> None:
        write_env_state(self.env_dir(state.name), state)

    def remove_partial_dir(self, name: str) -> None:
        env_dir = self.env_dir(name)
        if not env_dir.is_dir():
            raise StateError(f"environment not found: {name}")
        shutil.rmtree(env_dir)
