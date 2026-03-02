"""Facade APIs for the DeltaPorts v3 DSL engine."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from dportsv3.engine.ast import AstDocument
from dportsv3.engine.apply import apply_plan
from dportsv3.engine.lexer import lex_dsl
from dportsv3.engine.parser import parse_tokens
from dportsv3.engine.planner import compile_plan
from dportsv3.engine.semantic import analyze_document
from dportsv3.engine.models import (
    ApplyContext,
    ApplyResult,
    CheckResult,
    Diagnostic,
    LexResult,
    ParseResult,
    PlanResult,
)


def parse_dsl(text: str, source_path: Path | None = None) -> ParseResult:
    """Parse DSL source into an AST (lexer + parser pipeline)."""
    lexed: LexResult = lex_dsl(text, source_path)
    if not lexed.ok:
        return ParseResult(
            ok=False,
            diagnostics=lexed.diagnostics,
            ast=lexed.tokens,
        )

    return parse_tokens(lexed.tokens, source_path)


def check_dsl(text: str, source_path: Path | None = None) -> CheckResult:
    """Run syntax+semantic checks."""
    lexed: LexResult = lex_dsl(text, source_path)
    if not lexed.ok:
        return CheckResult(ok=False, diagnostics=lexed.diagnostics)

    parsed = parse_tokens(lexed.tokens, source_path)
    if not parsed.ok:
        return CheckResult(ok=False, diagnostics=parsed.diagnostics)

    if not isinstance(parsed.ast, AstDocument):
        diagnostic = Diagnostic(
            severity="error",
            code="E_SEM_INVALID_OPERATION_STATE",
            message="semantic check requires parsed AstDocument",
            source_path=str(source_path) if source_path is not None else None,
            line=None,
            column=None,
        )
        return CheckResult(ok=False, diagnostics=[diagnostic])

    ast_doc = cast(AstDocument, parsed.ast)
    analyzed = analyze_document(ast_doc, source_path)
    if not analyzed.ok:
        return CheckResult(ok=False, diagnostics=analyzed.diagnostics)

    return CheckResult(ok=True, diagnostics=[])


def build_plan(text: str, source_path: Path | None = None) -> PlanResult:
    """Build normalized in-memory plan."""
    lexed: LexResult = lex_dsl(text, source_path)
    if not lexed.ok:
        return PlanResult(ok=False, diagnostics=lexed.diagnostics, plan=None)

    parsed = parse_tokens(lexed.tokens, source_path)
    if not parsed.ok:
        return PlanResult(ok=False, diagnostics=parsed.diagnostics, plan=None)

    if not isinstance(parsed.ast, AstDocument):
        diagnostic = Diagnostic(
            severity="error",
            code="E_PLAN_INVALID_OPERATION",
            message="planner requires parsed AstDocument",
            source_path=str(source_path) if source_path is not None else None,
            line=None,
            column=None,
        )
        return PlanResult(ok=False, diagnostics=[diagnostic], plan=None)

    ast_doc = cast(AstDocument, parsed.ast)
    analyzed = analyze_document(ast_doc, source_path)
    if not analyzed.ok:
        return PlanResult(ok=False, diagnostics=analyzed.diagnostics, plan=None)

    if analyzed.document is None:
        diagnostic = Diagnostic(
            severity="error",
            code="E_PLAN_METADATA_MISSING",
            message="planner requires semantic document",
            source_path=str(source_path) if source_path is not None else None,
            line=None,
            column=None,
        )
        return PlanResult(ok=False, diagnostics=[diagnostic], plan=None)

    return compile_plan(
        cast(AstDocument, analyzed.document), analyzed.scoped_ops, source_path
    )


def apply_dsl(
    text: str,
    *,
    source_path: Path | None = None,
    port_root: Path,
    target: str,
    dry_run: bool = False,
    strict: bool = False,
    emit_diff: bool = False,
    oracle_profile: str = "local",
) -> ApplyResult:
    """Parse/check/plan/apply pipeline entrypoint."""
    planned = build_plan(text, source_path)
    source_root = source_path.parent if source_path is not None else port_root
    context = ApplyContext(
        source_root=source_root,
        port_root=port_root,
        target=target,
        dry_run=dry_run,
        strict=strict,
        oracle_profile=oracle_profile,
    )
    if not planned.ok or planned.plan is None:
        return ApplyResult(
            ok=False,
            context=context,
            op_results=[],
            diagnostics=planned.diagnostics,
            oracle_profile=oracle_profile,
        )

    return apply_plan(
        planned.plan,
        source_root=source_root,
        port_root=port_root,
        target=target,
        dry_run=dry_run,
        strict=strict,
        emit_diff=emit_diff,
        oracle_profile=oracle_profile,
    )
