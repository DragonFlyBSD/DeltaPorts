"""Compose command handler for dportsv3."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from dportsv3.common.io import emit_json
from dportsv3.compose import run_compose
from dportsv3.compose_reporting import format_compose_result


def cmd_compose(args: Namespace) -> int:
    """Run v3 compose pipeline."""
    lock_root = Path(args.lock_root) if getattr(args, "lock_root", None) else None

    result = run_compose(
        target=str(args.target),
        output_path=Path(args.output),
        delta_root=Path(args.delta_root),
        freebsd_root=Path(args.freebsd_root),
        lock_root=lock_root,
        dry_run=bool(getattr(args, "dry_run", False)),
        strict=bool(getattr(args, "strict", False)),
        replace_output=bool(getattr(args, "replace_output", False)),
        prune_stale_overlays=bool(getattr(args, "prune_stale_overlays", False)),
        oracle_profile=str(getattr(args, "oracle_profile", "local")),
    )

    if bool(getattr(args, "json", False)):
        emit_json(result.to_dict(), pretty=True)
    else:
        for line in format_compose_result(result):
            print(line)

    return 0 if result.ok else 2
