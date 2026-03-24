"""Compatibility merge helpers aligned with legacy script behavior."""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from dportsv3.common.io import read_toml_file
from dportsv3.plan_types import materialize_plan_type


@dataclass
class CompatResult:
    """Per-origin compatibility execution result."""

    ok: bool = True
    mode: str = "compat"
    port_type: str = "port"
    changed: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    executed_stages: list[str] = field(default_factory=list)
    fallback_patch_count: int = 0
    payload_file_count: int = 0

    def add_error(self, message: str) -> None:
        self.ok = False
        self.errors.append(message)


def infer_compat_port_type(overlay_dir: Path) -> tuple[str, str]:
    """Infer compatibility port type from overlay metadata/files."""
    manifest = overlay_dir / "overlay.toml"
    if manifest.exists() and manifest.is_file():
        payload, error = read_toml_file(manifest)
        if error is not None or payload is None:
            payload = {}
        overlay = payload.get("overlay") if isinstance(payload, dict) else None
        status = payload.get("status") if isinstance(payload, dict) else None
        top_type = payload.get("type") if isinstance(payload, dict) else None
        overlay_type = overlay.get("type") if isinstance(overlay, dict) else None
        chosen = overlay_type or top_type
        if isinstance(chosen, str):
            normalized = chosen.strip().lower()
            if normalized in {"port", "mask", "dport", "lock"}:
                return normalized, "overlay.toml-type"
        ignore = None
        if isinstance(status, dict):
            ignore = status.get("ignore")
        if ignore is not None:
            return "mask", "overlay.toml-ignore"

    status_file = overlay_dir / "STATUS"
    if status_file.exists() and status_file.is_file():
        try:
            lines = status_file.read_text().splitlines()
        except OSError:
            lines = []
        if lines:
            first = lines[0].strip()
            token = first.split()[0].upper() if first else ""
            if token in {"PORT", "MASK", "DPORT", "LOCK"}:
                return token.lower(), "status-mode"

    if (overlay_dir / "newport").exists():
        return "dport", "newport-detected"

    return "port", "default-port"


def _read_remove_entries(overlay_dir: Path) -> list[str]:
    remove_file = overlay_dir / "diffs" / "REMOVE"
    if not remove_file.exists() or not remove_file.is_file():
        return []
    entries: list[str] = []
    try:
        for line in remove_file.read_text().splitlines():
            item = line.strip()
            if not item:
                continue
            entries.append(item)
    except OSError:
        return []
    return entries


def _copy_makefile_dragonfly(dst_port: Path, compat_makefile: Path) -> None:
    target = dst_port / "Makefile.DragonFly"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(compat_makefile, target)


def run_compat_merge(
    *,
    overlay_dir: Path,
    target: str,
    output_origin: Path,
    upstream_origin: Path,
    lock_origin: Path,
    compat_type: str,
    compat_makefile: Path | None,
    patches: list[Path],
    payload_files: list[tuple[Path, Path]],
    dry_run: bool,
    patch_runner,
) -> CompatResult:
    """Execute compatibility merge path for one origin."""
    result = CompatResult(port_type=compat_type)
    materialized = materialize_plan_type(
        plan_type=compat_type,
        output_origin=output_origin,
        upstream_origin=upstream_origin,
        newport_origin=overlay_dir / "newport",
        lock_origin=lock_origin,
        dry_run=dry_run,
        copy_port_base=True,
        missing_dport_error="missing newport source for dport",
        missing_lock_error="missing lock source",
        missing_port_error="missing upstream source for port",
    )

    if not materialized.ok:
        result.add_error(materialized.error or "materialization failed")
        return result
    result.changed += materialized.changed

    if compat_type == "mask":
        result.executed_stages.append("mask")
        return result
    if compat_type == "dport":
        result.executed_stages.append("copy_dport")
        return result
    if compat_type == "lock":
        result.executed_stages.append("copy_lock")
        return result

    runtime_root = materialized.runtime_root or output_origin
    result.executed_stages.append("copy_base")

    if compat_makefile is not None:
        result.executed_stages.append("apply_makefile")
        if not dry_run:
            _copy_makefile_dragonfly(output_origin, compat_makefile)
        result.changed += 1

    if payload_files:
        result.executed_stages.append("implicit_payload")
    for src, rel in payload_files:
        result.payload_file_count += 1
        if not dry_run:
            dst = output_origin / "dragonfly" / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        result.changed += 1

    remove_entries = _read_remove_entries(overlay_dir)
    if remove_entries:
        result.executed_stages.append("remove")
    for rel in remove_entries:
        remove_target = output_origin / rel
        try:
            remove_target.relative_to(output_origin)
        except ValueError:
            result.warnings.append(f"ignored unsafe REMOVE entry: {rel}")
            continue
        exists = remove_target.exists()
        if dry_run:
            if exists:
                result.changed += 1
            continue
        if not exists:
            continue
        if remove_target.is_dir():
            shutil.rmtree(remove_target)
        else:
            remove_target.unlink(missing_ok=True)
        result.changed += 1

    for patch in patches:
        result.executed_stages.append("fallback")
        result.fallback_patch_count += 1
        if not runtime_root.exists() or not runtime_root.is_dir():
            result.add_error(f"patch target missing for {compat_type}")
            continue
        ok, detail = patch_runner(patch, runtime_root, dry_run)
        if ok:
            result.changed += 1
        else:
            result.add_error(f"patch failed ({patch.name}): {detail}")

    return result
