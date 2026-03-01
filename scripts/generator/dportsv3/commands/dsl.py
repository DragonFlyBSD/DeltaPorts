"""DSL command handlers for DeltaPorts v3."""

from __future__ import annotations

import json
import sys
from argparse import Namespace
from pathlib import Path
from typing import cast

from dportsv3.common.io import read_text_file
from dportsv3.engine.api import apply_dsl, build_plan, check_dsl, parse_dsl
from dportsv3.engine.models import (
    ApplyResult,
    CheckResult,
    Diagnostic,
    ParseResult,
    PlanResult,
)


def _format_diagnostic(diag: Diagnostic) -> str:
    location = ""
    if diag.source_path:
        location = diag.source_path
        if diag.line is not None:
            location += f":{diag.line}"
            if diag.column is not None:
                location += f":{diag.column}"
    if location:
        location = f" [{location}]"
    return f"{diag.severity.upper()} {diag.code}: {diag.message}{location}"


def _emit_diagnostics(diags: list[Diagnostic]) -> None:
    for diag in diags:
        print(_format_diagnostic(diag), file=sys.stderr)


def _exit_code(ok: bool) -> int:
    return 0 if ok else 2


def _handle_parse(path: Path) -> int:
    source, error = read_text_file(path)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if source is None:
        return 1
    source = cast(str, source)

    result: ParseResult = parse_dsl(source, path)
    _emit_diagnostics(result.diagnostics)
    return _exit_code(result.ok)


def _handle_check(path: Path) -> int:
    source, error = read_text_file(path)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if source is None:
        return 1
    source = cast(str, source)

    result: CheckResult = check_dsl(source, path)
    _emit_diagnostics(result.diagnostics)
    return _exit_code(result.ok)


def _handle_plan(path: Path, emit_json: bool) -> int:
    source, error = read_text_file(path)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if source is None:
        return 1
    source = cast(str, source)

    result: PlanResult = build_plan(source, path)
    _emit_diagnostics(result.diagnostics)
    if emit_json and result.plan is not None:
        print(json.dumps(result.plan.to_dict(), indent=2, sort_keys=True))
    return _exit_code(result.ok)


def _handle_apply(
    path: Path,
    *,
    port_root: Path,
    target: str,
    dry_run: bool,
    strict: bool,
    emit_json: bool,
    emit_diff: bool,
    oracle_profile: str,
) -> int:
    if emit_diff and not dry_run:
        print(
            "ERROR E_APPLY_DIFF_REQUIRES_DRY_RUN: --diff requires --dry-run",
            file=sys.stderr,
        )
        return 2

    source, error = read_text_file(path)
    if error is not None:
        print(error, file=sys.stderr)
        return 1
    if source is None:
        return 1
    source = cast(str, source)

    result: ApplyResult = apply_dsl(
        source,
        source_path=path,
        port_root=port_root,
        target=target,
        dry_run=dry_run,
        strict=strict,
        emit_diff=emit_diff,
        oracle_profile=oracle_profile,
    )
    _emit_diagnostics(result.diagnostics)
    for row in result.op_results:
        _emit_diagnostics(row.diagnostics)

    if emit_json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    elif emit_diff:
        output = []
        for entry in result.diffs:
            block = entry.diff
            if block and not block.endswith("\n"):
                block += "\n"
            output.append(block)
        if output:
            print("".join(output), end="")

    return _exit_code(result.ok)


def cmd_dsl(args: Namespace) -> int:
    """Dispatch dsl subcommands."""
    action = args.dsl_command
    path = Path(args.path)

    if action == "parse":
        return _handle_parse(path)
    if action == "check":
        return _handle_check(path)
    if action == "plan":
        return _handle_plan(path, emit_json=bool(getattr(args, "json", False)))
    if action == "apply":
        return _handle_apply(
            path,
            port_root=Path(args.port_root),
            target=str(args.target),
            dry_run=bool(getattr(args, "dry_run", False)),
            strict=bool(getattr(args, "strict", False)),
            emit_json=bool(getattr(args, "json", False)),
            emit_diff=bool(getattr(args, "diff", False)),
            oracle_profile=str(getattr(args, "oracle_profile", "local")),
        )

    print(f"Unknown dsl action: {action}", file=sys.stderr)
    return 1
