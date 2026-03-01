"""Data models for compose pipeline execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class ComposeStageResult:
    """Result for one compose stage."""

    name: str
    success: bool = True
    changed: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def add_error(self, code: str, message: str) -> None:
        self.success = False
        self.errors.append(f"{code}: {message}")

    def add_warning(self, code: str, message: str) -> None:
        self.warnings.append(f"{code}: {message}")

    @property
    def duration(self) -> float | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "success": self.success,
            "changed": self.changed,
            "skipped": self.skipped,
            "warnings": self.warnings,
            "errors": self.errors,
            "metadata": self.metadata,
            "duration": self.duration,
        }


@dataclass
class ComposePortReport:
    """Per-port compose report row."""

    origin: str
    port_type: str = "port"
    total_ops: int = 0
    applied_ops: int = 0
    skipped_ops: int = 0
    warnings: int = 0
    errors: int = 0
    fallback_patch_count: int = 0
    implicit_files_copied: int = 0
    oracle_checks: int = 0
    oracle_failures: int = 0
    oracle_skipped: int = 0
    mode: str = "compat"
    mode_reason: str = "legacy-overlay"
    compat_stages_executed: list[str] = field(default_factory=list)
    dops_ops_executed: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "origin": self.origin,
            "type": self.port_type,
            "total_ops": self.total_ops,
            "applied_ops": self.applied_ops,
            "skipped_ops": self.skipped_ops,
            "warnings": self.warnings,
            "errors": self.errors,
            "fallback_patch_count": self.fallback_patch_count,
            "implicit_files_copied": self.implicit_files_copied,
            "oracle_checks": self.oracle_checks,
            "oracle_failures": self.oracle_failures,
            "oracle_skipped": self.oracle_skipped,
            "mode": self.mode,
            "mode_reason": self.mode_reason,
            "compat_stages_executed": self.compat_stages_executed,
            "dops_ops_executed": self.dops_ops_executed,
            "notes": self.notes,
        }


@dataclass
class ComposeResult:
    """Aggregate compose run result."""

    target: str
    output_path: Path
    ok: bool = True
    stages: list[ComposeStageResult] = field(default_factory=list)
    ports: list[ComposePortReport] = field(default_factory=list)
    oracle_profile: str = "local"
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def report_version(self) -> str:
        return "v1"

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "stage_total": len(self.stages),
            "port_total": len(self.ports),
            "total_ops": sum(port.total_ops for port in self.ports),
            "applied_ops": sum(port.applied_ops for port in self.ports),
            "skipped_ops": sum(port.skipped_ops for port in self.ports),
            "warnings": sum(port.warnings for port in self.ports)
            + sum(len(stage.warnings) for stage in self.stages),
            "errors": sum(port.errors for port in self.ports)
            + sum(len(stage.errors) for stage in self.stages),
            "fallback_patch_count": sum(
                port.fallback_patch_count for port in self.ports
            ),
            "oracle_profile": self.oracle_profile,
            "oracle_checks": sum(port.oracle_checks for port in self.ports),
            "oracle_failures": sum(port.oracle_failures for port in self.ports),
            "oracle_skipped": sum(port.oracle_skipped for port in self.ports),
            "oracle_failed_origins": sorted(
                [port.origin for port in self.ports if port.oracle_failures > 0]
            ),
        }

    def add_stage(self, stage: ComposeStageResult) -> None:
        self.stages.append(stage)
        if not stage.success:
            self.ok = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "target": self.target,
            "output_path": str(self.output_path),
            "oracle_profile": self.oracle_profile,
            "summary": self.summary,
            "stages": [stage.to_dict() for stage in self.stages],
            "ports": [port.to_dict() for port in self.ports],
        }


@dataclass
class ComposePortContext:
    """Resolved compose context for one origin."""

    origin: str
    path: Path
    dops_path: Path | None
    mode: str = "compat"
    mode_reason: str = "legacy-overlay"
    compat_makefile: Path | None = None
    compat_override_notes: list[str] = field(default_factory=list)
    compat_legacy_notes: list[str] = field(default_factory=list)
    plan_type: str = "port"
    stale: bool = False
    stale_reason: str | None = None
    fallback_patches: list[Path] = field(default_factory=list)
    implicit_payload_files: list[tuple[Path, Path]] = field(default_factory=list)
