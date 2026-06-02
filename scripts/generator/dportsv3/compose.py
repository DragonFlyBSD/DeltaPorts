"""Compose pipeline facade and orchestrator for DeltaPorts v3."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
import re

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
from dportsv3.fsutils import reconcile


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
    selected_origins: list[str] | None = None,
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
    requested_origins = sorted(
        {origin for origin in (selected_origins or []) if origin}
    )
    incremental = bool(requested_origins)

    invalid_origins = [
        origin
        for origin in requested_origins
        if re.fullmatch(r"[^/]+/[^/]+", origin) is None
    ]
    if invalid_origins:
        stage = ComposeStageResult(name="preflight_validate", started_at=datetime.now())
        for origin in invalid_origins:
            stage.add_error(
                "E_COMPOSE_INVALID_ORIGIN",
                f"invalid origin selector: {origin}",
            )
        stage.finished_at = datetime.now()
        result.add_stage(stage)
        result.finished_at = datetime.now()
        return result

    # Full-compose scratch indirection. dsynth's port-change detector
    # (subs.c::crcDirTree) folds mtime+size+path per file; rewriting
    # bit-identical content still bumps mtime and force-rebuilds the
    # package. Composing into a scratch tree and reconciling onto the
    # live output preserves mtime when content matches, so a no-change
    # recompose is a true filesystem no-op and dsynth stays quiet.
    # Scratch only applies to full composes — incremental composes
    # are explicit per-port operator actions where rebuilding the
    # selected ports is expected.
    final_output = output_path
    scratch_parent: Path | None = None
    scratch_root: Path | None = None
    use_scratch = not dry_run and not incremental
    if use_scratch:
        # mkdtemp first, BEFORE try, so the cleanup path knows about
        # the parent dir even if the mkdir below raises. Anything
        # inside the try is then safe to fail — finally will still
        # `rmtree(scratch_parent)`.
        scratch_parent = Path(tempfile.mkdtemp(prefix="dportsv3-compose-"))

    try:
        if scratch_parent is not None:
            scratch_root = scratch_parent / "tree"
            scratch_root.mkdir(parents=True, exist_ok=True)
            output_path = scratch_root  # downstream stages write here
        return _run_stages(
            target=target,
            target_branch=target_branch,
            output_path=output_path,
            final_output=final_output,
            scratch_root=scratch_root,
            delta_root=delta_root,
            freebsd_root=freebsd_root,
            lock_source=lock_source,
            requested_origins=requested_origins,
            incremental=incremental,
            dry_run=dry_run,
            strict=strict,
            replace_output=replace_output,
            prune_stale_overlays=prune_stale_overlays,
            normalized_oracle_profile=normalized_oracle_profile,
            result=result,
        )
    finally:
        if scratch_parent is not None:
            shutil.rmtree(scratch_parent, ignore_errors=True)


def _run_stages(
    *,
    target: str,
    target_branch: str,
    output_path: Path,
    final_output: Path,
    scratch_root: Path | None,
    delta_root: Path,
    freebsd_root: Path,
    lock_source: Path,
    requested_origins: list[str],
    incremental: bool,
    dry_run: bool,
    strict: bool,
    replace_output: bool,
    prune_stale_overlays: bool,
    normalized_oracle_profile: str,
    result: ComposeResult,
) -> ComposeResult:
    """Stage chain — extracted so the orchestrator's scratch cleanup
    fires on every exit, including the strict-mode early returns and
    the incremental-seed-fail early return."""
    stage_seed = seed_stage(
        freebsd_root=freebsd_root,
        output_path=output_path,
        dry_run=dry_run,
        replace_output=replace_output,
        incremental=incremental,
        selected_origins=requested_origins,
    )
    if incremental and not stage_seed.success:
        result.add_stage(stage_seed)
        result.finished_at = datetime.now()
        return result
    if _record_stage(result, stage_seed, strict=strict):
        return result

    stage_special = apply_special_stage(
        delta_root=delta_root,
        freebsd_root=freebsd_root,
        output_path=output_path,
        target=target,
        dry_run=dry_run,
        incremental=incremental,
        selected_origins=requested_origins,
        patch_runner=_apply_patch,
    )
    if _record_stage(result, stage_special, strict=strict):
        return result

    stage_preflight, contexts, reports = preflight_stage(
        target=target,
        target_branch=target_branch,
        delta_root=delta_root,
        freebsd_root=freebsd_root,
        selected_origins=requested_origins if incremental else None,
        dry_run=dry_run,
        prune_stale_overlays=prune_stale_overlays,
        build_plan_fn=build_plan,
    )
    if _record_stage(result, stage_preflight, strict=strict, reports=reports):
        return result

    stage_prune_stale = prune_stale_overlays_stage(
        contexts=contexts,
        reports=reports,
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
        incremental=incremental,
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
        selected_origins=requested_origins if incremental else None,
    )
    if _record_stage(result, stage_system_replacements, strict=strict, reports=reports):
        return result

    stage_finalize = finalize_stage(
        contexts=contexts,
        reports=reports,
        freebsd_root=freebsd_root,
        output_path=output_path,
        dry_run=dry_run,
        incremental=incremental,
        selected_origins=requested_origins if incremental else None,
        patch_artifact_finder=find_patch_artifacts,
    )
    result.add_stage(stage_finalize)

    if scratch_root is not None:
        result.add_stage(
            _reconcile_to_live(scratch_root=scratch_root, final_output=final_output)
        )

    result.ports = [reports[key] for key in sorted(reports.keys())]
    result.finished_at = datetime.now()
    return result


def _reconcile_to_live(*, scratch_root: Path, final_output: Path) -> ComposeStageResult:
    """Push the scratch tree onto the live output via the content-
    aware ``reconcile`` primitive — files whose content matches what
    live already had keep their previous mtime, so dsynth's per-file
    CRC stays stable and no spurious rebuilds are triggered."""
    stage = ComposeStageResult(name="reconcile_output", started_at=datetime.now())
    final_output.parent.mkdir(parents=True, exist_ok=True)
    try:
        if not final_output.exists():
            # First-ever compose into this path — nothing to reconcile
            # against, just rename scratch in place. copytree because
            # rename across filesystems isn't guaranteed.
            shutil.copytree(scratch_root, final_output, symlinks=True)
        else:
            reconcile(scratch_root, final_output)
        stage.changed = 1
    except Exception as exc:
        stage.add_error("E_COMPOSE_RECONCILE_FAILED", str(exc))
    stage.finished_at = datetime.now()
    return stage


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
