"""Verify command - verify port patches apply cleanly."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from argparse import Namespace
    from dports.config import Config

from dports.models import PortOrigin
from dports.overlay import Overlay
from dports.quarterly import validate_target
from dports.validate import validate_diff_applies
from dports.utils import get_logger, list_delta_ports


def cmd_verify(config: Config, args: Namespace) -> int:
    """Execute the verify command."""
    log = get_logger(__name__)

    target = validate_target(args.target)

    if args.port == "all" or args.port is None:
        log.info(f"Verifying all port patches for {target}")

        ports_base = config.paths.delta / "ports"
        success = 0
        failed = 0

        for origin_str in list_delta_ports(ports_base):
            origin = PortOrigin.parse(origin_str)
            overlay_path = config.get_overlay_port_path(str(origin))
            overlay = Overlay(overlay_path, origin)
            if not overlay.exists():
                continue

            try:
                manifest = overlay.manifest
            except Exception:
                continue

            if not manifest.has_diffs:
                continue

            diffs = overlay.get_diffs_for_target(target)

            for diff_file in diffs:
                target_dir = config.get_freebsd_port_path(str(origin), target)

                if target_dir.exists():
                    applies, msg = validate_diff_applies(diff_file, target_dir)

                    if applies:
                        success += 1
                    else:
                        failed += 1
                        log.error(f"{origin}: {diff_file.name} - {msg}")

        if success == 0 and failed == 0:
            log.warning("No diff patches found for verification")

        log.info(f"Verification complete: {success} passed, {failed} failed")
        return 1 if failed > 0 else 0
    else:
        origin = PortOrigin.parse(args.port)
        log.info(f"Verifying patches for {origin}")

        overlay_path = config.get_overlay_port_path(str(origin))
        overlay = Overlay(overlay_path, origin)

        if not overlay.exists() or not overlay.manifest.has_diffs:
            log.info(f"No diffs to verify for {origin}")
            return 0

        diffs = overlay.get_diffs_for_target(target)
        target_dir = config.get_freebsd_port_path(str(origin), target)

        failed = 0
        for diff_file in diffs:
            applies, msg = validate_diff_applies(diff_file, target_dir)

            if applies:
                log.info(f"  {diff_file.name}: OK")
            else:
                log.error(f"  {diff_file.name}: FAILED - {msg}")
                failed += 1

        return 1 if failed > 0 else 0
