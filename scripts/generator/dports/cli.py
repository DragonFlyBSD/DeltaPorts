"""
Command-line interface for DPorts v2.

Provides argument parsing and command dispatch for all dports operations.
Uses argparse with subcommands for each operation.

Example usage:
    dports merge --target 2025Q1 category/port
    dports sync --target 2025Q1
    dports check category/port
    dports state show
    dports list --customized
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace

from dports.config import Config
from dports.utils import setup_logging, DPortsError


def create_parser() -> argparse.ArgumentParser:
    """Create the main argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="dports",
        description="DPorts v2 - DragonFly BSD Ports overlay management",
    )
    parser.add_argument(
        "-c", "--config",
        type=Path,
        help="Path to dports.toml config file",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="count",
        default=0,
        help="Increase verbosity (can be repeated)",
    )
    parser.add_argument(
        "-q", "--quiet",
        action="store_true",
        help="Suppress non-error output",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 2.0.0-dev",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    
    # Register all command parsers
    _register_merge_parser(subparsers)
    _register_sync_parser(subparsers)
    _register_prune_parser(subparsers)
    _register_makefiles_parser(subparsers)
    _register_check_parser(subparsers)
    _register_migrate_parser(subparsers)
    _register_state_parser(subparsers)
    _register_list_parser(subparsers)
    _register_status_parser(subparsers)
    _register_verify_parser(subparsers)
    _register_add_parser(subparsers)
    _register_save_parser(subparsers)
    _register_diff_parser(subparsers)
    _register_special_parser(subparsers)
    _register_logs_parser(subparsers)
    _register_update_parser(subparsers)

    return parser


def _register_merge_parser(subparsers) -> None:
    """Register the merge command parser."""
    p = subparsers.add_parser(
        "merge",
        help="Merge FreeBSD port with DragonFly overlay",
    )
    p.add_argument(
        "--target", "-t",
        required=True,
        help="Target quarterly branch (e.g., 2025Q1)",
    )
    p.add_argument(
        "port",
        nargs="?",
        help="Port origin (category/name) or 'all'",
    )
    p.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force merge even if validation fails",
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be done without making changes",
    )


def _register_sync_parser(subparsers) -> None:
    """Register the sync command parser."""
    p = subparsers.add_parser(
        "sync",
        help="Sync FreeBSD ports tree for a quarterly",
    )
    p.add_argument(
        "--target", "-t",
        required=True,
        help="Target quarterly branch (e.g., 2025Q1)",
    )


def _register_prune_parser(subparsers) -> None:
    """Register the prune command parser."""
    p = subparsers.add_parser(
        "prune",
        help="Remove ports that no longer exist in FreeBSD",
    )
    p.add_argument(
        "--target", "-t",
        required=True,
        help="Target quarterly branch (e.g., 2025Q1)",
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be pruned without making changes",
    )


def _register_makefiles_parser(subparsers) -> None:
    """Register the makefiles command parser."""
    p = subparsers.add_parser(
        "makefiles",
        help="Regenerate category Makefiles",
    )
    p.add_argument(
        "--target", "-t",
        required=True,
        help="Target quarterly branch (e.g., 2025Q1)",
    )


def _register_check_parser(subparsers) -> None:
    """Register the check command parser (new in v2)."""
    p = subparsers.add_parser(
        "check",
        help="Validate overlay configuration and patches",
    )
    p.add_argument(
        "port",
        nargs="?",
        help="Port origin to check, or 'all' for all ports",
    )
    p.add_argument(
        "--target", "-t",
        help="Target quarterly for validation context",
    )


def _register_migrate_parser(subparsers) -> None:
    """Register the migrate command parser (new in v2)."""
    p = subparsers.add_parser(
        "migrate",
        help="Migrate v1 port configuration to v2 overlay.toml",
    )
    p.add_argument(
        "port",
        nargs="?",
        help="Port origin to migrate, or 'all' for all ports",
    )
    p.add_argument(
        "--output", "-o",
        type=Path,
        help="Output directory for migrated ports (default: migrated_ports/)",
    )
    p.add_argument(
        "--state-output", "-s",
        type=Path,
        help="Output path for builds.json (default: state/builds.json)",
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be migrated without making changes",
    )


def _register_state_parser(subparsers) -> None:
    """Register the state command parser (new in v2)."""
    p = subparsers.add_parser(
        "state",
        help="Manage build state database",
    )
    state_sub = p.add_subparsers(dest="state_cmd", metavar="ACTION")
    
    state_sub.add_parser("show", help="Show current build state")
    state_sub.add_parser("clear", help="Clear build state")
    
    imp = state_sub.add_parser("import", help="Import from STATUS files")
    imp.add_argument("--target", "-t", required=True, help="Target quarterly")
    
    exp = state_sub.add_parser("export", help="Export to STATUS files")
    exp.add_argument("--target", "-t", required=True, help="Target quarterly")


def _register_list_parser(subparsers) -> None:
    """Register the list command parser (new in v2)."""
    p = subparsers.add_parser(
        "list",
        help="List ports with various filters",
    )
    p.add_argument(
        "--customized",
        action="store_true",
        help="Only show ports with customizations",
    )
    p.add_argument(
        "--quarterly", "-t",
        help="Filter by quarterly support",
    )
    p.add_argument(
        "--format",
        choices=["simple", "json", "table"],
        default="simple",
        help="Output format",
    )


def _register_status_parser(subparsers) -> None:
    """Register the status command parser (ported from v1)."""
    p = subparsers.add_parser(
        "status",
        help="Show port build status",
    )
    p.add_argument(
        "port",
        nargs="?",
        help="Port origin to show status for",
    )
    p.add_argument(
        "--target", "-t",
        help="Target quarterly",
    )


def _register_verify_parser(subparsers) -> None:
    """Register the verify command parser (ported from v1)."""
    p = subparsers.add_parser(
        "verify",
        help="Verify port patches apply cleanly",
    )
    p.add_argument(
        "port",
        nargs="?",
        help="Port origin to verify, or 'all'",
    )
    p.add_argument(
        "--target", "-t",
        required=True,
        help="Target quarterly",
    )


def _register_add_parser(subparsers) -> None:
    """Register the add command parser (ported from v1)."""
    p = subparsers.add_parser(
        "add",
        help="Add a new port customization",
    )
    p.add_argument(
        "port",
        help="Port origin to add",
    )


def _register_save_parser(subparsers) -> None:
    """Register the save command parser (ported from v1)."""
    p = subparsers.add_parser(
        "save",
        help="Save current port state as new diff",
    )
    p.add_argument(
        "port",
        help="Port origin to save",
    )
    p.add_argument(
        "--target", "-t",
        help="Target quarterly for the diff",
    )


def _register_diff_parser(subparsers) -> None:
    """Register the diff command parser (ported from v1)."""
    p = subparsers.add_parser(
        "diff",
        help="Show diff between FreeBSD and merged port",
    )
    p.add_argument(
        "port",
        help="Port origin to diff",
    )
    p.add_argument(
        "--target", "-t",
        required=True,
        help="Target quarterly",
    )


def _register_special_parser(subparsers) -> None:
    """Register the special command parser (ported from v1)."""
    p = subparsers.add_parser(
        "special",
        help="Apply special/ directory patches (Mk, Templates, treetop)",
    )
    p.add_argument(
        "--target", "-t",
        required=True,
        help="Target quarterly",
    )
    p.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Show what would be done",
    )


def _register_logs_parser(subparsers) -> None:
    """Register the logs command parser (ported from v1)."""
    p = subparsers.add_parser(
        "logs",
        help="Show or manage merge logs",
    )
    p.add_argument(
        "port",
        nargs="?",
        help="Port origin to show logs for",
    )
    p.add_argument(
        "--clear",
        action="store_true",
        help="Clear logs",
    )


def _register_update_parser(subparsers) -> None:
    """Register the update command parser (ported from v1)."""
    p = subparsers.add_parser(
        "update",
        help="Update UPDATING file",
    )
    p.add_argument(
        "--target", "-t",
        required=True,
        help="Target quarterly",
    )


def main(argv: list[str] | None = None) -> int:
    """Main entry point for the dports CLI."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    try:
        # Setup logging based on verbosity
        setup_logging(verbose=args.verbose, quiet=args.quiet)
        
        # Load configuration
        config = Config.load(args.config)
        
        # Import and run the command
        from dports.commands import COMMANDS
        
        if args.command not in COMMANDS:
            print(f"Unknown command: {args.command}", file=sys.stderr)
            return 1
        
        cmd_func = COMMANDS[args.command]
        return cmd_func(config, args)

    except DPortsError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
