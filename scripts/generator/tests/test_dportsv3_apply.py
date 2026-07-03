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


def test_apply_plan_mk_var_eval_appends_immediate_before_include(
    tmp_path: Path,
) -> None:
    # mk eval appends a verbatim immediate `:=` line before the final include,
    # reproducing a trailing Makefile.DragonFly fragment. Upstream assignment
    # is left intact, so the self-reference expands the accumulated value and
    # is NOT recursive (unlike a `mk set` rewrite, which would render `=`).
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "OPTIONS_DEFAULT= STUNNEL\n\n.include <bsd.port.mk>\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.eval",
                payload={
                    "name": "OPTIONS_DEFAULT",
                    "value": "${OPTIONS_DEFAULT:NSTUNNEL}",
                },
            )
        ],
    )

    result = apply_plan(
        plan, port_root=tmp_path, target="@main", dry_run=False, oracle_profile="off"
    )

    assert result.ok
    assert makefile.read_text() == (
        "OPTIONS_DEFAULT= STUNNEL\n"
        "\n"
        "OPTIONS_DEFAULT:= ${OPTIONS_DEFAULT:NSTUNNEL}\n"
        ".include <bsd.port.mk>\n"
    )


def test_apply_plan_mk_var_eval_appends_at_eof_without_include(tmp_path: Path) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("USES= cargo\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.eval",
                payload={"name": "USES", "value": "${USES:S/cargo/cargo:extra/}"},
            )
        ],
    )

    result = apply_plan(
        plan, port_root=tmp_path, target="@main", dry_run=False, oracle_profile="off"
    )

    assert result.ok
    assert makefile.read_text() == "USES= cargo\nUSES:= ${USES:S/cargo/cargo:extra/}\n"


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


def test_apply_plan_mk_var_token_add_creates_when_subject_missing(tmp_path: Path) -> None:
    """`mk add VAR token` against an undefined VAR must create the
    assignment (matches make's `+=` semantics). The prior strict
    refusal was tighter than make and broke faithful translation of
    legacy Makefile.DragonFly artifacts (audio/cdparanoia 2026-05-26:
    convert emitted `mk add CFLAGS -D__FreeBSD_version=900001` from
    `CFLAGS+= ...`, executor rejected because upstream Makefile had
    no `CFLAGS=` line).
    """
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\ndo-build:\n\t@true\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.token_add",
                payload={"name": "CFLAGS", "value": "-D__FreeBSD_version=900001"},
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

    assert result.ok, [d.code for d in result.op_results[0].diagnostics]
    assert result.op_results[0].message == "mk-token-created"
    assert makefile.read_text() == (
        "PORTNAME= sample\nCFLAGS= -D__FreeBSD_version=900001\ndo-build:\n\t@true\n"
    )


def test_apply_plan_mk_var_token_add_appends_when_subject_present(tmp_path: Path) -> None:
    """When the subject variable exists, `mk add` appends the token —
    behavior unchanged from before the create-or-append fix."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\nUSES= cmake\ndo-build:\n\t@true\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.token_add",
                payload={"name": "USES", "value": "ssl"},
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
    assert result.op_results[0].message == "mk-token-added"
    assert "USES= cmake ssl" in makefile.read_text()


def test_apply_plan_mk_var_token_remove_handles_continued_assignment(
    tmp_path: Path,
) -> None:
    """`mk remove` on a `\\`-continued multi-line assignment must drop the
    token without turning the line-continuation backslashes into literal
    tokens (the bug that rendered `RUN_DEPENDS= a \\ b \\ c`). The value
    flattens to one clean line and the original operator is preserved."""
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "PORTNAME= sample\n"
        "RUN_DEPENDS=\ta>0:cat/a \\\n"
        "\t\tb>0:cat/b \\\n"
        "\t\tc>0:cat/c\n"
        ".include <bsd.port.mk>\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.token_remove",
                payload={"name": "RUN_DEPENDS", "value": "b>0:cat/b"},
            )
        ],
    )

    result = apply_plan(
        plan, port_root=tmp_path, target="@main", dry_run=False, oracle_profile="off",
    )

    assert result.ok, [d.code for d in result.op_results[0].diagnostics]
    out = makefile.read_text()
    assert "RUN_DEPENDS= a>0:cat/a c>0:cat/c\n" in out
    assert "b>0:cat/b" not in out
    # No stray continuation backslashes leaked into the rewritten assignment.
    assert "\\" not in out.split(".include")[0]


def test_apply_plan_mk_ensure_include_inserts_before_terminal_include(
    tmp_path: Path,
) -> None:
    """`mk ensure-include` gives a terminal-only port the framework-var-
    defining `bsd.port.options.mk`, inserted above the terminal include so a
    following `${DFLYVERSION}`/`${OPSYS}` conditional resolves."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\n\n.include <bsd.port.mk>\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.include.ensure",
                payload={"name": "bsd.port.options.mk"},
            )
        ],
    )

    result = apply_plan(
        plan, port_root=tmp_path, target="@main", dry_run=False, oracle_profile="off",
    )

    assert result.ok, [d.code for d in result.op_results[0].diagnostics]
    assert result.op_results[0].message == "mk-include-ensured"
    out = makefile.read_text()
    assert ".include <bsd.port.options.mk>" in out
    assert out.index("bsd.port.options.mk") < out.index("<bsd.port.mk>")


def test_apply_plan_mk_ensure_include_idempotent_when_present(tmp_path: Path) -> None:
    """No-op when the include is already present — no duplicate."""
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        "PORTNAME= sample\n.include <bsd.port.options.mk>\n.include <bsd.port.mk>\n"
    )
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.include.ensure",
                payload={"name": "bsd.port.options.mk"},
            )
        ],
    )

    result = apply_plan(
        plan, port_root=tmp_path, target="@main", dry_run=False, oracle_profile="off",
    )

    assert result.ok
    assert result.op_results[0].message == "mk-include-present"
    assert makefile.read_text().count("bsd.port.options.mk") == 1


def test_apply_plan_mk_var_token_add_preserves_plus_equals_operator(
    tmp_path: Path,
) -> None:
    """Single-match append must preserve the original operator. Pre-fix
    code rewrote `+=` lines as `=`, which silently changed semantics
    when the assignment lived inside an `.if` block or relied on `+=`
    accumulation across multiple files."""
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\nUSES+= cmake\ndo-build:\n\t@true\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.token_add",
                payload={"name": "USES", "value": "ssl"},
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
    assert result.op_results[0].message == "mk-token-added"
    assert "USES+= cmake ssl" in makefile.read_text()
    assert "USES= cmake" not in makefile.read_text()


def test_apply_plan_mk_var_token_add_preserves_question_equals_operator(
    tmp_path: Path,
) -> None:
    makefile = tmp_path / "Makefile"
    makefile.write_text("PORTNAME= sample\nUSES?= cmake\ndo-build:\n\t@true\n")
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.token_add",
                payload={"name": "USES", "value": "ssl"},
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
    assert "USES?= cmake ssl" in makefile.read_text()


def test_apply_plan_mk_var_token_add_multi_assignment_appends_new_line(
    tmp_path: Path,
) -> None:
    """When multiple assignments to the same name exist, the executor
    must not pick one to rewrite (semantics differ across `.if`
    branches and operator mixes). Instead it appends a fresh `+=`
    line, which `make` flattens at evaluation time."""
    makefile = tmp_path / "Makefile"
    original = (
        "PORTNAME= sample\n"
        "PLIST_SUB= ABI=foo\n"
        "PLIST_SUB+= OSMAJOR=10\n"
        ".include <bsd.port.mk>\n"
    )
    makefile.write_text(original)
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.token_add",
                payload={"name": "PLIST_SUB", "value": "NEWKEY=val"},
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
    assert result.op_results[0].message == "mk-token-added"
    contents = makefile.read_text()
    assert "PLIST_SUB= ABI=foo\n" in contents
    assert "PLIST_SUB+= OSMAJOR=10\n" in contents
    assert "PLIST_SUB+= NEWKEY=val\n" in contents


def test_apply_plan_mk_var_token_add_multi_assignment_token_already_present(
    tmp_path: Path,
) -> None:
    makefile = tmp_path / "Makefile"
    original = (
        "PORTNAME= sample\n"
        "PLIST_SUB= ABI=foo\n"
        "PLIST_SUB+= OSMAJOR=10\n"
        ".include <bsd.port.mk>\n"
    )
    makefile.write_text(original)
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.token_add",
                payload={"name": "PLIST_SUB", "value": "OSMAJOR=10"},
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
    assert result.op_results[0].message == "mk-token-exists"
    assert makefile.read_text() == original


def test_apply_plan_mk_var_token_add_continued_assignment_appends_new_line(
    tmp_path: Path,
) -> None:
    """A single matched assignment that uses line continuation must not
    be rewritten in place — the `\\` would survive as a literal token
    when splitting the value. Falls through to the append-new-line
    branch, leaving the original continued block intact."""
    makefile = tmp_path / "Makefile"
    original = (
        "PORTNAME= sample\n"
        "PLIST_SUB= ABI=foo \\\n"
        "           OSMAJOR=10\n"
        ".include <bsd.port.mk>\n"
    )
    makefile.write_text(original)
    plan = Plan(
        port="category/name",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="mk.var.token_add",
                payload={"name": "PLIST_SUB", "value": "NEWKEY=val"},
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
    assert result.op_results[0].message == "mk-token-added"
    contents = makefile.read_text()
    assert "PLIST_SUB= ABI=foo \\\n           OSMAJOR=10\n" in contents
    assert "PLIST_SUB+= NEWKEY=val\n" in contents
    # Original continued assignment must not have been collapsed.
    assert " \\ " not in contents


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


def test_apply_dsl_materialize_preserves_non_utf8_bytes(tmp_path: Path) -> None:
    # file.materialize is a verbatim byte copy — a Latin-1 patch with a
    # 0xa0 byte must not blow up the UTF-8 text path (the po4a failure).
    overlay_dir = tmp_path / "delta" / "ports" / "category" / "name"
    (overlay_dir / "dragonfly").mkdir(parents=True)
    raw = b"diff with \xa0 latin1 nbsp\n"
    (overlay_dir / "dragonfly" / "patch-b").write_bytes(raw)

    port_root = tmp_path / "port"
    port_root.mkdir()
    result = apply_dsl(
        "target @main\nport category/name\n"
        "file materialize dragonfly/patch-b -> dragonfly/patch-b\n",
        source_path=overlay_dir / "overlay.dops",
        port_root=port_root,
        target="@main",
        dry_run=False,
        oracle_profile="off",
    )

    assert result.ok
    assert (port_root / "dragonfly" / "patch-b").read_bytes() == raw


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


def test_apply_plan_patch_apply_resolves_via_source_root(tmp_path: Path) -> None:
    """patch.apply reads the patch FILE from source_root (the
    overlay tree where the dops file lives) — not from port_root
    (compose's materialized output). Compose calls apply_plan with
    distinct source_root and port_root; under dops-mode compose
    no longer copies dragonfly/patch-* into port_root
    (I_COMPOSE_MODE_DOPS_SUPPRESSES_COMPAT), so the executor must
    resolve the patch path against the overlay.

    Before the apply.py:68 fix this test would fail with
    E_APPLY_MISSING_SUBJECT because the executor looked in
    port_root which never had the patch file.
    """
    source = tmp_path / "src" / "ports" / "cat" / "port"
    port = tmp_path / "out" / "cat" / "port"
    (source / "dragonfly").mkdir(parents=True)
    port.mkdir(parents=True)
    # The target source lives in port_root (where compose materialized it).
    (port / "file.txt").write_text("old\n")
    # The patch file lives in the source overlay at dragonfly/.
    (source / "dragonfly" / "patch-file.txt").write_text(
        "--- file.txt\n+++ file.txt\n@@ -1 +1 @@\n-old\n+new\n"
    )

    plan = Plan(
        port="cat/port",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="patch.apply",
                payload={"path": "dragonfly/patch-file.txt"},
            )
        ],
    )
    result = apply_plan(
        plan,
        source_root=source,
        port_root=port,
        target="@main",
        oracle_profile="off",
    )
    assert result.ok, [
        (d.code, d.message) for d in result.op_results[0].diagnostics
    ]
    # Patch actually applied — the target was rewritten in port_root.
    assert (port / "file.txt").read_text() == "new\n"


def test_apply_plan_patch_apply_missing_in_source_root(tmp_path: Path) -> None:
    """If the patch file is missing from source_root, the diagnostic
    points at source_root (not port_root) — distinguishes 'forgot
    to ship the patch' from 'compose didn't copy it'."""
    source = tmp_path / "src" / "ports" / "cat" / "port"
    port = tmp_path / "out" / "cat" / "port"
    source.mkdir(parents=True)
    port.mkdir(parents=True)
    (port / "file.txt").write_text("old\n")
    # NB: no dragonfly/ directory under source — the patch is missing.

    plan = Plan(
        port="cat/port",
        ops=[
            PlanOp(
                id="op-1",
                target="@main",
                kind="patch.apply",
                payload={"path": "dragonfly/patch-missing"},
            )
        ],
    )
    result = apply_plan(
        plan,
        source_root=source,
        port_root=port,
        target="@main",
        oracle_profile="off",
    )
    assert not result.ok
    diags = result.op_results[0].diagnostics
    assert any(d.code == "E_APPLY_MISSING_SUBJECT" for d in diags)
    # The diagnostic's source_path is inside source_root, not port_root.
    err = next(d for d in diags if d.code == "E_APPLY_MISSING_SUBJECT")
    assert str(source) in str(err.source_path)


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


def test_apply_plan_local_unavailable_oracle_warning_keeps_ok(
    tmp_path: Path, monkeypatch
) -> None:
    """A clean apply stays ok=True when the only diagnostic is a
    warning-severity oracle skip (bmake unavailable under the local profile).

    Regression: ``ok = not diagnostics and ...`` used to treat any diagnostic
    -- including a warning -- as failure, so every dops port mis-reported
    E_COMPOSE_APPLY_FAILED on hosts without bmake.
    """
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
            ok=True,
            profile="local",
            checks_run=0,
            warnings=["bmake not found in PATH"],
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
        oracle_profile="local",
    )

    assert result.failed_ops == 0
    assert result.error_count == 0
    assert result.warning_count == 1
    assert any(d.code == "W_APPLY_ORACLE_SKIPPED" for d in result.diagnostics)
    assert result.ok
