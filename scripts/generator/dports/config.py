"""
Configuration management for DPorts v2.

Handles loading and validating dports.toml configuration files.
Provides default paths and settings when no config is specified.

Configuration file format (dports.toml):
    [paths]
    freebsd_ports = "/usr/fports"       # FreeBSD ports tree
    dports_overlay = "/usr/dports"      # DPorts overlay (ports/ directory)
    merged_output = "/usr/dports-work"  # Merged output directory
    logs = "/var/log/dports"            # Log directory
    
    [state]
    backend = "local"                   # local, git-branch, or external
    path = "state/builds.json"          # Path to state file
    
    [quarterly]
    default = "2025Q1"                  # Default quarterly (optional)
    
    [merge]
    cpdup_path = "/bin/cpdup"           # Path to cpdup binary
    patch_path = "/usr/bin/patch"       # Path to patch binary
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Any

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore


@dataclass
class PathsConfig:
    """Path configuration for dports directories."""
    
    freebsd_ports: Path = field(default_factory=lambda: Path("/usr/fports"))
    dports_overlay: Path = field(default_factory=lambda: Path("/usr/dports"))
    merged_output: Path = field(default_factory=lambda: Path("/usr/dports-work"))
    logs: Path = field(default_factory=lambda: Path("/var/log/dports"))
    delta: Path = field(default_factory=lambda: Path("/usr/DeltaPorts"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PathsConfig:
        """Create PathsConfig from a dictionary."""
        return cls(
            freebsd_ports=Path(data.get("freebsd_ports", "/usr/fports")),
            dports_overlay=Path(data.get("dports_overlay", "/usr/dports")),
            merged_output=Path(data.get("merged_output", "/usr/dports-work")),
            logs=Path(data.get("logs", "/var/log/dports")),
            delta=Path(data.get("delta", "/usr/DeltaPorts")),
        )


@dataclass
class StateConfig:
    """Build state storage configuration."""
    
    backend: str = "local"  # local, git-branch, external
    path: Path = field(default_factory=lambda: Path("state/builds.json"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateConfig:
        """Create StateConfig from a dictionary."""
        return cls(
            backend=data.get("backend", "local"),
            path=Path(data.get("path", "state/builds.json")),
        )


@dataclass
class QuarterlyConfig:
    """Quarterly branch configuration."""
    
    default: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QuarterlyConfig:
        """Create QuarterlyConfig from a dictionary."""
        return cls(default=data.get("default"))


@dataclass
class MergeConfig:
    """Merge operation configuration."""
    
    cpdup_path: Path = field(default_factory=lambda: Path("/bin/cpdup"))
    patch_path: Path = field(default_factory=lambda: Path("/usr/bin/patch"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MergeConfig:
        """Create MergeConfig from a dictionary."""
        return cls(
            cpdup_path=Path(data.get("cpdup_path", "/bin/cpdup")),
            patch_path=Path(data.get("patch_path", "/usr/bin/patch")),
        )


@dataclass
class Config:
    """Main configuration container for DPorts v2."""
    
    paths: PathsConfig = field(default_factory=PathsConfig)
    state: StateConfig = field(default_factory=StateConfig)
    quarterly: QuarterlyConfig = field(default_factory=QuarterlyConfig)
    merge: MergeConfig = field(default_factory=MergeConfig)
    config_path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> Config:
        """
        Load configuration from a TOML file.
        
        Search order:
        1. Explicit path if provided
        2. ./dports.toml
        3. ~/.config/dports/dports.toml
        4. /etc/dports/dports.toml
        5. Default values
        """
        search_paths = [
            path,
            Path("dports.toml"),
            Path.home() / ".config" / "dports" / "dports.toml",
            Path("/etc/dports/dports.toml"),
        ]

        for config_path in search_paths:
            if config_path and config_path.exists():
                return cls._load_from_file(config_path)

        # Return defaults if no config found
        return cls()

    @classmethod
    def _load_from_file(cls, path: Path) -> Config:
        """Load configuration from a specific file."""
        with open(path, "rb") as f:
            data = tomllib.load(f)

        return cls(
            paths=PathsConfig.from_dict(data.get("paths", {})),
            state=StateConfig.from_dict(data.get("state", {})),
            quarterly=QuarterlyConfig.from_dict(data.get("quarterly", {})),
            merge=MergeConfig.from_dict(data.get("merge", {})),
            config_path=path,
        )

    def get_freebsd_port_path(self, origin: str, quarterly: str) -> Path:
        """Get the path to a FreeBSD port for a specific quarterly."""
        # For multi-quarterly support, we might have different trees
        # For now, assume single tree at freebsd_ports
        return self.paths.freebsd_ports / origin

    def get_overlay_port_path(self, origin: str) -> Path:
        """Get the path to a port's overlay in DeltaPorts."""
        return self.paths.delta / "ports" / origin

    def get_merged_port_path(self, origin: str) -> Path:
        """Get the path to the merged output for a port."""
        return self.paths.merged_output / origin

    def get_special_path(self, name: str) -> Path:
        """Get the path to a special directory (Mk, Templates, treetop)."""
        return self.paths.delta / "special" / name
