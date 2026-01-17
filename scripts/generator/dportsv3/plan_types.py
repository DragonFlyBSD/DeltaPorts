"""Shared plan-type materialization helpers for compose execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from dportsv3.fsutils import copy_tree


@dataclass
class MaterializeResult:
    """Filesystem materialization outcome for one plan type."""

    ok: bool = True
    changed: int = 0
    runtime_root: Path | None = None
    error: str | None = None


def resolve_runtime_root(
    *,
    plan_type: str,
    dry_run: bool,
    output_origin: Path,
    upstream_origin: Path,
    newport_origin: Path,
    lock_origin: Path,
) -> Path:
    if not dry_run:
        return output_origin
    if plan_type == "port":
        return upstream_origin
    if plan_type == "dport":
        return newport_origin
    if plan_type == "lock":
        return lock_origin
    return output_origin


def materialize_plan_type(
    *,
    plan_type: str,
    output_origin: Path,
    upstream_origin: Path,
    newport_origin: Path,
    lock_origin: Path,
    dry_run: bool,
    copy_port_base: bool,
    missing_dport_error: str,
    missing_lock_error: str,
    missing_port_error: str,
) -> MaterializeResult:
    result = MaterializeResult(
        runtime_root=resolve_runtime_root(
            plan_type=plan_type,
            dry_run=dry_run,
            output_origin=output_origin,
            upstream_origin=upstream_origin,
            newport_origin=newport_origin,
            lock_origin=lock_origin,
        )
    )

    if plan_type == "mask":
        if output_origin.exists() and not dry_run:
            if output_origin.is_dir():
                import shutil

                shutil.rmtree(output_origin)
            else:
                output_origin.unlink(missing_ok=True)
            result.changed += 1
        return result

    if plan_type == "dport":
        if not newport_origin.exists() or not newport_origin.is_dir():
            result.ok = False
            result.error = missing_dport_error
            return result
        if not dry_run:
            copy_tree(newport_origin, output_origin)
        result.changed += 1
        return result

    if plan_type == "lock":
        if not lock_origin.exists() or not lock_origin.is_dir():
            result.ok = False
            result.error = missing_lock_error
            return result
        if not dry_run:
            copy_tree(lock_origin, output_origin)
        result.changed += 1
        return result

    if copy_port_base:
        if not upstream_origin.exists() or not upstream_origin.is_dir():
            result.ok = False
            result.error = missing_port_error
            return result
        if not dry_run:
            copy_tree(upstream_origin, output_origin)
        result.changed += 1

    return result
