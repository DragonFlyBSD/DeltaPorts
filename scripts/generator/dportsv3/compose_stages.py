"""Compose stage implementations."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable

from dportsv3.compat import infer_compat_port_type, run_compat_merge
from dportsv3.compose_discovery import (
    discover_overlay_contexts,
    list_port_origins,
    validate_target_scoped_payloads,
)
from dportsv3.compose_models import (
    ComposePortContext,
    ComposePortReport,
    ComposeStageResult,
)
from dportsv3.compose_patching import copy_treetop_identity_files
from dportsv3.engine.api import apply_dsl, build_plan
from dportsv3.fsutils import copy_tree
from dportsv3.plan_types import materialize_plan_type
from dportsv3.policy import (
    EXCLUDED_TOP_LEVEL,
    MOVED_KEEP_AFTER_YEAR,
    SPECIAL_COMPONENTS,
    UPDATING_ROLLING_WINDOW_DAYS,
)
from dportsv3.system_replacements import apply_system_replacements_to_port


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def seed_stage(
    *,
    freebsd_root: Path,
    output_path: Path,
    dry_run: bool,
    replace_output: bool,
) -> ComposeStageResult:
    stage = ComposeStageResult(name="seed_output", started_at=datetime.now())
    if not freebsd_root.exists() or not freebsd_root.is_dir():
        stage.add_error(
            "E_COMPOSE_INVALID_FREEBSD_ROOT", f"missing freebsd root: {freebsd_root}"
        )
        stage.finished_at = datetime.now()
        return stage

    if output_path.exists() and any(output_path.iterdir()) and not replace_output:
        stage.add_error(
            "E_COMPOSE_OUTPUT_NOT_EMPTY",
            f"output path is not empty: {output_path} (use --replace-output)",
        )
        stage.finished_at = datetime.now()
        return stage

    seeded_dirs = 0
    if not dry_run:
        if output_path.exists() and replace_output:
            shutil.rmtree(output_path)
        output_path.mkdir(parents=True, exist_ok=True)

        for entry in sorted(freebsd_root.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            if entry.name in EXCLUDED_TOP_LEVEL:
                continue
            copy_tree(entry, output_path / entry.name)
            seeded_dirs += 1
    stage.changed = seeded_dirs if not dry_run else 1
    stage.metadata["seeded_top_level_dirs"] = seeded_dirs
    stage.metadata["seeded_ports"] = len(list_port_origins(freebsd_root))
    stage.finished_at = datetime.now()
    return stage


def apply_special_stage(
    *,
    delta_root: Path,
    freebsd_root: Path,
    output_path: Path,
    dry_run: bool,
    patch_runner: Callable[[Path, Path, bool], tuple[bool, str]],
) -> ComposeStageResult:
    stage = ComposeStageResult(name="apply_special", started_at=datetime.now())
    gid_uid_copied = copy_treetop_identity_files(
        output_path,
        freebsd_root,
        dry_run=dry_run,
    )
    if gid_uid_copied > 0:
        stage.metadata["gid_uid_copied"] = gid_uid_copied

    special_root = delta_root / "special"
    if not special_root.exists() or not special_root.is_dir():
        stage.changed = gid_uid_copied
        stage.finished_at = datetime.now()
        return stage

    copied = gid_uid_copied
    patched = 0
    component_rows: list[dict[str, Any]] = []
    for component in SPECIAL_COMPONENTS:
        comp_copied = 0
        comp_patched = 0
        failed_patches: list[str] = []
        removed_legacy_files: list[str] = []

        if component == "treetop":
            src = freebsd_root
            dst = output_path
        else:
            src = freebsd_root / component
            dst = output_path / component

        if component != "treetop" and src.exists() and src.is_dir():
            comp_copied += 1
            if not dry_run:
                copy_tree(src, dst)

        if component == "Mk":
            bsd_gcc = dst / "bsd.gcc.mk"
            if bsd_gcc.exists():
                removed_legacy_files.append("bsd.gcc.mk")
                if not dry_run:
                    bsd_gcc.unlink(missing_ok=True)

        diffs_dir = special_root / component / "diffs"
        selected_patches: list[Path] = []
        if diffs_dir.exists() and diffs_dir.is_dir():
            selected_patches = sorted(
                path for path in diffs_dir.rglob("*.diff") if path.is_file()
            )

        for patch in selected_patches:
            base_dir = dst
            if not base_dir.exists():
                continue
            ok, detail = patch_runner(patch, base_dir, dry_run)
            if ok:
                comp_patched += 1
            else:
                failed_patches.append(patch.name)
                stage.add_error(
                    "E_COMPOSE_SPECIAL_PATCH_FAILED",
                    f"{component}/{patch.name}: {detail}",
                )

        replacements = special_root / component / "replacements"
        if replacements.exists() and replacements.is_dir():
            for repl in sorted(
                path for path in replacements.rglob("*") if path.is_file()
            ):
                comp_copied += 1
                if not dry_run:
                    rel = repl.relative_to(replacements)
                    target_file = dst / rel
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(repl, target_file)

        copied += comp_copied
        patched += comp_patched
        component_rows.append(
            {
                "component": component,
                "copied": comp_copied,
                "patched": comp_patched,
                "failed_patches": failed_patches,
                "selected_patches": len(selected_patches),
                "removed_legacy_files": removed_legacy_files,
            }
        )

    stage.changed = copied + patched
    stage.metadata["components"] = component_rows
    stage.metadata["copied"] = copied
    stage.metadata["patched"] = patched
    stage.finished_at = datetime.now()
    return stage


def _check_freebsd_git_state(
    *,
    stage: ComposeStageResult,
    freebsd_root: Path,
    target_branch: str,
) -> bool:
    code, stdout, stderr = _run_git(
        ["rev-parse", "--is-inside-work-tree"], freebsd_root
    )
    if code != 0 or stdout != "true":
        stage.add_error(
            "E_COMPOSE_FREEBSD_NOT_GIT", stderr or f"not a git repo: {freebsd_root}"
        )
        return False

    code, stdout, stderr = _run_git(
        ["symbolic-ref", "--quiet", "--short", "HEAD"], freebsd_root
    )
    if code != 0:
        code, stdout, stderr = _run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], freebsd_root
        )
    if code != 0:
        stage.add_error(
            "E_COMPOSE_GIT_BRANCH_CHECK_FAILED",
            stderr or "failed to inspect freebsd branch",
        )
        return True
    if stdout != target_branch:
        stage.add_error(
            "E_COMPOSE_TARGET_BRANCH_MISMATCH",
            f"expected branch {target_branch}, current {stdout}",
        )
    return True


def _apply_stale_overlay_policy(
    *,
    ctx: ComposePortContext,
    report: ComposePortReport,
    stage: ComposeStageResult,
    reason: str,
    prune_stale_overlays: bool,
) -> None:
    ctx.stale = True
    ctx.stale_reason = reason
    if prune_stale_overlays:
        stage.add_warning(
            "I_COMPOSE_STALE_OVERLAY_PRUNE_CANDIDATE",
            f"{ctx.origin}: {ctx.stale_reason}",
        )
        report.notes.append("stale-prune-candidate")
    else:
        stage.add_error(
            "E_COMPOSE_STALE_OVERLAY",
            f"{ctx.origin}: {ctx.stale_reason}",
        )
        report.errors += 1


def _record_preflight_mode_notes(
    *,
    ctx: ComposePortContext,
    report: ComposePortReport,
    stage: ComposeStageResult,
) -> None:
    report.mode = ctx.mode
    report.mode_reason = ctx.mode_reason
    if ctx.mode == "dops" and (
        ctx.compat_makefile is not None
        or bool(ctx.fallback_patches)
        or bool(ctx.implicit_payload_files)
    ):
        stage.add_warning(
            "I_COMPOSE_MODE_DOPS_SUPPRESSES_COMPAT",
            f"{ctx.origin}: compatibility artifacts ignored because overlay.dops is present",
        )
        report.notes.append("compat-artifacts-suppressed-by-dops")
    if ctx.mode == "compat" and ctx.compat_override_notes:
        for note in ctx.compat_override_notes:
            stage.add_warning(
                "I_COMPOSE_COMPAT_LAYER_OVERRIDE",
                f"{ctx.origin}: {note}",
            )
            report.notes.append(f"compat-layer-note={note}")
    if ctx.mode == "compat" and ctx.compat_legacy_notes:
        for note in ctx.compat_legacy_notes:
            stage.add_warning(
                "I_COMPOSE_COMPAT_LEGACY_ROOT_FALLBACK",
                f"{ctx.origin}: {note}",
            )
            report.notes.append(f"compat-legacy-note={note}")


def _record_target_scope_errors(
    *,
    ctx: ComposePortContext,
    report: ComposePortReport,
    stage: ComposeStageResult,
) -> None:
    payload_errors = validate_target_scoped_payloads(ctx)
    for error in payload_errors:
        stage.add_error("E_COMPOSE_INVALID_TARGET_SCOPE", f"{ctx.origin}: {error}")
        report.errors += 1


def preflight_stage(
    *,
    target: str,
    target_branch: str,
    delta_root: Path,
    freebsd_root: Path,
    prune_stale_overlays: bool,
    build_plan_fn: Callable[[str, Path | None], Any] = build_plan,
) -> tuple[ComposeStageResult, list[ComposePortContext], dict[str, ComposePortReport]]:
    stage = ComposeStageResult(name="preflight_validate", started_at=datetime.now())
    contexts = discover_overlay_contexts(delta_root, target)
    reports = {ctx.origin: ComposePortReport(origin=ctx.origin) for ctx in contexts}

    if not _check_freebsd_git_state(
        stage=stage,
        freebsd_root=freebsd_root,
        target_branch=target_branch,
    ):
        stage.finished_at = datetime.now()
        return stage, contexts, reports

    upstream_origins = list_port_origins(freebsd_root)
    for ctx in contexts:
        report = reports[ctx.origin]
        _record_preflight_mode_notes(ctx=ctx, report=report, stage=stage)
        _record_target_scope_errors(ctx=ctx, report=report, stage=stage)

        if ctx.dops_path is None:
            compat_type, compat_reason = infer_compat_port_type(ctx.path)
            ctx.plan_type = compat_type
            report.port_type = compat_type
            report.notes.append(f"compat-type={compat_type} ({compat_reason})")

            if compat_type == "port" and ctx.origin not in upstream_origins:
                _apply_stale_overlay_policy(
                    ctx=ctx,
                    report=report,
                    stage=stage,
                    reason="overlay origin missing in upstream target",
                    prune_stale_overlays=prune_stale_overlays,
                )
            continue

        source = ctx.dops_path.read_text()
        planned = build_plan_fn(source, ctx.dops_path)
        if not planned.ok or planned.plan is None:
            codes = ",".join(diag.code for diag in planned.diagnostics)
            stage.add_error("E_COMPOSE_PLAN_INVALID", f"{ctx.origin}: {codes}")
            report.errors += 1
            continue

        ctx.plan_type = planned.plan.type
        report.port_type = ctx.plan_type
        if planned.plan.type == "port" and ctx.origin not in upstream_origins:
            _apply_stale_overlay_policy(
                ctx=ctx,
                report=report,
                stage=stage,
                reason="type port but upstream origin is missing",
                prune_stale_overlays=prune_stale_overlays,
            )

    stage.metadata["origins"] = len(contexts)
    stage.changed = len(contexts)
    stage.finished_at = datetime.now()
    return stage, contexts, reports


def is_stale_port_context(ctx: ComposePortContext) -> bool:
    """Return true when context points to stale type=port overlay."""
    return ctx.stale and ctx.plan_type == "port"


def prune_stale_overlays_stage(
    *,
    contexts: list[ComposePortContext],
    reports: dict[str, ComposePortReport],
    delta_root: Path,
    output_path: Path,
    dry_run: bool,
    prune_stale_overlays: bool,
) -> ComposeStageResult:
    stage = ComposeStageResult(name="prune_stale_overlays", started_at=datetime.now())
    stale_contexts = [ctx for ctx in contexts if is_stale_port_context(ctx)]
    stage.metadata["candidates"] = [ctx.origin for ctx in stale_contexts]
    stage.metadata["candidate_total"] = len(stale_contexts)
    stage.metadata["delta_removed"] = []
    stage.metadata["output_removed"] = []

    if not prune_stale_overlays:
        stage.skipped = len(stale_contexts)
        stage.finished_at = datetime.now()
        return stage

    for ctx in stale_contexts:
        report = reports[ctx.origin]
        report.notes.append("stale-pruned")
        if dry_run:
            continue

        delta_overlay = delta_root / "ports" / ctx.origin
        if delta_overlay.exists() and delta_overlay.is_dir():
            shutil.rmtree(delta_overlay)
            stage.changed += 1
            stage.metadata["delta_removed"].append(ctx.origin)

        output_origin = output_path / ctx.origin
        if output_origin.exists() and output_origin.is_dir():
            shutil.rmtree(output_origin)
            stage.changed += 1
            stage.metadata["output_removed"].append(ctx.origin)

        stage.add_warning(
            "I_COMPOSE_STALE_OVERLAY_PRUNED",
            f"{ctx.origin}: removed stale type=port overlay from delta/output",
        )

    stage.finished_at = datetime.now()
    return stage


def semantic_stage(
    *,
    contexts: list[ComposePortContext],
    reports: dict[str, ComposePortReport],
    target: str,
    freebsd_root: Path,
    output_path: Path,
    lock_root: Path,
    dry_run: bool,
    strict: bool,
    oracle_profile: str,
    apply_dsl_fn: Callable[..., Any] = apply_dsl,
) -> ComposeStageResult:
    stage = ComposeStageResult(name="apply_semantic_ops", started_at=datetime.now())
    for ctx in contexts:
        report = reports[ctx.origin]
        if is_stale_port_context(ctx):
            stage.skipped += 1
            report.notes.append("stale-skipped")
            continue
        if ctx.mode != "dops":
            stage.skipped += 1
            report.notes.append("mode=compat")
            continue
        output_origin = output_path / ctx.origin
        upstream_origin = freebsd_root / ctx.origin
        overlay_newport = ctx.path / "newport"
        lock_origin = lock_root / ctx.origin

        materialized = materialize_plan_type(
            plan_type=ctx.plan_type,
            output_origin=output_origin,
            upstream_origin=upstream_origin,
            newport_origin=overlay_newport,
            lock_origin=lock_origin,
            dry_run=dry_run,
            copy_port_base=False,
            missing_dport_error="missing newport source",
            missing_lock_error="missing lock source",
            missing_port_error="missing upstream source",
        )
        if not materialized.ok:
            code = "E_COMPOSE_SEMANTIC_SOURCE_MISSING"
            if ctx.plan_type == "dport":
                code = "E_COMPOSE_DPORT_SOURCE_MISSING"
            elif ctx.plan_type == "lock":
                code = "E_COMPOSE_LOCK_SOURCE_MISSING"
            stage.add_error(
                code,
                f"{ctx.origin}: {materialized.error or 'missing source'}",
            )
            report.errors += 1
            if strict:
                stage.finished_at = datetime.now()
                return stage
            continue

        runtime_root = materialized.runtime_root or output_origin
        stage.changed += materialized.changed

        if ctx.plan_type == "mask":
            report.notes.append("masked")
            stage.skipped += 1
            continue

        if ctx.dops_path is None:
            stage.skipped += 1
            continue

        source = ctx.dops_path.read_text()
        apply_result = apply_dsl_fn(
            source,
            source_path=ctx.dops_path,
            port_root=runtime_root,
            target=target,
            dry_run=dry_run,
            strict=strict,
            emit_diff=False,
            oracle_profile=oracle_profile,
        )
        report.total_ops += apply_result.total_ops
        report.applied_ops += apply_result.applied_ops
        report.skipped_ops += apply_result.skipped_ops
        report.warnings += apply_result.warning_count
        report.errors += apply_result.error_count
        report.oracle_checks += apply_result.oracle_checks
        report.oracle_failures += apply_result.oracle_failures
        report.oracle_skipped += apply_result.oracle_skipped
        report.dops_ops_executed += apply_result.total_ops
        stage.changed += apply_result.applied_ops
        stage.skipped += apply_result.skipped_ops
        if not apply_result.ok:
            stage.add_error(
                "E_COMPOSE_APPLY_FAILED", f"{ctx.origin}: semantic apply failed"
            )
            if strict:
                stage.finished_at = datetime.now()
                return stage

    stage.finished_at = datetime.now()
    return stage


def fallback_stage(
    *,
    contexts: list[ComposePortContext],
    reports: dict[str, ComposePortReport],
    target: str,
    freebsd_root: Path,
    output_path: Path,
    lock_root: Path,
    dry_run: bool,
    strict: bool,
    patch_runner: Callable[[Path, Path, bool], tuple[bool, str]],
) -> ComposeStageResult:
    stage = ComposeStageResult(name="apply_compat_ops", started_at=datetime.now())
    for ctx in contexts:
        if is_stale_port_context(ctx):
            stage.skipped += 1
            reports[ctx.origin].notes.append("stale-skipped")
            continue
        if ctx.mode != "compat":
            continue
        report = reports[ctx.origin]
        compat_result = run_compat_merge(
            overlay_dir=ctx.path,
            target=target,
            output_origin=output_path / ctx.origin,
            upstream_origin=freebsd_root / ctx.origin,
            lock_origin=lock_root / ctx.origin,
            compat_type=ctx.plan_type,
            compat_makefile=ctx.compat_makefile,
            patches=ctx.fallback_patches,
            payload_files=ctx.implicit_payload_files,
            dry_run=dry_run,
            patch_runner=patch_runner,
        )
        stage.changed += compat_result.changed
        report.port_type = compat_result.port_type
        report.fallback_patch_count += compat_result.fallback_patch_count
        report.implicit_files_copied += compat_result.payload_file_count
        report.warnings += len(compat_result.warnings)
        report.errors += len(compat_result.errors)
        for executed in compat_result.executed_stages:
            if executed not in report.compat_stages_executed:
                report.compat_stages_executed.append(executed)
        if not compat_result.ok:
            for error in compat_result.errors:
                stage.add_error("E_COMPOSE_COMPAT_FAILED", f"{ctx.origin}: {error}")
            if strict:
                stage.finished_at = datetime.now()
                return stage

    stage.finished_at = datetime.now()
    return stage


def _write_category_makefiles(output_path: Path) -> int:
    excluded = {"Mk", "Tools", "Templates", "Keywords"}
    categories = [
        path
        for path in sorted(output_path.iterdir())
        if path.is_dir() and not path.name.startswith(".") and path.name not in excluded
    ]
    category_names = [category.name for category in categories]
    count = 0

    if category_names:
        root_lines = [f"SUBDIR += {name}" for name in category_names]
        (output_path / "Makefile").write_text("\n".join(root_lines) + "\n")
        count += 1
    else:
        root_makefile = output_path / "Makefile"
        if root_makefile.exists():
            root_makefile.unlink()

    for category in categories:
        ports = [
            port.name
            for port in sorted(category.iterdir())
            if port.is_dir() and not port.name.startswith(".")
        ]
        category_makefile = category / "Makefile"
        if ports:
            lines = [f"SUBDIR += {name}" for name in ports]
            category_makefile.write_text("\n".join(lines) + "\n")
            count += 1
        elif category_makefile.exists():
            category_makefile.unlink()
    return count


def _rewrite_tools_shebangs(output_path: Path) -> int:
    tools_root = output_path / "Tools"
    if not tools_root.exists() or not tools_root.is_dir():
        return 0
    changed = 0
    for tool in sorted(path for path in tools_root.rglob("*") if path.is_file()):
        try:
            text = tool.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        if not text.startswith("#!/usr/bin/perl"):
            continue
        updated = text.replace("#!/usr/bin/perl", "#!/usr/local/bin/perl", 1)
        if updated == text:
            continue
        tool.write_text(updated)
        changed += 1
    return changed


def _filter_moved_entries(source_text: str) -> str:
    lines: list[str] = []
    for line in source_text.splitlines():
        first_token = line.split(" ", 1)[0] if line else ""
        if first_token == "#":
            lines.append(line)
            continue
        parts = line.split("|")
        if len(parts) < 3:
            continue
        date_token = parts[2].strip().split("-", 1)[0]
        try:
            year = int(date_token)
        except ValueError:
            continue
        if year > MOVED_KEEP_AFTER_YEAR:
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def _filter_updating_entries(source_text: str, *, today: date | None = None) -> str:
    if today is None:
        today = date.today()
    cutoff = int(today.strftime("%Y%m%d")) - UPDATING_ROLLING_WINDOW_DAYS
    date_pattern = re.compile(r"^20\d{2}[01]\d[0-3]\d:$")
    lines: list[str] = []
    keep = True
    for line in source_text.splitlines():
        fields = line.split()
        if fields:
            token = fields[0]
            if date_pattern.match(token):
                if int(token[:8]) < cutoff:
                    keep = False
        if keep:
            lines.append(line)
    return "\n".join(lines) + ("\n" if lines else "")


def system_replacements_stage(
    *,
    output_path: Path,
    dry_run: bool,
) -> ComposeStageResult:
    stage = ComposeStageResult(
        name="apply_system_replacements", started_at=datetime.now()
    )
    if not output_path.exists() or not output_path.is_dir():
        stage.finished_at = datetime.now()
        return stage

    origins = sorted(list_port_origins(output_path))
    stage.metadata["origins"] = len(origins)
    files_scanned = 0
    files_changed = 0
    rule_hits: dict[str, int] = {}

    for origin in origins:
        stats = apply_system_replacements_to_port(output_path / origin, dry_run=dry_run)
        files_scanned += stats.files_scanned
        files_changed += stats.files_changed
        for rule_id, count in stats.rule_hits.items():
            rule_hits[rule_id] = rule_hits.get(rule_id, 0) + count

    stage.changed = files_changed
    stage.metadata["files_scanned"] = files_scanned
    stage.metadata["files_changed"] = files_changed
    stage.metadata["rule_hits"] = dict(sorted(rule_hits.items()))
    stage.finished_at = datetime.now()
    return stage


def finalize_stage(
    *,
    contexts: list[ComposePortContext],
    reports: dict[str, ComposePortReport],
    freebsd_root: Path,
    output_path: Path,
    dry_run: bool,
    patch_artifact_finder: Callable[[Path], list[Path]],
) -> ComposeStageResult:
    stage = ComposeStageResult(name="finalize_tree", started_at=datetime.now())
    if not output_path.exists() or not output_path.is_dir():
        stage.finished_at = datetime.now()
        return stage

    upstream = list_port_origins(freebsd_root)
    keep_extra = {
        ctx.origin
        for ctx in contexts
        if reports[ctx.origin].port_type in {"dport", "lock"}
        and reports[ctx.origin].port_type != "mask"
    }

    current = list_port_origins(output_path)
    for origin in sorted(current - upstream):
        if origin in keep_extra:
            continue
        stage.changed += 1
        if not dry_run:
            doomed = output_path / origin
            if doomed.exists():
                shutil.rmtree(doomed)

    if not dry_run:
        tools_rewritten = _rewrite_tools_shebangs(output_path)
        if tools_rewritten:
            stage.metadata["tools_shebang_rewritten"] = tools_rewritten
            stage.changed += tools_rewritten

        stage.metadata["makefiles_regenerated"] = _write_category_makefiles(output_path)
        stage.changed += int(stage.metadata["makefiles_regenerated"])

        upstream_updating = freebsd_root / "UPDATING"
        if upstream_updating.exists():
            filtered = _filter_updating_entries(upstream_updating.read_text())
            (output_path / "UPDATING").write_text(filtered)
            stage.changed += 1

        upstream_moved = freebsd_root / "MOVED"
        if upstream_moved.exists() and upstream_moved.is_file():
            filtered = _filter_moved_entries(upstream_moved.read_text())
            (output_path / "MOVED").write_text(filtered)
            stat = upstream_moved.stat()
            os.utime(output_path / "MOVED", (stat.st_atime, stat.st_mtime))
            stage.changed += 1

        artifacts = patch_artifact_finder(output_path)
        if artifacts:
            stage.metadata["patch_artifacts"] = [
                str(path.relative_to(output_path)) for path in artifacts[:100]
            ]
            stage.metadata["patch_artifact_total"] = len(artifacts)
            stage.add_error(
                "E_COMPOSE_PATCH_ARTIFACT_LEAK",
                f"unexpected .orig/.rej artifacts in output tree: {len(artifacts)}",
            )

    stage.finished_at = datetime.now()
    return stage
