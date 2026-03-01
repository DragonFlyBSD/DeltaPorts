"""Compose report summarizer command."""

from __future__ import annotations

import sys
from argparse import Namespace
from pathlib import Path

from dportsv3.common.io import emit_json, read_json_object
from dportsv3.compose_reporting import (
    build_compose_report_overview,
    format_compose_overview,
)


def cmd_compose_report(args: Namespace) -> int:
    """Build a compact compose overview from JSON report."""
    payload, error = read_json_object(
        Path(args.report),
        object_label="Compose report JSON",
    )
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if payload is None:
        return 2

    top = int(getattr(args, "top", 10))
    overview = build_compose_report_overview(payload, top=top)

    if bool(getattr(args, "json", False)):
        emit_json(overview, pretty=True)
    else:
        print(
            f"Compose report {'succeeded' if overview['ok'] else 'failed'} "
            f"for {overview['target']} -> {overview['output_path']}"
        )
        for line in format_compose_overview(overview):
            print(line)

    return 0
