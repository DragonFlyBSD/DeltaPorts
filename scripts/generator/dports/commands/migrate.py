"""Migrate command - normalize overlays to strict @target layout."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.migrate import (
    cleanup_status_only_dirs,
    default_output_tree_path,
    generate_builds_json_from_status,
    migrate_all_layouts_to_target,
    migrate_port_layout_to_target,
    migrate_special_diffs_to_target,
    prepare_output_tree,
)
from dports.quarterly import validate_target
from dports.utils import get_logger


def _parse_phases(value: str) -> tuple[set[str], str | None]:
    allowed = {"layout", "state", "cleanup"}
    phases = {p.strip().lower() for p in value.split(",") if p.strip()}
    invalid = sorted(phases - allowed)
    if invalid:
        return set(), f"Invalid migration phase(s): {', '.join(invalid)}"
    if not phases:
        return set(), "No migration phases selected"
    return phases, None


def _resolve_work_tree(
    config: Config, args: Namespace, target: str, dry_run: bool
) -> tuple[Path | None, bool]:
    """Resolve migration work tree path and whether it is in-place."""
    log = get_logger(__name__)
    source = config.paths.delta

    if args.in_place and args.output:
        log.error("Cannot use --in-place and --output together")
        return None, False

    if args.in_place:
        return source, True

    output = args.output
    if output is None:
        output = default_output_tree_path(source, target)

    output = Path(output).expanduser()
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()

    ok, message = prepare_output_tree(source, output, dry_run=dry_run)
    if not ok:
        log.error(message)
        return None, False

    log.info(message)
    return output, False


def _resolve_state_output(work_base: Path, args: Namespace) -> Path:
    if args.state_output is None:
        return work_base / "state" / "builds.json"

    p = Path(args.state_output).expanduser()
    if p.is_absolute():
        return p
    return work_base / p


def cmd_migrate(config: Config, args: Namespace) -> int:
    """Execute the migrate command."""
    log = get_logger(__name__)

    target = validate_target(args.target)
    dry_run = getattr(args, "dry_run", False)
    phases, phase_error = _parse_phases(getattr(args, "phases", "layout,state,cleanup"))
    if phase_error:
        log.error(phase_error)
        return 1

    work_base, in_place = _resolve_work_tree(config, args, target, dry_run)
    if work_base is None:
        return 1

    state_output = _resolve_state_output(work_base, args)

    errors: list[str] = []

    if args.port == "all" or args.port is None:
        log.info(f"Migrating all overlay candidates to @{target} layout")
        log.info(f"Work tree: {work_base}")

        ports_changed = 0
        ports_unchanged = 0
        special_moved = 0
        state_entries = 0
        cleaned_dirs = 0

        if "layout" in phases:
            ports_changed, ports_unchanged, layout_errors = (
                migrate_all_layouts_to_target(
                    config,
                    target=target,
                    dry_run=dry_run,
                    delta_base=work_base,
                )
            )
            errors.extend(layout_errors)

            if not getattr(args, "no_special", False):
                special_moved, special_errors = migrate_special_diffs_to_target(
                    config,
                    target=target,
                    dry_run=dry_run,
                    delta_base=work_base,
                )
                errors.extend(special_errors)

        if "state" in phases:
            state_entries = generate_builds_json_from_status(
                delta_base=work_base,
                target=target,
                output_path=state_output,
                dry_run=dry_run,
            )

        if "cleanup" in phases:
            if in_place and not getattr(args, "cleanup_status_only", False):
                log.warning(
                    "Skipping cleanup phase in in-place mode (use --cleanup-status-only to enable)"
                )
            else:
                cleaned_dirs = cleanup_status_only_dirs(work_base, dry_run=dry_run)

        log.info("Migration complete:")
        log.info(f"  Ports changed: {ports_changed}")
        log.info(f"  Ports unchanged: {ports_unchanged}")
        log.info(f"  Special diffs moved: {special_moved}")
        log.info(f"  State entries: {state_entries}")
        log.info(f"  STATUS-only dirs removed: {cleaned_dirs}")
        log.info(f"  State file: {state_output}")

        if errors:
            log.error(f"  Errors: {len(errors)}")
            for err in errors[:20]:
                log.error(f"    {err}")
            if len(errors) > 20:
                log.error(f"    ... and {len(errors) - 20} more")
            return 1

        return 0

    origin = PortOrigin.parse(args.port)
    log.info(f"Migrating {origin} to @{target} layout")
    log.info(f"Work tree: {work_base}")

    if "layout" in phases:
        result = migrate_port_layout_to_target(
            config,
            origin,
            target=target,
            dry_run=dry_run,
            delta_base=work_base,
        )

        if result.actions:
            log.info(f"Applied {len(result.actions)} migration actions")
            for action in result.actions[:20]:
                log.info(f"  {action}")
            if len(result.actions) > 20:
                log.info(f"  ... and {len(result.actions) - 20} more")
        else:
            log.info("No layout changes needed")

        if result.errors:
            log.error(f"Migration encountered {len(result.errors)} errors")
            for err in result.errors:
                log.error(f"  {err}")
            return 1

    if "state" in phases:
        entries = generate_builds_json_from_status(
            delta_base=work_base,
            target=target,
            output_path=state_output,
            dry_run=dry_run,
        )
        log.info(f"State entries written: {entries}")
        log.info(f"State file: {state_output}")

    if "cleanup" in phases:
        if in_place and not getattr(args, "cleanup_status_only", False):
            log.warning(
                "Skipping cleanup phase in in-place mode (use --cleanup-status-only to enable)"
            )
        else:
            removed = cleanup_status_only_dirs(work_base, dry_run=dry_run)
            log.info(f"STATUS-only dirs removed: {removed}")

    return 0
