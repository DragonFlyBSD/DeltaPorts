from __future__ import annotations

import json
import subprocess
import sys

import pytest

from dportsv3.cli import main
from tests.dportsv3_testutils import fixture_path


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_dsl_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["dsl", "--help"])
    assert exc.value.code == 0


def test_migrate_help_lists_wave_commands(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["migrate", "--help"])
    out = capsys.readouterr()

    assert exc.value.code == 0
    assert "wave-plan" in out.out
    assert "wave-report" in out.out


def test_parse_missing_file_returns_1(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["dsl", "parse", "./no-such-overlay.dops"])
    out = capsys.readouterr()

    assert code == 1
    assert "Input file not found" in out.err


def test_parse_valid_file_returns_zero(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text("target @main\nport category/name\n")

    code = main(["dsl", "parse", str(dops)])
    out = capsys.readouterr()

    assert code == 0
    assert out.err == ""


def test_parse_invalid_file_returns_2(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text("reason not-a-string\n")

    code = main(["dsl", "parse", str(dops)])
    out = capsys.readouterr()

    assert code == 2
    assert "E_PARSE_EXPECTED_TOKEN" in out.err


def test_parse_fixture_valid_and_invalid_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    valid = fixture_path("valid/basic.dops")
    invalid = fixture_path("invalid/parse/reason-missing-string.dops")

    valid_code = main(["dsl", "parse", str(valid)])
    valid_out = capsys.readouterr()
    assert valid_code == 0
    assert valid_out.out == ""
    assert valid_out.err == ""

    invalid_code = main(["dsl", "parse", str(invalid)])
    invalid_out = capsys.readouterr()
    assert invalid_code == 2
    assert invalid_out.out == ""
    assert "E_PARSE_EXPECTED_TOKEN" in invalid_out.err


def test_check_valid_file_returns_zero(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nport category/name\nmk set VAR "ok"\n')

    code = main(["dsl", "check", str(dops)])
    out = capsys.readouterr()

    assert code == 0
    assert out.err == ""


def test_check_semantic_invalid_file_returns_2(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nmk set VAR "missing port"\n')

    code = main(["dsl", "check", str(dops)])
    out = capsys.readouterr()

    assert code == 2
    assert "E_SEM_MISSING_PORT" in out.err


def test_check_fixture_valid_and_invalid_contract(
    capsys: pytest.CaptureFixture[str],
) -> None:
    valid = fixture_path("valid/multi_target.dops")
    invalid = fixture_path("invalid/semantic/missing-port.dops")

    valid_code = main(["dsl", "check", str(valid)])
    valid_out = capsys.readouterr()
    assert valid_code == 0
    assert valid_out.out == ""
    assert valid_out.err == ""

    invalid_code = main(["dsl", "check", str(invalid)])
    invalid_out = capsys.readouterr()
    assert invalid_code == 2
    assert invalid_out.out == ""
    assert "E_SEM_MISSING_PORT" in invalid_out.err


def test_plan_valid_file_returns_json(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nport category/name\nmk set VAR "ok"\n')

    code = main(["dsl", "plan", str(dops), "--json"])
    out = capsys.readouterr()

    assert code == 0
    assert out.err == ""
    payload = json.loads(out.out)
    assert payload["port"] == "category/name"
    assert payload["type"] == "port"
    assert len(payload["ops"]) == 1
    assert payload["ops"][0]["kind"] == "mk.var.set"


def test_plan_invalid_file_returns_2(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nmk set VAR "missing port"\n')

    code = main(["dsl", "plan", str(dops), "--json"])
    out = capsys.readouterr()

    assert code == 2
    assert "E_SEM_MISSING_PORT" in out.err
    assert out.out == ""


def test_plan_parse_invalid_fixture_returns_2_and_no_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = fixture_path("invalid/parse/reason-missing-string.dops")

    code = main(["dsl", "plan", str(fixture), "--json"])
    out = capsys.readouterr()

    assert code == 2
    assert "E_PARSE_EXPECTED_TOKEN" in out.err
    assert out.out == ""


def test_plan_fixture_json_deterministic_and_parseable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = fixture_path("golden/basic.dops")

    first_code = main(["dsl", "plan", str(fixture), "--json"])
    first_out = capsys.readouterr()
    second_code = main(["dsl", "plan", str(fixture), "--json"])
    second_out = capsys.readouterr()

    assert first_code == 0
    assert second_code == 0
    assert first_out.err == ""
    assert second_out.err == ""

    first_payload = json.loads(first_out.out)
    second_payload = json.loads(second_out.out)
    assert first_payload == second_payload
    assert first_out.out == second_out.out


def test_apply_dry_run_valid_returns_json(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nport category/name\nmk set VAR "ok"\n')
    (tmp_path / "Makefile").write_text("VAR= old\n")

    code = main(
        [
            "dsl",
            "apply",
            str(dops),
            "--port-root",
            str(tmp_path),
            "--target",
            "@main",
            "--oracle-profile",
            "off",
            "--dry-run",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["summary"]["total_ops"] == 1
    assert payload["summary"]["applied_ops"] == 1
    assert payload["summary"]["skipped_ops"] == 0
    assert payload["summary"]["failed_ops"] == 0
    assert payload["report"]["report_version"] == "v1"
    assert payload["report"]["fallback_patch_count"] == 0
    assert payload["report"]["oracle_profile"] == "off"
    assert payload["summary"]["oracle_checks"] == 0
    assert out.err == ""


def test_apply_invalid_oracle_profile_rejected_by_parser() -> None:
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "dsl",
                "apply",
                "overlay.dops",
                "--port-root",
                ".",
                "--target",
                "@main",
                "--oracle-profile",
                "invalid",
            ]
        )
    assert exc.value.code == 2


def test_apply_diff_requires_dry_run(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nport category/name\nmk set VAR "ok"\n')
    (tmp_path / "Makefile").write_text("VAR= old\n")

    code = main(
        [
            "dsl",
            "apply",
            str(dops),
            "--port-root",
            str(tmp_path),
            "--target",
            "@main",
            "--oracle-profile",
            "off",
            "--diff",
        ]
    )
    out = capsys.readouterr()

    assert code == 2
    assert "E_APPLY_DIFF_REQUIRES_DRY_RUN" in out.err


def test_apply_dry_run_diff_prints_unified_diff(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nport category/name\nmk set VAR "ok"\n')
    (tmp_path / "Makefile").write_text("VAR= old\n")

    code = main(
        [
            "dsl",
            "apply",
            str(dops),
            "--port-root",
            str(tmp_path),
            "--target",
            "@main",
            "--oracle-profile",
            "off",
            "--dry-run",
            "--diff",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    assert out.err == ""
    assert "--- a/Makefile" in out.out
    assert "+++ b/Makefile" in out.out
    assert "+VAR= ok" in out.out
    assert (tmp_path / "Makefile").read_text() == "VAR= old\n"


def test_apply_dry_run_diff_json_contains_diffs(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nport category/name\nmk set VAR "ok"\n')
    (tmp_path / "Makefile").write_text("VAR= old\n")

    code = main(
        [
            "dsl",
            "apply",
            str(dops),
            "--port-root",
            str(tmp_path),
            "--target",
            "@main",
            "--oracle-profile",
            "off",
            "--dry-run",
            "--diff",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["report"]["report_version"] == "v1"
    assert len(payload["diffs"]) == 1
    assert payload["diffs"][0]["path"] == "Makefile"
    assert payload["diffs"][0]["change_type"] == "modified"


def test_apply_invalid_target_returns_2(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @main\nport category/name\nmk set VAR "ok"\n')

    code = main(
        [
            "dsl",
            "apply",
            str(dops),
            "--port-root",
            str(tmp_path),
            "--target",
            "main",
            "--oracle-profile",
            "off",
            "--dry-run",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 2
    assert "E_APPLY_INVALID_TARGET" in out.err


def test_apply_target_mismatch_skips_deterministically(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    dops = tmp_path / "overlay.dops"
    dops.write_text('target @2025Q1\nport category/name\nmk set VAR "ok"\n')

    code = main(
        [
            "dsl",
            "apply",
            str(dops),
            "--port-root",
            str(tmp_path),
            "--target",
            "@main",
            "--oracle-profile",
            "off",
            "--dry-run",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["summary"]["total_ops"] == 1
    assert payload["summary"]["skipped_ops"] == 1
    assert payload["summary"]["failed_ops"] == 0
    assert "I_APPLY_TARGET_MISMATCH" in out.err


def test_python_module_entrypoint_help() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "dportsv3", "--help"],
        cwd=".",
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "DeltaPorts v3 DSL tooling" in result.stdout


def test_compose_report_json_overview(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "compose.json"
    report.write_text(
        json.dumps(
            {
                "ok": False,
                "target": "@main",
                "output_path": "/tmp/out",
                "stages": [
                    {
                        "name": "preflight_validate",
                        "errors": [
                            "E_COMPOSE_STALE_OVERLAY: devel/missing: overlay origin missing in upstream target"
                        ],
                        "warnings": [],
                        "metadata": {},
                    },
                    {
                        "name": "apply_compat_ops",
                        "errors": [
                            "E_COMPOSE_COMPAT_FAILED: devel/a: patch failed (Makefile.diff): hunk FAILED"
                        ],
                        "warnings": [],
                        "metadata": {},
                    },
                ],
                "ports": [{"mode": "compat"}],
            }
        )
    )

    code = main(["compose-report", str(report), "--json"])
    out = capsys.readouterr()

    assert code == 0
    payload = json.loads(out.out)
    assert payload["ok"] is False
    assert payload["target"] == "@main"
    assert payload["top_error_codes"][0]["code"] == "E_COMPOSE_STALE_OVERLAY"
    assert payload["top_failed_patches"][0]["patch"] == "Makefile.diff"


def test_compose_report_text_overview(
    tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    report = tmp_path / "compose.json"
    report.write_text(
        json.dumps(
            {
                "ok": False,
                "target": "@main",
                "output_path": "/tmp/out",
                "stages": [
                    {
                        "name": "apply_compat_ops",
                        "errors": [
                            "E_COMPOSE_COMPAT_FAILED: devel/a: patch failed (pkg-plist.diff): hunk FAILED"
                        ],
                        "warnings": [],
                        "metadata": {},
                    }
                ],
                "ports": [{"mode": "compat"}],
            }
        )
    )

    code = main(["compose-report", str(report)])
    out = capsys.readouterr()

    assert code == 0
    assert "Compose report failed" in out.out
    assert "top_error_codes:" in out.out
    assert "top_failed_patches:" in out.out
