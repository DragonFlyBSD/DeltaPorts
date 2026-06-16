"""Tests for the dops serializer (`engine/emit.py`).

The contract is plan-level round-trip: text produced by the builders parses
and plans into the intended ops. AST equality can't be the contract (nodes
carry source spans), so we compare normalized plan ops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from dportsv3.engine import emit
from dportsv3.engine.api import build_plan


def _plan_ops(text: str) -> list[dict]:
    result = build_plan(text, Path("overlay.dops"))
    assert result.ok, [d.code for d in result.diagnostics]
    assert result.plan is not None
    return [
        {k: v for k, v in op.items() if k not in ("id", "span")}
        for op in result.plan.to_dict()["ops"]
    ]


def _one_op(op_line: str) -> dict:
    doc = emit.overlay(emit.header(port="cat/name", type="port", reason="r"), [op_line])
    ops = _plan_ops(doc)
    assert len(ops) == 1
    return ops[0]


# --- header --------------------------------------------------------------

def test_header_canonical_field_order() -> None:
    assert emit.header(port="cat/name", type="dport", reason="why") == (
        "port cat/name\ntype dport\nreason \"why\"\ntarget @any"
    )


def test_header_with_maintainer_and_target() -> None:
    assert emit.header(
        port="cat/name", type="port", reason="r",
        target="@main", maintainer="m@e.org",
    ) == 'port cat/name\ntype port\nreason "r"\nmaintainer "m@e.org"\ntarget @main'


def test_header_rejects_unknown_type() -> None:
    with pytest.raises(ValueError):
        emit.header(port="cat/name", type="bogus", reason="r")


# --- scalar ops ----------------------------------------------------------

def test_mk_set_round_trip() -> None:
    assert _one_op(emit.mk_set("FOO", "a b")) == {
        "target": "@any", "kind": "mk.var.set", "name": "FOO", "value": "a b",
    }


def test_mk_eval_round_trip() -> None:
    op = _one_op(emit.mk_eval("OPTIONS_DEFAULT", "${OPTIONS_DEFAULT:NLLVM}"))
    assert op["kind"] == "mk.var.eval"
    assert op["value"] == "${OPTIONS_DEFAULT:NLLVM}"


def test_mk_shell_round_trip() -> None:
    op = _one_op(emit.mk_shell("PG_UID", "grep x | awk y; echo"))
    assert op["kind"] == "mk.var.shell"
    assert op["value"] == "grep x | awk y; echo"


def test_mk_unset_with_on_missing() -> None:
    assert _one_op(emit.mk_unset("DU", on_missing="warn")) == {
        "target": "@any", "kind": "mk.var.unset", "name": "DU", "on_missing": "warn",
    }


# --- token ops -----------------------------------------------------------

def test_mk_add_round_trip() -> None:
    op = _one_op(emit.mk_add("USES", "ssl"))
    assert op["kind"] == "mk.var.token_add"
    assert op["value"] == "ssl"


def test_mk_add_quotes_dependency_spec() -> None:
    # Tokens are always quoted (chars like > : would be fragile bare).
    line = emit.mk_add("RUN_DEPENDS", "p5-libwww>=0:www/p5-libwww")
    assert line == 'mk add RUN_DEPENDS "p5-libwww>=0:www/p5-libwww"'
    assert _one_op(line)["value"] == "p5-libwww>=0:www/p5-libwww"


def test_mk_remove_with_on_missing() -> None:
    op = _one_op(emit.mk_remove("OPTIONS_DEFAULT", "TERMCAP", on_missing="noop"))
    assert op["kind"] == "mk.var.token_remove"
    assert op["value"] == "TERMCAP"
    assert op["on_missing"] == "noop"


# --- block ops -----------------------------------------------------------

def test_mk_block_set_round_trip() -> None:
    op = _one_op(emit.mk_block_set("${DFLYVERSION} >= 400706", ["PLIST_FILES+=\tinc/x.h"]))
    assert op["kind"] == "mk.block.set"
    assert op["condition"] == "${DFLYVERSION} >= 400706"
    assert op["recipe"] == ["PLIST_FILES+=\tinc/x.h"]


def test_mk_disable_if_with_contains() -> None:
    op = _one_op(emit.mk_disable_if("${OPSYS} == FreeBSD", contains="openssl"))
    assert op["kind"] == "mk.block.disable"
    assert op["condition"] == "${OPSYS} == FreeBSD"
    assert op["contains"] == "openssl"


def test_mk_replace_if_round_trip() -> None:
    op = _one_op(emit.mk_replace_if("${X} == gcc", "${X} == gcc && ${OPSYS} == FreeBSD"))
    assert op["kind"] == "mk.block.replace_condition"
    assert op["from"] == "${X} == gcc"
    assert op["to"] == "${X} == gcc && ${OPSYS} == FreeBSD"


# --- target ops ----------------------------------------------------------

def test_mk_target_set_round_trip() -> None:
    op = _one_op(emit.mk_target_set("dfly-configure", ["\tcmd one", "\tcmd two"]))
    assert op["kind"] == "mk.target.set"
    assert op["name"] == "dfly-configure"
    assert op["recipe"] == ["\tcmd one", "\tcmd two"]


def test_mk_target_remove_and_rename() -> None:
    assert _one_op(emit.mk_target_remove("post-install", on_missing="noop"))["kind"] == (
        "mk.target.remove"
    )
    rename = _one_op(emit.mk_target_rename("do-build", "do-build-dfly"))
    assert rename["kind"] == "mk.target.rename"
    assert rename["old"] == "do-build"
    assert rename["new"] == "do-build-dfly"


# --- file ops ------------------------------------------------------------

def test_file_materialize_bare_path() -> None:
    line = emit.file_materialize("dragonfly/patch-x", "dragonfly/patch-x")
    assert line == "file materialize dragonfly/patch-x -> dragonfly/patch-x"
    op = _one_op(line)
    assert op == {
        "target": "@any", "kind": "file.materialize",
        "src": "dragonfly/patch-x", "dst": "dragonfly/patch-x",
    }


def test_file_remove_with_on_missing() -> None:
    op = _one_op(emit.file_remove("share/foo", on_missing="noop"))
    assert op == {
        "target": "@any", "kind": "file.remove",
        "path": "share/foo", "on_missing": "noop",
    }


def test_file_path_with_space_is_quoted() -> None:
    assert emit.file_materialize("a b/c", "d") == 'file materialize "a b/c" -> d'


# --- helpers -------------------------------------------------------------

def test_quote_escapes() -> None:
    assert emit.quote('a"b\\c\td\ne') == '"a\\"b\\\\c\\td\\ne"'


def test_on_missing_rejects_bad_value() -> None:
    with pytest.raises(ValueError):
        emit.mk_remove("V", "t", on_missing="bogus")


def test_heredoc_tag_avoids_recipe_collision() -> None:
    # A recipe line equal to the default tag forces a fresh tag.
    line = emit.mk_target_set("t", ["MK", "\treal"])
    assert "<<'MK1'" in line
    assert _one_op(line)["recipe"] == ["MK", "\treal"]


def test_overlay_structure() -> None:
    doc = emit.overlay(emit.header(port="c/n", type="port", reason="r"), [emit.mk_set("A", "b")])
    assert doc == 'port c/n\ntype port\nreason "r"\ntarget @any\n\nmk set A "b"\n'


def test_overlay_header_only() -> None:
    doc = emit.overlay(emit.header(port="c/n", type="dport", reason="r"), [])
    assert doc == 'port c/n\ntype dport\nreason "r"\ntarget @any\n'
