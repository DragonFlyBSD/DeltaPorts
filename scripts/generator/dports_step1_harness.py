#!/usr/bin/env python3
"""Run Step 1 integration harness for dports building blocks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dports.config import Config
from dports.integration import run_step1_harness
from dports.models import SelectionMode
from dports.utils import setup_logging


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dports-step1-harness",
        description="Run Step 1 integration harness for compose/migrate building blocks",
    )
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        help="Path to dports.toml config file",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        default=["main", "2025Q2"],
        help="Targets to test (default: main 2025Q2)",
    )
    parser.add_argument(
        "--work-base",
        "-w",
        type=Path,
        required=True,
        help="Workspace directory for migrated/composed output trees",
    )
    parser.add_argument(
        "--selection",
        choices=[SelectionMode.OVERLAY_CANDIDATES.value, SelectionMode.FULL_TREE.value],
        default=SelectionMode.OVERLAY_CANDIDATES.value,
        help="Compose selection mode",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run real mutations (default is dry-run)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase verbosity",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)

    setup_logging(verbose=args.verbose)
    config = Config.load(args.config)

    selection = SelectionMode(args.selection)
    result = run_step1_harness(
        config=config,
        targets=args.targets,
        work_base=args.work_base,
        dry_run=not args.apply,
        selection=selection,
    )

    print(f"Harness {'SUCCESS' if result.success else 'FAILED'}")
    print(f"Targets: {', '.join(result.targets)}")
    print(f"Work base: {result.work_base}")

    for item in result.results:
        compose_ok = item.compose_result.success if item.compose_result else False
        print(
            f"- {item.target}: success={item.success} "
            f"migrate_changed={item.migration_ports_changed} "
            f"state_entries={item.migration_state_entries} "
            f"compose_ok={compose_ok}"
        )
        if item.migration_errors:
            for err in item.migration_errors[:10]:
                print(f"    migration_error: {err}")
        if item.compose_result and item.compose_result.errors:
            for err in item.compose_result.errors[:10]:
                print(f"    compose_error: {err}")

    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(main())
