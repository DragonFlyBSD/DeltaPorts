from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .builder import CreateOptions, EnvironmentBuilder, default_delta_root
from .config import load_config, require_root, validate_cache_root
from .errors import DevEnvError, UsageError
from .fs import safe_remove_tree
from .log import error, info, warn
from .mounts import mounts_under, unmount_under
from .session import EnvironmentSession
from .store import EnvironmentStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dportsv3 dev-env")
    subparsers = parser.add_subparsers(dest="action", metavar="ACTION")

    create = subparsers.add_parser("create", help="Create one throwaway DragonFly chroot dev environment")
    create.add_argument("--name")
    create.add_argument("--target", required=True)
    create.add_argument("--origin")
    create.add_argument("--delta-root")
    create.add_argument("--backend", default="chroot")
    create.add_argument("--freebsd-branch")
    create.add_argument("--dports-branch")
    create.add_argument("--shell", action="store_true")
    create.add_argument("--allow-dirty", action="store_true")
    create.add_argument("--no-initial-compose", action="store_true")
    create.add_argument("--oracle-profile", choices=["off", "local", "ci"], default="off")

    shell = subparsers.add_parser("shell", help="Enter one existing environment via chroot")
    shell.add_argument("--refresh", action="store_true")
    shell.add_argument("name")

    destroy = subparsers.add_parser("destroy", help="Unmount and remove one environment")
    destroy.add_argument("name")

    subparsers.add_parser("list", help="List known environments")
    subparsers.add_parser("cleanup-mounts", help="Unmount stale dports-dev mounts under the cache root")
    return parser


def cmd_list(_args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    for env_dir, env_info in store.list_infos():
        mount_status = "mounted" if mounts_under(env_dir / "root") else "unmounted"
        print(f"{env_info.name}\t{env_info.backend}\t{env_info.target}\t{env_info.origin}\t{mount_status}\t{env_info.status}")
    return 0


def cmd_cleanup_mounts(_args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    info(f"cleaning mounts under {config.cache_root}")
    unmount_under(config.cache_root)
    survivors = mounts_under(config.cache_root)
    if survivors:
        error("some dports-dev mounts remain:")
        for mount in survivors:
            print(str(mount.target), file=sys.stderr)
        error("if these paths no longer exist, reboot may be required to clear orphaned mounts")
        return 1
    info(f"no dports-dev mounts remain under {config.cache_root}")
    return 0


def cmd_destroy(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    env_dir = store.env_dir(args.name)
    if not env_dir.is_dir():
        raise UsageError(f"environment not found: {args.name}")

    try:
        state = store.load(args.name)
        info(f"destroying environment {state.name}")
    except DevEnvError:
        warn(f"environment {args.name} has no valid env.json; cleaning partial environment")
    unmount_under(env_dir)
    survivors = mounts_under(env_dir)
    if survivors:
        error(f"refusing to remove {env_dir}; the following mounts are still present:")
        for mount in survivors:
            print(str(mount.target), file=sys.stderr)
        raise UsageError("unmount the listed paths and re-run destroy")
    safe_remove_tree(config, env_dir)
    return 0


def cmd_shell(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    validate_cache_root(config.cache_root)
    store = EnvironmentStore(config)
    EnvironmentSession(config, store).enter(args.name, refresh=args.refresh)
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    require_root()
    config = load_config()
    store = EnvironmentStore(config)
    options = CreateOptions(
        name=args.name,
        target=args.target,
        origin=args.origin,
        delta_root=Path(args.delta_root) if args.delta_root else default_delta_root(),
        backend=args.backend,
        freebsd_branch=args.freebsd_branch,
        dports_branch=args.dports_branch or config.dports_branch,
        enter_shell=args.shell,
        allow_dirty=args.allow_dirty,
        no_initial_compose=args.no_initial_compose,
        oracle_profile=args.oracle_profile,
    )
    result = EnvironmentBuilder(config, store, options).create()
    if args.shell:
        EnvironmentSession(config, store).enter(result.env_name)
    return result.exit_code


def dispatch(args: argparse.Namespace) -> int:
    if args.action is None:
        build_parser().print_help()
        return 0
    commands = {
        "create": cmd_create,
        "shell": cmd_shell,
        "destroy": cmd_destroy,
        "list": cmd_list,
        "cleanup-mounts": cmd_cleanup_mounts,
    }
    return commands[args.action](args)


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    try:
        args = parser.parse_args(sys.argv[1:] if argv is None else argv)
        raise SystemExit(dispatch(args))
    except DevEnvError as exc:
        error(str(exc))
        raise SystemExit(1) from None
