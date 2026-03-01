from __future__ import annotations

from pathlib import Path

from dportsv3.engine.api import parse_dsl
from dportsv3.engine.lexer import lex_dsl


def _without_newline_eof_kinds(text: str) -> list[str]:
    result = lex_dsl(text)
    return [t.kind for t in result.tokens if t.kind not in {"NEWLINE", "EOF"}]


def _without_newline_eof_values(text: str) -> list[str]:
    result = lex_dsl(text)
    return [t.value for t in result.tokens if t.kind not in {"NEWLINE", "EOF"}]


def test_basic_directive_and_arrow_tokens() -> None:
    text = "target @main\nfile copy a -> b\n"
    result = lex_dsl(text)

    assert result.ok
    assert _without_newline_eof_kinds(text) == [
        "WORD",
        "WORD",
        "WORD",
        "WORD",
        "WORD",
        "ARROW",
        "WORD",
    ]
    assert _without_newline_eof_values(text) == [
        "target",
        "@main",
        "file",
        "copy",
        "a",
        "->",
        "b",
    ]


def test_comments_ignored_outside_heredoc() -> None:
    text = "target @main # inline\n# full line\nport category/name\n"
    result = lex_dsl(text)

    assert result.ok
    assert _without_newline_eof_values(text) == [
        "target",
        "@main",
        "port",
        "category/name",
    ]


def test_string_escapes_are_decoded() -> None:
    text = 'reason "line\\nquote\\"tab\\tbackslash\\\\"\n'
    result = lex_dsl(text)

    assert result.ok
    string_token = next(t for t in result.tokens if t.kind == "STRING")
    assert string_token.value == 'line\nquote"tab\tbackslash\\'


def test_continuation_lines_join_logical_tokens() -> None:
    text = "mk set VAR value \\\n  more\n"
    result = lex_dsl(text)

    assert result.ok
    values = _without_newline_eof_values(text)
    assert values == ["mk", "set", "VAR", "value", "more"]
    newline_count = sum(1 for t in result.tokens if t.kind == "NEWLINE")
    assert newline_count == 1


def test_heredoc_body_is_preserved_exactly() -> None:
    text = "mk target set dfly-patch <<'MK'\n\tcmd1\n\tcmd2\nMK\n"
    result = lex_dsl(text)

    assert result.ok
    start = next(t for t in result.tokens if t.kind == "HEREDOC_START")
    body = next(t for t in result.tokens if t.kind == "HEREDOC_BODY")
    assert start.value == "MK"
    assert body.value == "\tcmd1\n\tcmd2\n"


def test_invalid_escape_reports_e_parse_invalid_escape(tmp_path: Path) -> None:
    source = tmp_path / "overlay.dops"
    source.write_text('reason "bad\\x"\n')
    result = lex_dsl(source.read_text(), source)

    assert not result.ok
    codes = [d.code for d in result.diagnostics]
    assert "E_PARSE_INVALID_ESCAPE" in codes


def test_unterminated_string_reports_e_parse_unterminated_string(
    tmp_path: Path,
) -> None:
    source = tmp_path / "overlay.dops"
    source.write_text('reason "oops\n')
    result = parse_dsl(source.read_text(), source)

    assert not result.ok
    codes = [d.code for d in result.diagnostics]
    assert "E_PARSE_UNTERMINATED_STRING" in codes
    assert "E_NOT_IMPLEMENTED" not in codes


def test_unterminated_heredoc_reports_e_parse_unterminated_heredoc(
    tmp_path: Path,
) -> None:
    source = tmp_path / "overlay.dops"
    source.write_text("mk target set x <<'MK'\nfoo\n")
    result = lex_dsl(source.read_text(), source)

    assert not result.ok
    codes = [d.code for d in result.diagnostics]
    assert "E_PARSE_UNTERMINATED_HEREDOC" in codes


def test_token_spans_are_1_based_and_precise() -> None:
    text = "target @main\nfile copy a -> b\n"
    result = lex_dsl(text)
    assert result.ok

    first = result.tokens[0]
    assert first.value == "target"
    assert first.span.line_start == 1
    assert first.span.column_start == 1
    assert first.span.line_end == 1
    assert first.span.column_end == 6

    arrow = next(t for t in result.tokens if t.kind == "ARROW")
    assert arrow.span.line_start == 2
    assert arrow.span.column_start == 13
    assert arrow.span.line_end == 2
    assert arrow.span.column_end == 14
