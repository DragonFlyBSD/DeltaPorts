from __future__ import annotations

from dportsv3.engine.makefile_cst import (
    AssignmentNode,
    DirectiveElseNode,
    DirectiveElifNode,
    DirectiveEndifNode,
    DirectiveIfNode,
    IncludeNode,
    RecipeLineNode,
    TargetNode,
    parse_makefile_cst,
    render_makefile,
)
from tests.dportsv3_testutils import read_text_fixture


def test_parse_simple_assignments_and_indexes() -> None:
    text = read_text_fixture("makefile/simple.mk")
    result = parse_makefile_cst(text)

    assert result.ok
    assert result.document is not None
    assert isinstance(result.document.nodes[0], AssignmentNode)
    assert isinstance(result.document.nodes[1], AssignmentNode)
    assert result.document.assignment_index["PORTNAME"] == [0]
    assert result.document.assignment_index["USES"] == [1]


def test_parse_continuation_assignment_preserves_lines() -> None:
    text = read_text_fixture("makefile/continuation.mk")
    result = parse_makefile_cst(text)

    assert result.ok
    assert result.document is not None
    node = result.document.nodes[0]
    assert isinstance(node, AssignmentNode)
    assert node.continued is True
    assert len(node.raw_lines) == 3
    assert render_makefile(result.document) == text


def test_parse_target_and_recipe_nodes() -> None:
    text = read_text_fixture("makefile/target_recipe.mk")
    result = parse_makefile_cst(text)

    assert result.ok
    assert result.document is not None
    assert isinstance(result.document.nodes[0], TargetNode)
    assert isinstance(result.document.nodes[1], RecipeLineNode)
    assert isinstance(result.document.nodes[2], RecipeLineNode)
    assert result.document.target_index["dfly-patch"] == [0]
    assert result.document.nodes[1].raw_text.startswith("\t")


def test_parse_conditionals_and_directive_regions() -> None:
    text = read_text_fixture("makefile/conditional.mk")
    result = parse_makefile_cst(text)

    assert result.ok
    assert result.document is not None
    assert any(isinstance(node, DirectiveIfNode) for node in result.document.nodes)
    assert any(isinstance(node, DirectiveElifNode) for node in result.document.nodes)
    assert any(isinstance(node, DirectiveElseNode) for node in result.document.nodes)
    assert any(isinstance(node, DirectiveEndifNode) for node in result.document.nodes)
    assert len(result.document.directive_regions) == 1


def test_parse_include_nodes() -> None:
    text = read_text_fixture("makefile/include.mk")
    result = parse_makefile_cst(text)

    assert result.ok
    assert result.document is not None
    include_nodes = [n for n in result.document.nodes if isinstance(n, IncludeNode)]
    assert len(include_nodes) == 2
    assert "bsd.port.pre.mk" in include_nodes[0].include
    assert "bsd.port.post.mk" in include_nodes[1].include


def test_parse_unbalanced_directive_reports_error() -> None:
    text = ".if ${OPSYS} == DragonFly\nPORTNAME= a\n"
    result = parse_makefile_cst(text)

    assert not result.ok
    assert any(d.code == "E_MKPARSE_UNBALANCED_DIRECTIVE" for d in result.diagnostics)


def test_parse_continuation_eof_reports_error() -> None:
    text = "USES+= a \\\n"
    result = parse_makefile_cst(text)

    assert not result.ok
    assert any(d.code == "E_MKPARSE_CONTINUATION_EOF" for d in result.diagnostics)


def test_render_preserves_input_exactly() -> None:
    text = read_text_fixture("makefile/conditional.mk")
    result = parse_makefile_cst(text)

    assert result.document is not None
    assert render_makefile(result.document) == text


def test_node_spans_present_and_stable() -> None:
    text = read_text_fixture("makefile/simple.mk")
    result = parse_makefile_cst(text)

    assert result.document is not None
    first = result.document.nodes[0]
    assert first.span.line_start == 1
    assert first.span.column_start == 1
    assert first.span.line_end == 1
