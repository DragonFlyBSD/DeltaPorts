from __future__ import annotations

from pathlib import Path

from .base import find_ready_provisioned_base
from .chroot import command_exists, exec_shell
from .config import DevEnvConfig
from .dsynth import write_dsynth_config
from .errors import UsageError
from .helpers import write_shell_rc
from .log import info, warn
from .mounts import mounts_under
from .runtime import mount_env_root, mount_env_writable_dirs, prepare_env_writable_dirs, prepare_root_runtime
from .state import EnvironmentState
from .store import EnvironmentStore


class EnvironmentSession:
    def __init__(self, config: DevEnvConfig, store: EnvironmentStore) -> None:
        self.config = config
        self.store = store

    def enter(self, name: str, *, refresh: bool = False) -> None:
        state = self.store.load(name)
        if state.backend != "chroot":
            raise UsageError(f"unsupported backend in environment: {state.backend}")

        env_dir = self.store.env_dir(name)
        info(f"preparing shell entry for environment {name}")
        self.ensure_root_mounted(env_dir, state)
        if refresh or not (state.root_dir / "etc/dsynth/dsynth.ini").is_file():
            write_dsynth_config(self.config, state)
        if refresh or not (state.root_dir / "root/.dports-dev-env.sh").is_file():
            write_shell_rc(state)
        if not (state.root_dir / "usr/local/bin/dbuild").exists():
            warn("helper scripts missing in /usr/local/bin; recreate the env to pick up current helpers")
        prepare_root_runtime(self.config, state.root_dir)
        info(f"entering shell for env={name} target={state.target} origin={state.origin or '<full-tree>'}")
        info(f"compose root will be /work/artifacts/compose/{state.target}")
        if not command_exists(state.root_dir, "bash"):
            warn("bash is unavailable in the environment; falling back to /bin/sh")
        exec_shell(state.root_dir)

    def ensure_root_mounted(self, env_dir: Path, state: EnvironmentState) -> None:
        if mounts_under(state.root_dir):
            prepare_env_writable_dirs(env_dir)
            mount_env_writable_dirs(env_dir, state.root_dir)
            return
        provisioned_root = find_ready_provisioned_base(self.config, state.provisioned_base_id)
        mount_env_root(provisioned_root, env_dir, state.root_dir)
