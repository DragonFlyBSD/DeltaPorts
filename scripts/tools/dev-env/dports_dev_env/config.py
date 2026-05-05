from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    cache_root: Path
    envs_dir: Path


def load_config() -> Config:
    cache_root = Path(os.environ.get("DPORTS_DEV_CACHE_ROOT", "/root/.cache/dports-dev"))
    return Config(cache_root=cache_root, envs_dir=cache_root / "envs")


def validate_cache_root(cache_root: Path) -> None:
    if not cache_root.is_absolute():
        raise SystemExit(f"ERROR: DPORTS_DEV_CACHE_ROOT must be absolute: {cache_root}")
    if str(cache_root) in {"/", "/root", "/home", "/usr", "/var", "/tmp"}:
        raise SystemExit(f"ERROR: DPORTS_DEV_CACHE_ROOT is too broad for safe cleanup: {cache_root}")


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("ERROR: dports dev-env must run as root")
