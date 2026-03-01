"""Lexer for DeltaPorts v3 DSL."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dportsv3.engine.models import Diagnostic, LexResult, SourceSpan, Token


@dataclass
class _Cursor:
    text: str
    index: int = 0
    line: int = 1
    column: int = 1

    def eof(self) -> bool:
        return self.index >= len(self.text)

    def current(self) -> str:
        return self.text[self.index]

    def peek(self, offset: int = 1) -> str | None:
        pos = self.index + offset
        if pos >= len(self.text):
            return None
        return self.text[pos]

    def advance(self) -> tuple[str, int, int]:
        ch = self.text[self.index]
        line = self.line
        column = self.column

        self.index += 1
        if ch == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1

        return ch, line, column


def _span(
    line_start: int,
    column_start: int,
    line_end: int,
    column_end: int,
) -> SourceSpan:
    return SourceSpan(
        line_start=line_start,
        column_start=column_start,
        line_end=line_end,
        column_end=column_end,
    )


def _diagnostic(
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


def _is_line_continuation(cursor: _Cursor) -> bool:
    if cursor.current() != "\\":
        return False

    i = cursor.index + 1
    while i < len(cursor.text) and cursor.text[i] in {" ", "\t", "\r"}:
        i += 1

    return i < len(cursor.text) and cursor.text[i] == "\n"


def _consume_line_continuation(cursor: _Cursor) -> None:
    cursor.advance()  # backslash
    while not cursor.eof() and cursor.current() in {" ", "\t", "\r"}:
        cursor.advance()
    if not cursor.eof() and cursor.current() == "\n":
        cursor.advance()


def _lex_string(
    cursor: _Cursor,
    tokens: list[Token],
    diagnostics: list[Diagnostic],
    source_path: Path | None,
) -> None:
    _, start_line, start_col = cursor.advance()  # opening quote
    value_chars: list[str] = []
    end_line = start_line
    end_col = start_col

    while not cursor.eof():
        ch, line, col = cursor.advance()
        end_line = line
        end_col = col

        if ch == '"':
            tokens.append(
                Token(
                    kind="STRING",
                    value="".join(value_chars),
                    span=_span(start_line, start_col, end_line, end_col),
                )
            )
            return

        if ch == "\\":
            if cursor.eof():
                diagnostics.append(
                    _diagnostic(
                        "E_PARSE_UNTERMINATED_STRING",
                        "Unterminated string literal",
                        source_path,
                        start_line,
                        start_col,
                    )
                )
                return

            esc, esc_line, esc_col = cursor.advance()
            end_line = esc_line
            end_col = esc_col

            if esc == "\\":
                value_chars.append("\\")
            elif esc == '"':
                value_chars.append('"')
            elif esc == "n":
                value_chars.append("\n")
            elif esc == "t":
                value_chars.append("\t")
            else:
                diagnostics.append(
                    _diagnostic(
                        "E_PARSE_INVALID_ESCAPE",
                        f"Invalid escape sequence: \\{esc}",
                        source_path,
                        esc_line,
                        esc_col,
                    )
                )
                value_chars.append(esc)
            continue

        if ch == "\n":
            diagnostics.append(
                _diagnostic(
                    "E_PARSE_UNTERMINATED_STRING",
                    "Unterminated string literal",
                    source_path,
                    start_line,
                    start_col,
                )
            )
            return

        value_chars.append(ch)

    diagnostics.append(
        _diagnostic(
            "E_PARSE_UNTERMINATED_STRING",
            "Unterminated string literal",
            source_path,
            start_line,
            start_col,
        )
    )


def _read_line(cursor: _Cursor) -> tuple[str, bool, int, int, int, int]:
    start_line = cursor.line
    start_col = cursor.column
    chars: list[str] = []
    end_line = start_line
    end_col = start_col

    while not cursor.eof() and cursor.current() != "\n":
        ch, line, col = cursor.advance()
        chars.append(ch)
        end_line = line
        end_col = col

    had_newline = False
    if not cursor.eof() and cursor.current() == "\n":
        _, line, col = cursor.advance()
        had_newline = True
        end_line = line
        end_col = col

    return "".join(chars), had_newline, start_line, start_col, end_line, end_col


def _lex_heredoc(
    cursor: _Cursor,
    tokens: list[Token],
    diagnostics: list[Diagnostic],
    source_path: Path | None,
) -> None:
    start_line = cursor.line
    start_col = cursor.column

    cursor.advance()  # <
    cursor.advance()  # <

    if cursor.eof() or cursor.current() != "'":
        diagnostics.append(
            _diagnostic(
                "E_PARSE_INVALID_HEREDOC_START",
                "Invalid heredoc start, expected <<'TAG'",
                source_path,
                start_line,
                start_col,
            )
        )
        return

    cursor.advance()  # opening quote
    tag_chars: list[str] = []
    quote_line = start_line
    quote_col = start_col

    while not cursor.eof() and cursor.current() not in {"'", "\n"}:
        ch, _, _ = cursor.advance()
        tag_chars.append(ch)

    if cursor.eof() or cursor.current() != "'":
        diagnostics.append(
            _diagnostic(
                "E_PARSE_INVALID_HEREDOC_START",
                "Invalid heredoc start, expected closing quote in <<'TAG'",
                source_path,
                start_line,
                start_col,
            )
        )
        return

    _, quote_line, quote_col = cursor.advance()  # closing quote
    tag = "".join(tag_chars)
    if not tag or any(c.isspace() for c in tag):
        diagnostics.append(
            _diagnostic(
                "E_PARSE_INVALID_HEREDOC_START",
                "Invalid heredoc tag",
                source_path,
                start_line,
                start_col,
            )
        )

    tokens.append(
        Token(
            kind="HEREDOC_START",
            value=tag,
            span=_span(start_line, start_col, quote_line, quote_col),
        )
    )

    while not cursor.eof() and cursor.current() in {" ", "\t", "\r"}:
        cursor.advance()

    if cursor.eof():
        diagnostics.append(
            _diagnostic(
                "E_PARSE_UNTERMINATED_HEREDOC",
                f"Unterminated heredoc, missing terminator line '{tag}'",
                source_path,
                start_line,
                start_col,
            )
        )
        return

    if cursor.current() != "\n":
        diagnostics.append(
            _diagnostic(
                "E_PARSE_INVALID_HEREDOC_START",
                "Invalid heredoc start, expected newline after <<'TAG'",
                source_path,
                cursor.line,
                cursor.column,
            )
        )
        while not cursor.eof() and cursor.current() != "\n":
            cursor.advance()

    if not cursor.eof() and cursor.current() == "\n":
        _, nl_line, nl_col = cursor.advance()
        tokens.append(
            Token(
                kind="NEWLINE",
                value="\n",
                span=_span(nl_line, nl_col, nl_line, nl_col),
            )
        )

    body_chunks: list[str] = []
    body_start_line = cursor.line
    body_start_col = cursor.column
    body_end_line = body_start_line
    body_end_col = body_start_col
    saw_body = False

    while not cursor.eof():
        line_text, had_newline, line_start, line_col, line_end, line_end_col = (
            _read_line(cursor)
        )

        if line_text == tag:
            if saw_body:
                body_span = _span(
                    body_start_line,
                    body_start_col,
                    body_end_line,
                    body_end_col,
                )
            else:
                body_span = _span(line_start, line_col, line_start, line_col)

            tokens.append(
                Token(
                    kind="HEREDOC_BODY",
                    value="".join(body_chunks),
                    span=body_span,
                )
            )

            if had_newline:
                tokens.append(
                    Token(
                        kind="NEWLINE",
                        value="\n",
                        span=_span(line_end, line_end_col, line_end, line_end_col),
                    )
                )
            return

        chunk = line_text + ("\n" if had_newline else "")
        body_chunks.append(chunk)
        if not saw_body:
            body_start_line = line_start
            body_start_col = line_col
            saw_body = True

        body_end_line = line_end
        body_end_col = line_end_col

    diagnostics.append(
        _diagnostic(
            "E_PARSE_UNTERMINATED_HEREDOC",
            f"Unterminated heredoc, missing terminator line '{tag}'",
            source_path,
            start_line,
            start_col,
        )
    )


def _lex_word(
    cursor: _Cursor,
    tokens: list[Token],
    diagnostics: list[Diagnostic],
    source_path: Path | None,
) -> None:
    start_line = cursor.line
    start_col = cursor.column
    chars: list[str] = []
    end_line = start_line
    end_col = start_col

    while not cursor.eof():
        ch = cursor.current()

        if ch in {" ", "\t", "\r", "\n", "#", '"'}:
            break
        if ch == "-" and cursor.peek() == ">":
            break
        if ch == "<" and cursor.peek() == "<":
            break
        if ch == "\\" and _is_line_continuation(cursor):
            break
        if ch == "'":
            diagnostics.append(
                _diagnostic(
                    "E_PARSE_UNEXPECTED_CHAR",
                    "Unexpected character: '",
                    source_path,
                    cursor.line,
                    cursor.column,
                )
            )
            cursor.advance()
            if chars:
                break
            continue

        _, line, col = cursor.advance()
        chars.append(ch)
        end_line = line
        end_col = col

    if chars:
        tokens.append(
            Token(
                kind="WORD",
                value="".join(chars),
                span=_span(start_line, start_col, end_line, end_col),
            )
        )


def lex_dsl(text: str, source_path: Path | None = None) -> LexResult:
    """Lex DSL source into tokens with source spans."""
    cursor = _Cursor(text=text)
    tokens: list[Token] = []
    diagnostics: list[Diagnostic] = []

    while not cursor.eof():
        ch = cursor.current()

        if ch in {" ", "\t", "\r"}:
            cursor.advance()
            continue

        if ch == "\\" and _is_line_continuation(cursor):
            _consume_line_continuation(cursor)
            continue

        if ch == "\n":
            _, line, col = cursor.advance()
            tokens.append(
                Token(
                    kind="NEWLINE",
                    value="\n",
                    span=_span(line, col, line, col),
                )
            )
            continue

        if ch == "#":
            while not cursor.eof() and cursor.current() != "\n":
                cursor.advance()
            continue

        if ch == '"':
            _lex_string(cursor, tokens, diagnostics, source_path)
            continue

        if ch == "-" and cursor.peek() == ">":
            _, line_s, col_s = cursor.advance()
            _, line_e, col_e = cursor.advance()
            tokens.append(
                Token(
                    kind="ARROW",
                    value="->",
                    span=_span(line_s, col_s, line_e, col_e),
                )
            )
            continue

        if ch == "<" and cursor.peek() == "<":
            _lex_heredoc(cursor, tokens, diagnostics, source_path)
            continue

        if ch == "'":
            diagnostics.append(
                _diagnostic(
                    "E_PARSE_UNEXPECTED_CHAR",
                    "Unexpected character: '",
                    source_path,
                    cursor.line,
                    cursor.column,
                )
            )
            cursor.advance()
            continue

        _lex_word(cursor, tokens, diagnostics, source_path)

    eof_line = cursor.line
    eof_col = cursor.column
    tokens.append(
        Token(
            kind="EOF",
            value="",
            span=_span(eof_line, eof_col, eof_line, eof_col),
        )
    )

    return LexResult(ok=not diagnostics, tokens=tokens, diagnostics=diagnostics)
