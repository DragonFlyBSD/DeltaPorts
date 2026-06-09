from __future__ import annotations

import json
from pathlib import Path

from dportsv3.cli import main
from dportsv3.migration.batch import run_batch
from dportsv3.migration.classify import classify_inventory
from dportsv3.migration.convert import convert_record
from dportsv3.migration.dashboard import build_migration_dashboard
from dportsv3.migration.inventory import scan_inventory
from dportsv3.migration.policy import evaluate_forward_policy
from dportsv3.migration.progress import evaluate_completion
from dportsv3.migration.touched import extract_touched_origins


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "ports" / "devel" / "tool").mkdir(parents=True)
    (repo / "ports" / "devel" / "tool" / "Makefile.DragonFly").write_text(
        'USES+= ssl\nBROKEN= broken on xyz\ndfly-patch:\n\t${REINPLACE_CMD} -e "s/a/b/" file\n'
    )

    (repo / "ports" / "sysutils" / "raw").mkdir(parents=True)
    (repo / "ports" / "sysutils" / "raw" / "diffs").mkdir()
    (repo / "ports" / "sysutils" / "raw" / "diffs" / "Makefile.diff").write_text(
        "--- a/Makefile\n+++ b/Makefile\n"
    )
    return repo


def test_inventory_and_classification_pipeline(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    inventory = scan_inventory(repo)

    assert len(inventory) == 2
    origins = [row["origin"] for row in inventory]
    assert origins == ["devel/tool", "sysutils/raw"]
    by_origin_inventory = {row["origin"]: row for row in inventory}
    assert by_origin_inventory["devel/tool"]["target_mode"] == "baseline"
    assert by_origin_inventory["devel/tool"]["available_targets"] == ["@any"]
    assert by_origin_inventory["sysutils/raw"]["target_mode"] == "baseline"
    assert by_origin_inventory["sysutils/raw"]["available_targets"] == ["@any"]

    classified = classify_inventory(inventory)
    by_origin = {row["origin"]: row for row in classified}
    assert by_origin["devel/tool"]["bucket"] == "auto-safe"
    assert by_origin["sysutils/raw"]["bucket"] == "fallback-only"


def test_converter_writes_overlay_dops_for_auto_safe(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    inventory = scan_inventory(repo)
    classified = classify_inventory(inventory)
    record = next(row for row in classified if row["origin"] == "devel/tool")

    result = convert_record(record, repo_root=repo, dry_run=False)
    dops_path = repo / "ports" / "devel" / "tool" / "overlay.dops"

    assert result["status"] == "converted"
    assert result["parse_ok"] is True
    assert result["check_ok"] is True
    assert result["plan_ok"] is True
    assert result["deterministic_ok"] is True
    assert dops_path.exists()
    # The legacy source file must be removed after a successful
    # conversion: leaving it in place keeps `classify_dops` returning
    # `auto_safe_pending`, which loops the runner.
    assert not (repo / "ports" / "devel" / "tool" / "Makefile.DragonFly").exists()
    # Regression: deterministic-converted overlays must declare
    # `target @any`. auto_safe_pending only fires for UNSCOPED
    # Makefile.DragonFly (the classifier excludes
    # Makefile.DragonFly.@xxx variants), so the dops translation
    # must be target-agnostic. The prior hardcoded `target @main`
    # produced silently-dead overlays on every env whose target
    # wasn't @main (archivers/liblz4 2026-05-26).
    dops_text = dops_path.read_text()
    assert "target @any" in dops_text
    assert "target @main" not in dops_text


def test_converter_removes_legacy_makefile_on_revalidate(tmp_path: Path) -> None:
    """Second-pass `convert_record` against an already-converted port
    must still drop a stray Makefile.DragonFly (e.g. one re-introduced
    by a partial revert) so the runner can escape the
    auto_safe_pending loop without a fresh re-render."""
    repo = _make_repo(tmp_path)
    inventory = scan_inventory(repo)
    classified = classify_inventory(inventory)
    record = next(row for row in classified if row["origin"] == "devel/tool")

    convert_record(record, repo_root=repo, dry_run=False)
    port = repo / "ports" / "devel" / "tool"
    (port / "Makefile.DragonFly").write_text("dummy resurrected\n")

    result = convert_record(record, repo_root=repo, dry_run=False)

    assert result["status"] == "converted"
    assert not (port / "Makefile.DragonFly").exists()
    assert (port / "overlay.dops").exists()


def test_converter_dry_run_leaves_makefile_alone(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    inventory = scan_inventory(repo)
    classified = classify_inventory(inventory)
    record = next(row for row in classified if row["origin"] == "devel/tool")

    result = convert_record(record, repo_root=repo, dry_run=True)

    assert result["status"] == "converted"
    assert (repo / "ports" / "devel" / "tool" / "Makefile.DragonFly").exists()
    assert not (repo / "ports" / "devel" / "tool" / "overlay.dops").exists()


def test_converter_drops_default_status(tmp_path: Path) -> None:
    """A `PORT` (default-type) STATUS is part of the dead compat-overlay
    set once overlay.dops exists; the deterministic converter must drop
    it, matching the LLM path's files_removed cleanup. Without this the
    port looks half-migrated (overlay.dops live, STATUS orphaned)."""
    repo = _make_repo(tmp_path)
    port = repo / "ports" / "devel" / "tool"
    (port / "STATUS").write_text("PORT\nLast success: 1.0\n")
    classified = classify_inventory(scan_inventory(repo))
    record = next(row for row in classified if row["origin"] == "devel/tool")

    result = convert_record(record, repo_root=repo, dry_run=False)

    assert result["status"] == "converted"
    assert (port / "overlay.dops").exists()
    assert not (port / "Makefile.DragonFly").exists()
    assert not (port / "STATUS").exists()


def test_converter_keeps_nondefault_status(tmp_path: Path) -> None:
    """The deterministic translator always renders `type port`. If
    STATUS declares MASK/DPORT/LOCK, deleting it would silently switch
    the port's role to `port`, so STATUS is retained — the surviving
    file surfaces the (wrong) `type port` half-migration for review."""
    repo = _make_repo(tmp_path)
    port = repo / "ports" / "devel" / "tool"
    (port / "STATUS").write_text("MASK\nLast success: 1.0\n")
    classified = classify_inventory(scan_inventory(repo))
    record = next(row for row in classified if row["origin"] == "devel/tool")

    result = convert_record(record, repo_root=repo, dry_run=False)

    assert result["status"] == "converted"
    assert (port / "overlay.dops").exists()
    assert not (port / "Makefile.DragonFly").exists()
    assert (port / "STATUS").exists()


def test_converter_dry_run_leaves_status_alone(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    port = repo / "ports" / "devel" / "tool"
    (port / "STATUS").write_text("PORT\n")
    classified = classify_inventory(scan_inventory(repo))
    record = next(row for row in classified if row["origin"] == "devel/tool")

    result = convert_record(record, repo_root=repo, dry_run=True)

    assert result["status"] == "converted"
    assert (port / "STATUS").exists()


def test_batch_policy_and_progress_reports(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)
    classified = classify_inventory(scan_inventory(repo))

    batch = run_batch(
        classified,
        repo_root=repo,
        buckets=["auto-safe", "fallback-only"],
        dry_run=True,
        max_ports=10,
    )
    assert batch["artifacts"]["converted_count"] == 1
    assert batch["artifacts"]["fallback_count"] == 1

    policy = evaluate_forward_policy(
        classified,
        touched_origins=["devel/tool", "sysutils/raw"],
    )
    assert policy["pass"] is False
    assert policy["violation_count"] >= 1
    assert policy["policy_version"] == "v1"
    assert policy["summary"]["touched_violation_count"] >= 1

    progress = evaluate_completion(classified, conversion_results=batch["results"])
    assert progress["in_scope_total"] == 2
    assert progress["auto_safe_total"] == 1
    assert progress["progress_version"] == "v1"
    assert "classification_coverage" in progress["ratios"]

    dashboard = build_migration_dashboard(
        classified,
        conversion_results=batch["results"],
        touched_origins=["devel/tool", "sysutils/raw"],
        strict_policy=True,
        strict_progress=False,
        metadata={"root": str(repo)},
    )
    assert dashboard["dashboard_version"] == "v1"
    assert dashboard["metadata"]["root"] == str(repo)
    assert dashboard["gates"]["policy_pass"] is False
    assert dashboard["gates"]["ci_pass"] is False


def test_cli_inventory_classify_convert_batch_policy_progress(
    tmp_path: Path, capsys
) -> None:
    repo = _make_repo(tmp_path)
    inventory_path = tmp_path / "inventory.json"
    classify_path = tmp_path / "classified.json"
    touched_path = tmp_path / "touched.txt"
    touched_path.write_text("devel/tool\nsysutils/raw\n")

    inv_code = main(["migrate", "inventory", "--root", str(repo), "--json"])
    inv_out = capsys.readouterr()
    assert inv_code == 0
    inv_payload = json.loads(inv_out.out)
    inventory_path.write_text(inv_out.out)
    assert inv_payload["record_total"] == 2

    class_code = main(["migrate", "classify", str(inventory_path), "--json"])
    class_out = capsys.readouterr()
    assert class_code == 0
    class_payload = json.loads(class_out.out)
    classify_path.write_text(class_out.out)
    assert class_payload["record_total"] == 2

    convert_code = main(
        [
            "migrate",
            "convert",
            str(classify_path),
            "devel/tool",
            "--root",
            str(repo),
            "--json",
        ]
    )
    convert_out = capsys.readouterr()
    assert convert_code == 0
    convert_payload = json.loads(convert_out.out)
    assert convert_payload["status"] == "converted"

    batch_code = main(
        [
            "migrate",
            "batch",
            str(classify_path),
            "--root",
            str(repo),
            "--bucket",
            "auto-safe",
            "--bucket",
            "fallback-only",
            "--json",
        ]
    )
    batch_out = capsys.readouterr()
    assert batch_code == 0
    batch_payload = json.loads(batch_out.out)
    assert batch_payload["report"]["total"] >= 1

    policy_code = main(
        [
            "migrate",
            "policy-check",
            str(classify_path),
            "--touched",
            str(touched_path),
            "--json",
        ]
    )
    policy_out = capsys.readouterr()
    assert policy_code == 0
    policy_payload = json.loads(policy_out.out)
    assert "pass" in policy_payload

    progress_code = main(
        [
            "migrate",
            "progress",
            str(classify_path),
            "--json",
        ]
    )
    progress_out = capsys.readouterr()
    assert progress_code == 0
    progress_payload = json.loads(progress_out.out)
    assert "operationally_complete" in progress_payload

    dashboard_code = main(
        [
            "migrate",
            "dashboard",
            str(classify_path),
            "--touched",
            str(touched_path),
            "--strict-policy",
            "--json",
        ]
    )
    dashboard_out = capsys.readouterr()
    assert dashboard_code == 2
    dashboard_payload = json.loads(dashboard_out.out)
    assert dashboard_payload["dashboard_version"] == "v1"
    assert dashboard_payload["gates"]["policy_pass"] is False


def test_converter_self_referential_assignment_emits_mk_eval(tmp_path: Path) -> None:
    """A self-referential assignment (`VAR:= ${VAR:mod}`) is the BSD filter/
    transform idiom, not a literal self-set. `mk set` would render a fatal
    recursive `=`; the converter emits `mk eval` (verbatim immediate `:=`
    append) for any modifier. Non-self-referential values stay `mk set`."""
    from dportsv3.engine.api import parse_dsl
    from dportsv3.migration.convert import _parse_makefile_dragonfly, _render_dops

    mk = tmp_path / "Makefile.DragonFly"
    mk.write_text(
        "OPTIONS_DEFAULT:=\t${OPTIONS_DEFAULT:NSTUNNEL}\n"
        "USES:=\t${USES:S/cargo/cargo:extra/}\n"
        "LDFLAGS:=\t-L${LOCALBASE}/lib ${LDFLAGS}\n"
        "USE_GCC_VERSION=\t${GCC_DEFAULT}\n"
    )
    ops, errors = _parse_makefile_dragonfly(mk)

    assert errors == []
    assert 'mk eval OPTIONS_DEFAULT "${OPTIONS_DEFAULT:NSTUNNEL}"' in ops
    assert 'mk eval USES "${USES:S/cargo/cargo:extra/}"' in ops
    assert 'mk eval LDFLAGS "-L${LOCALBASE}/lib ${LDFLAGS}"' in ops
    # references a DIFFERENT variable -> not self-referential -> mk set
    assert 'mk set USE_GCC_VERSION "${GCC_DEFAULT}"' in ops
    assert parse_dsl(_render_dops("devel/x", ops), Path("overlay.dops")).ok


def test_converter_quotes_special_char_plus_assign(tmp_path: Path) -> None:
    """Regression: `+=` values with non-word chars (`>`, `:`, embedded
    `"`) must be emitted as quoted `mk add` tokens, not bare — a bare
    token with those chars produces unparseable dops
    (E_PARSE_EXPECTED_NEWLINE / E_PARSE_UNEXPECTED_CHAR)."""
    from dportsv3.engine.api import parse_dsl
    from dportsv3.migration.convert import _parse_makefile_dragonfly, _render_dops

    mk = tmp_path / "Makefile.DragonFly"
    mk.write_text(
        "BUILD_DEPENDS+= cxxfs_gcc8>0:misc/cxxfs_gcc8\n"
        'CMAKE_ARGS+= -DUSER_LIBS:STRING="stdc++fs"\n'
    )
    ops, errors = _parse_makefile_dragonfly(mk)

    assert errors == []
    assert 'mk add BUILD_DEPENDS "cxxfs_gcc8>0:misc/cxxfs_gcc8"' in ops
    assert 'mk add CMAKE_ARGS "-DUSER_LIBS:STRING=\\"stdc++fs\\""' in ops
    # the rendered overlay must parse cleanly
    assert parse_dsl(_render_dops("devel/x", ops), Path("overlay.dops")).ok


def test_extract_touched_origins_helper_is_deterministic() -> None:
    changed_paths = [
        "ports/devel/tool/overlay.dops",
        "./ports/devel/tool/diffs/@main/Makefile.diff",
        "ports/sysutils/raw/Makefile.DragonFly",
        "scripts/generator/dportsv3/cli.py",
        "ports/.hidden/ignore/overlay.dops",
    ]
    assert extract_touched_origins(changed_paths) == ["devel/tool", "sysutils/raw"]
