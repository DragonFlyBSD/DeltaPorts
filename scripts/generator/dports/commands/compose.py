"""Compose command - build final DPorts output tree."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.compose import run_compose, format_compose_result
from dports.models import PortOrigin, SelectionMode
from dports.quarterly import validate_target
from dports.utils import get_logger


def _selection_from_args(args: Namespace) -> tuple[SelectionMode, PortOrigin | None]:
    if getattr(args, "port", None):
        return SelectionMode.SINGLE, PortOrigin.parse(args.port)

    selection_raw = getattr(args, "selection", SelectionMode.OVERLAY_CANDIDATES.value)
    if selection_raw == SelectionMode.OVERLAY_CANDIDATES.value:
        return SelectionMode.OVERLAY_CANDIDATES, None
    if selection_raw == SelectionMode.FULL_TREE.value:
        return SelectionMode.FULL_TREE, None
    raise ValueError(f"Invalid selection mode: {selection_raw}")


def cmd_compose(config: Config, args: Namespace) -> int:
    """Execute full compose pipeline."""
    log = get_logger(__name__)

    target = validate_target(args.target)
    output = Path(args.output).expanduser() if getattr(args, "output", None) else None
    dry_run = getattr(args, "dry_run", False)
    replace_output = getattr(args, "replace_output", False)
    preflight_validate = not getattr(args, "no_validate", False)

    try:
        selection, origin = _selection_from_args(args)
    except Exception as e:
        log.error(str(e))
        return 1

    result = run_compose(
        config=config,
        target=target,
        output_path=output,
        selection=selection,
        origin=origin,
        dry_run=dry_run,
        replace_output=replace_output,
        preflight_validate=preflight_validate,
    )

    for line in format_compose_result(result):
        if line.startswith("error:"):
            log.error(f"  {line.removeprefix('error: ')}")
        else:
            log.info(line)

    return 0 if result.success else 1
