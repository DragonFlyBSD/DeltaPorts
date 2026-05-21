from __future__ import annotations

import sys
from pathlib import Path

import pytest


_DEV_ENV_PKG = Path(__file__).resolve().parents[2] / "tools" / "dev-env"
if _DEV_ENV_PKG.is_dir() and str(_DEV_ENV_PKG) not in sys.path:
    sys.path.insert(0, str(_DEV_ENV_PKG))


def test_default_runtime_profile_loads():
    from dports_dev_env.runtime_profiles import load_runtime_profile

    profile = load_runtime_profile()
    assert profile.name == "dportsv3-py311"
    assert profile.python == "3.11"
    assert "py311-fastapi" in profile.packages


def test_unknown_runtime_profile_fails(monkeypatch):
    from dports_dev_env.errors import ConfigError
    from dports_dev_env.runtime_profiles import load_runtime_profile

    monkeypatch.setenv("DPORTS_DEV_RUNTIME_PROFILE", "missing")
    with pytest.raises(ConfigError, match="unknown runtime profile"):
        load_runtime_profile()


def test_runtime_profile_participates_in_base_id(monkeypatch, tmp_path):
    from dataclasses import replace

    from dports_dev_env.base import BaseArchive, provisioned_base_id
    from dports_dev_env.config import load_config
    from dports_dev_env.runtime_profiles import RuntimeProfile

    monkeypatch.setenv("DPORTS_DEV_CACHE_ROOT", str(tmp_path / "cache"))
    config = load_config()
    archive = BaseArchive("world.tar.gz", tmp_path / "world.tar.gz", "abc123")

    changed = replace(
        config,
        runtime_profile=RuntimeProfile(
            schema=1,
            name="dportsv3-py312",
            python="3.12",
            packages=["py312-fastapi"],
        ),
    )

    assert provisioned_base_id(config, archive) != provisioned_base_id(changed, archive)
