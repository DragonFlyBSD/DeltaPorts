from __future__ import annotations

from pathlib import Path

from dportsv3.engine.api import check_dsl, parse_dsl
from dportsv3.engine.ast import AstDocument, MkOpNode, PortDirective, TargetDirective
from dportsv3.engine.models import SourceSpan
from dportsv3.engine.semantic import analyze_document


def _span(line: int, col_start: int = 1, col_end: int = 1) -> SourceSpan:
    return SourceSpan(
        line_start=line,
        column_start=col_start,
        line_end=line,
        column_end=col_end,
    )


def test_check_valid_document_passes() -> None:
    text = (
        "target @main\n"
        "port category/name\n"
        'mk set VAR "ok"\n'
        "mk block set condition \"defined(LITE)\" <<'BLK'\n"
        "\tPORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        "BLK\n"
    )
    result = check_dsl(text)

    assert result.ok
    assert result.diagnostics == []


def test_missing_port_fails_with_e_sem_missing_port() -> None:
    text = 'target @main\nmk set VAR "x"\n'
    result = check_dsl(text)

    assert not result.ok
    assert any(d.code == "E_SEM_MISSING_PORT" for d in result.diagnostics)


def test_duplicate_singleton_directives_fail() -> None:
    text = (
        "target @main\n"
        "port cat/one\n"
        "port cat/two\n"
        "type port\n"
        "type dport\n"
        'reason "a"\n'
        'reason "b"\n'
        'maintainer "a@x"\n'
        'maintainer "b@x"\n'
    )
    result = check_dsl(text)

    assert not result.ok
    codes = [d.code for d in result.diagnostics]
    assert "E_SEM_DUPLICATE_PORT" in codes
    assert "E_SEM_DUPLICATE_TYPE" in codes
    assert "E_SEM_DUPLICATE_REASON" in codes
    assert "E_SEM_DUPLICATE_MAINTAINER" in codes


def test_operation_before_first_target_uses_implicit_any_scope() -> None:
    text = 'port category/name\nmk set VAR "x"\n'
    result = check_dsl(text)

    assert result.ok
    parsed = parse_dsl(text)
    assert parsed.ok
    assert isinstance(parsed.ast, AstDocument)
    analyzed = analyze_document(parsed.ast)
    assert analyzed.ok
    assert [item.target for item in analyzed.scoped_ops] == ["@any"]


def test_mixed_target_blocks_resolve_scopes() -> None:
    text = (
        "target @main\n"
        "port category/name\n"
        'mk set VAR "one"\n'
        "target @2025Q1\n"
        "mk remove USES linux\n"
    )
    parsed = parse_dsl(text)
    assert parsed.ok
    assert isinstance(parsed.ast, AstDocument)

    analyzed = analyze_document(parsed.ast)
    assert analyzed.ok
    assert [item.target for item in analyzed.scoped_ops] == ["@main", "@2025Q1"]


def test_multi_target_directive_expands_scopes_in_order() -> None:
    text = 'target @2025Q4,@2026Q1\nport category/name\nmk set VAR "one"\n'
    parsed = parse_dsl(text)
    assert parsed.ok
    assert isinstance(parsed.ast, AstDocument)

    analyzed = analyze_document(parsed.ast)
    assert analyzed.ok
    assert [item.target for item in analyzed.scoped_ops] == ["@2025Q4", "@2026Q1"]


def test_any_cannot_be_combined_with_explicit_targets() -> None:
    text = "target @any,@main\nport category/name\n"
    result = check_dsl(text)

    assert not result.ok
    assert any(d.code == "E_SEM_INVALID_TARGET_SCOPE" for d in result.diagnostics)


def test_invalid_semantic_operation_state_is_reported() -> None:
    document = AstDocument(
        span=_span(1),
        statements=[
            TargetDirective(span=_span(1, 1, 12), target="@main", targets=("@main",)),
            PortDirective(span=_span(2, 1, 17), origin="category/name"),
            MkOpNode(
                span=_span(3, 1, 20),
                action="target-rename",
                old_name="old",
                new_name=None,
            ),
        ],
    )
    analyzed = analyze_document(document)

    assert not analyzed.ok
    assert any(d.code == "E_SEM_INVALID_OPERATION_STATE" for d in analyzed.diagnostics)


def test_block_set_on_missing_is_rejected() -> None:
    document = AstDocument(
        span=_span(1),
        statements=[
            TargetDirective(span=_span(1, 1, 12), target="@main", targets=("@main",)),
            PortDirective(span=_span(2, 1, 17), origin="category/name"),
            MkOpNode(
                span=_span(3, 1, 30),
                action="block-set",
                condition="defined(LITE)",
                heredoc_tag="BLK",
                recipe="\tPORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n",
                on_missing="warn",
            ),
        ],
    )
    analyzed = analyze_document(document)

    assert not analyzed.ok
    assert any(d.code == "E_SEM_INVALID_OPERATION_STATE" for d in analyzed.diagnostics)


def test_semantic_diagnostics_include_source_location(tmp_path: Path) -> None:
    source = tmp_path / "overlay.dops"
    source.write_text('target @any,@main\nport category/name\nmk set VAR "x"\n')
    result = check_dsl(source.read_text(), source)

    assert not result.ok
    diag = next(d for d in result.diagnostics if d.code == "E_SEM_INVALID_TARGET_SCOPE")
    assert diag.source_path == str(source)
    assert diag.line == 1
    assert diag.column == 1
