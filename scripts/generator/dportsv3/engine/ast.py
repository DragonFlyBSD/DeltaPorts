"""AST node models for DeltaPorts v3 DSL parser."""

from __future__ import annotations

from dataclasses import dataclass

from dportsv3.engine.models import SourceSpan


@dataclass(frozen=True)
class AstNode:
    """Base AST node."""

    span: SourceSpan


@dataclass(frozen=True)
class TargetDirective(AstNode):
    target: str
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class PortDirective(AstNode):
    origin: str


@dataclass(frozen=True)
class TypeDirective(AstNode):
    port_type: str


@dataclass(frozen=True)
class ReasonDirective(AstNode):
    reason: str


@dataclass(frozen=True)
class MaintainerDirective(AstNode):
    maintainer: str


DirectiveNode = (
    TargetDirective
    | PortDirective
    | TypeDirective
    | ReasonDirective
    | MaintainerDirective
)


@dataclass(frozen=True)
class MkOpNode(AstNode):
    action: str
    var: str | None = None
    value: str | None = None
    token: str | None = None
    condition: str | None = None
    condition_from: str | None = None
    condition_to: str | None = None
    contains: str | None = None
    name: str | None = None
    old_name: str | None = None
    new_name: str | None = None
    heredoc_tag: str | None = None
    recipe: str | None = None
    on_missing: str | None = None


@dataclass(frozen=True)
class FileOpNode(AstNode):
    action: str
    src: str | None = None
    dst: str | None = None
    path: str | None = None
    on_missing: str | None = None


@dataclass(frozen=True)
class TextOpNode(AstNode):
    action: str
    file_path: str
    exact: str | None = None
    anchor: str | None = None
    line: str | None = None
    from_text: str | None = None
    to_text: str | None = None
    on_missing: str | None = None


@dataclass(frozen=True)
class PatchOpNode(AstNode):
    path: str


OperationNode = MkOpNode | FileOpNode | TextOpNode | PatchOpNode
StatementNode = DirectiveNode | OperationNode


@dataclass(frozen=True)
class AstDocument(AstNode):
    statements: list[StatementNode]
