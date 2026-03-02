from __future__ import annotations

from dportsv3.engine.api import build_plan
from dportsv3.engine.ast import AstDocument, MkOpNode, PortDirective, TargetDirective
from dportsv3.engine.models import SourceSpan
from dportsv3.engine.planner import compile_plan
from dportsv3.engine.semantic import ScopedOperation
from tests.dportsv3_testutils import (
    list_fixture_paths,
    read_json_fixture,
    read_text_fixture,
)


def _span(line: int, column_start: int = 1, column_end: int = 1) -> SourceSpan:
    return SourceSpan(
        line_start=line,
        column_start=column_start,
        line_end=line,
        column_end=column_end,
    )


def test_build_plan_valid_document_metadata_and_kinds() -> None:
    text = (
        "target @main\n"
        "port category/name\n"
        'reason "r"\n'
        'maintainer "m@example.com"\n'
        'mk set VAR "v"\n'
        "mk remove USES linux\n"
        "file copy a -> b\n"
        "file materialize dragonfly/patch-a -> dragonfly/patch-a\n"
        'text replace-once file Makefile from "a" to "b"\n'
        "patch apply dragonfly/@main/patch.diff\n"
    )
    result = build_plan(text)

    assert result.ok
    assert result.plan is not None
    assert result.plan.port == "category/name"
    assert result.plan.type == "port"
    assert result.plan.reason == "r"
    assert result.plan.maintainer == "m@example.com"
    assert [op.kind for op in result.plan.ops] == [
        "mk.var.set",
        "mk.var.token_remove",
        "file.copy",
        "file.materialize",
        "text.replace_once",
        "patch.apply",
    ]


def test_build_plan_matches_golden_fixtures() -> None:
    dops_paths = list_fixture_paths("golden/*.dops")
    assert dops_paths

    for dops_path in dops_paths:
        base = dops_path.stem
        expected = read_json_fixture(f"golden/{base}.plan.json")

        result = build_plan(read_text_fixture(f"golden/{base}.dops"))
        assert result.ok
        assert result.plan is not None
        assert result.plan.to_dict() == expected


def test_build_plan_carries_target_scope_into_ops() -> None:
    text = (
        "target @main\n"
        "port category/name\n"
        'mk set VAR "one"\n'
        "target @2025Q1\n"
        "mk remove USES linux\n"
    )
    result = build_plan(text)

    assert result.ok
    assert result.plan is not None
    assert [op.target for op in result.plan.ops] == ["@main", "@2025Q1"]


def test_build_plan_uses_implicit_any_scope_before_first_target() -> None:
    text = 'port category/name\nmk set VAR "one"\n'
    result = build_plan(text)

    assert result.ok
    assert result.plan is not None
    assert [op.target for op in result.plan.ops] == ["@any"]


def test_build_plan_expands_multi_target_scope() -> None:
    text = 'target @2025Q4,@2026Q1\nport category/name\nmk set VAR "one"\n'
    result = build_plan(text)

    assert result.ok
    assert result.plan is not None
    assert [op.target for op in result.plan.ops] == ["@2025Q4", "@2026Q1"]


def test_build_plan_is_deterministic_for_ids() -> None:
    text = "target @main\nport category/name\nmk add USES ssl\n"
    first = build_plan(text)
    second = build_plan(text)

    assert first.ok and second.ok
    assert first.plan is not None and second.plan is not None

    first_ids = [op.id for op in first.plan.ops]
    second_ids = [op.id for op in second.plan.ops]
    assert first_ids == second_ids
    assert first.plan.to_dict() == second.plan.to_dict()


def test_build_plan_ignores_comments_and_whitespace_consistently() -> None:
    first_text = "target @main\nport category/name\nmk add USES ssl\n"
    second_text = "target @main  # comment\n\nport category/name\nmk add USES ssl\n"

    first = build_plan(first_text)
    second = build_plan(second_text)

    assert first.ok and second.ok
    assert first.plan is not None and second.plan is not None
    assert first.plan.to_dict() == second.plan.to_dict()


def test_build_plan_preserves_recipe_for_target_ops() -> None:
    text = (
        "target @main\n"
        "port category/name\n"
        "mk target set dfly-patch <<'MK'\n"
        "\tcmd1\n"
        "\tcmd2\n"
        "MK\n"
        "mk target append dfly-patch <<'APP'\n"
        "\tcmd3\n"
        "APP\n"
    )
    result = build_plan(text)

    assert result.ok
    assert result.plan is not None
    assert result.plan.ops[0].kind == "mk.target.set"
    assert result.plan.ops[0].payload["recipe"] == ["\tcmd1", "\tcmd2"]
    assert result.plan.ops[1].kind == "mk.target.append"
    assert result.plan.ops[1].payload["recipe"] == ["\tcmd3"]


def test_build_plan_maps_block_set_operation() -> None:
    text = (
        "target @main\n"
        "port category/name\n"
        'mk block set condition "defined(LITE)" contains "LITE" <<\'BLK\'\n'
        "\tPORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        "BLK\n"
    )
    result = build_plan(text)

    assert result.ok
    assert result.plan is not None
    assert len(result.plan.ops) == 1
    op = result.plan.ops[0]
    assert op.kind == "mk.block.set"
    assert op.payload["condition"] == "defined(LITE)"
    assert op.payload["contains"] == "LITE"
    assert op.payload["recipe"] == ["\tPORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"]


def test_compile_plan_reports_metadata_missing() -> None:
    document = AstDocument(
        span=_span(1),
        statements=[TargetDirective(span=_span(1), target="@main", targets=("@main",))],
    )
    result = compile_plan(document=document, scoped_ops=[])

    assert not result.ok
    assert any(d.code == "E_PLAN_METADATA_MISSING" for d in result.diagnostics)


def test_compile_plan_reports_unsupported_action() -> None:
    document = AstDocument(
        span=_span(1),
        statements=[
            TargetDirective(span=_span(1), target="@main", targets=("@main",)),
            PortDirective(span=_span(2), origin="category/name"),
        ],
    )
    scoped = [
        ScopedOperation(
            target="@main",
            operation=MkOpNode(span=_span(3), action="unknown-action"),
        )
    ]
    result = compile_plan(document=document, scoped_ops=scoped)

    assert not result.ok
    assert any(d.code == "E_PLAN_UNSUPPORTED_ACTION" for d in result.diagnostics)


def test_compile_plan_reports_invalid_operation() -> None:
    document = AstDocument(
        span=_span(1),
        statements=[
            TargetDirective(span=_span(1), target="@main", targets=("@main",)),
            PortDirective(span=_span(2), origin="category/name"),
        ],
    )
    scoped = [
        ScopedOperation(
            target="@main",
            operation=MkOpNode(span=_span(3), action="set", var=None, value=None),
        )
    ]
    result = compile_plan(document=document, scoped_ops=scoped)

    assert not result.ok
    assert any(d.code == "E_PLAN_INVALID_OPERATION" for d in result.diagnostics)
