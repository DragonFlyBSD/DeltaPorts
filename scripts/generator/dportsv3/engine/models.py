"""Data models for the DeltaPorts v3 DSL engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Severity = Literal["error", "warning", "info"]
TokenKind = Literal[
    "WORD",
    "STRING",
    "ARROW",
    "HEREDOC_START",
    "HEREDOC_BODY",
    "NEWLINE",
    "EOF",
]


@dataclass(frozen=True)
class SourceSpan:
    """Source location span (1-based)."""

    line_start: int
    column_start: int
    line_end: int
    column_end: int


@dataclass(frozen=True)
class Token:
    """Single lexical token."""

    kind: TokenKind
    value: str
    span: SourceSpan


@dataclass(frozen=True)
class Diagnostic:
    """Structured diagnostic message for parse/check/plan stages."""

    severity: Severity
    code: str
    message: str
    source_path: str | None = None
    line: int | None = None
    column: int | None = None


@dataclass
class ParseResult:
    """Result from parser facade."""

    ok: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    ast: Any | None = None


@dataclass
class LexResult:
    """Result from lexer."""

    ok: bool
    tokens: list[Token] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class CheckResult:
    """Result from semantic checker facade."""

    ok: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class PlanOp:
    """Single normalized plan operation."""

    id: str
    target: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "target": self.target,
            "kind": self.kind,
        }
        data.update(self.payload)
        return data


@dataclass
class Plan:
    """Normalized in-memory plan model."""

    port: str
    type: str = "port"
    reason: str = ""
    maintainer: str = ""
    ops: list[PlanOp] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "port": self.port,
            "type": self.type,
            "reason": self.reason,
            "maintainer": self.maintainer,
            "ops": [op.to_dict() for op in self.ops],
        }


@dataclass
class PlanResult:
    """Result from planner facade."""

    ok: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    plan: Plan | None = None


@dataclass
class ApplyContext:
    """Execution context for apply stage."""

    port_root: Path
    target: str
    dry_run: bool = False
    strict: bool = False
    oracle_profile: str = "local"


@dataclass
class ApplyOpResult:
    """Per-operation apply result."""

    id: str
    kind: str
    target: str
    status: Literal["applied", "skipped", "failed"]
    message: str = ""
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "status": self.status,
            "message": self.message,
            "diagnostics": [
                {
                    "severity": d.severity,
                    "code": d.code,
                    "message": d.message,
                    "source_path": d.source_path,
                    "line": d.line,
                    "column": d.column,
                }
                for d in self.diagnostics
            ],
        }


@dataclass
class ApplyDiff:
    """Per-file diff artifact for apply preview output."""

    path: str
    change_type: Literal["modified", "created", "removed", "fallback_patch"]
    diff: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "change_type": self.change_type,
            "diff": self.diff,
        }


@dataclass
class ApplyResult:
    """Aggregate apply stage result."""

    ok: bool
    context: ApplyContext
    op_results: list[ApplyOpResult] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    diffs: list[ApplyDiff] = field(default_factory=list)
    oracle_profile: str = "local"
    oracle_checks: int = 0
    oracle_failures: int = 0
    oracle_skipped: int = 0

    @property
    def report_version(self) -> str:
        return "v1"

    @property
    def total_ops(self) -> int:
        return len(self.op_results)

    @property
    def applied_ops(self) -> int:
        return sum(1 for row in self.op_results if row.status == "applied")

    @property
    def skipped_ops(self) -> int:
        return sum(1 for row in self.op_results if row.status == "skipped")

    @property
    def failed_ops(self) -> int:
        return sum(1 for row in self.op_results if row.status == "failed")

    @property
    def warning_count(self) -> int:
        all_diags = [*self.diagnostics]
        for row in self.op_results:
            all_diags.extend(row.diagnostics)
        return sum(1 for d in all_diags if d.severity == "warning")

    @property
    def error_count(self) -> int:
        all_diags = [*self.diagnostics]
        for row in self.op_results:
            all_diags.extend(row.diagnostics)
        return sum(1 for d in all_diags if d.severity == "error")

    @property
    def fallback_patch_count(self) -> int:
        return sum(
            1
            for row in self.op_results
            if row.kind == "patch.apply" and row.status != "skipped"
        )

    @property
    def report(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "total_ops": self.total_ops,
            "applied_ops": self.applied_ops,
            "skipped_ops": self.skipped_ops,
            "failed_ops": self.failed_ops,
            "warnings": self.warning_count,
            "errors": self.error_count,
            "fallback_patch_count": self.fallback_patch_count,
            "oracle_profile": self.oracle_profile,
            "oracle_checks": self.oracle_checks,
            "oracle_failures": self.oracle_failures,
            "oracle_skipped": self.oracle_skipped,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "context": {
                "port_root": str(self.context.port_root),
                "target": self.context.target,
                "dry_run": self.context.dry_run,
                "strict": self.context.strict,
                "oracle_profile": self.context.oracle_profile,
            },
            "summary": {
                "total_ops": self.total_ops,
                "applied_ops": self.applied_ops,
                "skipped_ops": self.skipped_ops,
                "failed_ops": self.failed_ops,
                "warning_count": self.warning_count,
                "error_count": self.error_count,
                "fallback_patch_count": self.fallback_patch_count,
                "oracle_checks": self.oracle_checks,
                "oracle_failures": self.oracle_failures,
                "oracle_skipped": self.oracle_skipped,
            },
            "report": self.report,
            "diagnostics": [
                {
                    "severity": d.severity,
                    "code": d.code,
                    "message": d.message,
                    "source_path": d.source_path,
                    "line": d.line,
                    "column": d.column,
                }
                for d in self.diagnostics
            ],
            "op_results": [row.to_dict() for row in self.op_results],
            "diffs": [entry.to_dict() for entry in self.diffs],
        }


def diagnostic_not_implemented(stage: str, source_path: Path | None) -> Diagnostic:
    """Create standardized not-implemented diagnostic."""
    return Diagnostic(
        severity="error",
        code="E_NOT_IMPLEMENTED",
        message=f"{stage} is not implemented yet",
        source_path=str(source_path) if source_path is not None else None,
    )
