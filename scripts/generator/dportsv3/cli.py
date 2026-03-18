"""Command-line interface for DeltaPorts v3."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dportsv3 import __version__
from dportsv3.commands.compose import cmd_compose
from dportsv3.commands.compose_report import cmd_compose_report
from dportsv3.commands.dsl import cmd_dsl
from dportsv3.commands.migrate import cmd_migrate
from dportsv3.commands.tracker import cmd_tracker


def create_parser() -> argparse.ArgumentParser:
    """Create the main CLI parser."""
    parser = argparse.ArgumentParser(
        prog="dportsv3",
        description="DeltaPorts v3 DSL tooling",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    _register_compose_parser(subparsers)
    _register_compose_report_parser(subparsers)
    _register_dsl_parser(subparsers)
    _register_migrate_parser(subparsers)
    _register_tracker_parser(subparsers)
    return parser


def _register_compose_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register compose command."""
    compose = subparsers.add_parser("compose", help="Compose full target output tree")
    compose.add_argument(
        "--target",
        type=str,
        required=True,
        help="Target selector (@main or @YYYYQ[1-4])",
    )
    compose.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Output directory for composed tree",
    )
    compose.add_argument(
        "--delta-root",
        type=Path,
        default=Path("."),
        help="Delta repository root containing ports/ and special/",
    )
    compose.add_argument(
        "--freebsd-root",
        type=Path,
        required=True,
        help="FreeBSD ports tree root for selected target",
    )
    compose.add_argument(
        "--lock-root",
        type=Path,
        help="Optional lock source tree for type lock overlays",
    )
    compose.add_argument(
        "--dry-run",
        action="store_true",
        help="Run compose without filesystem writes",
    )
    compose.add_argument(
        "--strict",
        action="store_true",
        help="Fail fast on first failed stage",
    )
    compose.add_argument(
        "--replace-output",
        action="store_true",
        help="Replace existing output directory when non-empty",
    )
    compose.add_argument(
        "--prune-stale-overlays",
        action="store_true",
        help="Remove stale type=port overlays from delta/output during compose",
    )
    compose.add_argument(
        "--json",
        action="store_true",
        help="Emit compose report as JSON",
    )
    compose.add_argument(
        "--oracle-profile",
        choices=["off", "local", "ci"],
        default="local",
        help="Oracle profile for post-rewrite validation",
    )


def _register_compose_report_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register compose-report command."""
    report = subparsers.add_parser(
        "compose-report",
        help="Summarize compose JSON report for humans/tools",
    )
    report.add_argument(
        "report",
        type=Path,
        help="Path to compose JSON report file",
    )
    report.add_argument(
        "--top",
        type=int,
        default=10,
        help="Max rows per overview section",
    )
    report.add_argument(
        "--json",
        action="store_true",
        help="Emit overview as JSON",
    )


def _register_dsl_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register DSL subcommands."""
    p = subparsers.add_parser("dsl", help="DSL parse/check/plan commands")
    dsl_sub = p.add_subparsers(dest="dsl_command", metavar="ACTION")

    parse = dsl_sub.add_parser("parse", help="Parse a DSL file")
    parse.add_argument("path", type=Path, help="Path to overlay.dops")

    check = dsl_sub.add_parser("check", help="Check DSL syntax and semantics")
    check.add_argument("path", type=Path, help="Path to overlay.dops")

    plan = dsl_sub.add_parser("plan", help="Build normalized plan from DSL")
    plan.add_argument("path", type=Path, help="Path to overlay.dops")
    plan.add_argument(
        "--json",
        action="store_true",
        help="Emit plan as JSON",
    )

    apply_cmd = dsl_sub.add_parser("apply", help="Apply plan to a port root")
    apply_cmd.add_argument("path", type=Path, help="Path to overlay.dops")
    apply_cmd.add_argument(
        "--port-root",
        type=Path,
        required=True,
        help="Port root directory where ops are applied",
    )
    apply_cmd.add_argument(
        "--target",
        type=str,
        required=True,
        help="Target selector (@main or @YYYYQ[1-4])",
    )
    apply_cmd.add_argument(
        "--dry-run",
        action="store_true",
        help="Run apply without writing files",
    )
    apply_cmd.add_argument(
        "--strict",
        action="store_true",
        help="Fail fast on first failed operation",
    )
    apply_cmd.add_argument(
        "--json",
        action="store_true",
        help="Emit apply report as JSON",
    )
    apply_cmd.add_argument(
        "--diff",
        action="store_true",
        help="Emit planned unified diff (requires --dry-run)",
    )
    apply_cmd.add_argument(
        "--oracle-profile",
        choices=["off", "local", "ci"],
        default="local",
        help="Oracle profile for post-rewrite validation",
    )


def _register_migrate_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register migration subcommands."""
    p = subparsers.add_parser(
        "migrate",
        help="Migration utilities (compose-first workflow)",
    )
    migrate_sub = p.add_subparsers(dest="migrate_action", metavar="ACTION")

    inventory = migrate_sub.add_parser("inventory", help="Scan migration inventory")
    inventory.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Repository root containing ports/",
    )
    inventory.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )

    classify = migrate_sub.add_parser("classify", help="Classify migration inventory")
    classify.add_argument("inventory", type=Path, help="Path to inventory records JSON")
    classify.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )

    convert = migrate_sub.add_parser("convert", help="Convert one classified origin")
    convert.add_argument(
        "classified", type=Path, help="Path to classified records JSON"
    )
    convert.add_argument("origin", type=str, help="Origin to convert (category/name)")
    convert.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Repository root containing ports/",
    )
    convert.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write overlay.dops to disk",
    )
    convert.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on blocked/failed conversion",
    )
    convert.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )

    batch = migrate_sub.add_parser(
        "batch", help="Run batch conversion from classified records"
    )
    batch.add_argument("classified", type=Path, help="Path to classified records JSON")
    batch.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Repository root containing ports/",
    )
    batch.add_argument(
        "--bucket",
        action="append",
        default=[],
        help="Include only this bucket (repeatable)",
    )
    batch.add_argument(
        "--target",
        action="append",
        default=[],
        help="Include only this target (repeatable)",
    )
    batch.add_argument(
        "--category",
        action="append",
        default=[],
        help="Include only this category (repeatable)",
    )
    batch.add_argument(
        "--max-ports",
        type=int,
        default=100,
        help="Max selected records in this wave",
    )
    batch.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write overlay.dops to disk",
    )
    batch.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when quality gates fail",
    )
    batch.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )

    policy = migrate_sub.add_parser(
        "policy-check", help="Evaluate forward migration policy"
    )
    policy.add_argument("classified", type=Path, help="Path to classified records JSON")
    policy.add_argument(
        "--touched",
        type=Path,
        help="Path to newline-delimited touched origins",
    )
    policy.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when policy violations exist",
    )
    policy.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )

    progress = migrate_sub.add_parser(
        "progress", help="Evaluate migration completion thresholds"
    )
    progress.add_argument(
        "classified", type=Path, help="Path to classified records JSON"
    )
    progress.add_argument(
        "--results",
        type=Path,
        help="Optional conversion result rows JSON",
    )
    progress.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when completion thresholds are not met",
    )
    progress.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )

    dashboard = migrate_sub.add_parser(
        "dashboard", help="Build migration policy/progress dashboard"
    )
    dashboard.add_argument(
        "classified", type=Path, help="Path to classified records JSON"
    )
    dashboard.add_argument(
        "--results",
        type=Path,
        help="Optional conversion result rows JSON",
    )
    dashboard.add_argument(
        "--touched",
        type=Path,
        help="Optional newline-delimited touched origins",
    )
    dashboard.add_argument(
        "--changed-files",
        type=Path,
        help="Optional newline-delimited changed file paths for touched origin derivation",
    )
    dashboard.add_argument(
        "--root",
        type=Path,
        default=Path("."),
        help="Repository root for dashboard metadata",
    )
    dashboard.add_argument(
        "--strict-policy",
        action="store_true",
        help="Fail when policy gates fail",
    )
    dashboard.add_argument(
        "--strict-progress",
        action="store_true",
        help="Fail when progress gates fail",
    )
    dashboard.add_argument(
        "--strict",
        action="store_true",
        help="Fail when either policy or progress gates fail",
    )
    dashboard.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )

    wave_plan = migrate_sub.add_parser("wave-plan", help="Select wave candidates")
    wave_plan.add_argument("inventory", type=Path, help="Path to inventory JSON list")
    wave_plan.add_argument(
        "--bucket",
        action="append",
        default=[],
        help="Include only this bucket (repeatable)",
    )
    wave_plan.add_argument(
        "--target",
        action="append",
        default=[],
        help="Include only this target (repeatable)",
    )
    wave_plan.add_argument(
        "--category",
        action="append",
        default=[],
        help="Include only this category (repeatable)",
    )
    wave_plan.add_argument(
        "--max-ports",
        type=int,
        default=100,
        help="Max selected records in this wave",
    )
    wave_plan.add_argument(
        "--dry-run",
        action="store_true",
        help="Mark output as dry-run selection",
    )
    wave_plan.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )

    wave_report = migrate_sub.add_parser("wave-report", help="Evaluate wave results")
    wave_report.add_argument("results", type=Path, help="Path to results JSON list")
    wave_report.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when quality gates fail",
    )
    wave_report.add_argument(
        "--json",
        action="store_true",
        help="Pretty JSON output",
    )


def _register_tracker_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register tracker subcommands."""
    p = subparsers.add_parser("tracker", help="Build tracker server and queries")
    tracker_sub = p.add_subparsers(dest="tracker_action", metavar="ACTION")

    serve = tracker_sub.add_parser("serve", help="Run tracker HTTP server")
    serve.add_argument("--port", type=int, default=8080, help="Listen port")
    serve.add_argument(
        "--db",
        type=Path,
        default=Path("tracker.db"),
        help="SQLite database path",
    )

    start = tracker_sub.add_parser("start-build", help="Create a build run")
    start.add_argument("--target", type=str, required=True, help="Build target")
    start.add_argument("--type", type=str, required=True, help="Build type")
    start.add_argument("--server", type=str, help="Tracker base URL")

    finish = tracker_sub.add_parser("finish-build", help="Finish a build run")
    finish.add_argument("--run", type=int, required=True, help="Build run ID")
    finish.add_argument("--server", type=str, help="Tracker base URL")
    finish.add_argument("--finished-at", type=str, help="Override finished timestamp")
    finish.add_argument("--commit-sha", type=str, help="Recorded commit SHA")
    finish.add_argument("--commit-branch", type=str, help="Recorded commit branch")
    finish.add_argument(
        "--commit-pushed-at",
        type=str,
        help="Commit push timestamp",
    )

    record = tracker_sub.add_parser("record-result", help="Record one build result")
    record.add_argument("--run", type=int, required=True, help="Build run ID")
    record.add_argument("--origin", type=str, required=True, help="Port origin")
    record.add_argument("--version", type=str, required=True, help="Port version")
    record.add_argument("--result", type=str, required=True, help="Build result")
    record.add_argument("--log-url", type=str, help="External build log URL")
    record.add_argument("--server", type=str, help="Tracker base URL")

    enqueue = tracker_sub.add_parser("enqueue-ports", help="Enqueue ports for a build")
    enqueue.add_argument("--run", type=int, required=True, help="Build run ID")
    enqueue.add_argument(
        "--file", type=Path, required=True, help="JSON file with ports list"
    )
    enqueue.add_argument("--total", type=int, help="Total expected port count")
    enqueue.add_argument("--server", type=str, help="Tracker base URL")

    mark_building = tracker_sub.add_parser(
        "mark-building", help="Mark a port as building"
    )
    mark_building.add_argument("--run", type=int, required=True, help="Build run ID")
    mark_building.add_argument(
        "--origin", type=str, required=True, help="Port origin"
    )
    mark_building.add_argument("--server", type=str, help="Tracker base URL")

    status = tracker_sub.add_parser("status", help="Query current port status")
    status.add_argument("--target", type=str, help="Filter by target")
    status.add_argument("--origin", type=str, help="Filter by origin")
    status.add_argument("--server", type=str, help="Tracker base URL")
    status.add_argument("--json", action="store_true", help="Pretty JSON output")

    failures = tracker_sub.add_parser("failures", help="Query current failures")
    failures.add_argument("--target", type=str, required=True, help="Build target")
    failures.add_argument("--server", type=str, help="Tracker base URL")
    failures.add_argument("--json", action="store_true", help="Pretty JSON output")

    diff = tracker_sub.add_parser("diff", help="Compare two targets")
    diff.add_argument("target_a", type=str, help="First target")
    diff.add_argument("target_b", type=str, help="Second target")
    diff.add_argument("--server", type=str, help="Tracker base URL")
    diff.add_argument("--json", action="store_true", help="Pretty JSON output")

    show = tracker_sub.add_parser("show-build", help="Show one build run")
    show.add_argument("--run", type=int, required=True, help="Build run ID")
    show.add_argument("--server", type=str, help="Tracker base URL")
    show.add_argument("--json", action="store_true", help="Pretty JSON output")

    compare = tracker_sub.add_parser(
        "compare-builds",
        help="Compare two build runs",
    )
    compare.add_argument("run_a", type=int, help="First build run ID")
    compare.add_argument("run_b", type=int, help="Second build run ID")
    compare.add_argument("--server", type=str, help="Tracker base URL")
    compare.add_argument("--json", action="store_true", help="Pretty JSON output")


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    if args.command == "dsl":
        if not args.dsl_command:
            print("Missing dsl action (use: dportsv3 dsl --help)", file=sys.stderr)
            return 1
        return cmd_dsl(args)

    if args.command == "compose":
        return cmd_compose(args)

    if args.command == "compose-report":
        return cmd_compose_report(args)

    if args.command == "migrate":
        if not args.migrate_action:
            print(
                "Missing migrate action (use: dportsv3 migrate --help)", file=sys.stderr
            )
            return 1
        return cmd_migrate(args)

    if args.command == "tracker":
        if not args.tracker_action:
            print(
                "Missing tracker action (use: dportsv3 tracker --help)", file=sys.stderr
            )
            return 1
        return cmd_tracker(args)

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1
