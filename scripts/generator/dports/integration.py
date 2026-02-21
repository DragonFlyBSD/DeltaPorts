"""Integration harness for Step 1 building blocks."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from dports.compose import run_compose
from dports.config import Config
from dports.migrate import (
    cleanup_status_only_dirs,
    generate_builds_json_from_status,
    migrate_all_layouts_to_target,
    migrate_special_diffs_to_target,
    prepare_output_tree,
)
from dports.models import ComposeResult, SelectionMode
from dports.quarterly import validate_target


@dataclass
class HarnessTargetResult:
    """Result for one target in the Step 1 harness."""

    target: str
    migrated_tree: Path
    composed_tree: Path
    migration_ports_changed: int = 0
    migration_ports_unchanged: int = 0
    migration_special_moved: int = 0
    migration_state_entries: int = 0
    migration_cleanup_removed: int = 0
    migration_errors: list[str] = field(default_factory=list)
    compose_result: ComposeResult | None = None

    @property
    def success(self) -> bool:
        return not self.migration_errors and bool(
            self.compose_result and self.compose_result.success
        )


@dataclass
class HarnessRunResult:
    """Aggregate Step 1 integration harness result."""

    targets: list[str]
    work_base: Path
    dry_run: bool
    started_at: datetime
    finished_at: datetime | None = None
    results: list[HarnessTargetResult] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return all(item.success for item in self.results)


def _with_paths(config: Config, *, delta: Path, merged_output: Path) -> Config:
    """Return config copy with overridden delta and merged_output paths."""
    paths = replace(config.paths, delta=delta, merged_output=merged_output)
    return replace(config, paths=paths)


def run_step1_harness(
    config: Config,
    targets: list[str],
    work_base: Path,
    dry_run: bool = True,
    selection: SelectionMode = SelectionMode.OVERLAY_CANDIDATES,
) -> HarnessRunResult:
    """
    Run Step 1 integration harness for multiple targets.

    For each target:
    1) Create migrated output tree from current DeltaPorts tree
    2) Run migration phases (layout + special + state + cleanup)
    3) Run full compose on migrated tree
    """
    run = HarnessRunResult(
        targets=[validate_target(t) for t in targets],
        work_base=work_base,
        dry_run=dry_run,
        started_at=datetime.now(),
    )

    work_base = work_base.expanduser().resolve()

    for target in run.targets:
        migrated_tree = work_base / f"migrated-{target}"
        composed_tree = work_base / f"composed-{target}"

        item = HarnessTargetResult(
            target=target,
            migrated_tree=migrated_tree,
            composed_tree=composed_tree,
        )

        ok, message = prepare_output_tree(
            config.paths.delta, migrated_tree, dry_run=dry_run
        )
        if not ok:
            item.migration_errors.append(message)
            run.results.append(item)
            continue

        migrated, unchanged, errors = migrate_all_layouts_to_target(
            config,
            target=target,
            dry_run=dry_run,
            delta_base=migrated_tree,
        )
        item.migration_ports_changed = migrated
        item.migration_ports_unchanged = unchanged
        item.migration_errors.extend(errors)

        special_moved, special_errors = migrate_special_diffs_to_target(
            config,
            target=target,
            dry_run=dry_run,
            delta_base=migrated_tree,
        )
        item.migration_special_moved = special_moved
        item.migration_errors.extend(special_errors)

        state_path = migrated_tree / "state" / "builds.json"
        state_entries = generate_builds_json_from_status(
            delta_base=migrated_tree,
            target=target,
            output_path=state_path,
            dry_run=dry_run,
        )
        item.migration_state_entries = state_entries

        removed = cleanup_status_only_dirs(migrated_tree, dry_run=dry_run)
        item.migration_cleanup_removed = removed

        harness_config = _with_paths(
            config, delta=migrated_tree, merged_output=composed_tree
        )
        item.compose_result = run_compose(
            config=harness_config,
            target=target,
            output_path=composed_tree,
            selection=selection,
            dry_run=dry_run,
            replace_output=True,
            preflight_validate=True,
        )

        run.results.append(item)

    run.finished_at = datetime.now()
    return run
