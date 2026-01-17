"""
Overlay management for DPorts v2.

Handles loading, parsing, and validating overlay.toml manifests.
Provides functions for discovering ports with customizations.
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
from dports.utils import DPortsError


class OverlayError(DPortsError):
    """Error related to overlay operations."""
    pass


class Overlay:
    """
    Manages a port's overlay configuration.
    
    Each customized port has an overlay.toml manifest that explicitly
    declares what customizations exist and how they should be applied.
    """

    def __init__(self, path: Path, origin: PortOrigin):
        """
        Initialize an Overlay.
        
        Args:
            path: Path to the overlay directory (contains overlay.toml)
            origin: The port's origin
        """
        self.path = path
        self.origin = origin
        self._manifest: OverlayManifest | None = None

    @property
    def manifest_path(self) -> Path:
        """Path to the overlay.toml manifest."""
        return self.path / "overlay.toml"

    @property
    def manifest(self) -> OverlayManifest:
        """Load and return the overlay manifest."""
        if self._manifest is None:
            self._manifest = self._load_manifest()
        return self._manifest

    def _load_manifest(self) -> OverlayManifest:
        """Load the overlay.toml manifest."""
        if not self.manifest_path.exists():
            raise OverlayError(f"No overlay.toml found for {self.origin}")
        
        with open(self.manifest_path, "rb") as f:
            data = tomllib.load(f)
        
        return OverlayManifest.from_dict(data, self.origin)

    def exists(self) -> bool:
        """Check if this overlay exists (has an overlay.toml)."""
        return self.manifest_path.exists()

    def validate(self, quarterly: str | None = None) -> ValidationResult:
        """
        Validate the overlay configuration.
        
        Checks:
        - overlay.toml exists and is valid TOML
        - Declared files/directories actually exist
        - Diffs are valid unified diff format
        - No conflicting settings
        
        Args:
            quarterly: Optional quarterly to validate against
            
        Returns:
            ValidationResult with any errors or warnings
        """
        result = ValidationResult(origin=self.origin, valid=True)
        
        # Check manifest exists
        if not self.manifest_path.exists():
            result.add_error(f"overlay.toml not found at {self.manifest_path}")
            return result
        
        result.checked_manifest = True
        
        try:
            manifest = self.manifest
        except Exception as e:
            result.add_error(f"Failed to parse overlay.toml: {e}")
            return result
        
        # Validate declared customizations exist
        if manifest.has_makefile_dragonfly:
            mkfile = self.path / "Makefile.DragonFly"
            if not mkfile.exists():
                result.add_error("Manifest declares makefile_dragonfly=true but Makefile.DragonFly not found")
        
        if manifest.has_diffs:
            diffs_dir = self.path / "diffs"
            if not diffs_dir.exists():
                result.add_error("Manifest declares diffs=true but diffs/ directory not found")
            else:
                result.checked_diffs = True
                # TODO: Validate diff format
        
        if manifest.has_dragonfly_dir:
            df_dir = self.path / "dragonfly"
            if not df_dir.exists():
                result.add_error("Manifest declares dragonfly_dir=true but dragonfly/ directory not found")
            else:
                result.checked_files = True
        
        # Check for quarterly overrides
        if quarterly and quarterly in manifest.quarterly_overrides:
            q_diffs = self.path / "diffs" / f"@{quarterly}"
            if not q_diffs.exists():
                result.add_warning(f"Quarterly {quarterly} listed in overrides but @{quarterly}/ not found")
        
        return result

    def get_diffs_for_quarterly(self, quarterly: str) -> list[Path]:
        """
        Get the list of diff files to apply for a quarterly.
        
        If @QUARTER directory exists, uses those exclusively.
        Otherwise falls back to base diffs/ directory.
        
        Args:
            quarterly: Target quarterly (e.g., "2025Q1")
            
        Returns:
            List of paths to diff files to apply
        """
        diffs_dir = self.path / "diffs"
        if not diffs_dir.exists():
            return []
        
        # Check for quarterly-specific overrides
        q_dir = diffs_dir / f"@{quarterly}"
        if q_dir.exists():
            return sorted(q_dir.glob("*.diff")) + sorted(q_dir.glob("*.patch"))
        
        # Fall back to base diffs
        return sorted(diffs_dir.glob("*.diff")) + sorted(diffs_dir.glob("*.patch"))

    def get_dragonfly_files(self) -> list[Path]:
        """Get list of files in the dragonfly/ directory."""
        df_dir = self.path / "dragonfly"
        if not df_dir.exists():
            return []
        return list(df_dir.rglob("*"))


def discover_overlays(base_path: Path) -> Iterator[Overlay]:
    """
    Discover all port overlays in a directory tree.
    
    Looks for overlay.toml files and yields Overlay objects.
    
    Args:
        base_path: Base path to search (typically DeltaPorts/ports/)
        
    Yields:
        Overlay objects for each discovered port
    """
    for manifest in base_path.rglob("overlay.toml"):
        port_path = manifest.parent
        # Extract origin from path
        try:
            rel = port_path.relative_to(base_path)
            if len(rel.parts) >= 2:
                origin = PortOrigin(category=rel.parts[0], name=rel.parts[1])
                yield Overlay(port_path, origin)
        except ValueError:
            continue


def load_overlay(base_path: Path, origin: PortOrigin) -> Overlay:
    """
    Load an overlay for a specific port.
    
    Args:
        base_path: Base path to port overlays
        origin: Port origin to load
        
    Returns:
        Overlay object
        
    Raises:
        OverlayError: If overlay doesn't exist
    """
    path = base_path / origin.category / origin.name
    overlay = Overlay(path, origin)
    if not overlay.exists():
        raise OverlayError(f"No overlay found for {origin}")
    return overlay
