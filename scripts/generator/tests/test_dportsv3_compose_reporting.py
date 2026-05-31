from __future__ import annotations

from pathlib import Path

from dportsv3.compose_models import ComposeResult, ComposeStageResult
from dportsv3.compose_reporting import (
    build_compose_report_overview,
    format_compose_overview,
    format_compose_result,
)


def test_build_compose_report_overview_extracts_special_metadata() -> None:
    payload = {
        "ok": False,
        "target": "@2026Q1",
        "output_path": "/tmp/out",
        "stages": [
            {
                "name": "apply_special",
                "success": False,
                "changed": 10,
                "skipped": 0,
                "warnings": [],
                "errors": [
                    "E_COMPOSE_SPECIAL_PATCH_FAILED: Mk/bsd.port.mk.diff: failed"
                ],
                "metadata": {
                    "components": [
                        {
                            "component": "Mk",
                            "selected_patches": 3,
                            "patched": 2,
                            "failed_patches": ["bsd.port.mk.diff"],
                            "copied": 2,
                            "auto_created_from_main": False,
                            "removed_legacy_files": ["bsd.gcc.mk"],
                        },
                        {
                            "component": "Templates",
                            "selected_patches": 1,
                            "patched": 1,
                            "failed_patches": [],
                            "copied": 1,
                            "auto_created_from_main": False,
                            "removed_legacy_files": [],
                        },
                    ]
                },
            }
        ],
        "ports": [{"origin": "devel/a", "mode": "compat"}],
    }

    overview = build_compose_report_overview(payload, top=5)

    assert overview["special"] == {
        "components": [
            {
                "component": "Mk",
                "selected": 3,
                "patched": 2,
                "failed": 1,
                "failed_patches": ["bsd.port.mk.diff"],
                "copied": 2,
                "bootstrapped": False,
                "removed_legacy": ["bsd.gcc.mk"],
            },
            {
                "component": "Templates",
                "selected": 1,
                "patched": 1,
                "failed": 0,
                "failed_patches": [],
                "copied": 1,
                "bootstrapped": False,
                "removed_legacy": [],
            },
        ],
        "total_selected": 4,
        "total_patched": 3,
        "total_failed": 1,
        "any_bootstrapped": False,
    }
    assert "fix failed special/ patches and rerun compose" in overview["hints"]


def test_build_compose_report_overview_special_bootstrap_hint() -> None:
    payload = {
        "ok": True,
        "target": "@2026Q1",
        "output_path": "/tmp/out",
        "stages": [
            {
                "name": "apply_special",
                "success": True,
                "changed": 1,
                "skipped": 0,
                "warnings": [
                    "I_COMPOSE_SPECIAL_TARGET_BOOTSTRAPPED: Mk: copied from @main"
                ],
                "errors": [],
                "metadata": {
                    "components": [
                        {
                            "component": "Mk",
                            "selected_patches": 0,
                            "patched": 0,
                            "failed_patches": [],
                            "copied": 1,
                            "auto_created_from_main": True,
                            "removed_legacy_files": [],
                        }
                    ]
                },
            }
        ],
        "ports": [],
    }

    overview = build_compose_report_overview(payload)

    assert overview["special"]["any_bootstrapped"] is True
    assert (
        "auto-bootstrapped target dirs created from @main -- review and "
        "customize patches for this target"
    ) in overview["hints"]


def test_build_compose_report_overview_without_apply_special_stage() -> None:
    payload = {
        "ok": True,
        "target": "@main",
        "output_path": "/tmp/out",
        "stages": [
            {
                "name": "seed_output",
                "success": True,
                "changed": 1,
                "skipped": 0,
                "warnings": [],
                "errors": [],
                "metadata": {},
            }
        ],
        "ports": [],
    }

    overview = build_compose_report_overview(payload)

    assert overview["special"] == {
        "components": [],
        "total_selected": 0,
        "total_patched": 0,
        "total_failed": 0,
        "any_bootstrapped": False,
    }


def test_build_compose_report_overview_tracks_stale_marked_removed() -> None:
    payload = {
        "ok": False,
        "target": "@main",
        "output_path": "/tmp/out",
        "stages": [
            {
                "name": "preflight_validate",
                "success": False,
                "changed": 1,
                "skipped": 0,
                "warnings": [
                    "I_COMPOSE_STALE_MARKED_REMOVED: devel/missing: added removed_in target @main to overlay.toml"
                ],
                "errors": [
                    "E_COMPOSE_STALE_OVERLAY: devel/missing: overlay origin missing in upstream target"
                ],
                "metadata": {},
            },
            {
                "name": "prune_stale_overlays",
                "success": True,
                "changed": 0,
                "skipped": 1,
                "warnings": [],
                "errors": [],
                "metadata": {"output_removed": []},
            },
        ],
        "ports": [{"origin": "devel/missing", "mode": "compat"}],
    }

    overview = build_compose_report_overview(payload)

    assert overview["stale"] == {
        "count": 1,
        "origins": ["devel/missing"],
        "marked_removed": 1,
        "pruned": 0,
    }
    assert (
        "stale overlays were marked with removed_in; rerun compose to skip persisted entries"
        in overview["hints"]
    )
    assert "stale: count=1 marked_removed=1 pruned=0" in format_compose_overview(
        overview,
        include_special=False,
    )


def test_format_compose_overview_includes_special_section() -> None:
    overview = {
        "top_error_codes": [],
        "top_warning_codes": [],
        "top_error_origins": [],
        "top_failed_patches": [],
        "mode_counts": {},
        "stale": {"count": 0, "origins": [], "marked_removed": 0, "pruned": 0},
        "special": {
            "components": [
                {
                    "component": "Mk",
                    "selected": 3,
                    "patched": 2,
                    "failed": 1,
                    "failed_patches": ["bsd.port.mk.diff"],
                    "copied": 2,
                    "bootstrapped": False,
                    "removed_legacy": ["bsd.gcc.mk"],
                },
                {
                    "component": "Tools",
                    "selected": 0,
                    "patched": 0,
                    "failed": 0,
                    "failed_patches": [],
                    "copied": 1,
                    "bootstrapped": True,
                    "removed_legacy": [],
                },
            ],
            "total_selected": 3,
            "total_patched": 2,
            "total_failed": 1,
            "any_bootstrapped": True,
        },
        "hints": [],
    }

    lines = format_compose_overview(overview)

    assert (
        "special/Mk: 2/3 patched 2 copied 1 failed [bsd.port.mk.diff] "
        "removed [bsd.gcc.mk]"
    ) in lines
    assert "special/Tools: 1 copied (bootstrapped from @main)" in lines


def test_format_compose_result_includes_special_detail_once() -> None:
    result = ComposeResult(target="@2026Q1", output_path=Path("/tmp/out"))
    result.add_stage(
        ComposeStageResult(
            name="apply_special",
            success=False,
            changed=3,
            errors=["E_COMPOSE_SPECIAL_PATCH_FAILED: Mk/bsd.port.mk.diff: failed"],
            metadata={
                "components": [
                    {
                        "component": "Mk",
                        "selected_patches": 3,
                        "patched": 2,
                        "failed_patches": ["bsd.port.mk.diff"],
                        "copied": 2,
                        "auto_created_from_main": False,
                        "removed_legacy_files": ["bsd.gcc.mk"],
                    }
                ]
            },
        )
    )

    lines = format_compose_result(result)
    mk_rows = [line for line in lines if "special/Mk:" in line]

    assert mk_rows == [
        "  special/Mk: 2/3 patched 2 copied 1 failed [bsd.port.mk.diff] removed [bsd.gcc.mk]"
    ]
    assert "hint: fix failed special/ patches and rerun compose" in lines


def test_format_compose_overview_can_skip_special_section() -> None:
    overview = {
        "top_error_codes": [],
        "top_warning_codes": [],
        "top_error_origins": [],
        "top_failed_patches": [],
        "mode_counts": {},
        "stale": {"count": 0, "origins": [], "marked_removed": 0, "pruned": 0},
        "special": {
            "components": [
                {
                    "component": "Mk",
                    "selected": 1,
                    "patched": 1,
                    "failed": 0,
                    "failed_patches": [],
                    "copied": 0,
                    "bootstrapped": False,
                    "removed_legacy": [],
                }
            ],
            "total_selected": 1,
            "total_patched": 1,
            "total_failed": 0,
            "any_bootstrapped": False,
        },
        "hints": [],
    }

    lines = format_compose_overview(overview, include_special=False)

    assert lines == []


def test_dops_failed_ops_carries_source_path():
    """Step 37: dops_failed_ops rows expose source_path so the
    convert handler's defer loop can identify which file the failing
    op was operating on. Pinned because the field is a contractual
    surface — runner._parse_compose_rejects scans the formatted
    output for `diffs/X.diff` literals."""
    payload = {
        "ok": False,
        "target": "@main",
        "output_path": "/tmp/out",
        "stages": [],
        "ports": [
            {
                "origin": "lang/python311",
                "mode": "dops",
                "dops_failed_op_results": [
                    {
                        "id": "op-0001-patch-apply",
                        "kind": "patch.apply",
                        "target": "@main",
                        "status": "failed",
                        "message": "e_apply_patch_failed",
                        "diagnostics": [
                            {
                                "severity": "error",
                                "code": "E_APPLY_PATCH_FAILED",
                                "message": "patching pkg-plist using Plan A...\nHunk #1 failed at 249.",
                                "source_path": "/work/DeltaPorts/ports/lang/python311/diffs/pkg-plist.diff",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    overview = build_compose_report_overview(payload, top=3)
    rows = overview["dops_failed_ops"]
    assert len(rows) == 1
    assert rows[0]["source_path"] == (
        "/work/DeltaPorts/ports/lang/python311/diffs/pkg-plist.diff"
    )
    lines = format_compose_overview(overview)
    text = "\n".join(lines)
    # The formatted line must carry the path so plain-text consumers
    # (the patch-tool stdout that compose pipes through) can scan it.
    assert "diffs/pkg-plist.diff" in text


def test_dops_failed_ops_source_path_empty_when_absent():
    """Non-patch op kinds may not carry a source_path. The field is
    still present but empty; the formatter omits the trailing
    [path] suffix in that case."""
    payload = {
        "ok": False,
        "target": "@main",
        "output_path": "/tmp/out",
        "stages": [],
        "ports": [
            {
                "origin": "x/y",
                "mode": "dops",
                "dops_failed_op_results": [
                    {
                        "id": "op-0001-mk-set",
                        "kind": "mk.var.set",
                        "target": "@main",
                        "status": "failed",
                        "message": "e_apply_failed",
                        "diagnostics": [
                            {"severity": "error", "code": "E_APPLY_FAILED",
                             "message": "boom", "source_path": ""},
                        ],
                    }
                ],
            }
        ],
    }
    overview = build_compose_report_overview(payload, top=3)
    rows = overview["dops_failed_ops"]
    assert rows[0]["source_path"] == ""
    lines = format_compose_overview(overview)
    text = "\n".join(lines)
    # No path → no [ ] suffix
    assert "[]" not in text
