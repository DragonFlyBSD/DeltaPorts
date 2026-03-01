from __future__ import annotations

from dportsv3.engine.makefile_cst import parse_makefile_cst
from dportsv3.engine.makefile_rewrite import (
    find_condition,
    find_target,
    find_var_assignments,
    set_var,
    target_append,
    target_remove,
    target_rename,
    target_set,
    token_add,
    token_remove,
    unset_var,
)
from tests.dportsv3_testutils import read_text_fixture


def _doc(path: str):
    parsed = parse_makefile_cst(read_text_fixture(path))
    assert parsed.document is not None
    return parsed.document


def test_find_var_assignments_returns_source_order() -> None:
    document = _doc("makefile/simple.mk")
    rows = find_var_assignments(document, "USES")

    assert len(rows) == 1
    assert rows[0].operator == "+="


def test_set_unset_token_intents_are_deterministic() -> None:
    document = _doc("makefile/simple.mk")

    set_intent = set_var(document, "PORTNAME", "demo")
    unset_intent = unset_var(document, "USES")
    add_intent = token_add(document, "USES", "ssl")
    remove_intent = token_remove(document, "USES", "ssl")

    assert set_intent.action == "set_var"
    assert set_intent.node_indices == [0]
    assert unset_intent.node_indices == [1]
    assert add_intent.payload["token"] == "ssl"
    assert remove_intent.payload["token"] == "ssl"


def test_target_intents_resolve_existing_target() -> None:
    document = _doc("makefile/target_recipe.mk")

    found = find_target(document, "dfly-patch")
    set_intent = target_set(document, "dfly-patch", ["\t@echo x"])
    append_intent = target_append(document, "dfly-patch", ["\t@echo y"])
    remove_intent = target_remove(document, "dfly-patch")
    rename_intent = target_rename(document, "dfly-patch", "do-patch")

    assert len(found) == 1
    assert set_intent.node_indices == [0]
    assert append_intent.node_indices == [0]
    assert remove_intent.node_indices == [0]
    assert rename_intent.payload == {"old": "dfly-patch", "new": "do-patch"}


def test_ambiguous_intent_flags_when_multiple_matches() -> None:
    text = "USES+= ssl\nUSES+= x11\n"
    parsed = parse_makefile_cst(text)
    assert parsed.document is not None

    intent = token_add(parsed.document, "USES", "debug")
    assert intent.ambiguous is True
    assert intent.node_indices == [0, 1]


def test_find_condition_by_expr() -> None:
    document = _doc("makefile/conditional.mk")
    intent = find_condition(document, "${OPSYS} == DragonFly")

    assert intent.action == "find_condition"
    assert intent.node_indices == [0]


def test_find_condition_no_match_returns_empty() -> None:
    document = _doc("makefile/conditional.mk")
    intent = find_condition(document, "${OPSYS} == OpenBSD")

    assert intent.node_indices == []
    assert intent.ambiguous is False
