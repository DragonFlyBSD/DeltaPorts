"""BSD Makefile CST-lite runtime parser."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re

from dportsv3.engine.models import Diagnostic, SourceSpan

_ASSIGN_RE = re.compile(r"^\s*([A-Za-z0-9_.$(){}\-/]+)\s*(\+=|\?=|:=|!=|=)\s*(.*)$")
_TARGET_RE = re.compile(r"^\s*([^\s:#][^:]*)\s*:\s*(.*)$")


@dataclass(frozen=True)
class MakefileNode:
    """Base node for Makefile CST-lite nodes."""

    span: SourceSpan
    raw_lines: list[str]

    @property
    def raw_text(self) -> str:
        return "".join(self.raw_lines)


@dataclass(frozen=True)
class AssignmentNode(MakefileNode):
    name: str
    operator: str
    value: str
    continued: bool


@dataclass(frozen=True)
class DirectiveIfNode(MakefileNode):
    condition: str


@dataclass(frozen=True)
class DirectiveElifNode(MakefileNode):
    condition: str


@dataclass(frozen=True)
class DirectiveElseNode(MakefileNode):
    pass


@dataclass(frozen=True)
class DirectiveEndifNode(MakefileNode):
    pass


@dataclass(frozen=True)
class TargetNode(MakefileNode):
    name: str
    deps: str


@dataclass(frozen=True)
class RecipeLineNode(MakefileNode):
    text: str


@dataclass(frozen=True)
class IncludeNode(MakefileNode):
    include: str


@dataclass(frozen=True)
class RawLineNode(MakefileNode):
    text: str


Node = (
    AssignmentNode
    | DirectiveIfNode
    | DirectiveElifNode
    | DirectiveElseNode
    | DirectiveEndifNode
    | TargetNode
    | RecipeLineNode
    | IncludeNode
    | RawLineNode
)


@dataclass(frozen=True)
class MakefileParseResult:
    """Result of Makefile CST parsing."""

    ok: bool
    document: "MakefileDocument | None"
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class MakefileDocument:
    """Parsed Makefile CST document with indexes."""

    source_path: Path | None
    text: str
    nodes: list[Node]
    assignment_index: dict[str, list[int]] = field(default_factory=dict)
    target_index: dict[str, list[int]] = field(default_factory=dict)
    directive_regions: list[tuple[int, int]] = field(default_factory=list)

    def build_indexes(self) -> None:
        self.assignment_index.clear()
        self.target_index.clear()
        self.directive_regions.clear()

        stack: list[int] = []
        for idx, node in enumerate(self.nodes):
            if isinstance(node, AssignmentNode):
                self.assignment_index.setdefault(node.name, []).append(idx)
            elif isinstance(node, TargetNode):
                self.target_index.setdefault(node.name, []).append(idx)
            elif isinstance(node, DirectiveIfNode):
                stack.append(idx)
            elif isinstance(node, DirectiveEndifNode) and stack:
                start = stack.pop()
                self.directive_regions.append((start, idx))

    def render(self) -> str:
        return "".join(node.raw_text for node in self.nodes)


def render_makefile(document: MakefileDocument) -> str:
    """Render Makefile text from CST document."""
    return document.render()


def _diag(
    *,
    code: str,
    message: str,
    source_path: Path | None,
    line: int,
    column: int,
) -> Diagnostic:
    return Diagnostic(
        severity="error",
        code=code,
        message=message,
        source_path=str(source_path) if source_path is not None else None,
        line=line,
        column=column,
    )


def _span(line_start: int, line_end: int, line_lengths: list[int]) -> SourceSpan:
    start_len = (
        line_lengths[line_start - 1] if 0 < line_start <= len(line_lengths) else 0
    )
    end_len = line_lengths[line_end - 1] if 0 < line_end <= len(line_lengths) else 0
    return SourceSpan(
        line_start=line_start,
        column_start=1,
        line_end=line_end,
        column_end=max(1, end_len),
    )


def _strip_newline(line: str) -> str:
    return line[:-1] if line.endswith("\n") else line


def _continued(line_no_nl: str) -> bool:
    stripped = line_no_nl.rstrip(" \t\r")
    return stripped.endswith("\\")


def _consume_logical(
    lines: list[str],
    start: int,
) -> tuple[list[str], int, bool]:
    group: list[str] = []
    idx = start
    continuation_open = False

    while idx < len(lines):
        line = lines[idx]
        group.append(line)
        idx += 1
        continuation_open = _continued(_strip_newline(line))
        if not continuation_open:
            return group, idx, False

    return group, idx, continuation_open


def _make_raw_node(
    *,
    group: list[str],
    line_start: int,
    line_end: int,
    line_lengths: list[int],
) -> RawLineNode:
    text = "".join(group)
    return RawLineNode(
        span=_span(line_start, line_end, line_lengths),
        raw_lines=list(group),
        text=text,
    )


def parse_makefile_cst(
    text: str,
    source_path: Path | None = None,
) -> MakefileParseResult:
    """Parse BSD Makefile text to CST-lite structure."""
    lines = text.splitlines(keepends=True)
    line_lengths = [len(_strip_newline(line)) for line in lines]
    diagnostics: list[Diagnostic] = []
    nodes: list[Node] = []

    idx = 0
    directive_stack: list[int] = []

    while idx < len(lines):
        line_number = idx + 1
        current = lines[idx]

        if current.startswith("\t"):
            line_text = _strip_newline(current)
            nodes.append(
                RecipeLineNode(
                    span=_span(line_number, line_number, line_lengths),
                    raw_lines=[current],
                    text=line_text,
                )
            )
            idx += 1
            continue

        group, next_idx, continuation_eof = _consume_logical(lines, idx)
        logical = "".join(group)
        first_line = _strip_newline(group[0])
        logical_stripped = first_line.strip()
        line_start = idx + 1
        line_end = next_idx
        idx = next_idx

        if continuation_eof:
            diagnostics.append(
                _diag(
                    code="E_MKPARSE_CONTINUATION_EOF",
                    message="line continuation reaches end-of-file",
                    source_path=source_path,
                    line=line_start,
                    column=1,
                )
            )

        if not logical_stripped or logical_stripped.startswith("#"):
            nodes.append(
                _make_raw_node(
                    group=group,
                    line_start=line_start,
                    line_end=line_end,
                    line_lengths=line_lengths,
                )
            )
            continue

        if logical_stripped.startswith(".if"):
            condition = logical_stripped[3:].strip()
            nodes.append(
                DirectiveIfNode(
                    span=_span(line_start, line_end, line_lengths),
                    raw_lines=list(group),
                    condition=condition,
                )
            )
            directive_stack.append(line_start)
            continue

        if logical_stripped.startswith(".elif"):
            condition = logical_stripped[5:].strip()
            nodes.append(
                DirectiveElifNode(
                    span=_span(line_start, line_end, line_lengths),
                    raw_lines=list(group),
                    condition=condition,
                )
            )
            if not directive_stack:
                diagnostics.append(
                    _diag(
                        code="E_MKPARSE_UNBALANCED_DIRECTIVE",
                        message=".elif without matching .if",
                        source_path=source_path,
                        line=line_start,
                        column=1,
                    )
                )
            continue

        if logical_stripped.startswith(".else"):
            nodes.append(
                DirectiveElseNode(
                    span=_span(line_start, line_end, line_lengths),
                    raw_lines=list(group),
                )
            )
            if not directive_stack:
                diagnostics.append(
                    _diag(
                        code="E_MKPARSE_UNBALANCED_DIRECTIVE",
                        message=".else without matching .if",
                        source_path=source_path,
                        line=line_start,
                        column=1,
                    )
                )
            continue

        if logical_stripped.startswith(".endif"):
            nodes.append(
                DirectiveEndifNode(
                    span=_span(line_start, line_end, line_lengths),
                    raw_lines=list(group),
                )
            )
            if directive_stack:
                directive_stack.pop()
            else:
                diagnostics.append(
                    _diag(
                        code="E_MKPARSE_UNBALANCED_DIRECTIVE",
                        message=".endif without matching .if",
                        source_path=source_path,
                        line=line_start,
                        column=1,
                    )
                )
            continue

        if logical_stripped.startswith(".include"):
            include_arg = logical_stripped[len(".include") :].strip()
            nodes.append(
                IncludeNode(
                    span=_span(line_start, line_end, line_lengths),
                    raw_lines=list(group),
                    include=include_arg,
                )
            )
            continue

        assignment = _ASSIGN_RE.match(first_line)
        if assignment:
            name, operator, first_value = assignment.groups()
            value = first_value.rstrip("\n")
            if len(group) > 1:
                continuation_tail = "\n".join(
                    _strip_newline(part) for part in group[1:]
                )
                if continuation_tail:
                    value = value + "\n" + continuation_tail
            nodes.append(
                AssignmentNode(
                    span=_span(line_start, line_end, line_lengths),
                    raw_lines=list(group),
                    name=name,
                    operator=operator,
                    value=value,
                    continued=len(group) > 1,
                )
            )
            continue

        target = _TARGET_RE.match(first_line)
        if target and not logical_stripped.startswith("."):
            name, deps = target.groups()
            if not name.strip():
                diagnostics.append(
                    _diag(
                        code="E_MKPARSE_INVALID_TARGET",
                        message="target label is empty",
                        source_path=source_path,
                        line=line_start,
                        column=1,
                    )
                )
                nodes.append(
                    _make_raw_node(
                        group=group,
                        line_start=line_start,
                        line_end=line_end,
                        line_lengths=line_lengths,
                    )
                )
                continue

            nodes.append(
                TargetNode(
                    span=_span(line_start, line_end, line_lengths),
                    raw_lines=list(group),
                    name=name.strip(),
                    deps=deps.rstrip("\n"),
                )
            )
            continue

        if any(op in first_line for op in ["+=", "?=", ":=", "!=", "="]):
            diagnostics.append(
                _diag(
                    code="E_MKPARSE_INVALID_ASSIGNMENT",
                    message="line looks like assignment but could not be parsed",
                    source_path=source_path,
                    line=line_start,
                    column=1,
                )
            )

        nodes.append(
            _make_raw_node(
                group=group,
                line_start=line_start,
                line_end=line_end,
                line_lengths=line_lengths,
            )
        )

    for start_line in directive_stack:
        diagnostics.append(
            _diag(
                code="E_MKPARSE_UNBALANCED_DIRECTIVE",
                message=".if block missing matching .endif",
                source_path=source_path,
                line=start_line,
                column=1,
            )
        )

    document = MakefileDocument(source_path=source_path, text=text, nodes=nodes)
    document.build_indexes()

    return MakefileParseResult(
        ok=not diagnostics, document=document, diagnostics=diagnostics
    )
