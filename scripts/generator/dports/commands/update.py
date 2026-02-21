"""Update command - update UPDATING file."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.quarterly import validate_target
from dports.utils import get_logger


def cmd_update(config: Config, args: Namespace) -> int:
    """Execute the update command."""
    log = get_logger(__name__)

    target = validate_target(args.target)
    log.info(f"Updating UPDATING file for {target}")

    # Get UPDATING file paths
    fbsd_updating = config.paths.freebsd_ports / "UPDATING"
    merged_updating = config.paths.merged_output / "UPDATING"
    delta_updating = config.paths.delta / "UPDATING.DragonFly"

    if not fbsd_updating.exists():
        log.error(f"FreeBSD UPDATING not found: {fbsd_updating}")
        return 1

    # Start with FreeBSD UPDATING
    content = fbsd_updating.read_text()

    # Add DragonFly-specific entries if they exist
    if delta_updating.exists():
        dfly_content = delta_updating.read_text()

        # Prepend DragonFly entries
        header = f"""\
# DragonFly BSD specific UPDATING entries
# Merged for {target} on {datetime.now().strftime("%Y%m%d")}

{dfly_content}

# End of DragonFly-specific entries
# ============================================

"""
        content = header + content

    # Write merged UPDATING
    merged_updating.parent.mkdir(parents=True, exist_ok=True)
    merged_updating.write_text(content)

    log.info(f"Updated {merged_updating}")
    return 0
