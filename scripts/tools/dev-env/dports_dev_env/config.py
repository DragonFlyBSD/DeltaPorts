from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigError


@dataclass(frozen=True)
class DevEnvConfig:
    cache_root: Path
    bases_dir: Path
    archives_dir: Path
    provisioned_bases_dir: Path
    envs_dir: Path
    repos_dir: Path
    venvs_dir: Path
    generator_venvs_dir: Path
    locks_dir: Path
    avalon_releases_url: str
    freebsd_ports_url: str
    dports_url: str
    deltaports_branch: str
    dports_branch: str
    host_distdir: Path
    bootstrap_pkgs: list[str]
    tool_pkgs_required: list[str]
    tool_cmds_required: list[str]
    python_pkgs: list[str]
    tool_pkgs_optional: list[str]
    python_commands: list[str]
    dsynth_builders: int
    dsynth_jobs: int


def load_config() -> DevEnvConfig:
    cache_root = Path(os.environ.get("DPORTS_DEV_CACHE_ROOT", "/root/.cache/dports-dev"))
    bases_dir = cache_root / "bases"
    venvs_dir = cache_root / "venvs"
    return DevEnvConfig(
        cache_root=cache_root,
        bases_dir=bases_dir,
        archives_dir=Path(os.environ.get("DPORTS_DEV_ARCHIVES_DIR", str(bases_dir / "archives"))),
        provisioned_bases_dir=Path(os.environ.get("DPORTS_DEV_PROVISIONED_BASES_DIR", str(bases_dir / "provisioned"))),
        envs_dir=Path(os.environ.get("DPORTS_DEV_ENVS_DIR", str(cache_root / "envs"))),
        repos_dir=Path(os.environ.get("DPORTS_DEV_REPOS_DIR", str(cache_root / "repos"))),
        venvs_dir=venvs_dir,
        generator_venvs_dir=Path(os.environ.get("DPORTS_DEV_GENERATOR_VENVS_DIR", str(venvs_dir / "generator"))),
        locks_dir=Path(os.environ.get("DPORTS_DEV_LOCKS_DIR", str(cache_root / "locks"))),
        avalon_releases_url=os.environ.get("DPORTS_DEV_AVALON_RELEASES_URL", "https://avalon.dragonflybsd.org/snapshots/x86_64/assets/releases/"),
        freebsd_ports_url=os.environ.get("DPORTS_DEV_FREEBSD_PORTS_URL", "https://git.FreeBSD.org/ports.git"),
        dports_url=os.environ.get("DPORTS_DEV_DPORTS_URL", "https://github.com/DragonFlyBSD/DPorts.git"),
        deltaports_branch=os.environ.get("DPORTS_DEV_DELTAPORTS_BRANCH", "master"),
        dports_branch=os.environ.get("DPORTS_DEV_DPORTS_BRANCH", "staged"),
        host_distdir=Path(os.environ.get("DPORTS_DEV_HOST_DISTDIR", "/usr/distfiles")),
        bootstrap_pkgs=split_words(os.environ.get("DPORTS_DEV_BOOTSTRAP_PKGS", "indexinfo")),
        tool_pkgs_required=without_words(
            split_words(os.environ.get("DPORTS_DEV_TOOL_PKGS_REQUIRED", "bash curl git patch jq")),
            split_words(os.environ.get("DPORTS_DEV_BOOTSTRAP_PKGS", "indexinfo")),
        ),
        tool_cmds_required=split_words(os.environ.get("DPORTS_DEV_TOOL_CMDS_REQUIRED", "pkg indexinfo bash curl git patch jq python3")),
        python_pkgs=split_words(os.environ.get("DPORTS_DEV_PYTHON_PKGS", "python3 python313 python312 python311")),
        tool_pkgs_optional=split_words(os.environ.get("DPORTS_DEV_TOOL_PKGS_OPTIONAL", "dsynth python311 python312 python313 py311-pip py312-pip py313-pip genpatch")),
        python_commands=split_words(os.environ.get("DPORTS_DEV_PYTHON_COMMANDS", "python3 python3.13 python3.12 python3.11")),
        dsynth_builders=parse_positive_int("DPORTS_DEV_DSYNTH_BUILDERS", os.environ.get("DPORTS_DEV_DSYNTH_BUILDERS", "2")),
        dsynth_jobs=parse_positive_int("DPORTS_DEV_DSYNTH_JOBS", os.environ.get("DPORTS_DEV_DSYNTH_JOBS", "2")),
    )


def split_words(value: str) -> list[str]:
    return value.split()


def without_words(values: list[str], excluded: list[str]) -> list[str]:
    excluded_set = set(excluded)
    return [value for value in values if value not in excluded_set]


def parse_positive_int(name: str, value: str) -> int:
    try:
        parsed = int(value)
    except ValueError:
        raise ConfigError(f"{name} must be a positive integer (got: {value})") from None
    if parsed <= 0:
        raise ConfigError(f"{name} must be > 0")
    return parsed


Config = DevEnvConfig


def validate_cache_root(cache_root: Path) -> None:
    if not cache_root.is_absolute():
        raise ConfigError(f"DPORTS_DEV_CACHE_ROOT must be absolute: {cache_root}")
    if str(cache_root) in {"/", "/root", "/home", "/usr", "/var", "/tmp"}:
        raise ConfigError(f"DPORTS_DEV_CACHE_ROOT is too broad for safe cleanup: {cache_root}")


def ensure_cache_dirs(config: DevEnvConfig) -> None:
    validate_cache_root(config.cache_root)
    for path in [
        config.cache_root,
        config.bases_dir,
        config.archives_dir,
        config.provisioned_bases_dir,
        config.envs_dir,
        config.repos_dir,
        config.venvs_dir,
        config.generator_venvs_dir,
        config.locks_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)


def require_root() -> None:
    if os.geteuid() != 0:
        raise ConfigError("dports dev-env must run as root")
