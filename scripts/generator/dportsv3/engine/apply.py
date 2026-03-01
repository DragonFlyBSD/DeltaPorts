"""Apply-stage executor for dportsv3 plans."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Callable

from dportsv3.common.validation import is_compose_target, is_scoped_target
from dportsv3.engine.apply_common import (
    _build_patch_preview_diff,
    _build_staged_diffs,
    _diag,
    _failed_row,
    _materialize_staged_tree,
    _resolve_path,
    _success_row,
)
from dportsv3.engine.executors.file_text_patch import (
    exec_file_copy,
    exec_file_remove,
    exec_text_line_insert_after,
    exec_text_line_remove,
    exec_text_replace_once,
)
from dportsv3.engine.executors.mk_ops import (
    exec_mk_block_disable,
    exec_mk_block_replace_condition,
    exec_mk_target_append,
    exec_mk_target_remove,
    exec_mk_target_rename,
    exec_mk_target_set,
    exec_mk_var_set,
    exec_mk_var_token_add,
    exec_mk_var_token_remove,
    exec_mk_var_unset,
)
from dportsv3.engine.fsops import FileTransaction
from dportsv3.engine.models import (
    ApplyContext,
    ApplyDiff,
    ApplyOpResult,
    ApplyResult,
    Diagnostic,
    Plan,
    PlanOp,
)
from dportsv3.engine.oracle import normalize_oracle_profile, run_bmake_oracle
from dportsv3.policy import PATCH_TIMEOUT_SECONDS

Executor = Callable[[PlanOp, ApplyContext, FileTransaction], ApplyOpResult]


def _exec_patch_apply(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    _ = txn
    rel = op.payload.get("path")
    if not isinstance(rel, str):
        return _failed_row(
            op, code="E_APPLY_INVALID_PATH", message="patch.apply requires path"
        )

    try:
        patch_path = _resolve_path(context.port_root, rel)
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    if not patch_path.exists():
        return _failed_row(
            op,
            code="E_APPLY_MISSING_SUBJECT",
            message=f"patch file does not exist: {rel}",
            source_path=patch_path,
        )

    command = [
        "patch",
        "--batch",
        "--forward",
        "-V",
        "none",
        "-r",
        "-",
        "-p0",
        "-i",
        str(patch_path),
    ]
    if context.dry_run:
        command.insert(1, "--dry-run")

    try:
        proc = subprocess.run(
            command,
            cwd=str(context.port_root),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=PATCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return _failed_row(
            op,
            code="E_APPLY_PATCH_FAILED",
            message=f"patch timed out after {PATCH_TIMEOUT_SECONDS}s",
            source_path=patch_path,
        )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip() or "patch command failed"
        return _failed_row(
            op,
            code="E_APPLY_PATCH_FAILED",
            message=detail,
            source_path=patch_path,
        )

    return _success_row(op, "patch-applied")


def _known_registry() -> dict[str, Executor]:
    return {
        "mk.var.set": exec_mk_var_set,
        "mk.var.unset": exec_mk_var_unset,
        "mk.var.token_add": exec_mk_var_token_add,
        "mk.var.token_remove": exec_mk_var_token_remove,
        "mk.block.disable": exec_mk_block_disable,
        "mk.block.replace_condition": exec_mk_block_replace_condition,
        "mk.target.set": exec_mk_target_set,
        "mk.target.append": exec_mk_target_append,
        "mk.target.remove": exec_mk_target_remove,
        "mk.target.rename": exec_mk_target_rename,
        "file.copy": exec_file_copy,
        "file.remove": exec_file_remove,
        "text.line_remove": exec_text_line_remove,
        "text.line_insert_after": exec_text_line_insert_after,
        "text.replace_once": exec_text_replace_once,
        "patch.apply": _exec_patch_apply,
    }


def apply_plan(
    plan: Plan,
    *,
    port_root: Path,
    target: str,
    dry_run: bool = False,
    strict: bool = False,
    emit_diff: bool = False,
    oracle_profile: str = "local",
) -> ApplyResult:
    """Apply normalized plan to a port root using deterministic execution order."""
    try:
        normalized_oracle_profile = normalize_oracle_profile(oracle_profile)
    except ValueError as exc:
        fallback_context = ApplyContext(
            port_root=port_root,
            target=target,
            dry_run=dry_run,
            strict=strict,
            oracle_profile=str(oracle_profile),
        )
        return ApplyResult(
            ok=False,
            context=fallback_context,
            op_results=[],
            diagnostics=[
                _diag(
                    severity="error",
                    code="E_APPLY_INVALID_ORACLE_PROFILE",
                    message=str(exc),
                    source_path=port_root,
                )
            ],
            oracle_profile=str(oracle_profile),
        )

    context = ApplyContext(
        port_root=port_root,
        target=target,
        dry_run=dry_run,
        strict=strict,
        oracle_profile=normalized_oracle_profile,
    )
    diagnostics: list[Diagnostic] = []
    op_results: list[ApplyOpResult] = []
    diff_rows: list[ApplyDiff] = []
    oracle_checks = 0
    oracle_failures = 0
    oracle_skipped = 0

    if not is_compose_target(target):
        diagnostics.append(
            _diag(
                severity="error",
                code="E_APPLY_INVALID_TARGET",
                message=f"invalid apply target: {target}",
                source_path=port_root,
            )
        )
        return ApplyResult(
            ok=False,
            context=context,
            op_results=op_results,
            diagnostics=diagnostics,
            oracle_profile=normalized_oracle_profile,
            oracle_checks=oracle_checks,
            oracle_failures=oracle_failures,
            oracle_skipped=oracle_skipped,
        )

    if not port_root.exists() or not port_root.is_dir():
        diagnostics.append(
            _diag(
                severity="error",
                code="E_APPLY_INVALID_PORT_ROOT",
                message=f"invalid port root: {port_root}",
                source_path=port_root,
            )
        )
        return ApplyResult(
            ok=False,
            context=context,
            op_results=op_results,
            diagnostics=diagnostics,
            oracle_profile=normalized_oracle_profile,
            oracle_checks=oracle_checks,
            oracle_failures=oracle_failures,
            oracle_skipped=oracle_skipped,
        )

    registry = _known_registry()
    txn = FileTransaction(dry_run=dry_run)

    try:
        ordered_ops = [op for op in plan.ops if op.target == "@any"]
        ordered_ops.extend(op for op in plan.ops if op.target == target)
        ordered_ops.extend(op for op in plan.ops if op.target not in {"@any", target})

        for op in ordered_ops:
            if not is_scoped_target(op.target):
                row = ApplyOpResult(
                    id=op.id,
                    kind=op.kind,
                    target=op.target,
                    status="failed",
                    message="invalid-op-target",
                    diagnostics=[
                        _diag(
                            severity="error",
                            code="E_APPLY_INVALID_TARGET",
                            message=f"invalid op target: {op.target}",
                            source_path=port_root,
                        )
                    ],
                )
                op_results.append(row)
                if strict:
                    txn.rollback()
                    return ApplyResult(
                        ok=False,
                        context=context,
                        op_results=op_results,
                        diagnostics=diagnostics,
                        diffs=[],
                        oracle_profile=normalized_oracle_profile,
                        oracle_checks=oracle_checks,
                        oracle_failures=oracle_failures,
                        oracle_skipped=oracle_skipped,
                    )
                continue

            if op.target not in {"@any", target}:
                row = ApplyOpResult(
                    id=op.id,
                    kind=op.kind,
                    target=op.target,
                    status="skipped",
                    message="target-mismatch",
                    diagnostics=[
                        _diag(
                            severity="info",
                            code="I_APPLY_TARGET_MISMATCH",
                            message=f"op target {op.target} does not match requested target {target}",
                            source_path=port_root,
                        )
                    ],
                )
                op_results.append(row)
                continue

            executor = registry.get(op.kind)
            if executor is None:
                row = ApplyOpResult(
                    id=op.id,
                    kind=op.kind,
                    target=op.target,
                    status="failed",
                    message="unknown-kind",
                    diagnostics=[
                        _diag(
                            severity="error",
                            code="E_APPLY_UNKNOWN_KIND",
                            message=f"no executor registered for kind '{op.kind}'",
                            source_path=port_root,
                        )
                    ],
                )
                op_results.append(row)
                if strict:
                    txn.rollback()
                    return ApplyResult(
                        ok=False,
                        context=context,
                        op_results=op_results,
                        diagnostics=diagnostics,
                        diffs=[],
                        oracle_profile=normalized_oracle_profile,
                        oracle_checks=oracle_checks,
                        oracle_failures=oracle_failures,
                        oracle_skipped=oracle_skipped,
                    )
                continue

            try:
                row = executor(op, context, txn)
            except ValueError as exc:
                row = _failed_row(
                    op,
                    code="E_APPLY_PARSE_FAILED",
                    message=str(exc),
                    source_path=port_root,
                )
            op_results.append(row)

            if (
                emit_diff
                and dry_run
                and op.kind == "patch.apply"
                and row.status == "applied"
            ):
                preview = _build_patch_preview_diff(op, port_root)
                if preview is not None:
                    diff_rows.append(preview)
            if strict and row.status == "failed":
                txn.rollback()
                return ApplyResult(
                    ok=False,
                    context=context,
                    op_results=op_results,
                    diagnostics=diagnostics,
                    diffs=[],
                    oracle_profile=normalized_oracle_profile,
                    oracle_checks=oracle_checks,
                    oracle_failures=oracle_failures,
                    oracle_skipped=oracle_skipped,
                )

        if emit_diff:
            diff_rows.extend(_build_staged_diffs(txn, port_root))
            diff_rows.sort(key=lambda entry: (entry.path, entry.change_type))

        if any(row.status == "failed" for row in op_results):
            oracle_skipped = 1
            diagnostics.append(
                _diag(
                    severity="warning",
                    code="W_APPLY_ORACLE_SKIPPED",
                    message="oracle skipped because apply has failed operations",
                    source_path=port_root,
                )
            )
        elif normalized_oracle_profile == "off":
            oracle_skipped = 1
        else:
            oracle_root = _materialize_staged_tree(port_root, txn)
            try:
                oracle_result = run_bmake_oracle(
                    oracle_root, profile=normalized_oracle_profile
                )
            finally:
                shutil.rmtree(oracle_root, ignore_errors=True)

            oracle_checks = oracle_result.checks_run
            if oracle_result.skipped:
                oracle_skipped = 1

            failure_messages = list(oracle_result.failures)
            if oracle_result.unavailable:
                message = (
                    oracle_result.failures[0]
                    if oracle_result.failures
                    else (
                        oracle_result.warnings[0]
                        if oracle_result.warnings
                        else "bmake not available"
                    )
                )
                if normalized_oracle_profile == "ci":
                    diagnostics.append(
                        _diag(
                            severity="error",
                            code="E_APPLY_ORACLE_UNAVAILABLE",
                            message=message,
                            source_path=port_root,
                        )
                    )
                    oracle_failures += 1
                else:
                    diagnostics.append(
                        _diag(
                            severity="warning",
                            code="W_APPLY_ORACLE_SKIPPED",
                            message=message,
                            source_path=port_root,
                        )
                    )
                failure_messages = []

            emitted_warning_messages: set[str] = set()
            if oracle_result.unavailable:
                emitted_warning_messages.add(
                    oracle_result.failures[0]
                    if oracle_result.failures
                    else (
                        oracle_result.warnings[0]
                        if oracle_result.warnings
                        else "bmake not available"
                    )
                )

            for warning in oracle_result.warnings:
                if warning in emitted_warning_messages:
                    continue
                diagnostics.append(
                    _diag(
                        severity="warning",
                        code="W_APPLY_ORACLE_SKIPPED",
                        message=warning,
                        source_path=port_root,
                    )
                )

            for failure in failure_messages:
                diagnostics.append(
                    _diag(
                        severity="error",
                        code="E_APPLY_ORACLE_FAILED",
                        message=failure,
                        source_path=port_root,
                    )
                )
                oracle_failures += 1

            if oracle_failures > 0 and (strict or normalized_oracle_profile == "ci"):
                txn.rollback()
                return ApplyResult(
                    ok=False,
                    context=context,
                    op_results=op_results,
                    diagnostics=diagnostics,
                    diffs=diff_rows,
                    oracle_profile=normalized_oracle_profile,
                    oracle_checks=oracle_checks,
                    oracle_failures=oracle_failures,
                    oracle_skipped=oracle_skipped,
                )

        txn.commit()
    except OSError as exc:
        txn.rollback()
        diagnostics.append(
            _diag(
                severity="error",
                code="E_APPLY_WRITE_FAILED",
                message=f"apply write failure: {exc}",
                source_path=port_root,
            )
        )
        return ApplyResult(
            ok=False,
            context=context,
            op_results=op_results,
            diagnostics=diagnostics,
            diffs=diff_rows,
            oracle_profile=normalized_oracle_profile,
            oracle_checks=oracle_checks,
            oracle_failures=oracle_failures,
            oracle_skipped=oracle_skipped,
        )

    ok = not diagnostics and all(row.status != "failed" for row in op_results)
    return ApplyResult(
        ok=ok,
        context=context,
        op_results=op_results,
        diagnostics=diagnostics,
        diffs=diff_rows,
        oracle_profile=normalized_oracle_profile,
        oracle_checks=oracle_checks,
        oracle_failures=oracle_failures,
        oracle_skipped=oracle_skipped,
    )
