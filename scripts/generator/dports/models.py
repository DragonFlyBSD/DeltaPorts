"""
Data models for DPorts v2.

Defines the core data structures used throughout the system:
- PortOrigin: Represents a port's category/name identifier
- OverlayManifest: Parsed overlay.toml configuration
- MergeResult: Result of a merge operation
- ValidationResult: Result of validation checks
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any


class BuildStatus(Enum):
    """Build status for a port."""
    
    UNKNOWN = "unknown"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    PENDING = "pending"


class CustomizationType(Enum):
    """Types of port customizations."""
    
    MAKEFILE_DRAGONFLY = "makefile_dragonfly"
    DIFFS = "diffs"
    DRAGONFLY_DIR = "dragonfly_dir"
    EXTRA_PATCH = "extra_patch"


@dataclass(frozen=True)
class PortOrigin:
    """
    Represents a port's origin (category/name).
    
    Immutable and hashable for use as dict keys.
    """
    
    category: str
    name: str

    def __str__(self) -> str:
        return f"{self.category}/{self.name}"

    def __repr__(self) -> str:
        return f"PortOrigin({self.category!r}, {self.name!r})"

    @classmethod
    def parse(cls, origin: str) -> PortOrigin:
        """
        Parse a port origin string into a PortOrigin.
        
        Args:
            origin: String in format "category/name"
            
        Raises:
            ValueError: If format is invalid
        """
        parts = origin.strip().split("/")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise ValueError(f"Invalid port origin: {origin!r} (expected 'category/name')")
        return cls(category=parts[0], name=parts[1])

    @classmethod
    def from_path(cls, path: Path, base: Path) -> PortOrigin:
        """
        Create a PortOrigin from a filesystem path.
        
        Args:
            path: Path to the port directory
            base: Base path to calculate relative position
        """
        rel = path.relative_to(base)
        parts = rel.parts
        if len(parts) < 2:
            raise ValueError(f"Path {path} does not contain category/name structure")
        return cls(category=parts[0], name=parts[1])


@dataclass
class OverlayManifest:
    """
    Parsed overlay.toml manifest for a port.
    
    This is the v2 explicit manifest that replaces implicit detection
    of customization types.
    """
    
    # Required metadata
    origin: PortOrigin
    description: str = ""
    
    # Customization flags
    has_makefile_dragonfly: bool = False
    has_diffs: bool = False
    has_dragonfly_dir: bool = False
    has_extra_patches: bool = False
    
    # Quarterly-specific overrides
    quarterly_overrides: list[str] = field(default_factory=list)
    
    # Build hints
    broken: bool = False
    broken_reason: str = ""
    ignore: bool = False
    ignore_reason: str = ""
    
    # Maintainer info
    maintainer: str = ""
    
    # Raw TOML data for extensions
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any], origin: PortOrigin) -> OverlayManifest:
        """Create an OverlayManifest from parsed TOML data."""
        return cls(
            origin=origin,
            description=data.get("description", ""),
            has_makefile_dragonfly=data.get("makefile_dragonfly", False),
            has_diffs=data.get("diffs", False),
            has_dragonfly_dir=data.get("dragonfly_dir", False),
            has_extra_patches=data.get("extra_patches", False),
            quarterly_overrides=data.get("quarterly_overrides", []),
            broken=data.get("broken", False),
            broken_reason=data.get("broken_reason", ""),
            ignore=data.get("ignore", False),
            ignore_reason=data.get("ignore_reason", ""),
            maintainer=data.get("maintainer", ""),
            raw=data,
        )


@dataclass
class MergeResult:
    """Result of a port merge operation."""
    
    origin: PortOrigin
    success: bool
    message: str = ""
    
    # What was applied
    applied_makefile: bool = False
    applied_diffs: list[str] = field(default_factory=list)
    applied_dragonfly_files: list[str] = field(default_factory=list)
    
    # Warnings and errors
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    
    # Timing
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def duration(self) -> float | None:
        """Return merge duration in seconds, or None if not complete."""
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


@dataclass
class ValidationResult:
    """Result of validating a port overlay."""
    
    origin: PortOrigin
    valid: bool
    
    # Validation details
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    
    # What was checked
    checked_manifest: bool = False
    checked_diffs: bool = False
    checked_files: bool = False

    def add_error(self, msg: str) -> None:
        """Add an error and mark as invalid."""
        self.errors.append(msg)
        self.valid = False

    def add_warning(self, msg: str) -> None:
        """Add a warning (doesn't affect validity)."""
        self.warnings.append(msg)


@dataclass
class PortState:
    """Build state for a single port."""
    
    origin: PortOrigin
    status: BuildStatus = BuildStatus.UNKNOWN
    last_build: datetime | None = None
    last_success: datetime | None = None
    version: str = ""
    quarterly: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to a dictionary for JSON serialization."""
        return {
            "origin": str(self.origin),
            "status": self.status.value,
            "last_build": self.last_build.isoformat() if self.last_build else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "version": self.version,
            "quarterly": self.quarterly,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PortState:
        """Create from a dictionary."""
        return cls(
            origin=PortOrigin.parse(data["origin"]),
            status=BuildStatus(data.get("status", "unknown")),
            last_build=datetime.fromisoformat(data["last_build"]) if data.get("last_build") else None,
            last_success=datetime.fromisoformat(data["last_success"]) if data.get("last_success") else None,
            version=data.get("version", ""),
            quarterly=data.get("quarterly", ""),
            notes=data.get("notes", ""),
        )
