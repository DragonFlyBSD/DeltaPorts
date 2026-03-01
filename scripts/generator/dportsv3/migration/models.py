"""Typed record adapters for migration workflows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def normalize_target_fields(
    data: Mapping[str, Any],
) -> tuple[str, tuple[str, ...], str]:
    """Normalize target, available targets, and target mode fields."""
    target = str(data.get("target", "")).strip()

    available_targets_raw = data.get("available_targets")
    available_targets: list[str] = []
    if isinstance(available_targets_raw, list):
        available_targets = [str(item).strip() for item in available_targets_raw]
        available_targets = [item for item in available_targets if item]

    targets_raw = data.get("targets")
    if isinstance(targets_raw, list):
        targets = [str(item).strip() for item in targets_raw]
        targets = [item for item in targets if item]
        if not target and targets:
            target = targets[0]
        if not available_targets:
            available_targets = targets

    if target and target not in available_targets:
        available_targets.append(target)
    if not target and available_targets:
        target = available_targets[0]

    mode = str(data.get("target_mode", "")).strip()
    if not mode:
        mode = "baseline" if "@any" in available_targets else "explicit"
    if mode not in {"baseline", "explicit"}:
        raise ValueError(f"invalid target_mode: {mode}")

    return target, tuple(sorted(set(available_targets))), mode


def primary_target(data: Mapping[str, Any]) -> str:
    """Resolve one stable target selector from mixed record fields."""
    target, _, _ = normalize_target_fields(data)
    return target


def record_category(data: Mapping[str, Any]) -> str:
    """Resolve category field, falling back to origin prefix."""
    category = str(data.get("category", "")).strip()
    if category:
        return category
    origin = str(data.get("origin", ""))
    if "/" in origin:
        return origin.split("/", 1)[0]
    return ""


@dataclass(frozen=True)
class MigrationWaveRecord:
    """Normalized candidate record used for wave selection/reporting."""

    origin: str
    bucket: str
    target: str
    target_mode: str
    available_targets: tuple[str, ...]
    category: str
    churn: int

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "MigrationWaveRecord":
        origin = str(data.get("origin", "")).strip()
        bucket = str(data.get("bucket", "")).strip()
        target, available_targets, target_mode = normalize_target_fields(data)
        category = record_category(data)
        churn_value = data.get("churn", 0)
        try:
            churn = int(churn_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid churn for origin '{origin}': {churn_value}"
            ) from exc

        if not origin:
            raise ValueError("missing required field: origin")
        if not bucket:
            raise ValueError(f"missing required field: bucket ({origin})")
        if not target:
            raise ValueError(f"missing required field: target ({origin})")
        if not category:
            raise ValueError(f"missing required field: category ({origin})")

        return MigrationWaveRecord(
            origin=origin,
            bucket=bucket,
            target=target,
            target_mode=target_mode,
            available_targets=available_targets,
            category=category,
            churn=churn,
        )
