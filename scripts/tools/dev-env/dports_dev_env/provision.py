from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .base import BaseArchive, ProvisionedBase, provisioned_base_id
from .chroot import ChrootRunner, command_exists
from .config import DevEnvConfig
from .errors import ProvisionError
from .fs import safe_remove_tree
from .helpers import write_helper_scripts
from .locks import CacheLock
from .log import info, step_timer, subphase, warn
from .mounts import mounts_under, unmount_targets, unmount_under
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
                # Stale base_dir may have leftover mounts from a prior crash;
                # unmount and refuse to rmtree if any survivor remains.
                safe_remove_tree(self.config, base_dir)
            tmp_dir = self.config.provisioned_bases_dir / f"{base_id}.tmp"
            if tmp_dir.exists():
                safe_remove_tree(self.config, tmp_dir)
            tmp_root = tmp_dir / "root"
            tmp_root.mkdir(parents=True)
            try:
                subphase("extracting world archive")
                with step_timer("extract DragonFly world archive"):
                    self.extract_archive(archive.path, tmp_root)
                with step_timer("bootstrap provisioned base"):
                    self.bootstrap(tmp_root)
                self.write_metadata(tmp_dir, archive, base_id)
                (tmp_dir / "ready").write_text("")
                tmp_dir.replace(base_dir)
            except (Exception, KeyboardInterrupt):
                # Best-effort unmount; if anything is still mounted under
                # tmp_root, refuse to rmtree -- otherwise rmtree would walk
                # into the live mount and damage host files. KeyboardInterrupt
                # is BaseException, not Exception, so list it explicitly.
                unmount_under(tmp_root)
                survivors = mounts_under(tmp_root)
                if survivors:
                    warn(
                        f"refusing to remove {tmp_dir}; mounts remain under it: "
                        + ", ".join(str(m.target) for m in survivors)
                    )
                elif tmp_dir.exists():
                    # Use safe_remove_tree so chflags -R noschg,nouchg
                    # clears DragonFly's immutable flags before rmtree.
                    # Raw shutil.rmtree fails with EPERM on /sbin/init
                    # and friends.
                    safe_remove_tree(self.config, tmp_dir)
                raise
            return ProvisionedBase(base_id, root, metadata_path)

    def is_ready(self, root: Path, ready: Path) -> bool:
        return ready.exists() and root.is_dir() and self.validate_tools(root)

    def extract_archive(self, archive_path: Path, root: Path) -> None:
        result = subprocess.run(["tar", "-xpf", str(archive_path), "-C", str(root)], text=True)
        if result.returncode != 0:
            raise ProvisionError(f"failed to extract world archive: {archive_path}")

    def bootstrap(self, root: Path) -> None:
        mounted_targets: list[Path] = []
        try:
            mounted_targets = prepare_root_runtime(self.config, root)
            self.bootstrap_pkg(root)
            self.install_bootstrap_packages(root)
            self.install_required_packages(root)
            self.ensure_python(root)
            self.install_runtime_profile_packages(root)
            self.install_optional_packages(root)
            write_helper_scripts(root)
            self.prepare_mountpoints(root)
            # In-chroot pkg clean needs the bind mounts live (it shells
            # out to `pkg`, which expects /dev, /proc, etc.).
            self.run_pkg_clean(root)
        finally:
            unmount_targets(mounted_targets)
            unmount_under(root)
        # Refuse to publish a provisioned base while any mount is still live
        # under it. Validating + writing `ready` past surviving mounts would
        # leave the cached base referencing host filesystems.
        survivors = mounts_under(root)
        if survivors:
            raise ProvisionError(
                "provisioned base still has live mounts after bootstrap: "
                + ", ".join(str(mount.target) for mount in survivors)
            )
        if not self.validate_tools(root):
            raise ProvisionError("provisioned base is missing required developer tools")
        # Wholesale cache wipe happens AFTER unmount. Doing this while
        # bind mounts (especially the host's repos cache at
        # ``root/.cache/dports-dev/repos``) are still live would walk
        # rmtree into host filesystems and — were the mount not RO —
        # destroy the operator's local git clones. The survivor check
        # above guarantees no mounts remain under ``root`` at this point.
        self.wipe_cache_dirs(root)

    def bootstrap_pkg(self, root: Path) -> None:
        runner = ChrootRunner(root)
        if not command_exists(root, "pkg"):
            subphase("bootstrapping pkg from /usr")
            info("pkg is not present; bootstrapping it from /usr")
            result = runner.run_shell("cd /usr && make pkg-bootstrap >/dev/null")
            if result.returncode != 0:
                raise ProvisionError("failed to bootstrap pkg inside the chroot")
        subphase("refreshing pkg repository")
        runner.run(["pkg", "bootstrap", "-yf"], env={"ASSUME_ALWAYS_YES": "yes"})
        runner.run(["pkg", "update", "-f"], env={"ASSUME_ALWAYS_YES": "yes"})

    def install_bootstrap_packages(self, root: Path) -> None:
        if not self.config.bootstrap_pkgs:
            return
        subphase(f"installing bootstrap packages ({len(self.config.bootstrap_pkgs)})")
        info(f"installing bootstrap packages {' '.join(self.config.bootstrap_pkgs)}")
        result = ChrootRunner(root).run(["pkg", "install", "-y", *self.config.bootstrap_pkgs], env={"ASSUME_ALWAYS_YES": "yes"})
        if result.returncode != 0:
            raise ProvisionError("failed to install bootstrap packages in chroot")

    def install_required_packages(self, root: Path) -> None:
        if not self.config.tool_pkgs_required:
            return
        subphase(f"installing required packages ({len(self.config.tool_pkgs_required)})")
        info(f"installing required packages {' '.join(self.config.tool_pkgs_required)}")
        result = ChrootRunner(root).run(["pkg", "install", "-y", *self.config.tool_pkgs_required], env={"ASSUME_ALWAYS_YES": "yes"})
        if result.returncode != 0:
            raise ProvisionError("failed to install required packages in chroot")

    def ensure_python(self, root: Path) -> None:
        if self.find_python(root):
            self.ensure_python3_shim(root)
            return
        for package in self.config.python_pkgs:
            subphase(f"installing python candidate {package}")
            info(f"installing python candidate {package}")
            result = ChrootRunner(root).run(["pkg", "install", "-y", package], env={"ASSUME_ALWAYS_YES": "yes"})
            if result.returncode != 0:
                warn(f"python candidate unavailable or failed: {package}")
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
        result = ChrootRunner(root).run_shell('cmd=$(command -v "$1") && ln -sf "$cmd" /usr/local/bin/python3', python)
        if result.returncode != 0:
            raise ProvisionError("failed to create a python3 shim inside the chroot")

    def install_optional_packages(self, root: Path) -> None:
        if not self.config.tool_pkgs_optional:
            return
        subphase(f"installing optional packages ({len(self.config.tool_pkgs_optional)})")
        for package in self.config.tool_pkgs_optional:
            info(f"installing optional package {package}")
            result = ChrootRunner(root).run(["pkg", "install", "-y", package], env={"ASSUME_ALWAYS_YES": "yes"})
            if result.returncode != 0:
                warn(f"optional package unavailable or failed: {package}")

    def install_runtime_profile_packages(self, root: Path) -> None:
        packages = self.config.runtime_profile.packages
        if not packages:
            return
        subphase(
            f"installing runtime profile {self.config.runtime_profile.name} "
            f"packages ({len(packages)})"
        )
        info(
            f"installing runtime profile {self.config.runtime_profile.name} packages "
            + " ".join(packages)
        )
        result = ChrootRunner(root).run(
            ["pkg", "install", "-y", *packages],
            env={"ASSUME_ALWAYS_YES": "yes"},
        )
        if result.returncode != 0:
            raise ProvisionError(
                f"failed to install runtime profile {self.config.runtime_profile.name} packages"
            )

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
        ]:
            path.mkdir(parents=True, exist_ok=True)
            path.chmod(mode)
        # /usr/distfiles is a bind-mount target during prepare_root_runtime;
        # ensure it exists in the provisioned base so future env shells can
        # mount onto it, but never chmod -- the bind-mount would forward the
        # mode change to the host's distdir.
        (root / "usr/distfiles").mkdir(parents=True, exist_ok=True)

    def run_pkg_clean(self, root: Path) -> None:
        """Run ``pkg clean -ay`` inside the chroot. Needs live mounts."""
        info("cleaning package caches from provisioned base")
        ChrootRunner(root).run(
            ["pkg", "clean", "-ay"],
            env={"ASSUME_ALWAYS_YES": "yes"},
        )

    def wipe_cache_dirs(self, root: Path) -> None:
        """Wipe contents of cache directories on the unmounted base.

        Preserves the directories themselves so pkg/etc. don't have to
        recreate them on first env use. Must run with **no live mounts**
        under ``root`` — otherwise rmtree would descend into bind-mounted
        host filesystems (notably the host repos cache at
        ``root/.cache/dports-dev/repos``). Callers must guarantee that
        invariant; this function does not re-check.
        """
        for path in [root / "root/.cache", root / "var/cache/pkg",
                     root / "tmp", root / "var/tmp"]:
            if not path.is_dir():
                continue
            for child in path.iterdir():
                if child.is_symlink() or child.is_file():
                    child.unlink()
                elif child.is_dir():
                    shutil.rmtree(child)

    def write_metadata(self, base_dir: Path, archive: BaseArchive, base_id: str) -> None:
        metadata = {
            "schema": 1,
            "id": base_id,
            "archive": {"asset": archive.asset, "path": str(archive.path), "sha256": archive.sha256},
            "bootstrap_packages": self.config.bootstrap_pkgs,
            "required_packages": self.config.tool_pkgs_required,
            "required_commands": self.config.tool_cmds_required,
            "python_packages": self.config.python_pkgs,
            "python_commands": self.config.python_commands,
            "runtime_profile": {
                "schema": self.config.runtime_profile.schema,
                "name": self.config.runtime_profile.name,
                "python": self.config.runtime_profile.python,
                "packages": self.config.runtime_profile.packages,
            },
            "optional_packages": self.config.tool_pkgs_optional,
        }
        (base_dir / "metadata.json").write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
