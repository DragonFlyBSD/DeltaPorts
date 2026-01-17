"""
DPorts v2 - DragonFly BSD Ports overlay management tool.

This package provides tools for maintaining patches/overlays to convert
FreeBSD's Ports Collection into DragonFly BSD's DPorts system.

Key features:
- Multi-quarterly FreeBSD support via @QUARTER directories
- Explicit overlay.toml manifests for customized ports
- Centralized build state management
- Strong validation before merge operations
"""

__version__ = "2.0.0-dev"
__all__ = [
    "Config",
    "Overlay",
    "PortOrigin",
    "MergeResult",
    "BuildState",
]

from dports.config import Config
from dports.models import PortOrigin, MergeResult
from dports.overlay import Overlay
from dports.state import BuildState
