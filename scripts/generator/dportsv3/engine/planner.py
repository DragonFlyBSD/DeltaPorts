"""Plan compiler for DeltaPorts v3 DSL."""

from __future__ import annotations

from pathlib import Path

from dportsv3.engine.ast import (
    AstDocument,
    FileOpNode,
    MaintainerDirective,
    MkOpNode,
    OperationNode,
    PatchOpNode,
    PortDirective,
    ReasonDirective,
    TextOpNode,
    TypeDirective,
)
from dportsv3.engine.models import Diagnostic, Plan, PlanOp, PlanResult, SourceSpan
from dportsv3.engine.semantic import ScopedOperation


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


def _recipe_lines(recipe: str | None) -> list[str]:
    if recipe is None:
        return []
    return recipe.splitlines()


def _with_on_missing(
    payload: dict[str, object], on_missing: str | None
) -> dict[str, object]:
    if on_missing is not None:
        payload["on_missing"] = on_missing
    return payload


def _map_operation(
    op: OperationNode,
    source_path: Path | None,
) -> tuple[str | None, dict[str, object], list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []

    if isinstance(op, MkOpNode):
        if op.action == "set":
            if op.var is None or op.value is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "mk set requires var and value",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return (
                "mk.var.set",
                _with_on_missing({"name": op.var, "value": op.value}, op.on_missing),
                diagnostics,
            )

        if op.action == "unset":
            if op.var is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "mk unset requires var",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return (
                "mk.var.unset",
                _with_on_missing({"name": op.var}, op.on_missing),
                diagnostics,
            )

        if op.action in {"add", "remove"}:
            if op.var is None or op.token is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        f"mk {op.action} requires var and token",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            kind = "mk.var.token_add" if op.action == "add" else "mk.var.token_remove"
            return (
                kind,
                _with_on_missing({"name": op.var, "value": op.token}, op.on_missing),
                diagnostics,
            )

        if op.action == "disable-if":
            if op.condition is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "mk disable-if requires condition",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            payload: dict[str, object] = {"condition": op.condition}
            if op.contains is not None:
                payload["contains"] = op.contains
            return (
                "mk.block.disable",
                _with_on_missing(payload, op.on_missing),
                diagnostics,
            )

        if op.action == "replace-if":
            if op.condition_from is None or op.condition_to is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "mk replace-if requires from and to conditions",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            payload = {
                "from": op.condition_from,
                "to": op.condition_to,
            }
            if op.contains is not None:
                payload["contains"] = op.contains
            return (
                "mk.block.replace_condition",
                _with_on_missing(payload, op.on_missing),
                diagnostics,
            )

        if op.action == "block-set":
            if op.condition is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "mk block set requires condition",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            payload = {
                "condition": op.condition,
                "recipe": _recipe_lines(op.recipe),
            }
            if op.contains is not None:
                payload["contains"] = op.contains
            if op.heredoc_tag is not None:
                payload["heredoc_tag"] = op.heredoc_tag
            return "mk.block.set", payload, diagnostics

        if op.action in {"target-set", "target-append"}:
            if op.name is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "mk target set/append requires target name",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            kind = "mk.target.set" if op.action == "target-set" else "mk.target.append"
            payload = {
                "name": op.name,
                "recipe": _recipe_lines(op.recipe),
            }
            if op.heredoc_tag is not None:
                payload["heredoc_tag"] = op.heredoc_tag
            return kind, payload, diagnostics

        if op.action == "target-remove":
            if op.name is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "mk target remove requires target name",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return (
                "mk.target.remove",
                _with_on_missing({"name": op.name}, op.on_missing),
                diagnostics,
            )

        if op.action == "target-rename":
            if op.old_name is None or op.new_name is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "mk target rename requires old and new names",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return (
                "mk.target.rename",
                _with_on_missing(
                    {"old": op.old_name, "new": op.new_name}, op.on_missing
                ),
                diagnostics,
            )

        diagnostics.append(
            _diag(
                "E_PLAN_UNSUPPORTED_ACTION",
                f"unsupported mk action: {op.action}",
                op.span,
                source_path,
            )
        )
        return None, {}, diagnostics

    if isinstance(op, FileOpNode):
        if op.action == "copy":
            if op.src is None or op.dst is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "file copy requires src and dst",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return "file.copy", {"src": op.src, "dst": op.dst}, diagnostics
        if op.action == "remove":
            if op.path is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "file remove requires path",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return (
                "file.remove",
                _with_on_missing({"path": op.path}, op.on_missing),
                diagnostics,
            )
        diagnostics.append(
            _diag(
                "E_PLAN_UNSUPPORTED_ACTION",
                f"unsupported file action: {op.action}",
                op.span,
                source_path,
            )
        )
        return None, {}, diagnostics

    if isinstance(op, TextOpNode):
        if op.action == "line-remove":
            if op.exact is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "text line-remove requires exact string",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return (
                "text.line_remove",
                _with_on_missing(
                    {"file": op.file_path, "exact": op.exact}, op.on_missing
                ),
                diagnostics,
            )
        if op.action == "line-insert-after":
            if op.anchor is None or op.line is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "text line-insert-after requires anchor and line",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return (
                "text.line_insert_after",
                _with_on_missing(
                    {"file": op.file_path, "anchor": op.anchor, "line": op.line},
                    op.on_missing,
                ),
                diagnostics,
            )
        if op.action == "replace-once":
            if op.from_text is None or op.to_text is None:
                diagnostics.append(
                    _diag(
                        "E_PLAN_INVALID_OPERATION",
                        "text replace-once requires from and to strings",
                        op.span,
                        source_path,
                    )
                )
                return None, {}, diagnostics
            return (
                "text.replace_once",
                _with_on_missing(
                    {"file": op.file_path, "from": op.from_text, "to": op.to_text},
                    op.on_missing,
                ),
                diagnostics,
            )
        diagnostics.append(
            _diag(
                "E_PLAN_UNSUPPORTED_ACTION",
                f"unsupported text action: {op.action}",
                op.span,
                source_path,
            )
        )
        return None, {}, diagnostics

    if isinstance(op, PatchOpNode):
        if not op.path:
            diagnostics.append(
                _diag(
                    "E_PLAN_INVALID_OPERATION",
                    "patch apply requires path",
                    op.span,
                    source_path,
                )
            )
            return None, {}, diagnostics
        return "patch.apply", {"path": op.path}, diagnostics

    diagnostics.append(
        _diag(
            "E_PLAN_UNSUPPORTED_ACTION",
            "unsupported operation node",
            op.span,
            source_path,
        )
    )
    return None, {}, diagnostics


def compile_plan(
    document: AstDocument,
    scoped_ops: list[ScopedOperation],
    source_path: Path | None = None,
) -> PlanResult:
    """Compile semantic output to normalized in-memory plan."""
    diagnostics: list[Diagnostic] = []

    port: str | None = None
    plan_type = "port"
    reason = ""
    maintainer = ""

    for statement in document.statements:
        if isinstance(statement, PortDirective) and port is None:
            port = statement.origin
        elif isinstance(statement, TypeDirective) and plan_type == "port":
            plan_type = statement.port_type
        elif isinstance(statement, ReasonDirective) and reason == "":
            reason = statement.reason
        elif isinstance(statement, MaintainerDirective) and maintainer == "":
            maintainer = statement.maintainer

    if port is None:
        diagnostics.append(
            _diag(
                "E_PLAN_METADATA_MISSING",
                "plan metadata missing required port directive",
                document.span,
                source_path,
            )
        )
        return PlanResult(ok=False, diagnostics=diagnostics, plan=None)

    ops: list[PlanOp] = []

    for idx, scoped in enumerate(scoped_ops, start=1):
        kind, payload, op_diags = _map_operation(scoped.operation, source_path)
        if op_diags:
            diagnostics.extend(op_diags)
            continue
        if kind is None:
            diagnostics.append(
                _diag(
                    "E_PLAN_INVALID_OPERATION",
                    "failed to map operation",
                    scoped.operation.span,
                    source_path,
                )
            )
            continue

        op_id = f"op-{idx:04d}-{kind.replace('.', '-')}"
        ops.append(
            PlanOp(
                id=op_id,
                target=scoped.target,
                kind=kind,
                payload=payload,
            )
        )

    if diagnostics:
        return PlanResult(ok=False, diagnostics=diagnostics, plan=None)

    return PlanResult(
        ok=True,
        diagnostics=[],
        plan=Plan(
            port=port,
            type=plan_type,
            reason=reason,
            maintainer=maintainer,
            ops=ops,
        ),
    )
