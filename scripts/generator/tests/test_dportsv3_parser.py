from __future__ import annotations

from pathlib import Path

from dportsv3.engine.api import parse_dsl
from dportsv3.engine.ast import AstDocument, MkOpNode, ReasonDirective, TargetDirective


def test_parse_valid_directives_and_operations() -> None:
    text = (
        "target @main\n"
        "port category/name\n"
        "type port\n"
        'reason "hello"\n'
        'maintainer "dev@example.com"\n'
        'mk set VAR "value"\n'
        "mk unset VAR2 on-missing warn\n"
        "mk add USES ssl\n"
        "mk remove USES linux\n"
        'mk disable-if condition "A" contains "B" on-missing noop\n'
        'mk replace-if from "A" to "B" contains "C"\n'
        'mk block set condition "defined(LITE)" contains "LITE" <<\'BLK\'\n'
        "\tPORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        "BLK\n"
        "mk target set dfly-patch <<'MK'\n"
        "\tcmd\n"
        "MK\n"
        "mk target append dfly-patch <<'APP'\n"
        "\tcmd2\n"
        "APP\n"
        "mk target remove dfly-patch on-missing error\n"
        "mk target rename old -> new on-missing warn\n"
        "file copy a -> b\n"
        "file remove c on-missing noop\n"
        'text line-remove file Makefile exact "x"\n'
        'text line-insert-after file Makefile anchor "a" line "b"\n'
        'text replace-once file Makefile from "a" to "b" on-missing warn\n'
        "patch apply dragonfly/@main/patch.diff\n"
    )
    result = parse_dsl(text)

    assert result.ok
    assert result.diagnostics == []
    assert isinstance(result.ast, AstDocument)
    assert len(result.ast.statements) == 22


def test_parse_heredoc_recipe_preserved() -> None:
    text = "mk target set t <<'MK'\n\tline1\n\tline2\nMK\n"
    result = parse_dsl(text)

    assert result.ok
    assert isinstance(result.ast, AstDocument)
    node = result.ast.statements[0]
    assert isinstance(node, MkOpNode)
    assert node.action == "target-set"
    assert node.heredoc_tag == "MK"
    assert node.recipe == "\tline1\n\tline2\n"


def test_optional_clauses_are_parsed() -> None:
    text = (
        'mk disable-if condition "x" contains "y" on-missing warn\n'
        "mk target remove t on-missing noop\n"
    )
    result = parse_dsl(text)

    assert result.ok
    assert isinstance(result.ast, AstDocument)
    first = result.ast.statements[0]
    second = result.ast.statements[1]

    assert isinstance(first, MkOpNode)
    assert first.action == "disable-if"
    assert first.contains == "y"
    assert first.on_missing == "warn"

    assert isinstance(second, MkOpNode)
    assert second.action == "target-remove"
    assert second.on_missing == "noop"


def test_parse_block_set_recipe_preserved() -> None:
    text = (
        'mk block set condition "defined(LITE)" contains "LITE" <<\'BLK\'\n'
        "\tPORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        "BLK\n"
    )
    result = parse_dsl(text)

    assert result.ok
    assert isinstance(result.ast, AstDocument)
    node = result.ast.statements[0]
    assert isinstance(node, MkOpNode)
    assert node.action == "block-set"
    assert node.condition == "defined(LITE)"
    assert node.contains == "LITE"
    assert node.heredoc_tag == "BLK"
    assert node.recipe == "\tPORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"


def test_parse_error_expected_token_for_reason() -> None:
    result = parse_dsl("reason not-a-string\n")

    assert not result.ok
    assert any(d.code == "E_PARSE_EXPECTED_TOKEN" for d in result.diagnostics)


def test_parse_error_invalid_on_missing() -> None:
    result = parse_dsl("mk remove USES linux on-missing maybe\n")

    assert not result.ok
    assert any(d.code == "E_PARSE_INVALID_ON_MISSING" for d in result.diagnostics)


def test_parse_error_expected_newline() -> None:
    result = parse_dsl("port category/name extra\n")

    assert not result.ok
    assert any(d.code == "E_PARSE_EXPECTED_NEWLINE" for d in result.diagnostics)


def test_parse_spans_are_attached_to_nodes() -> None:
    text = 'target @main\nreason "abc"\n'
    result = parse_dsl(text)

    assert result.ok
    assert isinstance(result.ast, AstDocument)
    first = result.ast.statements[0]
    second = result.ast.statements[1]

    assert isinstance(first, TargetDirective)
    assert first.span.line_start == 1
    assert first.span.column_start == 1
    assert first.span.line_end == 1
    assert first.span.column_end == 12
    assert first.targets == ("@main",)

    assert isinstance(second, ReasonDirective)
    assert second.span.line_start == 2
    assert second.span.column_start == 1


def test_parse_target_supports_any_and_multi_selector() -> None:
    text = "target @any\ntarget @2025Q4,@2026Q1\n"
    result = parse_dsl(text)

    assert result.ok
    assert isinstance(result.ast, AstDocument)
    first = result.ast.statements[0]
    second = result.ast.statements[1]
    assert isinstance(first, TargetDirective)
    assert isinstance(second, TargetDirective)
    assert first.target == "@any"
    assert first.targets == ("@any",)
    assert second.target == "@2025Q4"
    assert second.targets == ("@2025Q4", "@2026Q1")


def test_parser_diagnostics_include_source_location(tmp_path: Path) -> None:
    source = tmp_path / "overlay.dops"
    source.write_text("mk target rename old new\n")
    result = parse_dsl(source.read_text(), source)

    assert not result.ok
    diag = result.diagnostics[0]
    assert diag.source_path == str(source)
    assert diag.line is not None
    assert diag.column is not None
