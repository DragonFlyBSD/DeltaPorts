"""Compose pipeline facade and orchestrator for DeltaPorts v3."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from dportsv3.compose_discovery import normalize_target
from dportsv3.compose_models import ComposePortReport, ComposeResult, ComposeStageResult
from dportsv3.compose_patching import apply_patch as _apply_patch
from dportsv3.compose_patching import find_patch_artifacts
from dportsv3.compose_reporting import (
    build_compose_report_overview,
    format_compose_overview,
    format_compose_result,
)
from dportsv3.compose_stages import (
    apply_special_stage,
    fallback_stage,
    finalize_stage,
    preflight_stage,
    prune_stale_overlays_stage,
    seed_stage,
    semantic_stage,
    system_replacements_stage,
)
from dportsv3.engine.api import apply_dsl, build_plan
from dportsv3.engine.oracle import normalize_oracle_profile


def _record_stage(
    result: ComposeResult,
    stage: ComposeStageResult,
    *,
    strict: bool,
    reports: dict[str, ComposePortReport] | None = None,
) -> bool:
    result.add_stage(stage)
    if not strict or stage.success:
        return False
    if reports is not None:
        result.ports = [reports[key] for key in sorted(reports.keys())]
    result.finished_at = datetime.now()
    return True


def run_compose(
    *,
    target: str,
    output_path: Path,
    delta_root: Path,
    freebsd_root: Path,
    lock_root: Path | None = None,
    dry_run: bool = False,
    strict: bool = False,
    replace_output: bool = False,
    prune_stale_overlays: bool = False,
    oracle_profile: str = "local",
) -> ComposeResult:
    """Run dportsv3 compose pipeline for one target."""
    try:
        normalized_oracle_profile = normalize_oracle_profile(oracle_profile)
    except ValueError:
        normalized_oracle_profile = oracle_profile

    result = ComposeResult(
        target=target,
        output_path=output_path,
        oracle_profile=normalized_oracle_profile,
        started_at=datetime.now(),
    )

    if normalized_oracle_profile not in {"off", "local", "ci"}:
        stage = ComposeStageResult(name="preflight_validate", started_at=datetime.now())
        stage.add_error(
            "E_COMPOSE_INVALID_ORACLE_PROFILE",
            f"invalid oracle profile: {oracle_profile}",
        )
        stage.finished_at = datetime.now()
        result.add_stage(stage)
        result.finished_at = datetime.now()
        return result

    target_branch = normalize_target(target)
    if target_branch is None:
        stage = ComposeStageResult(name="preflight_validate", started_at=datetime.now())
        stage.add_error("E_COMPOSE_INVALID_TARGET", f"invalid target: {target}")
        stage.finished_at = datetime.now()
        result.add_stage(stage)
        result.finished_at = datetime.now()
        return result

    lock_source = lock_root if lock_root is not None else delta_root / "locked"

    stage_seed = seed_stage(
        freebsd_root=freebsd_root,
        output_path=output_path,
        dry_run=dry_run,
        replace_output=replace_output,
    )
    if _record_stage(result, stage_seed, strict=strict):
        return result

    stage_special = apply_special_stage(
        delta_root=delta_root,
        freebsd_root=freebsd_root,
        output_path=output_path,
        dry_run=dry_run,
        patch_runner=_apply_patch,
    )
    if _record_stage(result, stage_special, strict=strict):
        return result

    stage_preflight, contexts, reports = preflight_stage(
        target=target,
        target_branch=target_branch,
        delta_root=delta_root,
        freebsd_root=freebsd_root,
        prune_stale_overlays=prune_stale_overlays,
        build_plan_fn=build_plan,
    )
    if _record_stage(result, stage_preflight, strict=strict, reports=reports):
        return result

    stage_prune_stale = prune_stale_overlays_stage(
        contexts=contexts,
        reports=reports,
        delta_root=delta_root,
        output_path=output_path,
        dry_run=dry_run,
        prune_stale_overlays=prune_stale_overlays,
    )
    result.add_stage(stage_prune_stale)

    stage_semantic = semantic_stage(
        contexts=contexts,
        reports=reports,
        target=target,
        freebsd_root=freebsd_root,
        output_path=output_path,
        lock_root=lock_source,
        dry_run=dry_run,
        strict=strict,
        oracle_profile=normalized_oracle_profile,
        apply_dsl_fn=apply_dsl,
    )
    if _record_stage(result, stage_semantic, strict=strict, reports=reports):
        return result

    stage_fallback = fallback_stage(
        contexts=contexts,
        reports=reports,
        target=target,
        freebsd_root=freebsd_root,
        output_path=output_path,
        lock_root=lock_source,
        dry_run=dry_run,
        strict=strict,
        patch_runner=_apply_patch,
    )
    if _record_stage(result, stage_fallback, strict=strict, reports=reports):
        return result

    stage_system_replacements = system_replacements_stage(
        output_path=output_path,
        dry_run=dry_run,
    )
    if _record_stage(result, stage_system_replacements, strict=strict, reports=reports):
        return result

    stage_finalize = finalize_stage(
        contexts=contexts,
        reports=reports,
        freebsd_root=freebsd_root,
        output_path=output_path,
        dry_run=dry_run,
        patch_artifact_finder=find_patch_artifacts,
    )
    result.add_stage(stage_finalize)

    result.ports = [reports[key] for key in sorted(reports.keys())]
    result.finished_at = datetime.now()
    return result


__all__ = [
    "ComposePortReport",
    "ComposeResult",
    "ComposeStageResult",
    "build_compose_report_overview",
    "format_compose_overview",
    "format_compose_result",
    "run_compose",
    "_apply_patch",
    "apply_dsl",
]
