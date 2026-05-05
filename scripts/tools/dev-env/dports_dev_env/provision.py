from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .base import BaseArchive, ProvisionedBase, provisioned_base_id
from .chroot import command_exists, run_in_chroot
from .config import DevEnvConfig
from .errors import ProvisionError
from .helpers import write_helper_scripts
from .locks import CacheLock
from .log import info, step_timer, warn
from .mounts import unmount_under
from .runtime import prepare_root_runtime


class BaseProvisioner:
    def __init__(self, config: DevEnvConfig) -> None:
        self.config = config

    def prepare(self, archive: BaseArchive) -> ProvisionedBase:
        base_id = provisioned_base_id(self.config, archive)
        base_dir = self.config.provisioned_bases_dir / base_id
        root = base_dir / "root"
        metadata_path = base_dir / "metadata.json"
        with CacheLock(self.config.locks_dir, f"provision-{base_id}", timeout=3600):
            if self.is_ready(root, base_dir / "ready"):
                info(f"reusing provisioned base root {root}")
                return ProvisionedBase(base_id, root, metadata_path)
            if base_dir.exists():
                warn(f"discarding stale provisioned base {base_dir}")
                shutil.rmtree(base_dir)
            tmp_dir = self.config.provisioned_bases_dir / f"{base_id}.tmp"
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            tmp_root = tmp_dir / "root"
            tmp_root.mkdir(parents=True)
            try:
                with step_timer("extract DragonFly world archive"):
                    self.extract_archive(archive.path, tmp_root)
                with step_timer("bootstrap provisioned base"):
                    self.bootstrap(tmp_root)
                self.write_metadata(tmp_dir, archive, base_id)
                (tmp_dir / "ready").write_text("")
                tmp_dir.replace(base_dir)
            except Exception:
                unmount_under(tmp_root)
                if tmp_dir.exists():
                    shutil.rmtree(tmp_dir)
                raise
            return ProvisionedBase(base_id, root, metadata_path)

    def is_ready(self, root: Path, ready: Path) -> bool:
        return ready.exists() and root.is_dir() and self.validate_tools(root)

    def extract_archive(self, archive_path: Path, root: Path) -> None:
        result = subprocess.run(["tar", "-xpf", str(archive_path), "-C", str(root)], text=True)
        if result.returncode != 0:
            raise ProvisionError(f"failed to extract world archive: {archive_path}")

    def bootstrap(self, root: Path) -> None:
        try:
            prepare_root_runtime(self.config, root)
            self.bootstrap_pkg(root)
            self.install_required_packages(root)
            self.ensure_python(root)
            self.install_optional_packages(root)
            write_helper_scripts(root)
            self.prepare_mountpoints(root)
            self.clean_package_caches(root)
        finally:
            unmount_under(root)
        if not self.validate_tools(root):
            raise ProvisionError("provisioned base is missing required developer tools")

    def bootstrap_pkg(self, root: Path) -> None:
        if not command_exists(root, "pkg"):
            info("pkg is not present; bootstrapping it from /usr")
            result = run_in_chroot(root, "cd /usr && make pkg-bootstrap >/dev/null")
            if result.returncode != 0:
                raise ProvisionError("failed to bootstrap pkg inside the chroot")
        run_in_chroot(root, "ASSUME_ALWAYS_YES=yes pkg bootstrap -yf >/dev/null 2>&1 || true")
        run_in_chroot(root, "ASSUME_ALWAYS_YES=yes pkg update -f >/dev/null 2>&1 || true")

    def install_required_packages(self, root: Path) -> None:
        for package in self.config.tool_pkgs_required:
            info(f"installing required package {package}")
            result = run_in_chroot(root, 'ASSUME_ALWAYS_YES=yes pkg install -y "$1" >/dev/null', package)
            if result.returncode != 0:
                raise ProvisionError(f"failed to install required package in chroot: {package}")

    def ensure_python(self, root: Path) -> None:
        if self.find_python(root):
            self.ensure_python3_shim(root)
            return
        for package in self.config.python_pkgs:
            info(f"installing python candidate {package}")
            run_in_chroot(root, 'ASSUME_ALWAYS_YES=yes pkg install -y "$1" >/dev/null', package)
            if self.find_python(root):
                self.ensure_python3_shim(root)
                return
        raise ProvisionError("failed to install a python3 runtime inside the chroot")

    def find_python(self, root: Path) -> str | None:
        for command in self.config.python_commands:
            if command_exists(root, command):
                return command
        return None

    def ensure_python3_shim(self, root: Path) -> None:
        if command_exists(root, "python3"):
            return
        python = self.find_python(root)
        if not python:
            raise ProvisionError("failed to find python command for python3 shim")
        result = run_in_chroot(root, 'cmd=$(command -v "$1") && ln -sf "$cmd" /usr/local/bin/python3', python)
        if result.returncode != 0:
            raise ProvisionError("failed to create a python3 shim inside the chroot")

    def install_optional_packages(self, root: Path) -> None:
        for package in self.config.tool_pkgs_optional:
            info(f"installing optional package {package}")
            result = run_in_chroot(root, 'ASSUME_ALWAYS_YES=yes pkg install -y "$1" >/dev/null', package)
            if result.returncode != 0:
                warn(f"optional package unavailable or failed: {package}")

    def validate_tools(self, root: Path) -> bool:
        return all(command_exists(root, command) for command in self.config.tool_cmds_required)

    def prepare_mountpoints(self, root: Path) -> None:
        for path, mode in [
            (root / "work", 0o755),
            (root / "root", 0o700),
            (root / "tmp", 0o1777),
            (root / "var/tmp", 0o1777),
            (root / "etc/dsynth", 0o755),
            (root / "construction", 0o755),
            (root / "usr/distfiles", 0o755),
        ]:
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)

    def clean_package_caches(self, root: Path) -> None:
        info("cleaning package caches from provisioned base")
        run_in_chroot(root, "ASSUME_ALWAYS_YES=yes pkg clean -ay >/dev/null 2>&1 || true")
        for path in [root / "root/.cache", root / "var/cache/pkg"]:
            if path.exists():
                shutil.rmtree(path)
        for path in [root / "tmp", root / "var/tmp"]:
            if path.is_dir():
                for child in path.iterdir():
                    if child.is_dir():
                        shutil.rmtree(child)
                    else:
                        child.unlink()

    def write_metadata(self, base_dir: Path, archive: BaseArchive, base_id: str) -> None:
        metadata = {
            "schema": 1,
            "id": base_id,
            "archive": {"asset": archive.asset, "path": str(archive.path), "sha256": archive.sha256},
            "required_packages": self.config.tool_pkgs_required,
            "required_commands": self.config.tool_cmds_required,
            "python_packages": self.config.python_pkgs,
            "python_commands": self.config.python_commands,
            "optional_packages": self.config.tool_pkgs_optional,
        }
        (base_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
