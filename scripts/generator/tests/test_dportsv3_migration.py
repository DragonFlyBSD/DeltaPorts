from __future__ import annotations

import json
from typing import Any, cast

from dportsv3.cli import main
from dportsv3.migration.waves import build_wave_report, select_wave
from tests.dportsv3_testutils import fixture_path, read_json_fixture


def test_select_wave_filters_and_prioritizes_by_churn() -> None:
    inventory = cast(
        list[dict[str, Any]], read_json_fixture("migration/inventory-basic.json")
    )

    wave = select_wave(
        inventory,
        buckets=["auto-safe"],
        targets=["@main"],
        max_ports=2,
        dry_run=True,
    )

    assert wave["dry_run"] is True
    assert wave["selected_total"] == 2
    assert [item["origin"] for item in wave["selected"]] == ["devel/a", "devel/b"]
    assert all(
        item["selection_reason"] == "explicit_target_match" for item in wave["selected"]
    )


def test_select_wave_includes_baseline_entries_for_target_filter() -> None:
    inventory = [
        {
            "origin": "devel/base",
            "bucket": "auto-safe",
            "category": "devel",
            "churn": 50,
            "target_mode": "baseline",
            "available_targets": ["@any"],
            "targets": ["@any"],
        },
        {
            "origin": "devel/explicit",
            "bucket": "auto-safe",
            "category": "devel",
            "churn": 40,
            "target_mode": "explicit",
            "available_targets": ["@2025Q1"],
            "targets": ["@2025Q1"],
        },
        {
            "origin": "devel/excluded",
            "bucket": "auto-safe",
            "category": "devel",
            "churn": 30,
            "target_mode": "explicit",
            "available_targets": ["@main"],
            "targets": ["@main"],
        },
    ]

    wave = select_wave(
        inventory, buckets=["auto-safe"], targets=["@2025Q1"], max_ports=10
    )

    assert wave["selected_total"] == 2
    assert [item["origin"] for item in wave["selected"]] == [
        "devel/base",
        "devel/explicit",
    ]
    reasons = {item["origin"]: item["selection_reason"] for item in wave["selected"]}
    assert reasons["devel/base"] == "baseline_match"
    assert reasons["devel/explicit"] == "explicit_target_match"
    counters = wave["selection_counters"]
    assert counters["baseline_selected_count"] == 1
    assert counters["explicit_selected_count"] == 1
    assert counters["excluded_by_target_count"] == 1


def test_select_wave_is_deterministic() -> None:
    inventory = cast(
        list[dict[str, Any]], read_json_fixture("migration/inventory-basic.json")
    )
    first = select_wave(inventory, buckets=["auto-safe"], max_ports=3)
    second = select_wave(inventory, buckets=["auto-safe"], max_ports=3)

    assert first == second


def test_build_wave_report_gates_pass_and_fail() -> None:
    passing = build_wave_report(
        cast(list[dict[str, Any]], read_json_fixture("migration/results-pass.json"))
    )
    failing = build_wave_report(
        cast(list[dict[str, Any]], read_json_fixture("migration/results-fail.json"))
    )

    assert passing["gate_pass"] is True
    assert failing["gate_pass"] is False
    assert failing["gates"]["no_hard_failures"] is False
    assert failing["gates"]["deterministic_outputs"] is False
    assert failing["gates"]["no_unclassified_overlay"] is False


def test_cli_wave_plan_outputs_json(capsys) -> None:
    inventory = fixture_path("migration/inventory-basic.json")
    code = main(
        [
            "migrate",
            "wave-plan",
            str(inventory),
            "--bucket",
            "auto-safe",
            "--target",
            "@main",
            "--max-ports",
            "2",
            "--dry-run",
            "--json",
        ]
    )
    out = capsys.readouterr()

    assert code == 0
    assert out.err == ""
    payload = json.loads(out.out)
    assert payload["dry_run"] is True
    assert payload["selected_total"] == 2


def test_cli_wave_report_strict_exit(capsys) -> None:
    failing = fixture_path("migration/results-fail.json")
    code = main(["migrate", "wave-report", str(failing), "--strict", "--json"])
    out = capsys.readouterr()

    assert code == 2
    assert out.err == ""
    payload = json.loads(out.out)
    assert payload["gate_pass"] is False
