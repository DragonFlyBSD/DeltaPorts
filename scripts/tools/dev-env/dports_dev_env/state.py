from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .errors import StateError


STATE_SCHEMA = 1
EnvStatus = Literal["creating", "ready", "failed", "destroying"]
ComposeStatus = Literal["not-run", "running", "ok", "failed", "skipped"]


@dataclass(frozen=True)
class RepoState:
    deltaports_branch: str
    freebsd_branch: str
    dports_branch: str


@dataclass(frozen=True)
class SourceState:
    delta_root: str


@dataclass(frozen=True)
class RuntimeState:
    host_distdir: str
    oracle_profile: str


@dataclass(frozen=True)
class FailureState:
    reason: str


@dataclass(frozen=True)
class InitialComposeState:
    status: ComposeStatus
    updated_at: str
    reason: str = ""


@dataclass(frozen=True)
class EnvironmentState:
    schema: int
    name: str
    backend: str
    target: str
    origin: str
    status: EnvStatus
    created_at: str
    updated_at: str
    root_dir: Path
    writable_dir: Path
    provisioned_base_id: str
    repos: RepoState
    source: SourceState
    runtime: RuntimeState
    initial_compose: InitialComposeState | None = None
    failure: FailureState | None = None

    @property
    def oracle_profile(self) -> str:
        return self.runtime.oracle_profile


@dataclass(frozen=True)
class EnvInfo:
    name: str
    backend: str
    target: str
    origin: str
    status: str
    has_state: bool


def state_path(env_dir: Path) -> Path:
    return env_dir / "env.json"


def state_to_json(state: EnvironmentState) -> dict[str, object]:
    data = asdict(state)
    data["root_dir"] = str(state.root_dir)
    data["writable_dir"] = str(state.writable_dir)
    return data


def _require_str(data: dict[str, object], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise StateError(f"state file is missing required field: {key}")
    return value


_VALID_STATUSES = {"creating", "ready", "failed", "destroying"}
_VALID_COMPOSE_STATUSES = {"not-run", "running", "ok", "failed", "skipped"}


def state_from_json(data: dict[str, object]) -> EnvironmentState:
    schema = data.get("schema")
    if schema != STATE_SCHEMA:
        raise StateError(f"state schema {schema} incompatible with this tool (expected {STATE_SCHEMA})")
    repos = data.get("repos")
    source = data.get("source")
    runtime = data.get("runtime")
    if not isinstance(repos, dict) or not isinstance(source, dict) or not isinstance(runtime, dict):
        raise StateError("state file is missing structured repos/source/runtime data")
    failure_data = data.get("failure")
    failure = None
    if isinstance(failure_data, dict):
        failure = FailureState(reason=str(failure_data.get("reason", "")))
    initial_compose_data = data.get("initial_compose")
    initial_compose = None
    if isinstance(initial_compose_data, dict):
        compose_status = str(initial_compose_data.get("status", ""))
        if compose_status not in _VALID_COMPOSE_STATUSES:
            raise StateError(f"state file has invalid initial_compose status: {compose_status!r}")
        initial_compose = InitialComposeState(
            status=compose_status,  # type: ignore[arg-type]
            updated_at=str(initial_compose_data.get("updated_at", "")),
            reason=str(initial_compose_data.get("reason", "")),
        )
    status = str(data.get("status", ""))
    if status not in _VALID_STATUSES:
        raise StateError(f"state file has invalid status: {status!r}")
    return EnvironmentState(
        schema=STATE_SCHEMA,
        name=_require_str(data, "name"),
        backend=_require_str(data, "backend"),
        target=_require_str(data, "target"),
        origin=str(data.get("origin", "")),
        status=status,  # type: ignore[arg-type]
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
        root_dir=Path(_require_str(data, "root_dir")),
        writable_dir=Path(_require_str(data, "writable_dir")),
        provisioned_base_id=str(data.get("provisioned_base_id", "")),
        repos=RepoState(
            deltaports_branch=str(repos.get("deltaports_branch", "")),
            freebsd_branch=str(repos.get("freebsd_branch", "")),
            dports_branch=str(repos.get("dports_branch", "")),
        ),
        source=SourceState(delta_root=str(source.get("delta_root", ""))),
        runtime=RuntimeState(
            host_distdir=str(runtime.get("host_distdir", "")),
            oracle_profile=str(runtime.get("oracle_profile", "off")),
        ),
        initial_compose=initial_compose,
        failure=failure,
    )


def read_env_state(env_dir: Path) -> EnvironmentState:
    path = state_path(env_dir)
    if not path.exists():
        raise FileNotFoundError(path)
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise StateError(f"invalid state file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise StateError(f"invalid state file {path}: expected JSON object")
    return state_from_json(data)


def write_env_state(env_dir: Path, state: EnvironmentState) -> None:
    env_dir.mkdir(parents=True, exist_ok=True)
    path = state_path(env_dir)
    tmp_path = path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(state_to_json(state), indent=2, sort_keys=True) + "\n")
    tmp_path.replace(path)


def read_env_info(env_dir: Path) -> EnvInfo:
    try:
        state = read_env_state(env_dir)
    except FileNotFoundError:
        return EnvInfo(env_dir.name, "partial", "", "", "partial", False)
    except StateError:
        # A malformed state file should render as invalid in `list` and not
        # break recovery tools like `cleanup-mounts`.
        return EnvInfo(env_dir.name, "partial", "", "", "invalid", False)
    return EnvInfo(state.name, state.backend, state.target, state.origin, state.status, True)
