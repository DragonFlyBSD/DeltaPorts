"""Semantic analyzer for DeltaPorts v3 DSL."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dportsv3.common.validation import is_scoped_target, normalize_on_missing
from dportsv3.engine.ast import (
    AstDocument,
    FileOpNode,
    MaintainerDirective,
    MkOpNode,
    OperationNode,
    PatchOpNode,
    PortDirective,
    ReasonDirective,
    TargetDirective,
    TextOpNode,
    TypeDirective,
)
from dportsv3.engine.models import Diagnostic, SourceSpan


@dataclass(frozen=True)
class ScopedOperation:
    """Operation with resolved target scope."""

    target: str
    operation: OperationNode


@dataclass
class SemanticResult:
    """Semantic analysis result."""

    ok: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    document: AstDocument | None = None
    scoped_ops: list[ScopedOperation] = field(default_factory=list)


def _diag(
    code: str,
    message: str,
    span: SourceSpan,
    source_path: Path | None,
) -> Diagnostic:
    return Diagnostic(
        severity="error",
        code=code,
        message=message,
        source_path=str(source_path) if source_path is not None else None,
        line=span.line_start,
        column=span.column_start,
    )


def _validate_on_missing(
    value: str | None,
    allowed: bool,
    span: SourceSpan,
    source_path: Path | None,
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if value is None:
        return diagnostics

    if not allowed:
        diagnostics.append(
            _diag(
                "E_SEM_INVALID_OPERATION_STATE",
                "on-missing is not allowed for this operation",
                span,
                source_path,
            )
        )
        return diagnostics

    if normalize_on_missing(value) is None:
        diagnostics.append(
            _diag(
                "E_SEM_INVALID_OPERATION_STATE",
                "on-missing must be one of: error|warn|noop",
                span,
                source_path,
            )
        )

    return diagnostics


def _validate_operation(
    op: OperationNode, source_path: Path | None
) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    if isinstance(op, MkOpNode):
        if op.action == "set":
            if op.var is None or op.value is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "mk set requires var and value",
                        op.span,
                        source_path,
                    )
                )
        elif op.action == "unset":
            if op.var is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "mk unset requires var",
                        op.span,
                        source_path,
                    )
                )
        elif op.action in {"add", "remove"}:
            if op.var is None or op.token is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        f"mk {op.action} requires var and token",
                        op.span,
                        source_path,
                    )
                )
        elif op.action == "disable-if":
            if op.condition is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "mk disable-if requires condition",
                        op.span,
                        source_path,
                    )
                )
        elif op.action == "replace-if":
            if op.condition_from is None or op.condition_to is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "mk replace-if requires from and to conditions",
                        op.span,
                        source_path,
                    )
                )
        elif op.action in {"target-set", "target-append"}:
            if op.name is None or op.heredoc_tag is None or op.recipe is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        f"mk {op.action} requires target name, heredoc tag, and recipe",
                        op.span,
                        source_path,
                    )
                )
        elif op.action == "target-remove":
            if op.name is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "mk target-remove requires target name",
                        op.span,
                        source_path,
                    )
                )
        elif op.action == "target-rename":
            if op.old_name is None or op.new_name is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "mk target-rename requires old and new target names",
                        op.span,
                        source_path,
                    )
                )
        else:
            diagnostics.append(
                _diag(
                    "E_SEM_INVALID_OPERATION_STATE",
                    f"unknown mk action: {op.action}",
                    op.span,
                    source_path,
                )
            )

        on_missing_allowed = op.action not in {"target-set", "target-append"}
        diagnostics.extend(
            _validate_on_missing(
                op.on_missing, on_missing_allowed, op.span, source_path
            )
        )
        return diagnostics

    if isinstance(op, FileOpNode):
        if op.action == "copy":
            if op.src is None or op.dst is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "file copy requires src and dst",
                        op.span,
                        source_path,
                    )
                )
            diagnostics.extend(
                _validate_on_missing(op.on_missing, False, op.span, source_path)
            )
        elif op.action == "remove":
            if op.path is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "file remove requires path",
                        op.span,
                        source_path,
                    )
                )
            diagnostics.extend(
                _validate_on_missing(op.on_missing, True, op.span, source_path)
            )
        else:
            diagnostics.append(
                _diag(
                    "E_SEM_INVALID_OPERATION_STATE",
                    f"unknown file action: {op.action}",
                    op.span,
                    source_path,
                )
            )
        return diagnostics

    if isinstance(op, TextOpNode):
        if not op.file_path:
            diagnostics.append(
                _diag(
                    "E_SEM_INVALID_OPERATION_STATE",
                    "text operation requires file path",
                    op.span,
                    source_path,
                )
            )

        if op.action == "line-remove":
            if op.exact is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "text line-remove requires exact string",
                        op.span,
                        source_path,
                    )
                )
        elif op.action == "line-insert-after":
            if op.anchor is None or op.line is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "text line-insert-after requires anchor and line",
                        op.span,
                        source_path,
                    )
                )
        elif op.action == "replace-once":
            if op.from_text is None or op.to_text is None:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_OPERATION_STATE",
                        "text replace-once requires from and to strings",
                        op.span,
                        source_path,
                    )
                )
        else:
            diagnostics.append(
                _diag(
                    "E_SEM_INVALID_OPERATION_STATE",
                    f"unknown text action: {op.action}",
                    op.span,
                    source_path,
                )
            )

        diagnostics.extend(
            _validate_on_missing(op.on_missing, True, op.span, source_path)
        )
        return diagnostics

    if isinstance(op, PatchOpNode):
        if not op.path:
            diagnostics.append(
                _diag(
                    "E_SEM_INVALID_OPERATION_STATE",
                    "patch apply requires path",
                    op.span,
                    source_path,
                )
            )

    return diagnostics


def analyze_document(
    document: AstDocument,
    source_path: Path | None = None,
) -> SemanticResult:
    """Run semantic validation and target scope resolution."""
    diagnostics: list[Diagnostic] = []
    scoped_ops: list[ScopedOperation] = []

    port_count = 0
    type_count = 0
    reason_count = 0
    maintainer_count = 0

    current_targets: tuple[str, ...] = ("@any",)

    for statement in document.statements:
        if isinstance(statement, TargetDirective):
            targets = statement.targets or (statement.target,)
            if "@any" in targets and len(targets) > 1:
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_TARGET_SCOPE",
                        "target directive cannot combine @any with explicit selectors",
                        statement.span,
                        source_path,
                    )
                )
            for target in targets:
                if is_scoped_target(target):
                    continue
                diagnostics.append(
                    _diag(
                        "E_SEM_INVALID_TARGET_SCOPE",
                        "target directive must be @any, @main, or @YYYYQ[1-4]",
                        statement.span,
                        source_path,
                    )
                )
            current_targets = tuple(targets)
            continue

        if isinstance(statement, PortDirective):
            port_count += 1
            if port_count > 1:
                diagnostics.append(
                    _diag(
                        "E_SEM_DUPLICATE_PORT",
                        "port directive appears more than once",
                        statement.span,
                        source_path,
                    )
                )
            continue

        if isinstance(statement, TypeDirective):
            type_count += 1
            if type_count > 1:
                diagnostics.append(
                    _diag(
                        "E_SEM_DUPLICATE_TYPE",
                        "type directive appears more than once",
                        statement.span,
                        source_path,
                    )
                )
            continue

        if isinstance(statement, ReasonDirective):
            reason_count += 1
            if reason_count > 1:
                diagnostics.append(
                    _diag(
                        "E_SEM_DUPLICATE_REASON",
                        "reason directive appears more than once",
                        statement.span,
                        source_path,
                    )
                )
            continue

        if isinstance(statement, MaintainerDirective):
            maintainer_count += 1
            if maintainer_count > 1:
                diagnostics.append(
                    _diag(
                        "E_SEM_DUPLICATE_MAINTAINER",
                        "maintainer directive appears more than once",
                        statement.span,
                        source_path,
                    )
                )
            continue

        operation = statement
        for target in current_targets:
            scoped_ops.append(ScopedOperation(target=target, operation=operation))

        diagnostics.extend(_validate_operation(operation, source_path))

    if port_count == 0:
        diagnostics.append(
            _diag(
                "E_SEM_MISSING_PORT",
                "exactly one port directive is required",
                document.span,
                source_path,
            )
        )

    return SemanticResult(
        ok=not diagnostics,
        diagnostics=diagnostics,
        document=document,
        scoped_ops=scoped_ops,
    )
