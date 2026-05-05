from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EnvInfo:
    name: str
    backend: str
    target: str
    origin: str
    status: str
    has_state: bool


def parse_state_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        try:
            parsed = shlex.split(raw_value, posix=True)
        except ValueError:
            parsed = []
        values[key] = parsed[0] if parsed else ""
    return values


def read_env_info(env_dir: Path) -> EnvInfo:
    state_file = env_dir / "state.env"
    if not state_file.exists():
        return EnvInfo(env_dir.name, "partial", "", "", "partial", False)
    values = parse_state_env(state_file)
    return EnvInfo(
        name=env_dir.name,
        backend=values.get("BACKEND", ""),
        target=values.get("TARGET", ""),
        origin=values.get("ORIGIN", ""),
        status=values.get("ENV_STATUS", "ready"),
        has_state=True,
    )
