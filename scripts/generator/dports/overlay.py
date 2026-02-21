"""
Overlay management for DPorts v2.

v2 overlays are strictly target-scoped:
- Makefile.DragonFly.@<target>
- diffs/@<target>/
- dragonfly/@<target>/
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterator

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from dports.models import PortOrigin, OverlayManifest, ValidationResult
from dports.quarterly import (
    find_invalid_target_dirs,
    list_target_overrides,
    parse_target_dirname,
    target_dirname,
    validate_target,
)
from dports.utils import DPortsError


class OverlayError(DPortsError):
    """Error related to overlay operations."""


class Overlay:
    """Manages a port's overlay configuration."""

    def __init__(self, path: Path, origin: PortOrigin):
        self.path = path
        self.origin = origin
        self._manifest: OverlayManifest | None = None

    @property
    def manifest_path(self) -> Path:
        return self.path / "overlay.toml"

    @property
    def manifest(self) -> OverlayManifest:
        if self._manifest is None:
            self._manifest = self._load_manifest()
        return self._manifest

    def _load_manifest(self) -> OverlayManifest:
        if not self.manifest_path.exists():
            raise OverlayError(f"No overlay.toml found for {self.origin}")

        with open(self.manifest_path, "rb") as f:
            data = tomllib.load(f)

        return OverlayManifest.from_dict(data, self.origin)

    def exists(self) -> bool:
        return self.manifest_path.exists()

    def get_makefile_for_target(self, target: str) -> Path | None:
        normalized = validate_target(target)
        mk = self.path / f"Makefile.DragonFly.{target_dirname(normalized)}"
        return mk if mk.exists() else None

    def get_diffs_dir_for_target(self, target: str) -> Path | None:
        normalized = validate_target(target)
        path = self.path / "diffs" / target_dirname(normalized)
        return path if path.exists() and path.is_dir() else None

    def get_dragonfly_dir_for_target(self, target: str) -> Path | None:
        normalized = validate_target(target)
        path = self.path / "dragonfly" / target_dirname(normalized)
        return path if path.exists() and path.is_dir() else None

    def get_diffs_for_target(self, target: str) -> list[Path]:
        target_dir = self.get_diffs_dir_for_target(target)
        if target_dir is None:
            return []
        return sorted(target_dir.glob("*.diff")) + sorted(target_dir.glob("*.patch"))

    def get_dragonfly_files_for_target(self, target: str) -> list[Path]:
        target_dir = self.get_dragonfly_dir_for_target(target)
        if target_dir is None:
            return []
        return [f for f in target_dir.rglob("*") if f.is_file()]

    # Compatibility wrappers
    def get_diffs_for_quarterly(self, quarterly: str) -> list[Path]:
        return self.get_diffs_for_target(quarterly)

    def get_dragonfly_files(self) -> list[Path]:
        return []

    def get_available_targets(self) -> list[str]:
        targets: set[str] = set()

        for mk in self.path.glob("Makefile.DragonFly.@*"):
            target = parse_target_dirname(mk.name.removeprefix("Makefile.DragonFly."))
            if target is not None:
                targets.add(target)

        targets.update(list_target_overrides(self.path / "diffs"))
        targets.update(list_target_overrides(self.path / "dragonfly"))

        return sorted(targets)

    def find_root_component_violations(self) -> list[str]:
        """Find root component paths forbidden by branch-scoped design."""
        violations: list[str] = []

        root_mk = self.path / "Makefile.DragonFly"
        if root_mk.exists():
            violations.append("Makefile.DragonFly")

        diffs_dir = self.path / "diffs"
        if diffs_dir.exists() and diffs_dir.is_dir():
            for f in sorted(diffs_dir.iterdir()):
                if f.is_file() and f.suffix in {".diff", ".patch"}:
                    violations.append(str(f.relative_to(self.path)))

        dragonfly_dir = self.path / "dragonfly"
        if dragonfly_dir.exists() and dragonfly_dir.is_dir():
            for f in sorted(dragonfly_dir.iterdir()):
                if f.name.startswith("."):
                    continue
                if not (f.is_dir() and f.name.startswith("@")):
                    violations.append(str(f.relative_to(self.path)))

        return violations

    def find_invalid_target_entries(self) -> list[str]:
        """Find @-prefixed component entries that are not valid targets."""
        invalid: list[str] = []

        for mk in self.path.glob("Makefile.DragonFly.@*"):
            suffix = mk.name.removeprefix("Makefile.DragonFly.")
            if parse_target_dirname(suffix) is None:
                invalid.append(mk.name)

        diffs_dir = self.path / "diffs"
        if diffs_dir.exists() and diffs_dir.is_dir():
            invalid.extend(
                [f"diffs/{name}" for name in find_invalid_target_dirs(diffs_dir)]
            )

        dragonfly_dir = self.path / "dragonfly"
        if dragonfly_dir.exists() and dragonfly_dir.is_dir():
            invalid.extend(
                [
                    f"dragonfly/{name}"
                    for name in find_invalid_target_dirs(dragonfly_dir)
                ]
            )

        return sorted(set(invalid))

    def validate(self, target: str | None = None) -> ValidationResult:
        result = ValidationResult(origin=self.origin, valid=True)

        if not self.manifest_path.exists():
            result.add_error(f"overlay.toml not found at {self.manifest_path}")
            return result

        result.checked_manifest = True

        try:
            manifest = self.manifest
        except Exception as e:
            result.add_error(f"Failed to parse overlay.toml: {e}")
            return result

        if not manifest.description:
            result.add_error("overlay.reason is required")

        for invalid in self.find_invalid_target_entries():
            result.add_error(f"Invalid target entry: {invalid}")

        for violation in self.find_root_component_violations():
            result.add_error(f"Forbidden root component path: {violation}")

        normalized_target: str | None = None
        if target is not None:
            try:
                normalized_target = validate_target(target)
            except Exception as e:
                result.add_error(str(e))
                return result

        if manifest.has_makefile_dragonfly:
            if normalized_target:
                mk = self.get_makefile_for_target(normalized_target)
                if mk is None:
                    result.add_error(
                        f"Component makefile_dragonfly enabled but Makefile.DragonFly.@{normalized_target} not found"
                    )
            else:
                mk_any = list(self.path.glob("Makefile.DragonFly.@*"))
                if not mk_any:
                    result.add_error(
                        "Component makefile_dragonfly enabled but no Makefile.DragonFly.@<target> files found"
                    )

        if manifest.has_diffs:
            diffs_dir = self.path / "diffs"
            if not diffs_dir.exists() or not diffs_dir.is_dir():
                result.add_error(
                    "Component diffs enabled but diffs/ directory not found"
                )
            else:
                result.checked_diffs = True
                if normalized_target:
                    target_dir = self.get_diffs_dir_for_target(normalized_target)
                    if target_dir is None:
                        result.add_error(
                            f"Component diffs enabled but diffs/@{normalized_target}/ not found"
                        )
                    else:
                        diff_files = self.get_diffs_for_target(normalized_target)
                        if not diff_files:
                            result.add_warning(
                                f"diffs/@{normalized_target}/ exists but has no .diff/.patch files"
                            )

        if manifest.has_dragonfly_dir:
            df_dir = self.path / "dragonfly"
            if not df_dir.exists() or not df_dir.is_dir():
                result.add_error(
                    "Component dragonfly_dir enabled but dragonfly/ directory not found"
                )
            else:
                result.checked_files = True
                if normalized_target:
                    target_dir = self.get_dragonfly_dir_for_target(normalized_target)
                    if target_dir is None:
                        result.add_error(
                            f"Component dragonfly_dir enabled but dragonfly/@{normalized_target}/ not found"
                        )
                    else:
                        files = self.get_dragonfly_files_for_target(normalized_target)
                        if not files:
                            result.add_warning(
                                f"dragonfly/@{normalized_target}/ exists but has no files"
                            )

        return result


def discover_overlays(base_path: Path) -> Iterator[Overlay]:
    """Discover all overlays by finding overlay.toml files."""
    for manifest in base_path.rglob("overlay.toml"):
        port_path = manifest.parent
        try:
            rel = port_path.relative_to(base_path)
            if len(rel.parts) >= 2:
                origin = PortOrigin(category=rel.parts[0], name=rel.parts[1])
                yield Overlay(port_path, origin)
        except ValueError:
            continue


def load_overlay(base_path: Path, origin: PortOrigin) -> Overlay:
    """Load an overlay for a specific port."""
    path = base_path / origin.category / origin.name
    overlay = Overlay(path, origin)
    if not overlay.exists():
        raise OverlayError(f"No overlay found for {origin}")
    return overlay
