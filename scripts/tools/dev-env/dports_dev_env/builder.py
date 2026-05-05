from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .base import ensure_base_archive, fetch_latest_world_asset
from .chroot import ChrootRunner
from .config import DevEnvConfig, ensure_cache_dirs
from .dsynth import write_dsynth_config
from .errors import CommandError, DevEnvError, UsageError
from .helpers import write_shell_rc
from .locks import CacheLock
from .log import info, step_timer, warn
from .names import default_env_name, target_to_branch
from .provision import BaseProvisioner
from .repos import RepoCache
from .runtime import mount_env_root, prepare_root_runtime
from .state import EnvironmentState, FailureState, InitialComposeState, RepoState, RuntimeState, SourceState
from .store import EnvironmentStore
from .venv import GeneratorVenvCache


@dataclass(frozen=True)
class CreateOptions:
    name: str | None
    target: str
    origin: str | None
    delta_root: Path
    backend: str
    freebsd_branch: str | None
    dports_branch: str
    allow_dirty: bool
    no_initial_compose: bool
    oracle_profile: str


@dataclass(frozen=True)
class CreateResult:
    env_name: str
    exit_code: int


class EnvironmentBuilder:
    def __init__(self, config: DevEnvConfig, store: EnvironmentStore, options: CreateOptions) -> None:
        self.config = config
        self.store = store
        self.options = options
        self.env_name = options.name or default_env_name(options.target, options.origin)
        self.env_dir = store.env_dir(self.env_name)
        self.root_dir = store.root_dir(self.env_name)
        self.writable_dir = store.writable_dir(self.env_name)
        self.exit_code = 0

    def create(self) -> CreateResult:
        self.validate()
        ensure_cache_dirs(self.config)
        with CacheLock(self.config.locks_dir, f"env-{self.env_name}"):
            if self.env_dir.exists():
                raise UsageError(f"environment already exists: {self.env_name}")
            self.env_dir.mkdir(parents=True)
            state = self.initial_state(provisioned_base_id="")
            self.store.save(state)
            try:
                with step_timer(f"create environment {self.env_name}"):
                    state = self.build(state)
                self.store.save(replace(state, status="ready", updated_at=now_utc(), failure=None))
                info(f"environment ready: {self.env_name}")
                return CreateResult(self.env_name, self.exit_code)
            except DevEnvError as exc:
                warn(f"create failed; environment retained for manual investigation: {exc}")
                failed = replace(state, status="failed", updated_at=now_utc(), failure=FailureState(str(exc)))
                self.store.save(failed)
                return CreateResult(self.env_name, 1)
            except (Exception, KeyboardInterrupt) as exc:
                # Unexpected failure (incl. ^C) -- record it before bubbling up
                # so a re-run of `list` shows the env as failed instead of stuck
                # in `creating`. We re-raise so cli.main reports the real cause.
                warn(f"create interrupted; environment retained for manual investigation: {exc!r}")
                failed = replace(state, status="failed", updated_at=now_utc(), failure=FailureState(repr(exc)))
                self.store.save(failed)
                raise

    def build(self, state: EnvironmentState) -> EnvironmentState:
        info("[1/7] Resolving latest DragonFly world asset")
        with step_timer("resolve latest DragonFly world asset"):
            asset = fetch_latest_world_asset(self.config)

        info("[2/7] Preparing provisioned DragonFly base")
        with step_timer("prepare provisioned DragonFly base"):
            archive = ensure_base_archive(self.config, asset)
            provisioned_base = BaseProvisioner(self.config).prepare(archive)
            state = replace(state, provisioned_base_id=provisioned_base.id, updated_at=now_utc())
            self.store.save(state)

        info("[3/7] Refreshing cached repo mirrors")
        with step_timer("refresh cached repo mirrors"):
            mirrors = RepoCache(self.config).refresh_all(self.options.delta_root)

        info("[4/7] Mounting throwaway chroot root from provisioned base")
        with step_timer("create throwaway chroot root"):
            mount_env_root(provisioned_base.root, self.env_dir, self.root_dir)
            prepare_root_runtime(self.config, self.root_dir)

        info("[5/7] Seeding env-local source trees and writing runtime config")
        with step_timer("seed env-local source trees and runtime config"):
            repos = RepoCache(self.config)
            repos.clone_branch("DeltaPorts", mirrors.deltaports, state.repos.deltaports_branch, self.root_dir / "work/DeltaPorts")
            generator_venv = self.root_dir / "work/DeltaPorts/scripts/generator/.venv"
            if generator_venv.exists():
                shutil.rmtree(generator_venv)
            repos.clone_branch("freebsd-ports", mirrors.freebsd_ports, state.repos.freebsd_branch, self.root_dir / "work/freebsd-ports")
            repos.export_branch("DPorts", mirrors.dports, state.repos.dports_branch, self.root_dir / "work/DPorts")
            (self.root_dir / "work/artifacts/compose").mkdir(parents=True, exist_ok=True)
            write_dsynth_config(self.config, state)
            write_shell_rc(state)

        info("[6/7] Preparing generator venv")
        with step_timer("prepare generator venv"):
            GeneratorVenvCache(self.config).prepare(self.root_dir, provisioned_base.id)

        state = replace(state, status="ready", updated_at=now_utc(), failure=None)
        self.store.save(state)

        if self.options.no_initial_compose:
            info("[7/7] Skipping initial compose (--no-initial-compose); run 'regen' inside the shell when ready")
            state = replace(state, initial_compose=InitialComposeState("skipped", now_utc(), "--no-initial-compose"), updated_at=now_utc())
            self.store.save(state)
            return state

        info("[7/7] Running initial compose")
        state = replace(state, initial_compose=InitialComposeState("running", now_utc()), updated_at=now_utc())
        self.store.save(state)
        with step_timer("initial compose"):
            try:
                self.compose_inside_env(state)
            except CommandError as exc:
                self.exit_code = 1
                state = replace(state, initial_compose=InitialComposeState("failed", now_utc(), str(exc)), updated_at=now_utc())
                self.store.save(state)
                warn(f"initial compose failed; environment remains ready for inspection: {exc}")
                return state
        state = replace(state, initial_compose=InitialComposeState("ok", now_utc()), updated_at=now_utc())
        self.store.save(state)
        return state

    def validate(self) -> None:
        if self.options.backend != "chroot":
            raise UsageError(f"unsupported backend: {self.options.backend}")
        if self.options.oracle_profile not in {"off", "local", "ci"}:
            raise UsageError("--oracle-profile must be one of: off, local, ci")
        if not self.options.delta_root.is_dir() or not (self.options.delta_root / "dportsv3").is_file():
            raise UsageError(f"Delta root does not look like this repo: {self.options.delta_root}")
        self.run_git(["git", "-C", str(self.options.delta_root), "rev-parse", "--git-dir"])
        dirty = subprocess.run(["git", "-C", str(self.options.delta_root), "status", "--porcelain"], text=True, capture_output=True)
        if dirty.stdout.strip():
            warn("host DeltaPorts checkout has uncommitted changes; only committed state will appear in the env")
            if not self.options.allow_dirty:
                raise UsageError("refusing to create env from a dirty host checkout (pass --allow-dirty to proceed)")
        for command in ["tar", "git", "chroot", "mount_null", "mount_procfs"]:
            if shutil.which(command) is None:
                raise UsageError(f"required command not found: {command}")

    def initial_state(self, provisioned_base_id: str) -> EnvironmentState:
        created_at = now_utc()
        freebsd_branch = self.options.freebsd_branch or target_to_branch(self.options.target)
        return EnvironmentState(
            schema=1,
            name=self.env_name,
            backend="chroot",
            target=self.options.target,
            origin=self.options.origin or "",
            status="creating",
            created_at=created_at,
            updated_at=created_at,
            root_dir=self.root_dir,
            writable_dir=self.writable_dir,
            provisioned_base_id=provisioned_base_id,
            repos=RepoState(
                deltaports_branch=self.config.deltaports_branch,
                freebsd_branch=freebsd_branch,
                dports_branch=self.options.dports_branch,
            ),
            source=SourceState(delta_root=str(self.options.delta_root)),
            runtime=RuntimeState(host_distdir=str(self.config.host_distdir), oracle_profile=self.options.oracle_profile),
            initial_compose=InitialComposeState("not-run", created_at),
        )

    def compose_inside_env(self, state: EnvironmentState) -> None:
        result = ChrootRunner(self.root_dir).run(
            [
                "/work/DeltaPorts/dportsv3",
                "compose",
                "--target",
                state.target,
                "--delta-root",
                "/work/DeltaPorts",
                "--freebsd-root",
                "/work/freebsd-ports",
                "--lock-root",
                "/work/DPorts",
                "--output",
                f"/work/artifacts/compose/{state.target}",
                "--replace-output",
                "--oracle-profile",
                state.oracle_profile,
            ]
        )
        if result.returncode != 0:
            raise CommandError("initial compose failed")

    def run_git(self, command: list[str]) -> None:
        result = subprocess.run(command, text=True, capture_output=True)
        if result.returncode != 0:
            raise UsageError(f"command failed: {' '.join(command)}")


def now_utc() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_delta_root() -> Path:
    return Path(__file__).resolve().parents[4]
