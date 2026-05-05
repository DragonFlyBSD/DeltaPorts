from __future__ import annotations

import os
import sys
from pathlib import Path

from .config import load_config, require_root, validate_cache_root
from .mounts import mounts_under, unmount_under
from .state import read_env_info


def legacy_script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "dports-dev-env"


def exec_legacy(argv: list[str]) -> None:
    legacy = legacy_script_path()
    if not legacy.exists():
        print(f"dports-dev-env: legacy script not found: {legacy}", file=sys.stderr)
        raise SystemExit(1)
    os.execv(str(legacy), [str(legacy), *argv])


def print_usage() -> None:
    print(
        """usage: dportsv3 dev-env ACTION [options]

Actions:
  create   Create one throwaway DragonFly chroot dev environment (legacy shell)
  shell    Enter one existing environment via chroot (legacy shell)
  destroy  Unmount and remove one environment (legacy shell)
  list     List known environments
  cleanup-mounts
           Unmount stale dports-dev mounts under the cache root
""".rstrip()
    )


def cmd_list() -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    if not config.envs_dir.is_dir():
        return 0
    for env_dir in sorted(path for path in config.envs_dir.iterdir() if path.is_dir()):
        info = read_env_info(env_dir)
        mount_status = "mounted" if mounts_under(env_dir / "root") else "unmounted"
        print(f"{info.name}\t{info.backend}\t{info.target}\t{info.origin}\t{mount_status}\t{info.status}")
    return 0


def cmd_cleanup_mounts() -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    print(f"INFO: cleaning mounts under {config.cache_root}")
    unmount_under(config.cache_root)
    survivors = mounts_under(config.cache_root)
    if survivors:
        print("ERROR: some dports-dev mounts remain:", file=sys.stderr)
        for mount in survivors:
            print(str(mount.target), file=sys.stderr)
        print("ERROR: if these paths no longer exist, reboot may be required to clear orphaned mounts", file=sys.stderr)
        return 1
    print(f"INFO: no dports-dev mounts remain under {config.cache_root}")
    return 0


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    action = args[0] if args else ""
    if action in {"", "help", "--help", "-h"}:
        print_usage()
        raise SystemExit(0)
    if action == "list":
        raise SystemExit(cmd_list())
    if action == "cleanup-mounts":
        raise SystemExit(cmd_cleanup_mounts())
    # Keep lifecycle commands delegated until their Python replacements are
    # complete and tested against the DragonFly mount/provisioning workflow.
    exec_legacy(args)
