"""Recursive-descent parser for DeltaPorts v3 DSL."""

from __future__ import annotations

import re
from pathlib import Path

from dportsv3.common.validation import is_scoped_target, normalize_on_missing
from dportsv3.engine.ast import (
    AstDocument,
    FileOpNode,
    MaintainerDirective,
    MkOpNode,
    PatchOpNode,
    PortDirective,
    ReasonDirective,
    StatementNode,
    TargetDirective,
    TextOpNode,
    TypeDirective,
)
from dportsv3.engine.models import Diagnostic, ParseResult, SourceSpan, Token

_ORIGIN_RE = re.compile(r"^[^/\s]+/[^/\s]+$")


def _join_span(start: SourceSpan, end: SourceSpan) -> SourceSpan:
    return SourceSpan(
        line_start=start.line_start,
        column_start=start.column_start,
        line_end=end.line_end,
        column_end=end.column_end,
    )


class _Parser:
    def __init__(self, tokens: list[Token], source_path: Path | None):
        self.tokens = tokens
        self.source_path = source_path
        self.index = 0
        self.diagnostics: list[Diagnostic] = []

    def _current(self) -> Token:
        return self.tokens[min(self.index, len(self.tokens) - 1)]

    def _peek(self, offset: int = 1) -> Token:
        return self.tokens[min(self.index + offset, len(self.tokens) - 1)]

    def _advance(self) -> Token:
        token = self._current()
        if token.kind != "EOF":
            self.index += 1
        return token

    def _at(self, kind: str | None = None, value: str | None = None) -> bool:
        token = self._current()
        if kind is not None and token.kind != kind:
            return False
        if value is not None and token.value != value:
            return False
        return True

    def _error(self, code: str, message: str, token: Token | None = None) -> None:
        tok = token or self._current()
        self.diagnostics.append(
            Diagnostic(
                severity="error",
                code=code,
                message=message,
                source_path=str(self.source_path)
                if self.source_path is not None
                else None,
                line=tok.span.line_start,
                column=tok.span.column_start,
            )
        )

    def _sync_line(self) -> None:
        while not self._at("EOF") and not self._at("NEWLINE"):
            self._advance()
        if self._at("NEWLINE"):
            self._advance()

    def _expect_kind(self, kind: str, hint: str) -> Token | None:
        if self._at(kind):
            return self._advance()
        self._error(
            "E_PARSE_EXPECTED_TOKEN",
            f"expected {kind} {hint}",
        )
        return None

    def _expect_word(self, hint: str) -> Token | None:
        return self._expect_kind("WORD", hint)

    def _expect_string(self, hint: str) -> Token | None:
        return self._expect_kind("STRING", hint)

    def _expect_value(self, hint: str) -> Token | None:
        if self._at("WORD") or self._at("STRING"):
            return self._advance()
        self._error(
            "E_PARSE_EXPECTED_TOKEN",
            f"expected WORD or STRING {hint}",
        )
        return None

    def _expect_word_value(self, value: str, hint: str) -> Token | None:
        token = self._expect_word(hint)
        if token is None:
            return None
        if token.value != value:
            self._error(
                "E_PARSE_EXPECTED_TOKEN",
                f"expected '{value}' {hint}",
                token,
            )
            return None
        return token

    def _finish_statement(self) -> None:
        if self._at("NEWLINE"):
            self._advance()
            return
        if self._at("EOF"):
            return
        self._error(
            "E_PARSE_EXPECTED_NEWLINE",
            "expected NEWLINE before next statement",
        )
        self._sync_line()

    def _parse_on_missing(self) -> tuple[str | None, Token | None]:
        if not self._at("WORD", "on-missing"):
            return None, None
        self._advance()
        value = self._expect_word("after 'on-missing'")
        if value is None:
            return None, None
        normalized = normalize_on_missing(value.value)
        if normalized is None:
            self._error(
                "E_PARSE_INVALID_ON_MISSING",
                "expected on-missing value: error|warn|noop",
                value,
            )
            return None, value
        return normalized, value

    def _parse_contains(self) -> tuple[str | None, Token | None]:
        if not self._at("WORD", "contains"):
            return None, None
        self._advance()
        value = self._expect_string("after 'contains'")
        if value is None:
            return None, None
        return value.value, value

    def parse_document(self) -> AstDocument:
        statements: list[StatementNode] = []

        while not self._at("EOF"):
            if self._at("NEWLINE"):
                self._advance()
                continue

            statement = self._parse_statement()
            if statement is not None:
                statements.append(statement)

        if statements:
            span = _join_span(statements[0].span, statements[-1].span)
        else:
            span = self._current().span

        return AstDocument(span=span, statements=statements)

    def _parse_statement(self) -> StatementNode | None:
        if not self._at("WORD"):
            self._error("E_PARSE_EXPECTED_STATEMENT", "expected statement start")
            self._sync_line()
            return None

        keyword = self._current().value
        if keyword == "target":
            return self._parse_target_directive()
        if keyword == "port":
            return self._parse_port_directive()
        if keyword == "type":
            return self._parse_type_directive()
        if keyword == "reason":
            return self._parse_reason_directive()
        if keyword == "maintainer":
            return self._parse_maintainer_directive()
        if keyword == "mk":
            return self._parse_mk_op()
        if keyword == "file":
            return self._parse_file_op()
        if keyword == "text":
            return self._parse_text_op()
        if keyword == "patch":
            return self._parse_patch_op()

        self._error(
            "E_PARSE_EXPECTED_STATEMENT",
            "expected statement (directive or operation)",
            self._current(),
        )
        self._sync_line()
        return None

    def _parse_target_directive(self) -> TargetDirective | None:
        start = self._expect_word_value("target", "at directive start")
        if start is None:
            self._sync_line()
            return None
        value = self._expect_word("after 'target'")
        if value is None:
            self._sync_line()
            return None
        selectors = [chunk.strip() for chunk in value.value.split(",") if chunk.strip()]
        if not selectors:
            selectors = [value.value]
        for selector in selectors:
            if is_scoped_target(selector):
                continue
            self._error(
                "E_PARSE_EXPECTED_TOKEN",
                "expected target token '@any', '@main', or '@YYYYQ[1-4]'",
                value,
            )
            break

        if selectors and not is_scoped_target(selectors[0]):
            primary = value.value
        else:
            primary = selectors[0] if selectors else value.value

        if not is_scoped_target(primary):
            self._error(
                "E_PARSE_EXPECTED_TOKEN",
                "expected target token '@any', '@main', or '@YYYYQ[1-4]'",
                value,
            )
        self._finish_statement()
        return TargetDirective(
            target=primary,
            targets=tuple(selectors),
            span=_join_span(start.span, value.span),
        )

    def _parse_port_directive(self) -> PortDirective | None:
        start = self._expect_word_value("port", "at directive start")
        if start is None:
            self._sync_line()
            return None
        value = self._expect_word("after 'port'")
        if value is None:
            self._sync_line()
            return None
        if not _ORIGIN_RE.match(value.value):
            self._error(
                "E_PARSE_EXPECTED_TOKEN",
                "expected origin in form 'category/name'",
                value,
            )
        self._finish_statement()
        return PortDirective(
            origin=value.value, span=_join_span(start.span, value.span)
        )

    def _parse_type_directive(self) -> TypeDirective | None:
        start = self._expect_word_value("type", "at directive start")
        if start is None:
            self._sync_line()
            return None
        value = self._expect_word("after 'type'")
        if value is None:
            self._sync_line()
            return None
        if value.value not in {"port", "mask", "dport", "lock"}:
            self._error(
                "E_PARSE_EXPECTED_TOKEN",
                "expected type value: port|mask|dport|lock",
                value,
            )
        self._finish_statement()
        return TypeDirective(
            port_type=value.value, span=_join_span(start.span, value.span)
        )

    def _parse_reason_directive(self) -> ReasonDirective | None:
        start = self._expect_word_value("reason", "at directive start")
        if start is None:
            self._sync_line()
            return None
        value = self._expect_string("after 'reason'")
        if value is None:
            self._sync_line()
            return None
        self._finish_statement()
        return ReasonDirective(
            reason=value.value, span=_join_span(start.span, value.span)
        )

    def _parse_maintainer_directive(self) -> MaintainerDirective | None:
        start = self._expect_word_value("maintainer", "at directive start")
        if start is None:
            self._sync_line()
            return None
        value = self._expect_string("after 'maintainer'")
        if value is None:
            self._sync_line()
            return None
        self._finish_statement()
        return MaintainerDirective(
            maintainer=value.value,
            span=_join_span(start.span, value.span),
        )

    def _parse_mk_op(self) -> MkOpNode | None:
        start = self._expect_word_value("mk", "at operation start")
        if start is None:
            self._sync_line()
            return None

        action = self._expect_word("after 'mk'")
        if action is None:
            self._sync_line()
            return None

        if action.value in {"set", "unset", "add", "remove"}:
            return self._parse_mk_var_op(start, action.value)
        if action.value in {"disable-if", "replace-if"}:
            return self._parse_mk_block_op(start, action.value)
        if action.value == "block":
            return self._parse_mk_block_set_op(start)
        if action.value == "target":
            return self._parse_mk_target_op(start)

        self._error(
            "E_PARSE_UNEXPECTED_TOKEN",
            "unexpected mk action; expected set|unset|add|remove|disable-if|replace-if|block|target",
            action,
        )
        self._sync_line()
        return None

    def _parse_mk_block_set_op(self, start: Token) -> MkOpNode | None:
        if self._expect_word_value("set", "after 'mk block'") is None:
            self._sync_line()
            return None

        if self._expect_word_value("condition", "after 'mk block set'") is None:
            self._sync_line()
            return None
        condition_tok = self._expect_string("after 'condition'")
        if condition_tok is None:
            self._sync_line()
            return None

        contains, _ = self._parse_contains()
        heredoc_start = self._expect_kind(
            "HEREDOC_START",
            "after block condition (expected <<'TAG')",
        )
        if heredoc_start is None:
            self._sync_line()
            return None
        if self._expect_kind("NEWLINE", "after heredoc start") is None:
            self._sync_line()
            return None
        heredoc_body = self._expect_kind("HEREDOC_BODY", "for heredoc body")
        if heredoc_body is None:
            self._sync_line()
            return None

        end_span = heredoc_body.span
        if self._at("NEWLINE"):
            end_span = self._advance().span

        return MkOpNode(
            span=_join_span(start.span, end_span),
            action="block-set",
            condition=condition_tok.value,
            contains=contains,
            heredoc_tag=heredoc_start.value,
            recipe=heredoc_body.value,
        )

    def _parse_mk_var_op(self, start: Token, action: str) -> MkOpNode | None:
        var = self._expect_word(f"after 'mk {action}'")
        if var is None:
            self._sync_line()
            return None

        value: str | None = None
        token: str | None = None
        end_span = var.span

        if action == "set":
            value_tok = self._expect_string("after 'mk set <VAR>'")
            if value_tok is None:
                self._sync_line()
                return None
            value = value_tok.value
            end_span = value_tok.span
        elif action in {"add", "remove"}:
            token_tok = self._expect_value(f"after 'mk {action} <VAR>'")
            if token_tok is None:
                self._sync_line()
                return None
            token = token_tok.value
            end_span = token_tok.span

        on_missing, on_missing_tok = self._parse_on_missing()
        if on_missing_tok is not None:
            end_span = on_missing_tok.span

        self._finish_statement()
        return MkOpNode(
            span=_join_span(start.span, end_span),
            action=action,
            var=var.value,
            value=value,
            token=token,
            on_missing=on_missing,
        )

    def _parse_mk_block_op(self, start: Token, action: str) -> MkOpNode | None:
        if action == "disable-if":
            if self._expect_word_value("condition", "after 'mk disable-if'") is None:
                self._sync_line()
                return None
            condition_tok = self._expect_string("after 'condition'")
            if condition_tok is None:
                self._sync_line()
                return None

            contains, contains_tok = self._parse_contains()
            end_span = (
                contains_tok.span if contains_tok is not None else condition_tok.span
            )

            on_missing, on_missing_tok = self._parse_on_missing()
            if on_missing_tok is not None:
                end_span = on_missing_tok.span

            self._finish_statement()
            return MkOpNode(
                span=_join_span(start.span, end_span),
                action=action,
                condition=condition_tok.value,
                contains=contains,
                on_missing=on_missing,
            )

        if self._expect_word_value("from", "after 'mk replace-if'") is None:
            self._sync_line()
            return None
        from_tok = self._expect_string("after 'from'")
        if from_tok is None:
            self._sync_line()
            return None
        if self._expect_word_value("to", "after replace-if from expression") is None:
            self._sync_line()
            return None
        to_tok = self._expect_string("after 'to'")
        if to_tok is None:
            self._sync_line()
            return None

        contains, contains_tok = self._parse_contains()
        end_span = contains_tok.span if contains_tok is not None else to_tok.span

        on_missing, on_missing_tok = self._parse_on_missing()
        if on_missing_tok is not None:
            end_span = on_missing_tok.span

        self._finish_statement()
        return MkOpNode(
            span=_join_span(start.span, end_span),
            action=action,
            condition_from=from_tok.value,
            condition_to=to_tok.value,
            contains=contains,
            on_missing=on_missing,
        )

    def _parse_mk_target_op(self, start: Token) -> MkOpNode | None:
        target_action = self._expect_word("after 'mk target'")
        if target_action is None:
            self._sync_line()
            return None

        if target_action.value in {"set", "append"}:
            name = self._expect_word("after 'mk target set|append'")
            if name is None:
                self._sync_line()
                return None
            heredoc_start = self._expect_kind(
                "HEREDOC_START",
                "after target name (expected <<'TAG')",
            )
            if heredoc_start is None:
                self._sync_line()
                return None
            if self._expect_kind("NEWLINE", "after heredoc start") is None:
                self._sync_line()
                return None
            heredoc_body = self._expect_kind("HEREDOC_BODY", "for heredoc body")
            if heredoc_body is None:
                self._sync_line()
                return None
            end_span = heredoc_body.span
            if self._at("NEWLINE"):
                end_span = self._advance().span

            action = "target-set" if target_action.value == "set" else "target-append"
            return MkOpNode(
                span=_join_span(start.span, end_span),
                action=action,
                name=name.value,
                heredoc_tag=heredoc_start.value,
                recipe=heredoc_body.value,
            )

        if target_action.value == "remove":
            name = self._expect_word("after 'mk target remove'")
            if name is None:
                self._sync_line()
                return None
            on_missing, on_missing_tok = self._parse_on_missing()
            end_span = on_missing_tok.span if on_missing_tok is not None else name.span
            self._finish_statement()
            return MkOpNode(
                span=_join_span(start.span, end_span),
                action="target-remove",
                name=name.value,
                on_missing=on_missing,
            )

        if target_action.value == "rename":
            old_name = self._expect_word("after 'mk target rename'")
            if old_name is None:
                self._sync_line()
                return None
            arrow = self._expect_kind("ARROW", "between old and new target names")
            if arrow is None:
                self._sync_line()
                return None
            new_name = self._expect_word("after '->'")
            if new_name is None:
                self._sync_line()
                return None
            on_missing, on_missing_tok = self._parse_on_missing()
            end_span = (
                on_missing_tok.span if on_missing_tok is not None else new_name.span
            )
            self._finish_statement()
            return MkOpNode(
                span=_join_span(start.span, end_span),
                action="target-rename",
                old_name=old_name.value,
                new_name=new_name.value,
                on_missing=on_missing,
            )

        self._error(
            "E_PARSE_UNEXPECTED_TOKEN",
            "unexpected mk target action; expected set|append|remove|rename",
            target_action,
        )
        self._sync_line()
        return None

    def _parse_file_op(self) -> FileOpNode | None:
        start = self._expect_word_value("file", "at operation start")
        if start is None:
            self._sync_line()
            return None
        action = self._expect_word("after 'file'")
        if action is None:
            self._sync_line()
            return None

        if action.value == "copy":
            src = self._expect_value("after 'file copy'")
            if src is None:
                self._sync_line()
                return None
            if self._expect_kind("ARROW", "between source and destination") is None:
                self._sync_line()
                return None
            dst = self._expect_value("after '->'")
            if dst is None:
                self._sync_line()
                return None
            self._finish_statement()
            return FileOpNode(
                span=_join_span(start.span, dst.span),
                action="copy",
                src=src.value,
                dst=dst.value,
            )

        if action.value == "remove":
            path = self._expect_value("after 'file remove'")
            if path is None:
                self._sync_line()
                return None
            on_missing, on_missing_tok = self._parse_on_missing()
            end_span = on_missing_tok.span if on_missing_tok is not None else path.span
            self._finish_statement()
            return FileOpNode(
                span=_join_span(start.span, end_span),
                action="remove",
                path=path.value,
                on_missing=on_missing,
            )

        self._error(
            "E_PARSE_UNEXPECTED_TOKEN",
            "unexpected file action; expected copy|remove",
            action,
        )
        self._sync_line()
        return None

    def _parse_text_op(self) -> TextOpNode | None:
        start = self._expect_word_value("text", "at operation start")
        if start is None:
            self._sync_line()
            return None
        action = self._expect_word("after 'text'")
        if action is None:
            self._sync_line()
            return None

        if self._expect_word_value("file", "after text action") is None:
            self._sync_line()
            return None
        file_path = self._expect_value("after 'file'")
        if file_path is None:
            self._sync_line()
            return None

        if action.value == "line-remove":
            if (
                self._expect_word_value("exact", "after text line-remove file path")
                is None
            ):
                self._sync_line()
                return None
            exact = self._expect_string("after 'exact'")
            if exact is None:
                self._sync_line()
                return None
            on_missing, on_missing_tok = self._parse_on_missing()
            end_span = on_missing_tok.span if on_missing_tok is not None else exact.span
            self._finish_statement()
            return TextOpNode(
                span=_join_span(start.span, end_span),
                action="line-remove",
                file_path=file_path.value,
                exact=exact.value,
                on_missing=on_missing,
            )

        if action.value == "line-insert-after":
            if (
                self._expect_word_value(
                    "anchor",
                    "after text line-insert-after file path",
                )
                is None
            ):
                self._sync_line()
                return None
            anchor = self._expect_string("after 'anchor'")
            if anchor is None:
                self._sync_line()
                return None
            if self._expect_word_value("line", "after anchor string") is None:
                self._sync_line()
                return None
            line = self._expect_string("after 'line'")
            if line is None:
                self._sync_line()
                return None
            on_missing, on_missing_tok = self._parse_on_missing()
            end_span = on_missing_tok.span if on_missing_tok is not None else line.span
            self._finish_statement()
            return TextOpNode(
                span=_join_span(start.span, end_span),
                action="line-insert-after",
                file_path=file_path.value,
                anchor=anchor.value,
                line=line.value,
                on_missing=on_missing,
            )

        if action.value == "replace-once":
            if (
                self._expect_word_value("from", "after text replace-once file path")
                is None
            ):
                self._sync_line()
                return None
            from_text = self._expect_string("after 'from'")
            if from_text is None:
                self._sync_line()
                return None
            if self._expect_word_value("to", "after from string") is None:
                self._sync_line()
                return None
            to_text = self._expect_string("after 'to'")
            if to_text is None:
                self._sync_line()
                return None
            on_missing, on_missing_tok = self._parse_on_missing()
            end_span = (
                on_missing_tok.span if on_missing_tok is not None else to_text.span
            )
            self._finish_statement()
            return TextOpNode(
                span=_join_span(start.span, end_span),
                action="replace-once",
                file_path=file_path.value,
                from_text=from_text.value,
                to_text=to_text.value,
                on_missing=on_missing,
            )

        self._error(
            "E_PARSE_UNEXPECTED_TOKEN",
            "unexpected text action; expected line-remove|line-insert-after|replace-once",
            action,
        )
        self._sync_line()
        return None

    def _parse_patch_op(self) -> PatchOpNode | None:
        start = self._expect_word_value("patch", "at operation start")
        if start is None:
            self._sync_line()
            return None
        if self._expect_word_value("apply", "after 'patch'") is None:
            self._sync_line()
            return None
        path = self._expect_value("after 'patch apply'")
        if path is None:
            self._sync_line()
            return None
        self._finish_statement()
        return PatchOpNode(path=path.value, span=_join_span(start.span, path.span))


def parse_tokens(tokens: list[Token], source_path: Path | None = None) -> ParseResult:
    """Parse token stream into an AST document."""
    parser = _Parser(tokens=tokens, source_path=source_path)
    document = parser.parse_document()
    return ParseResult(
        ok=not parser.diagnostics, diagnostics=parser.diagnostics, ast=document
    )
