from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import dportsv3.compose as compose_module
from dportsv3.cli import main
from dportsv3.engine.models import ApplyContext, ApplyResult


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_freebsd_repo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    _run(["git", "init"], root)
    _run(["git", "checkout", "-b", "main"], root)


def test_compose_dry_run_json_pipeline(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    (freebsd / "UPDATING").write_text("upstream\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "a").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "overlay.dops").write_text(
        'target @main\nport devel/a\ntype port\nmk set VAR "new"\n'
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--dry-run",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert payload["summary"]["report_version"] == "v1"
    stage_names = [stage["name"] for stage in payload["stages"]]
    assert stage_names == [
        "seed_output",
        "apply_special",
        "preflight_validate",
        "prune_stale_overlays",
        "apply_semantic_ops",
        "apply_compat_ops",
        "apply_system_replacements",
        "finalize_tree",
    ]
    assert payload["ports"][0]["origin"] == "devel/a"
    assert payload["ports"][0]["total_ops"] == 1
    assert not output.exists()


def test_compose_seed_excludes_top_level_metadata_files(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    (freebsd / ".arcconfig").write_text("{\n}\n")
    (freebsd / "README").write_text("top level readme\n")
    (freebsd / "GIDs").write_text("root:*:0:\nnogroup:*:65533:\n")
    (freebsd / "UIDs").write_text(
        "root:*:0:0::0:0:System:/root:/bin/sh\n"
        "nobody:*:65534:65534::0:0:Unprivileged user:/nonexistent:/usr/sbin/nologin\n"
    )
    (freebsd / "MOVED").write_text("old/keep|new/keep|2014-01-01|moved\n")
    _init_freebsd_repo(freebsd)

    (delta / "special").mkdir(parents=True)

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--replace-output",
            "--oracle-profile",
            "off",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert (output / "devel" / "a" / "Makefile").exists()
    assert not (output / ".arcconfig").exists()
    assert not (output / "README").exists()


def test_compose_preflight_reports_stale_port_and_continues_non_strict(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "missing").mkdir(parents=True)
    (delta / "ports" / "devel" / "missing" / "overlay.dops").write_text(
        'target @main\nport devel/missing\ntype port\nmk set VAR "new"\n'
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 2
    payload = json.loads(out.out)
    assert [stage["name"] for stage in payload["stages"]] == [
        "seed_output",
        "apply_special",
        "preflight_validate",
        "prune_stale_overlays",
        "apply_semantic_ops",
        "apply_compat_ops",
        "apply_system_replacements",
        "finalize_tree",
    ]
    preflight = next(
        stage for stage in payload["stages"] if stage["name"] == "preflight_validate"
    )
    assert any("E_COMPOSE_STALE_OVERLAY" in error for error in preflight["errors"])


def test_compose_preflight_blocks_stale_port_in_strict_mode(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "missing").mkdir(parents=True)
    (delta / "ports" / "devel" / "missing" / "overlay.dops").write_text(
        'target @main\nport devel/missing\ntype port\nmk set VAR "new"\n'
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--strict",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 2
    payload = json.loads(out.out)
    assert [stage["name"] for stage in payload["stages"]] == [
        "seed_output",
        "apply_special",
        "preflight_validate",
    ]
    preflight = next(
        stage for stage in payload["stages"] if stage["name"] == "preflight_validate"
    )
    assert any("E_COMPOSE_STALE_OVERLAY" in error for error in preflight["errors"])


def test_compose_human_summary_includes_triage_overview(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "missing").mkdir(parents=True)
    (delta / "ports" / "devel" / "missing" / "Makefile.DragonFly").write_text(
        "LEGACY= yes\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
        ]
    )
    out = capsys.readouterr()

    assert code == 2
    assert "top_error_codes:" in out.out
    assert "stale:" in out.out
    assert "hint: rerun with --prune-stale-overlays" in out.out


def test_compose_prune_stale_overlays_keeps_delta_overlay(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    stale = delta / "ports" / "devel" / "missing"
    stale.mkdir(parents=True)
    (stale / "Makefile.DragonFly").write_text("LEGACY= yes\n")

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--prune-stale-overlays",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert stale.exists()  # delta overlay must NOT be deleted
    preflight = next(
        stage for stage in payload["stages"] if stage["name"] == "preflight_validate"
    )
    assert all("E_COMPOSE_STALE_OVERLAY" not in error for error in preflight["errors"])
    assert any(
        "I_COMPOSE_STALE_OVERLAY_PRUNE_CANDIDATE" in warning
        for warning in preflight["warnings"]
    )
    prune = next(
        stage for stage in payload["stages"] if stage["name"] == "prune_stale_overlays"
    )
    assert any(
        "I_COMPOSE_STALE_OVERLAY_PRUNED" in warning for warning in prune["warnings"]
    )


def test_compose_removed_in_skips_port_for_target(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    # Port exists upstream but is declared removed for @main
    (freebsd / "devel" / "gone").mkdir(parents=True)
    (freebsd / "devel" / "gone" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    gone = delta / "ports" / "devel" / "gone"
    gone.mkdir(parents=True)
    (gone / "Makefile.DragonFly").write_text("LEGACY= yes\n")
    (gone / "overlay.toml").write_text('removed_in = ["@main"]\n')

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert gone.exists()  # delta overlay untouched
    # Port should not appear in output
    assert not (output / "devel" / "gone").exists() or not (output / "devel" / "gone" / "Makefile.DragonFly").exists()
    # No stale error for this port
    preflight = next(
        stage for stage in payload["stages"] if stage["name"] == "preflight_validate"
    )
    assert all("devel/gone" not in error for error in preflight["errors"])
    # Check removed-for-target note in port report
    gone_report = next(p for p in payload["ports"] if p["origin"] == "devel/gone")
    assert "removed-for-target" in gone_report["notes"]


def test_compose_non_dry_run_handles_types_and_dops_suppresses_compat_fallback(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    lock_root = tmp_path / "lock"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    (freebsd / "devel" / "base").mkdir(parents=True)
    (freebsd / "devel" / "base" / "Makefile").write_text("BASE= yes\n")
    (freebsd / "UPDATING").write_text("upstream\n")
    _init_freebsd_repo(freebsd)

    # port
    (delta / "ports" / "devel" / "a").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "overlay.dops").write_text(
        'target @main\nport devel/a\ntype port\nmk set VAR "new"\n'
    )
    (delta / "ports" / "devel" / "a" / "diffs" / "@main").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "diffs" / "@main" / "add.patch").write_text(
        "--- Makefile\n+++ Makefile\n@@ -1 +1,2 @@\n VAR= new\n+PATCHED= yes\n"
    )
    (delta / "ports" / "devel" / "a" / "dragonfly" / "@main").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "dragonfly" / "@main" / "pkg-descr").write_text(
        "payload\n"
    )

    # mask
    (delta / "ports" / "devel" / "m").mkdir(parents=True)
    (delta / "ports" / "devel" / "m" / "overlay.dops").write_text(
        "target @main\nport devel/m\ntype mask\n"
    )

    # dport
    (delta / "ports" / "devel" / "dp" / "newport").mkdir(parents=True)
    (delta / "ports" / "devel" / "dp" / "newport" / "Makefile").write_text(
        "DPORT= yes\n"
    )
    (delta / "ports" / "devel" / "dp" / "overlay.dops").write_text(
        "target @main\nport devel/dp\ntype dport\n"
    )

    # lock
    (lock_root / "devel" / "lk").mkdir(parents=True)
    (lock_root / "devel" / "lk" / "Makefile").write_text("LOCK= yes\n")
    (delta / "ports" / "devel" / "lk").mkdir(parents=True)
    (delta / "ports" / "devel" / "lk" / "overlay.dops").write_text(
        "target @main\nport devel/lk\ntype lock\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--lock-root",
            str(lock_root),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert payload["summary"]["fallback_patch_count"] == 0

    assert (output / "devel" / "a" / "Makefile").read_text() == "VAR= new\n"
    assert not (output / "devel" / "a" / "pkg-descr").exists()
    assert (output / "devel" / "dp" / "Makefile").read_text() == "DPORT= yes\n"
    assert (output / "devel" / "lk" / "Makefile").read_text() == "LOCK= yes\n"
    assert not (output / "devel" / "m").exists()

    a_port = next(port for port in payload["ports"] if port["origin"] == "devel/a")
    assert a_port["mode"] == "dops"
    assert a_port["mode_reason"] == "overlay.dops-present"
    assert "compat-artifacts-suppressed-by-dops" in a_port["notes"]


def test_compose_compat_mode_runs_when_dops_missing(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "a").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "diffs" / "@main").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "diffs" / "@main" / "add.diff").write_text(
        "--- Makefile\n+++ Makefile\n@@ -1 +1,2 @@\n VAR= old\n+PATCHED= yes\n"
    )
    (delta / "ports" / "devel" / "a" / "dragonfly" / "@main").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "dragonfly" / "@main" / "pkg-descr").write_text(
        "payload\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["summary"]["fallback_patch_count"] == 1
    assert (
        output / "devel" / "a" / "Makefile"
    ).read_text() == "VAR= old\nPATCHED= yes\n"
    assert (
        output / "devel" / "a" / "dragonfly" / "@main" / "pkg-descr"
    ).read_text() == "payload\n"
    a_port = next(port for port in payload["ports"] if port["origin"] == "devel/a")
    assert a_port["mode"] == "compat"
    assert a_port["mode_reason"] == "overlay.dops-missing"
    assert "fallback" in a_port["compat_stages_executed"]
    assert "implicit_payload" in a_port["compat_stages_executed"]


def test_compose_failed_patch_does_not_leave_patch_artifacts(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "a" / "diffs" / "@main").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "diffs" / "@main" / "broken.diff").write_text(
        "--- Makefile\n+++ Makefile\n@@ -1 +1 @@\n-NOTTHERE= old\n+VAR= new\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--replace-output",
            "--oracle-profile",
            "off",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 2
    payload = json.loads(out.out)
    compat = next(
        stage for stage in payload["stages"] if stage["name"] == "apply_compat_ops"
    )
    assert any("E_COMPOSE_COMPAT_FAILED" in error for error in compat["errors"])

    leaked_orig = sorted(path for path in output.rglob("*.orig") if path.is_file())
    leaked_rej = sorted(path for path in output.rglob("*.rej") if path.is_file())
    assert leaked_orig == []
    assert leaked_rej == []


def test_compose_patch_runner_uses_noninteractive_patch_command(
    tmp_path, monkeypatch
) -> None:
    patch_path = tmp_path / "change.diff"
    patch_path.write_text("--- file\n+++ file\n")
    target_dir = tmp_path / "target"
    target_dir.mkdir(parents=True)

    observed: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed.update(kwargs)
        return _Proc()

    monkeypatch.setattr("dportsv3.compose_patching.subprocess.run", fake_run)
    ok, detail = compose_module._apply_patch(patch_path, target_dir, dry_run=False)

    assert ok
    assert detail == ""
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


def test_compose_relative_delta_root_resolves_patch_paths(
    tmp_path, capsys, monkeypatch
) -> None:
    workspace = tmp_path / "ws"
    freebsd = workspace / "freebsd"
    delta = workspace / "delta"
    output = workspace / "out"
    runner = workspace / "runner"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "a" / "diffs" / "@main").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "diffs" / "@main" / "ok.diff").write_text(
        "--- Makefile\n+++ Makefile\n@@ -1 +1 @@\n-VAR= old\n+VAR= new\n"
    )

    runner.mkdir(parents=True)
    monkeypatch.chdir(runner)

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            "../out",
            "--delta-root",
            "../delta",
            "--freebsd-root",
            "../freebsd",
            "--replace-output",
            "--oracle-profile",
            "off",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert (output / "devel" / "a" / "Makefile").read_text() == "VAR= new\n"


def test_compose_compat_mode_accepts_legacy_root_payloads(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    port = delta / "ports" / "devel" / "a"
    (port / "diffs").mkdir(parents=True)
    (port / "diffs" / "legacy.diff").write_text(
        "--- Makefile\n+++ Makefile\n@@ -1 +1 @@\n-VAR= old\n+VAR= patched\n"
    )
    (port / "dragonfly").mkdir(parents=True)
    (port / "dragonfly" / "pkg-descr").write_text("legacy payload\n")
    (port / "Makefile.DragonFly").write_text("LEGACY= yes\n")

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    makefile_text = (output / "devel" / "a" / "Makefile").read_text()
    assert "VAR= patched" in makefile_text
    assert (
        output / "devel" / "a" / "Makefile.DragonFly"
    ).read_text() == "LEGACY= yes\n"
    assert (
        output / "devel" / "a" / "dragonfly" / "pkg-descr"
    ).read_text() == "legacy payload\n"

    preflight = next(
        stage for stage in payload["stages"] if stage["name"] == "preflight_validate"
    )
    assert any(
        "I_COMPOSE_COMPAT_LEGACY_ROOT_FALLBACK" in warning
        for warning in preflight["warnings"]
    )
    a_port = next(port for port in payload["ports"] if port["origin"] == "devel/a")
    assert "apply_makefile" in a_port["compat_stages_executed"]
    assert "fallback" in a_port["compat_stages_executed"]
    assert "implicit_payload" in a_port["compat_stages_executed"]


def test_compose_compat_remove_list_deletes_files_before_patching(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    (freebsd / "devel" / "a" / "obsolete.txt").write_text("remove me\n")
    _init_freebsd_repo(freebsd)

    port = delta / "ports" / "devel" / "a"
    (port / "diffs").mkdir(parents=True)
    (port / "diffs" / "REMOVE").write_text("obsolete.txt\n")
    (port / "diffs" / "keep.diff").write_text(
        "--- Makefile\n+++ Makefile\n@@ -1 +1 @@\n-VAR= old\n+VAR= new\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert (output / "devel" / "a" / "Makefile").read_text() == "VAR= new\n"
    assert not (output / "devel" / "a" / "obsolete.txt").exists()
    a_port = next(port for port in payload["ports"] if port["origin"] == "devel/a")
    assert "remove" in a_port["compat_stages_executed"]


def test_compose_finalize_applies_global_script_parity_steps(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("OPTIONS_DEFAULT_amd64= yes\n")
    (freebsd / "devel" / "b").mkdir(parents=True)
    (freebsd / "devel" / "b" / "Makefile").write_text("VAR= old\n")
    (freebsd / "editors" / "c").mkdir(parents=True)
    (freebsd / "editors" / "c" / "Makefile").write_text("VAR= old\n")
    (freebsd / "Keywords").mkdir(parents=True)
    (freebsd / "Keywords" / "README").write_text("keywords\n")
    (freebsd / "Tools").mkdir(parents=True)
    (freebsd / "Tools" / "tool.pl").write_text("#!/usr/bin/perl\nprint 1;\n")
    (freebsd / "GIDs").write_text("root:*:0:\nnogroup:*:65533:\n")
    (freebsd / "UIDs").write_text(
        "root:*:0:0::0:0:System:/root:/bin/sh\n"
        "nobody:*:65534:65534::0:0:Unprivileged user:/nonexistent:/usr/sbin/nologin\n"
    )
    (freebsd / "MOVED").write_text(
        "# comment\n"
        "#\t\tin PST/PDT)\n"
        "old/port|new/port|2011-01-01|removed\n"
        "old/keep|new/keep|2014-01-01|moved\n"
    )
    recent_date = f"{date.today().year:04d}0101"
    old_date = f"{date.today().year - 2:04d}0101"
    (freebsd / "UPDATING").write_text(
        f"header line\n{recent_date}:\n  recent entry\n{old_date}:\n  old entry\n"
    )
    _init_freebsd_repo(freebsd)
    (delta / "special").mkdir(parents=True)
    (delta / "UPDATING.DragonFly").write_text("dragonfly custom entry\n")

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert (
        output / "devel" / "a" / "Makefile"
    ).read_text() == "OPTIONS_DEFAULT_x86_64= yes\n"
    assert (
        (output / "Tools" / "tool.pl").read_text().startswith("#!/usr/local/bin/perl\n")
    )
    assert (output / "Makefile").read_text() == "SUBDIR += devel\nSUBDIR += editors\n"
    assert (output / "devel" / "Makefile").read_text() == "SUBDIR += a\nSUBDIR += b\n"
    gids = (output / "GIDs").read_text()
    assert gids == "root:*:0:\navenger:*:60149:\ncbsd:*:60150:\nnogroup:*:65533:\n"
    uids = (output / "UIDs").read_text()
    assert uids == (
        "root:*:0:0::0:0:System:/root:/bin/sh\n"
        "avenger:*:60149:60149::0:0:Mail Avenger:/var/spool/avenger:/usr/sbin/nologin\n"
        "cbsd:*:60150:60150::0:0:Cbsd user:/nonexistent:/bin/sh\n"
        "nobody:*:65534:65534::0:0:Unprivileged user:/nonexistent:/usr/sbin/nologin\n"
    )
    assert (
        output / "MOVED"
    ).read_text() == "# comment\nold/keep|new/keep|2014-01-01|moved\n"
    updating = (output / "UPDATING").read_text()
    assert "recent entry" in updating
    assert "old entry" not in updating
    assert "dragonfly custom entry" not in updating


def test_compose_system_replacements_apply_to_dops_and_compat(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("CFLAGS_amd64= -O2\n")
    (freebsd / "devel" / "b").mkdir(parents=True)
    (freebsd / "devel" / "b" / "Makefile").write_text("OPTIONS_DEFINE_amd64= yes\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "a").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "overlay.dops").write_text(
        "target @main\nport devel/a\ntype port\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert "CFLAGS_x86_64= -O2" in (output / "devel" / "a" / "Makefile").read_text()
    assert (
        "OPTIONS_DEFINE_x86_64= yes"
        in (output / "devel" / "b" / "Makefile").read_text()
    )

    stage = next(
        row for row in payload["stages"] if row["name"] == "apply_system_replacements"
    )
    assert stage["metadata"]["files_changed"] >= 2
    assert stage["metadata"]["rule_hits"]["cflags-amd64"] >= 1
    assert stage["metadata"]["rule_hits"]["options-define-amd64"] >= 1


def test_compose_compat_mode_handles_manifest_types(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    lock_root = tmp_path / "lock"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    (freebsd / "devel" / "m").mkdir(parents=True)
    (freebsd / "devel" / "m" / "Makefile").write_text("MASK= old\n")
    _init_freebsd_repo(freebsd)

    # compat port
    (delta / "ports" / "devel" / "a").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "overlay.toml").write_text("type = 'port'\n")
    (delta / "ports" / "devel" / "a" / "Makefile.DragonFly").write_text("EXTRA= yes\n")

    # compat mask
    (delta / "ports" / "devel" / "m").mkdir(parents=True)
    (delta / "ports" / "devel" / "m" / "overlay.toml").write_text("type = 'mask'\n")

    # compat dport
    (delta / "ports" / "devel" / "dp" / "newport").mkdir(parents=True)
    (delta / "ports" / "devel" / "dp" / "newport" / "Makefile").write_text(
        "DPORT= yes\n"
    )

    # compat lock
    (delta / "ports" / "devel" / "lk").mkdir(parents=True)
    (delta / "ports" / "devel" / "lk" / "overlay.toml").write_text("type = 'lock'\n")
    (lock_root / "devel" / "lk").mkdir(parents=True)
    (lock_root / "devel" / "lk" / "Makefile").write_text("LOCK= yes\n")

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--lock-root",
            str(lock_root),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert (output / "devel" / "a" / "Makefile").read_text() == "VAR= old\n"
    assert (output / "devel" / "a" / "Makefile.DragonFly").read_text() == "EXTRA= yes\n"
    assert not (output / "devel" / "m").exists()
    assert (output / "devel" / "dp" / "Makefile").read_text() == "DPORT= yes\n"
    assert (output / "devel" / "lk" / "Makefile").read_text() == "LOCK= yes\n"

    ports = {row["origin"]: row for row in payload["ports"]}
    assert ports["devel/a"]["type"] == "port"
    assert "apply_makefile" in ports["devel/a"]["compat_stages_executed"]
    assert ports["devel/m"]["type"] == "mask"
    assert "mask" in ports["devel/m"]["compat_stages_executed"]
    assert ports["devel/dp"]["type"] == "dport"
    assert "copy_dport" in ports["devel/dp"]["compat_stages_executed"]
    assert ports["devel/lk"]["type"] == "lock"
    assert "copy_lock" in ports["devel/lk"]["compat_stages_executed"]


def test_compose_compat_mode_respects_status_types(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"
    lock_root = tmp_path / "lock"

    (freebsd / "devel" / "p").mkdir(parents=True)
    (freebsd / "devel" / "p" / "Makefile").write_text("PORT= old\n")
    (freebsd / "devel" / "m").mkdir(parents=True)
    (freebsd / "devel" / "m" / "Makefile").write_text("MASK= old\n")
    _init_freebsd_repo(freebsd)

    # STATUS=PORT
    (delta / "ports" / "devel" / "p").mkdir(parents=True)
    (delta / "ports" / "devel" / "p" / "STATUS").write_text("PORT\nLast attempt: 1.0\n")
    (delta / "ports" / "devel" / "p" / "Makefile.DragonFly").write_text("PORT= new\n")

    # STATUS=MASK
    (delta / "ports" / "devel" / "m").mkdir(parents=True)
    (delta / "ports" / "devel" / "m" / "STATUS").write_text("MASK\n")

    # STATUS=DPORT
    (delta / "ports" / "devel" / "dp" / "newport").mkdir(parents=True)
    (delta / "ports" / "devel" / "dp" / "newport" / "Makefile").write_text(
        "DPORT= yes\n"
    )
    (delta / "ports" / "devel" / "dp" / "STATUS").write_text("DPORT\n")

    # STATUS=LOCK
    (delta / "ports" / "devel" / "lk").mkdir(parents=True)
    (delta / "ports" / "devel" / "lk" / "STATUS").write_text("LOCK\n")
    (lock_root / "devel" / "lk").mkdir(parents=True)
    (lock_root / "devel" / "lk" / "Makefile").write_text("LOCK= yes\n")

    # malformed STATUS should fallback to port
    (delta / "ports" / "devel" / "bad").mkdir(parents=True)
    (delta / "ports" / "devel" / "bad" / "STATUS").write_text("\nLast attempt: 1\n")
    (freebsd / "devel" / "bad").mkdir(parents=True)
    (freebsd / "devel" / "bad" / "Makefile").write_text("BAD= old\n")

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--lock-root",
            str(lock_root),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert (output / "devel" / "p" / "Makefile.DragonFly").read_text() == "PORT= new\n"
    assert not (output / "devel" / "m").exists()
    assert (output / "devel" / "dp" / "Makefile").read_text() == "DPORT= yes\n"
    assert (output / "devel" / "lk" / "Makefile").read_text() == "LOCK= yes\n"
    assert (output / "devel" / "bad" / "Makefile").read_text() == "BAD= old\n"

    ports = {row["origin"]: row for row in payload["ports"]}
    assert ports["devel/p"]["type"] == "port"
    assert any("status-mode" in note for note in ports["devel/p"]["notes"])
    assert ports["devel/m"]["type"] == "mask"
    assert ports["devel/dp"]["type"] == "dport"
    assert ports["devel/lk"]["type"] == "lock"
    assert ports["devel/bad"]["type"] == "port"


def test_compose_special_applies_patches_before_replacements_and_reports(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "Mk").mkdir(parents=True)
    (freebsd / "Mk" / "bsd.port.mk").write_text("BASE= yes\n")
    (freebsd / "Mk" / "bsd.gcc.mk").write_text("LEGACY= yes\n")
    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "special" / "Mk" / "diffs").mkdir(parents=True)
    (delta / "special" / "Mk" / "diffs" / "mk.diff").write_text(
        "--- bsd.port.mk\n+++ bsd.port.mk\n@@ -1 +1 @@\n-BASE= yes\n+PATCHED= yes\n"
    )
    (delta / "special" / "Mk" / "replacements").mkdir(parents=True)
    (delta / "special" / "Mk" / "replacements" / "bsd.port.mk").write_text(
        "REPLACED= yes\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert (output / "Mk" / "bsd.port.mk").read_text() == "REPLACED= yes\n"
    assert not (output / "Mk" / "bsd.gcc.mk").exists()

    special = next(
        stage for stage in payload["stages"] if stage["name"] == "apply_special"
    )
    mk_row = next(
        row for row in special["metadata"]["components"] if row["component"] == "Mk"
    )
    assert mk_row["patched"] == 1
    assert mk_row["copied"] >= 1
    assert mk_row["failed_patches"] == []
    assert mk_row["selected_patches"] == 1
    assert "bsd.gcc.mk" in mk_row["removed_legacy_files"]


def test_compose_special_applies_root_recursive_diffs(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "Mk").mkdir(parents=True)
    (freebsd / "Mk" / "bsd.port.mk").write_text("BASE= yes\n")
    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "special" / "Mk" / "diffs").mkdir(parents=True)
    (delta / "special" / "Mk" / "diffs" / "mk.diff").write_text(
        "--- bsd.port.mk\n+++ bsd.port.mk\n@@ -1 +1 @@\n-BASE= yes\n+PATCHED= yes\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert (output / "Mk" / "bsd.port.mk").read_text() == "PATCHED= yes\n"

    special = next(
        stage for stage in payload["stages"] if stage["name"] == "apply_special"
    )
    mk_row = next(
        row for row in special["metadata"]["components"] if row["component"] == "Mk"
    )
    assert mk_row["patched"] == 1
    assert mk_row["selected_patches"] == 1


def test_compose_special_main_ignores_target_scoped_diffs(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "Mk").mkdir(parents=True)
    (freebsd / "Mk" / "bsd.port.mk").write_text("BASE= yes\n")
    (freebsd / "Mk" / "bsd.sites.mk").write_text("SITE= yes\n")
    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "special" / "Mk" / "diffs").mkdir(parents=True)
    (delta / "special" / "Mk" / "diffs" / "root.diff").write_text(
        "--- bsd.port.mk\n+++ bsd.port.mk\n@@ -1 +1 @@\n-BASE= yes\n+BASE= legacy\n"
    )
    (delta / "special" / "Mk" / "diffs" / "@2025Q2").mkdir(parents=True)
    (delta / "special" / "Mk" / "diffs" / "@2025Q2" / "nested.diff").write_text(
        "--- bsd.sites.mk\n+++ bsd.sites.mk\n@@ -1 +1 @@\n-SITE= yes\n+SITE= target\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert (output / "Mk" / "bsd.port.mk").read_text() == "BASE= legacy\n"
    assert (output / "Mk" / "bsd.sites.mk").read_text() == "SITE= yes\n"

    special = next(
        stage for stage in payload["stages"] if stage["name"] == "apply_special"
    )
    mk_row = next(
        row for row in special["metadata"]["components"] if row["component"] == "Mk"
    )
    assert mk_row["patched"] == 1
    assert mk_row["selected_patches"] == 1


def test_compose_special_treetop_gid_uid_patches_apply_with_identity_injection(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    (freebsd / "GIDs").write_text("root:*:0:\nnogroup:*:65533:\n")
    (freebsd / "UIDs").write_text(
        "root:*:0:0::0:0:System:/root:/bin/sh\n"
        "nobody:*:65534:65534::0:0:Unprivileged user:/nonexistent:/usr/sbin/nologin\n"
    )
    _init_freebsd_repo(freebsd)

    (delta / "special" / "treetop" / "diffs").mkdir(parents=True)
    (delta / "special" / "treetop" / "diffs" / "GIDs.diff").write_text(
        "--- GIDs.orig\n+++ GIDs\n@@ -2 +2,2 @@\n nogroup:*:65533:\n+audit:*:149:\n"
    )
    (delta / "special" / "treetop" / "diffs" / "UIDs.diff").write_text(
        "--- UIDs.orig\n+++ UIDs\n@@ -2 +2,2 @@\n nobody:*:65534:65534::0:0:Unprivileged user:/nonexistent:/usr/sbin/nologin\n+pgsql:*:150:150::0:0:PostgreSQL pseudo-user:/usr/local/pgsql:/bin/sh\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is True
    assert "audit:*:149:" in (output / "GIDs").read_text()
    assert (
        "pgsql:*:150:150::0:0:PostgreSQL pseudo-user:/usr/local/pgsql:/bin/sh"
        in (output / "UIDs").read_text()
    )
    special = next(
        stage for stage in payload["stages"] if stage["name"] == "apply_special"
    )
    assert not any(
        "E_COMPOSE_SPECIAL_PATCH_FAILED: treetop/GIDs.diff" in e
        for e in special["errors"]
    )
    assert not any(
        "E_COMPOSE_SPECIAL_PATCH_FAILED: treetop/UIDs.diff" in e
        for e in special["errors"]
    )


def test_compose_special_non_main_uses_only_target_scoped_payloads(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "Mk").mkdir(parents=True)
    (freebsd / "Mk" / "bsd.port.mk").write_text("BASE= yes\n")
    (freebsd / "Mk" / "bsd.sites.mk").write_text("SITE= yes\n")
    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)
    _run(["git", "checkout", "-b", "2025Q2"], freebsd)

    (delta / "special" / "Mk" / "diffs").mkdir(parents=True)
    (delta / "special" / "Mk" / "diffs" / "main.diff").write_text(
        "--- bsd.port.mk\n+++ bsd.port.mk\n@@ -1 +1 @@\n-BASE= yes\n+BASE= main\n"
    )
    (delta / "special" / "Mk" / "diffs" / "@2025Q2").mkdir(parents=True)
    (delta / "special" / "Mk" / "diffs" / "@2025Q2" / "quarter.diff").write_text(
        "--- bsd.sites.mk\n+++ bsd.sites.mk\n@@ -1 +1 @@\n-SITE= yes\n+SITE= quarter\n"
    )
    (delta / "special" / "Mk" / "replacements" / "@2025Q2").mkdir(parents=True)

    code = main(
        [
            "compose",
            "--target",
            "@2025Q2",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert (output / "Mk" / "bsd.port.mk").read_text() == "BASE= yes\n"
    assert (output / "Mk" / "bsd.sites.mk").read_text() == "SITE= quarter\n"

    special = next(
        stage for stage in payload["stages"] if stage["name"] == "apply_special"
    )
    mk_row = next(
        row for row in special["metadata"]["components"] if row["component"] == "Mk"
    )
    assert mk_row["patched"] == 1
    assert mk_row["selected_patches"] == 1
    assert mk_row["auto_created_from_main"] is False
    assert mk_row["missing_target_dir"] is False
    assert not any(
        "I_COMPOSE_SPECIAL_TARGET_BOOTSTRAPPED: Mk/diffs/@2025Q2" in warning
        for warning in special["warnings"]
    )


def test_compose_special_non_main_bootstraps_diffs_from_main(tmp_path, capsys) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "Mk").mkdir(parents=True)
    (freebsd / "Mk" / "bsd.port.mk").write_text("BASE= yes\n")
    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)
    _run(["git", "checkout", "-b", "2025Q2"], freebsd)

    (delta / "special" / "Mk" / "diffs").mkdir(parents=True)
    (delta / "special" / "Mk" / "diffs" / "mk.diff").write_text(
        "--- bsd.port.mk\n+++ bsd.port.mk\n@@ -1 +1 @@\n-BASE= yes\n+PATCHED= yes\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@2025Q2",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert (output / "Mk" / "bsd.port.mk").read_text() == "PATCHED= yes\n"
    assert (delta / "special" / "Mk" / "diffs" / "@2025Q2" / "mk.diff").read_text() == (
        delta / "special" / "Mk" / "diffs" / "mk.diff"
    ).read_text()

    special = next(
        stage for stage in payload["stages"] if stage["name"] == "apply_special"
    )
    mk_row = next(
        row for row in special["metadata"]["components"] if row["component"] == "Mk"
    )
    assert mk_row["patched"] == 1
    assert mk_row["selected_patches"] == 1
    assert mk_row["auto_created_from_main"] is True
    assert mk_row["missing_target_dir"] is True
    assert any(
        "I_COMPOSE_SPECIAL_TARGET_BOOTSTRAPPED: Mk/diffs/@2025Q2: created from unscoped main payloads"
        in warning
        for warning in special["warnings"]
    )


def test_compose_special_non_main_bootstraps_replacements_from_main(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "Mk").mkdir(parents=True)
    (freebsd / "Mk" / "bsd.port.mk").write_text("BASE= yes\n")
    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)
    _run(["git", "checkout", "-b", "2025Q2"], freebsd)

    (delta / "special" / "Mk" / "replacements" / "Uses").mkdir(parents=True)
    (delta / "special" / "Mk" / "replacements" / "bsd.port.mk").write_text(
        "REPLACED= yes\n"
    )
    (delta / "special" / "Mk" / "replacements" / "Uses" / "linux.mk").write_text(
        "LINUX= yes\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@2025Q2",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert (output / "Mk" / "bsd.port.mk").read_text() == "REPLACED= yes\n"
    assert (output / "Mk" / "Uses" / "linux.mk").read_text() == "LINUX= yes\n"
    assert (
        delta / "special" / "Mk" / "replacements" / "@2025Q2" / "bsd.port.mk"
    ).read_text() == (
        delta / "special" / "Mk" / "replacements" / "bsd.port.mk"
    ).read_text()
    assert (
        delta / "special" / "Mk" / "replacements" / "@2025Q2" / "Uses" / "linux.mk"
    ).read_text() == (
        delta / "special" / "Mk" / "replacements" / "Uses" / "linux.mk"
    ).read_text()

    special = next(
        stage for stage in payload["stages"] if stage["name"] == "apply_special"
    )
    mk_row = next(
        row for row in special["metadata"]["components"] if row["component"] == "Mk"
    )
    assert mk_row["auto_created_from_main"] is True
    assert mk_row["missing_target_dir"] is True
    assert any(
        "I_COMPOSE_SPECIAL_TARGET_BOOTSTRAPPED: Mk/replacements/@2025Q2: created from unscoped main payloads"
        in warning
        for warning in special["warnings"]
    )


def test_compose_compat_keeps_dragonfly_tree_and_makefile_dragonfly(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "a").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "diffs" / "@any").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "diffs" / "@any" / "base.diff").write_text(
        "--- Makefile\n+++ Makefile\n@@ -1 +1 @@\n-VAR= old\n+VAR= any\n"
    )
    (delta / "ports" / "devel" / "a" / "dragonfly" / "@any").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "dragonfly" / "@any" / "pkg-descr").write_text(
        "baseline\n"
    )
    (delta / "ports" / "devel" / "a" / "Makefile.DragonFly.@any").write_text(
        "BASELINE= yes\n"
    )

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert "VAR= any" in (output / "devel" / "a" / "Makefile").read_text()
    assert (
        output / "devel" / "a" / "Makefile.DragonFly"
    ).read_text() == "BASELINE= yes\n"
    assert (
        output / "devel" / "a" / "dragonfly" / "@any" / "pkg-descr"
    ).read_text() == "baseline\n"


def test_compose_compat_makefile_prefers_root_over_target_scoped(
    tmp_path, capsys
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    port_dir = delta / "ports" / "devel" / "a"
    port_dir.mkdir(parents=True)
    (port_dir / "diffs").mkdir(parents=True)
    (port_dir / "diffs" / "base.diff").write_text(
        "--- Makefile\n+++ Makefile\n@@ -1 +1 @@\n-VAR= old\n+VAR= patched\n"
    )
    (port_dir / "dragonfly" / "@any").mkdir(parents=True)
    (port_dir / "dragonfly" / "@main").mkdir(parents=True)
    (port_dir / "dragonfly" / "@any" / "pkg-descr").write_text("any\n")
    (port_dir / "dragonfly" / "@main" / "pkg-descr").write_text("main\n")
    (port_dir / "Makefile.DragonFly").write_text("ROOT= yes\n")
    (port_dir / "Makefile.DragonFly.@any").write_text("BASELINE= yes\n")
    (port_dir / "Makefile.DragonFly.@main").write_text("EXPLICIT= yes\n")

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "off",
            "--replace-output",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    makefile_text = (output / "devel" / "a" / "Makefile").read_text()
    assert "VAR= patched" in makefile_text
    assert (output / "devel" / "a" / "Makefile.DragonFly").read_text() == "ROOT= yes\n"
    assert (
        output / "devel" / "a" / "dragonfly" / "@any" / "pkg-descr"
    ).read_text() == "any\n"
    assert (
        output / "devel" / "a" / "dragonfly" / "@main" / "pkg-descr"
    ).read_text() == "main\n"

    preflight = next(
        stage for stage in payload["stages"] if stage["name"] == "preflight_validate"
    )
    assert any(
        "I_COMPOSE_COMPAT_LAYER_OVERRIDE" in row for row in preflight["warnings"]
    )


def test_compose_forwards_oracle_profile_and_aggregates_metrics(
    tmp_path, capsys, monkeypatch
) -> None:
    freebsd = tmp_path / "freebsd"
    delta = tmp_path / "delta"
    output = tmp_path / "out"

    (freebsd / "devel" / "a").mkdir(parents=True)
    (freebsd / "devel" / "a" / "Makefile").write_text("VAR= old\n")
    _init_freebsd_repo(freebsd)

    (delta / "ports" / "devel" / "a").mkdir(parents=True)
    (delta / "ports" / "devel" / "a" / "overlay.dops").write_text(
        'target @main\nport devel/a\ntype port\nmk set VAR "new"\n'
    )

    seen_profiles: list[str] = []

    def fake_apply(*_args, **kwargs):
        seen_profiles.append(kwargs["oracle_profile"])
        return ApplyResult(
            ok=True,
            context=ApplyContext(
                source_root=Path(kwargs["source_path"]).parent,
                port_root=Path(kwargs["port_root"]),
                target=kwargs["target"],
                dry_run=kwargs["dry_run"],
                strict=kwargs["strict"],
                oracle_profile=kwargs["oracle_profile"],
            ),
            oracle_profile=kwargs["oracle_profile"],
            oracle_checks=2,
            oracle_failures=1,
            oracle_skipped=0,
        )

    monkeypatch.setattr("dportsv3.compose.apply_dsl", fake_apply)

    code = main(
        [
            "compose",
            "--target",
            "@main",
            "--output",
            str(output),
            "--delta-root",
            str(delta),
            "--freebsd-root",
            str(freebsd),
            "--oracle-profile",
            "ci",
            "--dry-run",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    assert seen_profiles == ["ci"]
    payload = json.loads(out.out)
    assert payload["summary"]["oracle_profile"] == "ci"
    assert payload["summary"]["oracle_checks"] == 2
    assert payload["summary"]["oracle_failures"] == 1
    assert payload["summary"]["oracle_failed_origins"] == ["devel/a"]
