"""Compose pipeline for building final DPorts trees."""

from __future__ import annotations

import shutil
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from dports.config import Config
from dports.merge import merge_port
from dports.models import ComposeResult, PortOrigin, SelectionMode, StageResult
from dports.quarterly import validate_target
from dports.selection import resolve_selection
from dports.special import merge_infrastructure
from dports.utils import (
    DPortsError,
    ensure_git_branch,
    get_logger,
    list_ports,
)
from dports.validate import validate_port


class ComposeError(DPortsError):
    """Error raised by compose pipeline operations."""


def _with_output(config: Config, output_path: Path) -> Config:
    """Return a config copy with merged_output overridden."""
    new_paths = replace(config.paths, merged_output=output_path)
    return replace(config, paths=new_paths)


def seed_base_tree(
    config: Config,
    target: str,
    output_path: Path,
    dry_run: bool = False,
    replace_output: bool = False,
) -> StageResult:
    """Seed output tree from target FreeBSD ports source."""
    stage = StageResult(name="seed_base_tree", started_at=datetime.now())
    source = config.paths.freebsd_ports

    try:
        validate_target(target)
        ensure_git_branch(source, target)

        if not source.exists() or not source.is_dir():
            stage.add_error(f"FreeBSD ports tree not found: {source}")
            return stage

        if output_path.exists():
            has_entries = any(output_path.iterdir())
            if has_entries and not replace_output:
                stage.add_error(
                    f"Output path is not empty: {output_path} (set replace_output=True to overwrite)"
                )
                return stage
            if has_entries and replace_output:
                stage.changed += 1
                if not dry_run:
                    shutil.rmtree(output_path)

        stage.metadata["source"] = str(source)
        stage.metadata["output"] = str(output_path)
        stage.metadata["ports"] = len(list_ports(source))

        if not dry_run:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            if output_path.exists() and not any(output_path.iterdir()):
                output_path.rmdir()
            shutil.copytree(
                source,
                output_path,
                symlinks=True,
                ignore=shutil.ignore_patterns(".git"),
            )

        stage.changed += 1

    except Exception as e:
        stage.add_error(str(e))
    finally:
        stage.finished_at = datetime.now()

    return stage


def apply_infrastructure(
    config: Config,
    target: str,
    output_path: Path,
    dry_run: bool = False,
) -> StageResult:
    """Apply infrastructure merge stage (Mk/Templates/treetop/Tools/Keywords)."""
    stage = StageResult(name="apply_infrastructure", started_at=datetime.now())

    try:
        work_config = _with_output(config, output_path)
        results = merge_infrastructure(work_config, target, dry_run=dry_run)
        stage.metadata["components"] = results

        failed = [name for name, ok in results.items() if not ok]
        if failed:
            for name in failed:
                stage.add_error(f"Infrastructure component failed: {name}")

        stage.changed = sum(1 for ok in results.values() if ok)
        stage.skipped = sum(1 for ok in results.values() if not ok)

    except Exception as e:
        stage.add_error(str(e))
    finally:
        stage.finished_at = datetime.now()

    return stage


def validate_overlay_selection(
    config: Config,
    target: str,
    selection: SelectionMode,
    origin: PortOrigin | None = None,
) -> StageResult:
    """Validate overlays selected for compose execution."""
    stage = StageResult(name="validate_overlays", started_at=datetime.now())

    try:
        resolved = resolve_selection(config, selection, origin)
        invalid: list[str] = []
        checked = 0

        for selected_origin in resolved.selected:
            origin_str = str(selected_origin)
            if (
                selection == SelectionMode.FULL_TREE
                and origin_str not in resolved.candidates
            ):
                stage.skipped += 1
                continue

            checked += 1
            result = validate_port(config, selected_origin, target)
            if result.valid:
                stage.changed += 1
            else:
                invalid.append(origin_str)
                for err in result.errors:
                    stage.add_error(f"{origin_str}: {err}")

            for warning in result.warnings:
                stage.add_warning(f"{origin_str}: {warning}")

        stage.metadata["checked"] = checked
        stage.metadata["invalid"] = len(invalid)
        if invalid:
            stage.metadata["invalid_origins"] = invalid[:50]

    except Exception as e:
        stage.add_error(str(e))
    finally:
        stage.finished_at = datetime.now()

    return stage


def apply_overlay_ports(
    config: Config,
    target: str,
    output_path: Path,
    selection: SelectionMode = SelectionMode.OVERLAY_CANDIDATES,
    origin: PortOrigin | None = None,
    dry_run: bool = False,
) -> StageResult:
    """Apply overlay ports for the selected scope."""
    stage = StageResult(name="apply_overlay_ports", started_at=datetime.now())

    try:
        resolved = resolve_selection(config, selection, origin)
        work_config = _with_output(config, output_path)
        failed: list[str] = []

        for selected_origin in resolved.selected:
            origin_str = str(selected_origin)
            if (
                selection == SelectionMode.FULL_TREE
                and origin_str not in resolved.candidates
            ):
                stage.skipped += 1
                continue

            result = merge_port(work_config, selected_origin, target, dry_run=dry_run)
            if result.success:
                stage.changed += 1
                for warning in result.warnings:
                    stage.add_warning(f"{origin_str}: {warning}")
            else:
                failed.append(origin_str)
                for err in result.errors:
                    stage.add_error(f"{origin_str}: {err}")
                if not result.errors and result.message:
                    stage.add_error(f"{origin_str}: {result.message}")

        stage.metadata["selected"] = len(resolved.selected)
        stage.metadata["failed"] = len(failed)
        if failed:
            stage.metadata["failed_origins"] = failed[:50]

    except Exception as e:
        stage.add_error(str(e))
    finally:
        stage.finished_at = datetime.now()

    return stage


def _prune_removed_ports(
    config: Config, output_path: Path, dry_run: bool
) -> tuple[int, list[str]]:
    """Prune ports present in output but not in current FreeBSD target tree."""
    errors: list[str] = []

    try:
        fbsd_ports = set(list_ports(config.paths.freebsd_ports))
        merged_ports = set(list_ports(output_path))
        to_remove = sorted(merged_ports - fbsd_ports)

        if not dry_run:
            for origin in to_remove:
                try:
                    shutil.rmtree(output_path / origin)
                except OSError as e:
                    errors.append(f"Failed to prune {origin}: {e}")

        return len(to_remove), errors
    except Exception as e:
        return 0, [str(e)]


def _regenerate_makefiles(output_path: Path, dry_run: bool) -> tuple[int, list[str]]:
    """Regenerate category Makefiles in output tree."""
    errors: list[str] = []
    category_count = 0

    if not output_path.exists():
        return 0, [f"Output path not found: {output_path}"]

    categories = sorted(
        [
            d.name
            for d in output_path.iterdir()
            if d.is_dir()
            and not d.name.startswith(".")
            and d.name
            not in {"distfiles", "packages", "Mk", "Templates", "Tools", "Keywords"}
        ]
    )

    for category in categories:
        cat_dir = output_path / category
        ports = sorted(
            [
                p.name
                for p in cat_dir.iterdir()
                if p.is_dir()
                and not p.name.startswith(".")
                and (p / "Makefile").exists()
            ]
        )

        content_lines = [
            f"# Category: {category}",
            "# Autogenerated by dports compose",
            "",
            f"COMMENT = Ports in the {category} category",
            "",
            "SUBDIR += " + " \\\n+\t".join(ports) if ports else "# No ports",
            "",
            ".include <bsd.port.subdir.mk>",
            "",
        ]

        if not dry_run:
            try:
                (cat_dir / "Makefile").write_text("\n".join(content_lines))
            except Exception as e:
                errors.append(f"Failed to write {cat_dir / 'Makefile'}: {e}")
                continue

        category_count += 1

    return category_count, errors


def _update_updating(
    config: Config, target: str, output_path: Path, dry_run: bool
) -> tuple[bool, str | None]:
    """Update merged UPDATING file in output tree."""
    fbsd_updating = config.paths.freebsd_ports / "UPDATING"
    merged_updating = output_path / "UPDATING"
    delta_updating = config.paths.delta / "UPDATING.DragonFly"

    if not fbsd_updating.exists():
        return False, f"FreeBSD UPDATING not found: {fbsd_updating}"

    try:
        content = fbsd_updating.read_text()
        if delta_updating.exists():
            dfly_content = delta_updating.read_text()
            header = (
                "# DragonFly BSD specific UPDATING entries\n"
                f"# Merged for {target} on {datetime.now().strftime('%Y%m%d')}\n\n"
                f"{dfly_content}\n\n"
                "# End of DragonFly-specific entries\n"
                "# ============================================\n\n"
            )
            content = header + content

        if not dry_run:
            merged_updating.parent.mkdir(parents=True, exist_ok=True)
            merged_updating.write_text(content)

        return True, None
    except Exception as e:
        return False, str(e)


def finalize_tree(
    config: Config,
    target: str,
    output_path: Path,
    dry_run: bool = False,
) -> StageResult:
    """Finalize output tree by pruning, regenerating makefiles, and updating metadata."""
    stage = StageResult(name="finalize_tree", started_at=datetime.now())

    pruned, prune_errors = _prune_removed_ports(config, output_path, dry_run)
    stage.metadata["pruned_ports"] = pruned
    stage.changed += pruned
    for error in prune_errors:
        stage.add_error(error)

    regenerated, mk_errors = _regenerate_makefiles(output_path, dry_run)
    stage.metadata["makefiles_regenerated"] = regenerated
    stage.changed += regenerated
    for error in mk_errors:
        stage.add_error(error)

    updating_ok, updating_err = _update_updating(config, target, output_path, dry_run)
    stage.metadata["updating_merged"] = updating_ok
    if updating_ok:
        stage.changed += 1
    elif updating_err:
        stage.add_error(updating_err)

    stage.finished_at = datetime.now()
    return stage


def run_compose(
    config: Config,
    target: str,
    output_path: Path | None = None,
    selection: SelectionMode = SelectionMode.OVERLAY_CANDIDATES,
    origin: PortOrigin | None = None,
    dry_run: bool = False,
    replace_output: bool = False,
    preflight_validate: bool = True,
) -> ComposeResult:
    """Run full compose pipeline to produce final DPorts tree."""
    log = get_logger(__name__)
    normalized_target = validate_target(target)
    output = output_path or config.paths.merged_output

    result = ComposeResult(
        target=normalized_target,
        output_path=output,
        started_at=datetime.now(),
    )

    try:
        ensure_git_branch(config.paths.freebsd_ports, normalized_target)

        stages = [
            seed_base_tree(
                config,
                normalized_target,
                output,
                dry_run=dry_run,
                replace_output=replace_output,
            ),
            apply_infrastructure(config, normalized_target, output, dry_run=dry_run),
        ]

        if preflight_validate:
            stages.append(
                validate_overlay_selection(
                    config,
                    normalized_target,
                    selection=selection,
                    origin=origin,
                )
            )

        stages.extend(
            [
                apply_overlay_ports(
                    config,
                    normalized_target,
                    output,
                    selection=selection,
                    origin=origin,
                    dry_run=dry_run,
                ),
                finalize_tree(config, normalized_target, output, dry_run=dry_run),
            ]
        )

        for stage in stages:
            result.add_stage(stage)
            if not stage.success:
                log.error(f"Compose stage failed: {stage.name}")
                break

    except Exception as e:
        result.success = False
        result.errors.append(str(e))
    finally:
        result.finished_at = datetime.now()

    return result


def format_compose_result(result: ComposeResult) -> list[str]:
    """Render a consistent, human-readable compose result summary."""
    lines = [
        (
            f"Compose {'succeeded' if result.success else 'failed'} "
            f"for {result.target} -> {result.output_path}"
        )
    ]

    for stage in result.stages:
        status = "ok" if stage.success else "fail"
        duration = f"{stage.duration:.2f}s" if stage.duration is not None else "n/a"
        lines.append(
            (
                f"[{status}] {stage.name}: changed={stage.changed} skipped={stage.skipped} "
                f"warnings={len(stage.warnings)} errors={len(stage.errors)} duration={duration}"
            )
        )

    if result.errors:
        lines.extend([f"error: {err}" for err in result.errors])

    return lines
