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


def test_assignment_name_with_trailing_plus_is_parsed() -> None:
    # FreeBSD license-tag-with-`+` idiom (`bsd.licenses.mk`): the
    # variable name itself ends in `+` (the literal tag character of
    # GPLv2+/GPLv3+/LGPL3+/AGPL3+ licenses), and the assignment uses
    # `<name>+ = value` — a space before `=` disambiguates from `+=`.
    # 292 upstream FreeBSD ports use this shape; before this was
    # parsed, every `mk.*` op (and the deterministic converter) blew
    # up at compose time with E_APPLY_PARSE_FAILED on those ports.
    text = "LICENSE_FILE_LGPL3+ =\t${WRKSRC}/COPYING.LIB\n"
    result = parse_makefile_cst(text)
    assert result.ok, [d.message for d in result.diagnostics]
    assert isinstance(result.document.nodes[0], AssignmentNode)
    node = result.document.nodes[0]
    assert node.name == "LICENSE_FILE_LGPL3+"
    assert node.operator == "="
    assert node.value == "${WRKSRC}/COPYING.LIB"


def test_plus_equals_still_parses_as_append_not_trailing_plus() -> None:
    # Regression guard: a naive fix that just adds `+` to the
    # name-char class silently re-binds `CXXFLAGS+= -Wall` to
    # (name=`CXXFLAGS+`, op=`=`). The negative lookahead in
    # `_ASSIGN_RE` prevents that — `+` joins the name only when not
    # immediately followed by `=`.
    text = "CXXFLAGS+= -Wall\n"
    result = parse_makefile_cst(text)
    assert result.ok
    node = result.document.nodes[0]
    assert isinstance(node, AssignmentNode)
    assert node.name == "CXXFLAGS"
    assert node.operator == "+="
    assert node.value == "-Wall"


def test_node_spans_present_and_stable() -> None:
    text = read_text_fixture("makefile/simple.mk")
    result = parse_makefile_cst(text)

    assert result.document is not None
    first = result.document.nodes[0]
    assert first.span.line_start == 1
    assert first.span.column_start == 1
    assert first.span.line_end == 1


def test_dot_directive_whitespace_after_dot() -> None:
    # BSD make allows whitespace between the leading '.' and the keyword
    # (".  if", ".    elif", indented nests, slave-port ladders). These
    # must parse as directives, not fall through to E_MKPARSE_INVALID_
    # ASSIGNMENT because their condition contains "==".
    text = (
        ".  if ${_SLAVE_PORT} == glib\n"
        "USES=\tfoo\n"
        ".    elif ${ARCHDEF} == \"NONE\"\n"
        "USES=\tbar\n"
        ". else\n"
        "USES=\tbaz\n"
        ".  endif\n"
    )
    result = parse_makefile_cst(text)
    assert result.diagnostics == []
    kinds = [type(n).__name__ for n in result.document.nodes]
    assert kinds.count("DirectiveIfNode") == 1
    assert kinds.count("DirectiveElifNode") == 1
    assert kinds.count("DirectiveElseNode") == 1
    assert kinds.count("DirectiveEndifNode") == 1
    # condition strips the dot+whitespace+keyword, leaving just the expr
    ifnode = next(n for n in result.document.nodes if type(n).__name__ == "DirectiveIfNode")
    assert ifnode.condition == "${_SLAVE_PORT} == glib"
    # rendering is lossless — original spacing preserved
    assert render_makefile(result.document) == text


def test_unmodeled_dot_directive_is_raw_not_assignment() -> None:
    # dot-directives we don't model (.undef, .MAKEFLAGS, .for, ...) must
    # be preserved as raw lines, never raise E_MKPARSE_INVALID_ASSIGNMENT
    # even when they contain '=' (.undef FOO= bar, .MAKEFLAGS: X=y).
    text = (
        "PORTNAME=\tfoo\n"
        ".undef WITHOUT_DOCS=\ttrue\n"
        '.MAKEFLAGS:\tWITH="${OPTIONS_DEFINE}" EXCLUDE=\n'
        "USES=\tgmake\n"
    )
    result = parse_makefile_cst(text)
    assert result.diagnostics == []
    assert render_makefile(result.document) == text
    # the two real assignments still parse as assignments
    assigns = [n for n in result.document.nodes if isinstance(n, AssignmentNode)]
    assert {a.name for a in assigns} == {"PORTNAME", "USES"}
