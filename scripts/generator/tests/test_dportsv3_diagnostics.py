from __future__ import annotations

from pathlib import Path

from dportsv3.engine.api import apply_dsl, build_plan, check_dsl, parse_dsl
from tests.dportsv3_testutils import read_text_fixture


def _codes(diagnostics) -> list[str]:
    return [d.code for d in diagnostics]


def _assert_prefixes(codes: list[str], allowed: tuple[str, ...]) -> None:
    assert codes
    for code in codes:
        assert code.startswith(allowed)


def test_parse_fixture_contract_codes_and_locations() -> None:
    fixtures = [
        ("invalid/parse/reason-missing-string.dops", "E_PARSE_EXPECTED_TOKEN"),
        ("invalid/parse/invalid-escape.dops", "E_PARSE_INVALID_ESCAPE"),
        ("invalid/parse/invalid-heredoc-start.dops", "E_PARSE_INVALID_HEREDOC_START"),
        ("invalid/parse/invalid-on-missing.dops", "E_PARSE_INVALID_ON_MISSING"),
    ]

    for relative, expected_code in fixtures:
        result = parse_dsl(read_text_fixture(relative))
        assert not result.ok
        assert expected_code in _codes(result.diagnostics)
        assert all(d.line is not None for d in result.diagnostics)
        assert all(d.column is not None for d in result.diagnostics)


def test_semantic_fixture_contract_codes_and_locations() -> None:
    fixtures = [
        ("invalid/semantic/missing-port.dops", "E_SEM_MISSING_PORT"),
        ("invalid/semantic/duplicate-port.dops", "E_SEM_DUPLICATE_PORT"),
        (
            "invalid/semantic/invalid-target-combination.dops",
            "E_SEM_INVALID_TARGET_SCOPE",
        ),
    ]

    for relative, expected_code in fixtures:
        result = check_dsl(read_text_fixture(relative))
        assert not result.ok
        assert expected_code in _codes(result.diagnostics)
        assert all(d.line is not None for d in result.diagnostics)
        assert all(d.column is not None for d in result.diagnostics)


def test_plan_diagnostics_surface_parse_and_semantic_errors() -> None:
    parse_result = build_plan(
        read_text_fixture("invalid/parse/reason-missing-string.dops")
    )
    semantic_result = build_plan(
        read_text_fixture("invalid/planner/semantic-gate-missing-port.dops")
    )

    assert not parse_result.ok
    assert "E_PARSE_EXPECTED_TOKEN" in _codes(parse_result.diagnostics)

    assert not semantic_result.ok
    assert "E_SEM_MISSING_PORT" in _codes(semantic_result.diagnostics)


def test_diagnostic_families_are_stable_by_stage(tmp_path: Path) -> None:
    parse_invalid = read_text_fixture("invalid/parse/reason-missing-string.dops")
    semantic_invalid = read_text_fixture("invalid/semantic/missing-port.dops")
    valid_apply = 'target @main\nport category/name\nmk set VAR "value"\n'

    parse_result = parse_dsl(parse_invalid)
    _assert_prefixes(_codes(parse_result.diagnostics), ("E_PARSE_",))

    check_parse_result = check_dsl(parse_invalid)
    _assert_prefixes(_codes(check_parse_result.diagnostics), ("E_PARSE_",))

    check_semantic_result = check_dsl(semantic_invalid)
    _assert_prefixes(_codes(check_semantic_result.diagnostics), ("E_SEM_",))

    plan_parse_result = build_plan(parse_invalid)
    _assert_prefixes(_codes(plan_parse_result.diagnostics), ("E_PARSE_",))

    plan_semantic_result = build_plan(semantic_invalid)
    _assert_prefixes(_codes(plan_semantic_result.diagnostics), ("E_SEM_",))

    apply_parse_result = apply_dsl(
        parse_invalid,
        source_path=tmp_path / "overlay.dops",
        port_root=tmp_path,
        target="@main",
        dry_run=True,
    )
    _assert_prefixes(_codes(apply_parse_result.diagnostics), ("E_PARSE_",))

    apply_runtime_result = apply_dsl(
        valid_apply,
        source_path=tmp_path / "overlay.dops",
        port_root=tmp_path,
        target="main",
        dry_run=True,
    )
    _assert_prefixes(_codes(apply_runtime_result.diagnostics), ("E_APPLY_",))
