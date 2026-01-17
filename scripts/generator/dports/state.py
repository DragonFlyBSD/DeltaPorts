"""
Build state management for DPorts v2.

Replaces the 32K individual STATUS files with centralized state storage.
Supports multiple backends:
- local: JSON file in the DeltaPorts repository
- git-branch: Separate git branch for state
- external: External file/database
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Iterator
    from dports.config import Config

from dports.models import PortOrigin, PortState, BuildStatus
from dports.utils import DPortsError, get_logger


class StateError(DPortsError):
    """Error in state management."""
    pass


class BuildState:
    """
    Manages build state for all ports.
    
    Provides a unified interface regardless of storage backend.
    """

    def __init__(self, config: Config):
        self.config = config
        self.log = get_logger(__name__)
        self._states: dict[str, PortState] = {}
        self._loaded = False
        self._dirty = False

    def load(self) -> None:
        """Load state from storage."""
        backend = self.config.state.backend
        
        if backend == "local":
            self._load_local()
        elif backend == "git-branch":
            self._load_git_branch()
        elif backend == "external":
            self._load_external()
        else:
            raise StateError(f"Unknown state backend: {backend}")
        
        self._loaded = True

    def save(self) -> None:
        """Save state to storage."""
        if not self._dirty:
            return
        
        backend = self.config.state.backend
        
        if backend == "local":
            self._save_local()
        elif backend == "git-branch":
            self._save_git_branch()
        elif backend == "external":
            self._save_external()
        else:
            raise StateError(f"Unknown state backend: {backend}")
        
        self._dirty = False

    def get(self, origin: PortOrigin | str) -> PortState | None:
        """Get state for a port."""
        if not self._loaded:
            self.load()
        
        key = str(origin) if isinstance(origin, PortOrigin) else origin
        return self._states.get(key)

    def set(self, state: PortState) -> None:
        """Set state for a port."""
        if not self._loaded:
            self.load()
        
        self._states[str(state.origin)] = state
        self._dirty = True

    def update_status(
        self,
        origin: PortOrigin | str,
        status: BuildStatus,
        version: str = "",
        quarterly: str = "",
        notes: str = "",
    ) -> None:
        """Update the status of a port."""
        if isinstance(origin, str):
            origin = PortOrigin.parse(origin)
        
        existing = self.get(origin)
        now = datetime.now()
        
        if existing:
            state = existing
            state.status = status
            state.last_build = now
            if status == BuildStatus.SUCCESS:
                state.last_success = now
            if version:
                state.version = version
            if quarterly:
                state.quarterly = quarterly
            if notes:
                state.notes = notes
        else:
            state = PortState(
                origin=origin,
                status=status,
                last_build=now,
                last_success=now if status == BuildStatus.SUCCESS else None,
                version=version,
                quarterly=quarterly,
                notes=notes,
            )
        
        self.set(state)

    def remove(self, origin: PortOrigin | str) -> bool:
        """Remove state for a port."""
        if not self._loaded:
            self.load()
        
        key = str(origin) if isinstance(origin, PortOrigin) else origin
        if key in self._states:
            del self._states[key]
            self._dirty = True
            return True
        return False

    def iter_all(self) -> Iterator[PortState]:
        """Iterate over all port states."""
        if not self._loaded:
            self.load()
        yield from self._states.values()

    def iter_by_status(self, status: BuildStatus) -> Iterator[PortState]:
        """Iterate over ports with a specific status."""
        for state in self.iter_all():
            if state.status == status:
                yield state

    def clear(self) -> None:
        """Clear all state."""
        self._states.clear()
        self._dirty = True

    def _load_local(self) -> None:
        """Load state from local JSON file."""
        state_path = self._get_local_path()
        
        if not state_path.exists():
            self.log.debug(f"No state file found at {state_path}")
            return
        
        try:
            with open(state_path, "r") as f:
                data = json.load(f)
            
            for entry in data.get("ports", []):
                state = PortState.from_dict(entry)
                self._states[str(state.origin)] = state
            
            self.log.info(f"Loaded {len(self._states)} port states")
        except Exception as e:
            raise StateError(f"Failed to load state: {e}") from e

    def _save_local(self) -> None:
        """Save state to local JSON file."""
        state_path = self._get_local_path()
        state_path.parent.mkdir(parents=True, exist_ok=True)
        
        data = {
            "version": 1,
            "updated": datetime.now().isoformat(),
            "ports": [state.to_dict() for state in self._states.values()],
        }
        
        with open(state_path, "w") as f:
            json.dump(data, f, indent=2)
        
        self.log.info(f"Saved {len(self._states)} port states to {state_path}")

    def _get_local_path(self) -> Path:
        """Get the path to the local state file."""
        path = self.config.state.path
        if not path.is_absolute():
            path = self.config.paths.delta / path
        return path

    def _load_git_branch(self) -> None:
        """Load state from git branch."""
        # TODO: Implement git-branch backend
        raise StateError("git-branch backend not yet implemented")

    def _save_git_branch(self) -> None:
        """Save state to git branch."""
        raise StateError("git-branch backend not yet implemented")

    def _load_external(self) -> None:
        """Load state from external source."""
        # TODO: Implement external backend
        raise StateError("external backend not yet implemented")

    def _save_external(self) -> None:
        """Save state to external source."""
        raise StateError("external backend not yet implemented")


def import_from_status_files(
    config: Config,
    quarterly: str,
) -> BuildState:
    """
    Import state from legacy STATUS files.
    
    Reads the STATUS files from the merged ports tree and creates
    a new BuildState with all the information.
    
    Args:
        config: DPorts configuration
        quarterly: Quarterly to import from
        
    Returns:
        BuildState populated from STATUS files
    """
    log = get_logger(__name__)
    state = BuildState(config)
    
    merged_base = config.paths.merged_output
    
    # Find all STATUS files
    for status_file in merged_base.rglob("STATUS"):
        port_dir = status_file.parent
        try:
            origin = PortOrigin.from_path(port_dir, merged_base)
        except ValueError:
            continue
        
        # Parse STATUS file
        try:
            content = status_file.read_text().strip()
            lines = content.split("\n")
            
            # Parse status and version from STATUS file format
            status_str = lines[0] if lines else "unknown"
            version = lines[1] if len(lines) > 1 else ""
            
            if status_str.lower() in ("success", "ok", "built"):
                status = BuildStatus.SUCCESS
            elif status_str.lower() in ("failed", "fail", "error"):
                status = BuildStatus.FAILED
            else:
                status = BuildStatus.UNKNOWN
            
            state.update_status(
                origin,
                status=status,
                version=version,
                quarterly=quarterly,
            )
        except Exception as e:
            log.warning(f"Failed to parse STATUS for {origin}: {e}")
    
    log.info(f"Imported {len(list(state.iter_all()))} port states from STATUS files")
    return state
