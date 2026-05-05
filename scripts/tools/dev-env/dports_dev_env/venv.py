from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from .chroot import run_in_chroot
from .config import DevEnvConfig
from .errors import ProvisionError
from .fs import copy_tree
from .locks import CacheLock
from .log import info, step_timer, warn


GENERATOR_VENV_SCHEMA = 1


class GeneratorVenvCache:
    def __init__(self, config: DevEnvConfig) -> None:
        self.config = config

    def prepare(self, root_dir: Path, provisioned_base_id: str) -> None:
        pyproject = root_dir / "work/DeltaPorts/scripts/generator/pyproject.toml"
        if not pyproject.is_file():
            raise ProvisionError(f"missing generator project in env: {pyproject}")
        python_version = self.chroot_output(root_dir, 'python3 -c "import sys; print(\"%d.%d.%d\" % sys.version_info[:3])"')
        pyproject_hash = hashlib.sha256(pyproject.read_bytes()).hexdigest()
        venv_id = self.venv_id(provisioned_base_id, python_version, pyproject_hash)
        cache_root = self.config.generator_venvs_dir / venv_id
        cache_venv = cache_root / "venv"
        venv_dest = root_dir / "work/DeltaPorts/scripts/generator/.venv"

        with CacheLock(self.config.locks_dir, f"venv-generator-{venv_id}", timeout=1800):
            if (cache_root / "ready").exists():
                info(f"restoring cached generator venv {venv_id}")
                if venv_dest.exists():
                    shutil.rmtree(venv_dest)
                copy_tree(cache_venv, venv_dest)
                if self.validate(root_dir):
                    return
                warn("cached generator venv failed validation; rebuilding it")
                shutil.rmtree(venv_dest)
                shutil.rmtree(cache_root)

            with step_timer("bootstrap generator venv"):
                result = run_in_chroot(root_dir, "/work/DeltaPorts/dportsv3 --help >/dev/null")
                if result.returncode != 0:
                    raise ProvisionError("failed to bootstrap dportsv3 generator venv inside chroot")
            tmp_cache = cache_root.with_suffix(".tmp")
            if tmp_cache.exists():
                shutil.rmtree(tmp_cache)
            tmp_cache.mkdir(parents=True)
            copy_tree(venv_dest, tmp_cache / "venv")
            metadata = {
                "schema": GENERATOR_VENV_SCHEMA,
                "provisioned_base_id": provisioned_base_id,
                "python": python_version,
                "pyproject_sha256": pyproject_hash,
            }
            (tmp_cache / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
            (tmp_cache / "ready").write_text("")
            if cache_root.exists():
                shutil.rmtree(cache_root)
            tmp_cache.replace(cache_root)

    def validate(self, root_dir: Path) -> bool:
        return run_in_chroot(root_dir, "/work/DeltaPorts/dportsv3 --help >/dev/null").returncode == 0

    def venv_id(self, provisioned_base_id: str, python_version: str, pyproject_hash: str) -> str:
        data = {
            "schema": GENERATOR_VENV_SCHEMA,
            "provisioned_base_id": provisioned_base_id,
            "python": python_version,
            "pyproject_sha256": pyproject_hash,
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:32]

    def chroot_output(self, root_dir: Path, script: str) -> str:
        result = run_in_chroot(root_dir, script, capture_output=True)
        if result.returncode != 0:
            raise ProvisionError(f"chroot command failed: {script}")
        return result.stdout.strip()
