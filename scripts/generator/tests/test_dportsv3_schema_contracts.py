from __future__ import annotations

from pathlib import Path

from dportsv3.compose_models import ComposePortReport, ComposeResult, ComposeStageResult
from dportsv3.compose_reporting import build_compose_report_overview
from dportsv3.engine.models import ApplyContext, ApplyResult
from dportsv3.migration.waves import build_wave_report


def test_apply_result_schema_contract() -> None:
    result = ApplyResult(
        ok=True,
        context=ApplyContext(
            source_root=Path("/tmp/source"),
            port_root=Path("/tmp/port"),
            target="@main",
        ),
    )

    payload = result.to_dict()
    assert set(payload.keys()) == {
        "ok",
        "context",
        "summary",
        "report",
        "diagnostics",
        "op_results",
        "diffs",
    }
    assert payload["report"]["report_version"] == "v1"


def test_compose_result_and_overview_schema_contract() -> None:
    result = ComposeResult(target="@main", output_path=Path("/tmp/out"))
    result.add_stage(ComposeStageResult(name="seed_output"))
    result.ports = [ComposePortReport(origin="devel/a", mode="dops")]

    payload = result.to_dict()
    assert set(payload.keys()) == {
        "ok",
        "target",
        "output_path",
        "oracle_profile",
        "summary",
        "stages",
        "ports",
    }
    assert payload["summary"]["report_version"] == "v1"

    overview = build_compose_report_overview(payload, top=3)
    assert set(overview.keys()) == {
        "ok",
        "target",
        "output_path",
        "top_error_codes",
        "top_warning_codes",
        "top_error_origins",
        "top_failed_patches",
        "mode_counts",
        "stale",
        "special",
        "hints",
    }
    assert set(overview["stale"].keys()) == {
        "count",
        "origins",
        "marked_removed",
        "pruned",
    }


def test_migration_wave_schema_contract() -> None:
    wave_report = build_wave_report(
        [
            {
                "origin": "devel/a",
                "status": "converted",
                "parse_ok": True,
                "check_ok": True,
                "plan_ok": True,
                "deterministic_ok": True,
                "classified": True,
            }
        ]
    )
    assert set(wave_report.keys()) == {
        "total",
        "status_counts",
        "validation_failures",
        "determinism_failures",
        "unclassified_count",
        "gates",
        "gate_pass",
        "rows",
    }
