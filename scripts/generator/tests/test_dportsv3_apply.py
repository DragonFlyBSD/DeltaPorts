from __future__ import annotations

import subprocess
from pathlib import Path

from dportsv3.engine.api import apply_dsl
from dportsv3.engine.apply import apply_plan
from dportsv3.engine.models import Plan, PlanOp
from dportsv3.engine.oracle import OracleResult


def test_apply_plan_invalid_target_fails(tmp_path: Path) -> None:
    plan = Plan(port="category/name", ops=[])
    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="main",
        dry_run=True,
        oracle_profile="off",
    )

    assert not result.ok
    assert any(d.code == "E_APPLY_INVALID_TARGET" for d in result.diagnostics)


def test_apply_plan_invalid_port_root_fails(tmp_path: Path) -> None:
    plan = Plan(port="category/name", ops=[])
    missing = tmp_path / "missing"
    result = apply_plan(
        plan,
        port_root=missing,
        target="@main",
        dry_run=True,
        oracle_profile="off",
    )

    assert not result.ok
    assert any(d.code == "E_APPLY_INVALID_PORT_ROOT" for d in result.diagnostics)


def test_apply_plan_target_mismatch_and_order_are_deterministic(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("VAR= old\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@2025Q1",
                kind="mk.var.set",
                payload={"name": "VAR", "value": "one"},
            ),
            PlanOp(
                id="op-2",
                target="@main",
                kind="mk.var.set",
                payload={"name": "VAR", "value": "two"},
            ),
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        oracle_profile="off",
    )

    assert result.ok
    assert [row.id for row in result.op_results] == ["op-2", "op-1"]
    assert result.op_results[0].status == "applied"
    assert result.op_results[1].status == "skipped"
    assert any(
        d.code == "I_APPLY_TARGET_MISMATCH" for d in result.op_results[1].diagnostics
    )


def test_apply_plan_applies_any_before_requested_target(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("VAR= old\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "VAR", "value": "main"},
            ),
            PlanOp(
                id="op-2",
                target="@any",
                kind="mk.var.set",
                payload={"name": "VAR", "value": "base"},
            ),
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert [row.id for row in result.op_results] == ["op-2", "op-1"]
    assert makefile.read_text() == "VAR= main\n"


def test_apply_plan_unknown_kind_strict_fail_fast(tmp_path: Path) -> None:
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(id="op-1", target="@main", kind="unknown.kind", payload={}),
            PlanOp(
                id="op-2",
                target="@main",
                kind="mk.var.set",
                payload={"name": "X", "value": "1"},
            ),
        ],
    )

    strict_result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        strict=True,
        oracle_profile="off",
    )
    loose_result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        strict=False,
        oracle_profile="off",
    )

    assert not strict_result.ok
    assert len(strict_result.op_results) == 1
    assert strict_result.op_results[0].status == "failed"
    assert any(
        d.code == "E_APPLY_UNKNOWN_KIND"
        for d in strict_result.op_results[0].diagnostics
    )

    assert not loose_result.ok
    assert len(loose_result.op_results) == 2


def test_apply_plan_file_copy_and_remove(tmp_path: Path) -> None:
    source = tmp_path / "src.txt"
    dest = tmp_path / "dst.txt"
    source.write_text("hello\n")

    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="file.copy",
                payload={"src": "src.txt", "dst": "dst.txt"},
            ),
            PlanOp(
                id="op-2",
                target="@main",
                kind="file.remove",
                payload={"path": "src.txt"},
            ),
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert all(row.status == "applied" for row in result.op_results)
    assert not source.exists()
    assert dest.read_text() == "hello\n"


def test_apply_plan_file_materialize_uses_source_root(tmp_path: Path) -> None:
    source_root = tmp_path / "delta" / "ports" / "category" / "name"
    source_root.mkdir(parents=True)
    (source_root / "dragonfly").mkdir(parents=True)
    (source_root / "dragonfly" / "patch-a").write_text("patch-content\n")

    port_root = tmp_path / "port"
    port_root.mkdir(parents=True)

    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="file.materialize",
                payload={"src": "dragonfly/patch-a", "dst": "dragonfly/patch-a"},
            )
        ],
    )

    result = apply_plan(
        plan,
        source_root=source_root,
        port_root=port_root,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert (port_root / "dragonfly" / "patch-a").read_text() == "patch-content\n"


def test_apply_plan_file_materialize_falls_back_to_port_root_when_source_root_missing(
    tmp_path: Path,
) -> None:
    (tmp_path / "source.txt").write_text("hello\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="file.materialize",
                payload={"src": "source.txt", "dst": "dst.txt"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert (tmp_path / "dst.txt").read_text() == "hello\n"


def test_apply_plan_file_materialize_missing_source_fails(tmp_path: Path) -> None:
    source_root = tmp_path / "delta"
    source_root.mkdir()
    port_root = tmp_path / "port"
    port_root.mkdir()
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="file.materialize",
                payload={"src": "missing.txt", "dst": "copied.txt"},
            )
        ],
    )

    result = apply_plan(
        plan,
        source_root=source_root,
        port_root=port_root,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert not result.ok
    assert result.failed_ops == 1
    assert any(
        d.code == "E_APPLY_MISSING_SUBJECT" for d in result.op_results[0].diagnostics
    )


def test_apply_plan_file_materialize_rejects_source_root_escape(tmp_path: Path) -> None:
    source_root = tmp_path / "delta"
    source_root.mkdir()
    (tmp_path / "outside.txt").write_text("secret\n")
    port_root = tmp_path / "port"
    port_root.mkdir()
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="file.materialize",
                payload={"src": "../outside.txt", "dst": "copied.txt"},
            )
        ],
    )

    result = apply_plan(
        plan,
        source_root=source_root,
        port_root=port_root,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert not result.ok
    assert result.failed_ops == 1
    assert any(
        d.code == "E_APPLY_INVALID_PATH" for d in result.op_results[0].diagnostics
    )


def test_apply_plan_text_replace_ambiguous_fails(tmp_path: Path) -> None:
    text_file = tmp_path / "notes.txt"
    text_file.write_text("A\nA\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="text.replace_once",
                payload={"file": "notes.txt", "from": "A", "to": "B"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert not result.ok
    assert result.failed_ops == 1
    assert any(
        d.code == "E_APPLY_AMBIGUOUS_MATCH" for d in result.op_results[0].diagnostics
    )


def test_apply_plan_mk_var_set_applies(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "PORTNAME", "value": "updated"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == "PORTNAME= updated\n"


def test_apply_plan_mk_var_set_creates_before_first_target(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\ndo-build:\n\t@true\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "PROBLEM_FILES", "value": "value"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\nPROBLEM_FILES= value\ndo-build:\n\t@true\n"
    )


def test_apply_plan_mk_var_set_creates_before_first_include(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n.include <bsd.port.post.mk>\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "PROBLEM_FILES", "value": "value"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\nPROBLEM_FILES= value\n.include <bsd.port.post.mk>\n"
    )


def test_apply_plan_mk_var_set_creates_at_eof_when_no_target_or_include(
    tmp_path: Path,
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "PROBLEM_FILES", "value": "value"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == "PORTNAME= sample\nPROBLEM_FILES= value\n"


def test_apply_plan_mk_var_set_missing_assignment_dry_run_reports_diff(
    tmp_path: Path,
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n.include <bsd.port.post.mk>\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "PROBLEM_FILES", "value": "value"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        emit_diff=True,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == "PORTNAME= sample\n.include <bsd.port.post.mk>\n"
    assert len(result.diffs) == 1
    assert result.diffs[0].path == "Makefile"
    assert "+PROBLEM_FILES= value" in result.diffs[0].diff


def test_apply_plan_mk_target_set_creates_before_bsd_port_mk(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n.include <bsd.port.mk>\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.target.set",
                payload={"name": "dfly-patch", "recipe": ["\t@true"]},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\ndfly-patch:\n\t@true\n\n.include <bsd.port.mk>\n"
    )


def test_apply_plan_mk_target_set_creates_before_last_include(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "PORTNAME= sample\n"
        ".include <bsd.port.options.mk>\n"
        ".include <bsd.port.pre.mk>\n"
        ".include <bsd.port.post.mk>\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.target.set",
                payload={"name": "post-stage", "recipe": ["\t@true"]},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\n"
        ".include <bsd.port.options.mk>\n"
        ".include <bsd.port.pre.mk>\n"
        "post-stage:\n"
        "\t@true\n"
        "\n"
        ".include <bsd.port.post.mk>\n"
    )


def test_apply_plan_mk_target_set_appends_without_include(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.target.set",
                payload={"name": "dfly-patch", "recipe": ["\t@true"]},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == "PORTNAME= sample\ndfly-patch:\n\t@true\n"


def test_apply_plan_mk_block_set_replaces_existing_if_block(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "PORTNAME= sample\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= OLD\n"
        ".endif\n"
        ".include <bsd.port.post.mk>\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.block.set",
                payload={
                    "condition": "defined(LITE)",
                    "recipe": ["PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"],
                },
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        ".endif\n"
        ".include <bsd.port.post.mk>\n"
    )


def test_apply_plan_mk_block_set_inserts_before_post_include(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n.include <bsd.port.post.mk>\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.block.set",
                payload={
                    "condition": "defined(LITE)",
                    "recipe": ["PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"],
                },
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        ".endif\n"
        "\n"
        ".include <bsd.port.post.mk>\n"
    )


def test_apply_plan_mk_block_set_inserts_before_bsd_port_mk(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n.include <bsd.port.mk>\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.block.set",
                payload={
                    "condition": "defined(LITE)",
                    "recipe": ["PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"],
                },
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        ".endif\n"
        "\n"
        ".include <bsd.port.mk>\n"
    )


def test_apply_plan_mk_block_set_inserts_before_last_include(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "PORTNAME= sample\n"
        ".include <bsd.port.options.mk>\n"
        ".include <bsd.port.pre.mk>\n"
        ".include <bsd.port.post.mk>\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.block.set",
                payload={
                    "condition": "defined(LITE)",
                    "recipe": ["PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"],
                },
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\n"
        ".include <bsd.port.options.mk>\n"
        ".include <bsd.port.pre.mk>\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        ".endif\n"
        "\n"
        ".include <bsd.port.post.mk>\n"
    )


def test_apply_plan_mk_block_set_duplicate_if_is_ambiguous(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "PORTNAME= sample\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= OLD1\n"
        ".endif\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= OLD2\n"
        ".endif\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.block.set",
                payload={
                    "condition": "defined(LITE)",
                    "recipe": ["PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"],
                },
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert not result.ok
    assert result.failed_ops == 1
    assert any(
        d.code == "E_APPLY_AMBIGUOUS_MATCH" for d in result.op_results[0].diagnostics
    )


def test_apply_plan_mk_block_set_contains_disambiguates(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "PORTNAME= sample\n"
        ".if defined(LITE)\n"
        "# lane-a\n"
        "PORT_OPTIONS+= OLD1\n"
        ".endif\n"
        ".if defined(LITE)\n"
        "# lane-b\n"
        "PORT_OPTIONS+= OLD2\n"
        ".endif\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.block.set",
                payload={
                    "condition": "defined(LITE)",
                    "contains": "lane-b",
                    "recipe": ["PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"],
                },
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\n"
        ".if defined(LITE)\n"
        "# lane-a\n"
        "PORT_OPTIONS+= OLD1\n"
        ".endif\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        ".endif\n"
    )


def test_apply_plan_mk_block_set_dry_run_diff_reports_changes(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n.include <bsd.port.post.mk>\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.block.set",
                payload={
                    "condition": "defined(LITE)",
                    "recipe": ["PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"],
                },
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        emit_diff=True,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == "PORTNAME= sample\n.include <bsd.port.post.mk>\n"
    assert len(result.diffs) == 1
    assert result.diffs[0].path == "Makefile"
    assert "+.if defined(LITE)" in result.diffs[0].diff
    assert "+PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS" in result.diffs[0].diff


def test_apply_plan_mk_block_set_matches_if_only_not_elif(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "PORTNAME= sample\n"
        ".if defined(BASE)\n"
        "USES+= ncurses\n"
        ".elif defined(LITE)\n"
        "PORT_OPTIONS+= OLD\n"
        ".endif\n"
        ".include <bsd.port.post.mk>\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.block.set",
                payload={
                    "condition": "defined(LITE)",
                    "recipe": ["PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS"],
                },
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert makefile.read_text() == (
        "PORTNAME= sample\n"
        ".if defined(BASE)\n"
        "USES+= ncurses\n"
        ".elif defined(LITE)\n"
        "PORT_OPTIONS+= OLD\n"
        ".endif\n"
        ".if defined(LITE)\n"
        "PORT_OPTIONS+= CSCOPE EXUBERANT_CTAGS\n"
        ".endif\n"
        "\n"
        ".include <bsd.port.post.mk>\n"
    )


def test_apply_plan_on_missing_warn_skips(tmp_path: Path) -> None:
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="file.remove",
                payload={"path": "missing.txt", "on_missing": "warn"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert result.skipped_ops == 1
    assert any(
        d.code == "W_APPLY_ON_MISSING_WARN" for d in result.op_results[0].diagnostics
    )


def test_apply_dsl_pipeline_dry_run_wiring(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("VAR= old\n")
    source = 'target @main\nport category/name\nmk set VAR "new"\n'
    result = apply_dsl(
        source,
        source_path=tmp_path / "overlay.dops",
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        strict=False,
        oracle_profile="off",
    )

    assert result.ok
    assert result.total_ops == 1
    assert result.applied_ops == 1
    assert result.failed_ops == 0
    assert makefile.read_text() == "VAR= old\n"


def test_apply_dsl_materialize_uses_overlay_source_root(tmp_path: Path) -> None:
    overlay_dir = tmp_path / "delta" / "ports" / "category" / "name"
    overlay_dir.mkdir(parents=True)
    (overlay_dir / "dragonfly").mkdir(parents=True)
    (overlay_dir / "dragonfly" / "patch-a").write_text("patch-content\n")

    port_root = tmp_path / "port"
    port_root.mkdir()
    source = (
        "target @main\n"
        "port category/name\n"
        "file materialize dragonfly/patch-a -> dragonfly/patch-a\n"
    )

    result = apply_dsl(
        source,
        source_path=overlay_dir / "overlay.dops",
        port_root=port_root,
        target="@main",
        dry_run=False,
        strict=False,
        oracle_profile="off",
    )

    assert result.ok
    assert (port_root / "dragonfly" / "patch-a").read_text() == "patch-content\n"


def test_apply_plan_dry_run_diff_preserves_files(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("VAR= old\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "VAR", "value": "new"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        emit_diff=True,
        oracle_profile="off",
    )

    assert result.ok
    assert result.applied_ops == 1
    assert makefile.read_text() == "VAR= old\n"
    assert len(result.diffs) == 1
    assert result.diffs[0].path == "Makefile"
    assert result.diffs[0].change_type == "modified"
    assert "--- a/Makefile" in result.diffs[0].diff
    assert "+VAR= new" in result.diffs[0].diff
    payload = result.to_dict()
    assert payload["report"]["report_version"] == "v1"
    assert payload["report"]["fallback_patch_count"] == 0


def test_apply_plan_patch_preview_counts_fallback_in_dry_run(tmp_path: Path) -> None:
    target_file = tmp_path / "file.txt"
    target_file.write_text("old\n")
    patch_file = tmp_path / "change.patch"
    patch_file.write_text("--- file.txt\n+++ file.txt\n@@ -1 +1 @@\n-old\n+new\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="patch.apply",
                payload={"path": "change.patch"},
            )
        ],
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        emit_diff=True,
        oracle_profile="off",
    )

    assert result.ok
    assert result.fallback_patch_count == 1
    assert target_file.read_text() == "old\n"
    assert any(diff.change_type == "fallback_patch" for diff in result.diffs)
    assert any(diff.path == "change.patch" for diff in result.diffs)


def test_apply_plan_patch_apply_uses_noninteractive_patch_command(
    tmp_path: Path, monkeypatch
) -> None:
    target_file = tmp_path / "file.txt"
    target_file.write_text("old\n")
    patch_file = tmp_path / "change.patch"
    patch_file.write_text("--- file.txt\n+++ file.txt\n@@ -1 +1 @@\n-old\n+new\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="patch.apply",
                payload={"path": "change.patch"},
            )
        ],
    )

    observed: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return _Proc()

    monkeypatch.setattr("dportsv3.engine.apply.subprocess.run", fake_run)

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    command = observed["command"]
    assert isinstance(command, list)
    assert "--batch" in command
    assert "--forward" in command
    assert "-V" in command
    assert "none" in command
    assert "-r" in command
    assert "-" in command
    assert observed["stdin"] is subprocess.DEVNULL
    assert observed["timeout"] == 30


def test_apply_plan_oracle_failure_rolls_back_when_strict(
    tmp_path: Path, monkeypatch
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("VAR= old\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "VAR", "value": "new"},
            )
        ],
    )

    monkeypatch.setattr(
        "dportsv3.engine.apply.run_bmake_oracle",
        lambda *_args, **_kwargs: OracleResult(
            ok=False,
            profile="local",
            checks_run=1,
            failures=["oracle failed"],
        ),
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=False,
        strict=True,
        oracle_profile="local",
    )

    assert not result.ok
    assert result.oracle_failures == 1
    assert makefile.read_text() == "VAR= old\n"
    assert any(d.code == "E_APPLY_ORACLE_FAILED" for d in result.diagnostics)


def test_apply_plan_oracle_dry_run_checks_staged_view(
    tmp_path: Path, monkeypatch
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("VAR= old\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "VAR", "value": "new"},
            )
        ],
    )

    def fake_oracle(root: Path, **_kwargs):
        assert (root / "Makefile").read_text() == "VAR= new\n"
        return OracleResult(ok=True, profile="local", checks_run=1)

    monkeypatch.setattr("dportsv3.engine.apply.run_bmake_oracle", fake_oracle)

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        oracle_profile="local",
    )

    assert result.ok
    assert result.oracle_checks == 1
    assert makefile.read_text() == "VAR= old\n"


def test_apply_plan_ci_unavailable_oracle_fails_without_strict(
    tmp_path: Path, monkeypatch
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("VAR= old\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.set",
                payload={"name": "VAR", "value": "new"},
            )
        ],
    )

    monkeypatch.setattr(
        "dportsv3.engine.apply.run_bmake_oracle",
        lambda *_args, **_kwargs: OracleResult(
            ok=False,
            profile="ci",
            checks_run=0,
            failures=["bmake not found in PATH"],
            unavailable=True,
            skipped=True,
        ),
    )

    result = apply_plan(
        plan,
        port_root=tmp_path,
        target="@main",
        dry_run=True,
        strict=False,
        oracle_profile="ci",
    )

    assert not result.ok
    assert result.oracle_failures == 1
    assert any(d.code == "E_APPLY_ORACLE_UNAVAILABLE" for d in result.diagnostics)
