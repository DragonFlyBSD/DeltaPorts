from __future__ import annotations

import hashlib
import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import DevEnvConfig
from .errors import ProvisionError, StateError
from .helpers import helper_signature
from .locks import CacheLock
from .log import info
from .names import sanitize_name


WORLD_ASSET_RE = re.compile(r'DragonFly-x86_64-[^"<>\s]*\.world\.tar\.gz')
PROVISION_SCHEMA = 1


@dataclass(frozen=True)
class BaseArchive:
    asset: str
    path: Path
    sha256: str


@dataclass(frozen=True)
class ProvisionedBase:
    id: str
    root: Path
    metadata_path: Path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_latest_world_asset(config: DevEnvConfig) -> str:
    with urllib.request.urlopen(config.avalon_releases_url, timeout=120) as response:
        listing = response.read().decode("utf-8", errors="replace")
    assets = sorted(set(WORLD_ASSET_RE.findall(listing)))
    if not assets:
        raise ProvisionError("could not locate DragonFly x86_64 world asset in Avalon listing")
    return assets[-1]


def ensure_base_archive(config: DevEnvConfig, asset: str) -> BaseArchive:
    config.archives_dir.mkdir(parents=True, exist_ok=True)
    path = config.archives_dir / asset
    # Serialize concurrent creates that resolve the same world asset; without
    # this they race on a shared tmp path and produce a corrupt archive.
    with CacheLock(config.locks_dir, f"archive-{sanitize_name(asset)}", timeout=1800):
        if not path.exists():
            info(f"downloading world archive {asset}")
            url = config.avalon_releases_url.rstrip("/") + "/" + asset
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            urllib.request.urlretrieve(url, tmp_path)
            tmp_path.replace(path)
        else:
            info(f"reusing cached world archive {path}")
        return BaseArchive(asset=asset, path=path, sha256=file_sha256(path))


def provisioned_base_id(config: DevEnvConfig, archive: BaseArchive) -> str:
    data = {
        "schema": PROVISION_SCHEMA,
        "asset": archive.asset,
        "archive_sha256": archive.sha256,
        "required_packages": config.tool_pkgs_required,
        "required_commands": config.tool_cmds_required,
        "python_packages": config.python_pkgs,
        "python_commands": config.python_commands,
        "optional_packages": config.tool_pkgs_optional,
        "helper_signature": helper_signature(),
    }
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:32]


def find_ready_provisioned_base(config: DevEnvConfig, provisioned_base_id: str) -> Path:
    if not provisioned_base_id:
        raise StateError("state lacks provisioned_base_id; cannot remount env root")
    root = config.provisioned_bases_dir / provisioned_base_id / "root"
    ready = config.provisioned_bases_dir / provisioned_base_id / "ready"
    if not root.is_dir() or not ready.exists():
        raise StateError(f"no ready provisioned base found: {provisioned_base_id}")
    return root
