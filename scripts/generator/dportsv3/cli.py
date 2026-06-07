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
    _register_artifact_store_parser(subparsers)
    _register_agent_queue_runner_parser(subparsers)
    from dportsv3.verify_fix import register_parser as _reg_verify_fix
    _reg_verify_fix(subparsers)
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
    compose.add_argument(
        "--origin",
        action="append",
        default=[],
        help="Re-compose only the selected origin (repeatable, requires existing output tree)",
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
        default=None,
        help="SQLite database path. If unset, uses DPORTSV3_STATE_DB env var, else $PWD/state.db",
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
    record.add_argument(
        "--result", type=str, required=True,
        choices=["success", "failure", "skipped", "ignored"],
        help="Build result. Must match the API's BuildResultLiteral; "
             "use these exact tokens (hooks must NOT send 'pass'/'fail').",
    )
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
    mark_building.add_argument("--origin", type=str, required=True, help="Port origin")
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

    # Agentic-side reads. Used by operators (and the analyzer subagent)
    # so bundle / job / activity inspection doesn't need curl + jq.
    # All read-only; all go through the tracker HTTP API.
    get_bundle_p = tracker_sub.add_parser(
        "get-bundle", help="Fetch one bundle's detail (includes artifact list)",
    )
    get_bundle_p.add_argument("bundle_id", type=str)
    get_bundle_p.add_argument(
        "--jobs", action="store_true",
        help="Also include the list of jobs that touched this bundle "
             "(saves a separate list-jobs join)",
    )
    get_bundle_p.add_argument("--server", type=str)
    get_bundle_p.add_argument("--json", action="store_true",
                              help="Pretty JSON output (default: terse text)")

    list_bundles_p = tracker_sub.add_parser(
        "list-bundles", help="List bundles, newest first",
    )
    list_bundles_p.add_argument("--origin", type=str,
                                help="Filter by port origin (e.g. devel/gperf)")
    list_bundles_p.add_argument("--target", type=str,
                                help="Filter by target (e.g. @main)")
    list_bundles_p.add_argument("--limit", type=int, default=20)
    list_bundles_p.add_argument("--server", type=str)
    list_bundles_p.add_argument("--json", action="store_true")

    get_job_p = tracker_sub.add_parser(
        "get-job", help="Fetch one job by ID",
    )
    get_job_p.add_argument("job_id", type=str)
    get_job_p.add_argument("--server", type=str)
    get_job_p.add_argument("--json", action="store_true")

    list_jobs_p = tracker_sub.add_parser(
        "list-jobs", help="List jobs",
    )
    list_jobs_p.add_argument("--state", type=str,
                             help="Filter by lifecycle state (e.g. queued, inflight, done, dead)")
    list_jobs_p.add_argument("--target", type=str)
    list_jobs_p.add_argument("--limit", type=int, default=50)
    list_jobs_p.add_argument("--server", type=str)
    list_jobs_p.add_argument("--json", action="store_true")

    get_activity_p = tracker_sub.add_parser(
        "get-activity", help="Activity-log rows (filter by job or target)",
    )
    get_activity_p.add_argument("--job", dest="job_id", type=str,
                                help="Per-job entries (oldest-first when paged)")
    get_activity_p.add_argument("--target", type=str)
    get_activity_p.add_argument("--stage", dest="stage_filter", type=str,
                                help="Substring filter on the stage column")
    get_activity_p.add_argument("--since-id", type=int, default=0,
                                help="Only return rows with id > N")
    get_activity_p.add_argument("--limit", type=int, default=50)
    get_activity_p.add_argument("--server", type=str)
    get_activity_p.add_argument("--json", action="store_true")

    fetch_artifact_p = tracker_sub.add_parser(
        "fetch-artifact",
        help="Dump a bundle artifact's raw bytes to stdout (pipe to "
             "a file for binary artifacts like *.gz or *.png)",
    )
    fetch_artifact_p.add_argument("bundle_id", type=str)
    fetch_artifact_p.add_argument(
        "relpath", type=str,
        help="e.g. analysis/triage.md, logs/errors.txt, logs/full.log.gz",
    )
    fetch_artifact_p.add_argument("--server", type=str)

    download_bundle_p = tracker_sub.add_parser(
        "download-bundle",
        help="Materialize one bundle's full contents (meta.json + all "
             "artifacts) into a local directory for offline analysis",
    )
    download_bundle_p.add_argument("bundle_id", type=str)
    download_bundle_p.add_argument(
        "--out", type=str, default=None,
        help="Output directory (default: ./bundles/<bundle_id>)",
    )
    download_bundle_p.add_argument("--server", type=str)


def _register_artifact_store_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register artifact-store command (serves bundles into state.db).

    The subparser is a marker only; argv past this subcommand is
    forwarded verbatim to ``dportsv3.artifact_store.main`` by ``main``
    below (REMAINDER nargs doesn't reliably absorb ``--flag``-style
    args).
    """
    subparsers.add_parser(
        "artifact-store",
        help="Run the artifact-store HTTP service (forwards --bind/--port/--logs-root)",
        add_help=False,
    )


def _register_agent_queue_runner_parser(
    subparsers: argparse._SubParsersAction,
) -> None:
    """Register agent-queue-runner forwarder.

    The runner lives at ``dportsv3.agent.runner``. The subcommand
    handler in ``main()`` calls ``runner.main()`` directly.
    """
    subparsers.add_parser(
        "agent-queue-runner",
        help="Run the agent queue runner (dportsv3.agent.runner.main)",
        add_help=False,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint."""
    raw = list(argv) if argv is not None else sys.argv[1:]

    # artifact-store is a thin forwarder — let its own argparse handle
    # --bind/--port/--logs-root rather than splitting flag parsing
    # across two layers. ``argparse.REMAINDER`` is unreliable for this.
    if raw and raw[0] == "artifact-store":
        from dportsv3.artifact_store import main as artifact_store_main

        artifact_store_main(raw[1:])
        return 0

    # agent-queue-runner lives at dportsv3.agent.runner; call its
    # main() directly with the remaining argv (so its own argparse
    # handles --queue-root / --once / etc).
    if raw and raw[0] == "agent-queue-runner":
        from dportsv3.agent.runner import main as runner_main

        return runner_main(raw[1:])

    parser = create_parser()
    args = parser.parse_args(raw)

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

    if args.command == "verify-fix":
        from dportsv3.verify_fix import cmd_verify_fix
        return cmd_verify_fix(args)

    print(f"Unknown command: {args.command}", file=sys.stderr)
    return 1
