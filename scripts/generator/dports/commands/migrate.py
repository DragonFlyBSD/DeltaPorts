"""Migrate command - normalize overlays to strict @target layout."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.migrate import (
    migrate_all_layouts_to_target,
    migrate_port_layout_to_target,
    migrate_special_diffs_to_target,
)
from dports.quarterly import validate_target
from dports.utils import get_logger


def cmd_migrate(config: Config, args: Namespace) -> int:
    """Execute the migrate command."""
    log = get_logger(__name__)

    target = validate_target(args.target)
    dry_run = getattr(args, "dry_run", False)

    if getattr(args, "output", None) is not None:
        log.warning("--output is deprecated and ignored; migration runs in-place")
    if getattr(args, "state_output", None) is not None:
        log.warning(
            "--state-output is deprecated and ignored; state migration is not part of this command"
        )

    if args.port == "all" or args.port is None:
        log.info(f"Migrating all overlay candidates to @{target} layout")

        migrated, unchanged, errors = migrate_all_layouts_to_target(
            config,
            target=target,
            dry_run=dry_run,
        )
        special_moved, special_errors = migrate_special_diffs_to_target(
            config,
            target=target,
            dry_run=dry_run,
        )
        errors.extend(special_errors)

        log.info("Migration complete:")
        log.info(f"  Ports changed: {migrated}")
        log.info(f"  Ports unchanged: {unchanged}")
        log.info(f"  Special diffs moved: {special_moved}")

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

    result = migrate_port_layout_to_target(
        config, origin, target=target, dry_run=dry_run
    )

    if result.actions:
        log.info(f"Applied {len(result.actions)} migration actions")
        for action in result.actions[:20]:
            log.info(f"  {action}")
        if len(result.actions) > 20:
            log.info(f"  ... and {len(result.actions) - 20} more")
    else:
        log.info("No changes needed")

    if result.errors:
        log.error(f"Migration encountered {len(result.errors)} errors")
        for err in result.errors:
            log.error(f"  {err}")
        return 1

    return 0
