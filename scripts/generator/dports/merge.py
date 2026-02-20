"""
Merge operations for DPorts v2.

Implements the core port merging logic with support for different port types:
- PORT: Copy FreeBSD port + apply customizations (Makefile.DragonFly, diffs, dragonfly/)
- MASK: Skip entirely (don't create output)
- DPORT: Use newport/ directory as complete port
- LOCK: Copy from built DPorts tree (config.paths.dports_built_tree)
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from dports.config import Config
    from dports.overlay import Overlay

from dports.models import PortOrigin, MergeResult, PortType
from dports.transform import (
    needs_transform,
    transform_file,
    transform_directory,
)
from dports.utils import (
    DPortsError,
    cpdup,
    apply_patch,
    cleanup_patch_artifacts,
    get_logger,
)


class MergeError(DPortsError):
    """Error during merge operation."""
    pass


class PortMerger:
    """
    Handles merging a single port.
    
    The merge process depends on the port type:
    
    PORT (standard):
        1. Copy base FreeBSD port using cpdup
        2. If Makefile.DragonFly exists, append/transform it
        3. Apply any diff patches (quarterly-specific or base)
        4. Copy dragonfly/ overlay files
        5. Apply arch transformations (amd64 -> x86_64, etc.)
    
    MASK:
        - Skip entirely, don't create output directory
    
    DPORT:
        - Copy newport/ directory as the complete port
    
    LOCK:
        - Copy from built DPorts tree (not from FreeBSD ports)
    """

    def __init__(
        self,
        config: Config,
        origin: PortOrigin,
        quarterly: str,
        dry_run: bool = False,
    ):
        self.config = config
        self.origin = origin
        self.quarterly = quarterly
        self.dry_run = dry_run
        self.log = get_logger(__name__)

    def merge(self, overlay: Overlay | None = None) -> MergeResult:
        """
        Perform the merge operation.
        
        Args:
            overlay: Optional pre-loaded overlay (loaded if not provided)
            
        Returns:
            MergeResult with details of the operation
        """
        result = MergeResult(
            origin=self.origin,
            success=False,
            started_at=datetime.now(),
        )
        
        try:
            # Get paths
            overlay_path = self.config.get_overlay_port_path(str(self.origin))

            # Load overlay if not provided
            if overlay is None and overlay_path.exists():
                from dports.overlay import Overlay as OverlayClass
                overlay = OverlayClass(overlay_path, self.origin)

            # Determine port type
            if overlay and overlay.exists():
                manifest = overlay.manifest
                port_type = manifest.port_type
            else:
                port_type = PortType.PORT
                manifest = None

            # Dispatch based on port type
            if port_type == PortType.MASK:
                return self._handle_mask(result, manifest)
            elif port_type == PortType.DPORT:
                return self._handle_dport(result, overlay_path, manifest)
            elif port_type == PortType.LOCK:
                return self._handle_lock(result, manifest)
            else:
                return self._handle_port(result, overlay, overlay_path)

        except Exception as e:
            result.errors.append(str(e))
            result.message = f"Merge failed: {e}"
            self.log.error(f"Merge failed for {self.origin}: {e}")

        finally:
            result.finished_at = datetime.now()

        return result

    def _handle_mask(self, result: MergeResult, manifest) -> MergeResult:
        """
        Handle MASK port type - skip entirely.
        
        MASK ports are intentionally excluded from the output.
        """
        reason = manifest.ignore_reason if manifest else "masked"
        self.log.info(f"Skipping MASK port {self.origin}: {reason}")
        
        result.success = True
        result.message = f"Skipped (MASK): {reason}"
        result.finished_at = datetime.now()
        return result

    def _handle_dport(self, result: MergeResult, overlay_path: Path, manifest) -> MergeResult:
        """
        Handle DPORT type - use newport/ as complete port.
        
        DPORT ports are DragonFly-specific and don't exist in FreeBSD.
        The complete port is in the newport/ directory.
        """
        dst_port = self.config.get_merged_port_path(str(self.origin))
        newport_path = overlay_path / "newport"
        
        if not newport_path.exists():
            result.errors.append(f"DPORT newport/ directory not found: {newport_path}")
            result.message = "DPORT merge failed: newport/ not found"
            result.finished_at = datetime.now()
            return result
        
        self.log.info(f"Copying DPORT {self.origin} from newport/")
        
        if not self.dry_run:
            # Ensure parent exists
            dst_port.parent.mkdir(parents=True, exist_ok=True)
            
            # Remove existing if present
            if dst_port.exists():
                shutil.rmtree(dst_port)
            
            # Copy newport/ contents to destination
            shutil.copytree(newport_path, dst_port)
        
        result.success = True
        result.message = "DPORT merged from newport/"
        result.finished_at = datetime.now()
        return result

    def _handle_lock(self, result: MergeResult, manifest) -> MergeResult:
        """
        Handle LOCK type - copy from built DPorts tree.
        
        LOCK ports are copied from an existing built DPorts tree
        (config.paths.dports_built_tree), NOT from FreeBSD ports.
        This is used for ports that need manual intervention or
        have complex build requirements.
        """
        src_port = self.config.get_built_dports_port_path(str(self.origin))
        dst_port = self.config.get_merged_port_path(str(self.origin))
        
        if not src_port.exists():
            result.errors.append(f"LOCK source not found in DPorts tree: {src_port}")
            result.message = f"LOCK merge failed: source not found at {src_port}"
            result.finished_at = datetime.now()
            return result
        
        self.log.info(f"Copying LOCK port {self.origin} from {src_port}")
        
        if not self.dry_run:
            # Ensure parent exists
            dst_port.parent.mkdir(parents=True, exist_ok=True)
            
            # Remove existing if present
            if dst_port.exists():
                shutil.rmtree(dst_port)
            
            # Use cpdup for efficient copying
            cpdup(src_port, dst_port)
        
        result.success = True
        result.message = f"LOCK merged from {src_port}"
        result.finished_at = datetime.now()
        return result

    def _handle_port(self, result: MergeResult, overlay: Overlay | None, overlay_path: Path) -> MergeResult:
        """
        Handle standard PORT type - merge FreeBSD + customizations.
        
        This is the standard merge process:
        1. Copy base FreeBSD port using cpdup
        2. If Makefile.DragonFly exists, append it
        3. Apply any diff patches
        4. Copy dragonfly/ overlay files
        5. Apply arch transformations
        """
        src_port = self.config.get_freebsd_port_path(str(self.origin), self.quarterly)
        dst_port = self.config.get_merged_port_path(str(self.origin))

        # Validate source exists
        if not src_port.exists():
            result.errors.append(f"FreeBSD port not found: {src_port}")
            result.finished_at = datetime.now()
            return result

        # Step 1: Copy base port
        self.log.info(f"Copying {src_port} -> {dst_port}")
        if not self.dry_run:
            self._copy_base_port(src_port, dst_port)

        # Step 2: Check for customizations
        if not overlay_path.exists():
            # No customizations, just a straight copy
            result.success = True
            result.message = "Copied without customizations"
            result.finished_at = datetime.now()
            return result

        # Load overlay if not already loaded
        if overlay is None:
            from dports.overlay import Overlay as OverlayClass
            overlay = OverlayClass(overlay_path, self.origin)
            if not overlay.exists():
                result.success = True
                result.message = "Copied without customizations (no overlay.toml)"
                result.finished_at = datetime.now()
                return result

        manifest = overlay.manifest

        # Step 3: Apply Makefile.DragonFly
        if manifest.has_makefile_dragonfly:
            mkfile = overlay_path / "Makefile.DragonFly"
            if mkfile.exists():
                self.log.info(f"Applying Makefile.DragonFly")
                if not self.dry_run:
                    self._apply_makefile_dragonfly(mkfile, dst_port)
                result.applied_makefile = True

        # Step 4: Apply diff patches
        if manifest.has_diffs:
            diffs = overlay.get_diffs_for_quarterly(self.quarterly)
            for diff_file in diffs:
                self.log.info(f"Applying patch: {diff_file.name}")
                if not self.dry_run:
                    success = self._apply_diff(diff_file, dst_port)
                    if not success:
                        result.warnings.append(f"Patch failed: {diff_file.name}")
                result.applied_diffs.append(diff_file.name)

        # Step 5: Copy dragonfly/ overlay files
        if manifest.has_dragonfly_dir:
            df_dir = overlay_path / "dragonfly"
            if df_dir.exists():
                self.log.info(f"Copying dragonfly/ overlay files")
                if not self.dry_run:
                    self._copy_dragonfly_files(df_dir, dst_port)
                for f in df_dir.rglob("*"):
                    if f.is_file():
                        result.applied_dragonfly_files.append(str(f.relative_to(df_dir)))

        # Step 6: Apply any transformations (amd64 -> x86_64, remove libomp)
        if not self.dry_run:
            files_to_transform = needs_transform(dst_port)
            if files_to_transform:
                self.log.info("Applying FreeBSD -> DragonFly transformations")
                transform_directory(dst_port, files_to_transform)

        # Cleanup
        if not self.dry_run:
            cleanup_patch_artifacts(dst_port)

        result.success = True
        result.message = "Merge completed successfully"
        result.finished_at = datetime.now()

        return result

    def _copy_base_port(self, src: Path, dst: Path) -> None:
        """Copy the base FreeBSD port using cpdup."""
        # Ensure parent exists
        dst.parent.mkdir(parents=True, exist_ok=True)
        
        # Remove existing if present
        if dst.exists():
            shutil.rmtree(dst)
        
        # Use cpdup for efficient copying
        cpdup(src, dst)

    def _apply_makefile_dragonfly(self, mkfile: Path, dst_port: Path) -> None:
        """Apply Makefile.DragonFly to the port's Makefile."""
        dst_makefile = dst_port / "Makefile"
        
        if not dst_makefile.exists():
            # Just copy it as Makefile
            shutil.copy2(mkfile, dst_makefile)
            return
        
        # Append the DragonFly-specific content
        with open(mkfile, "r") as f:
            dragonfly_content = f.read()
        
        with open(dst_makefile, "a") as f:
            f.write("\n# DragonFly BSD specific modifications\n")
            f.write(dragonfly_content)

    def _apply_diff(self, diff_file: Path, dst_port: Path) -> bool:
        """Apply a diff patch to the port."""
        return apply_patch(diff_file, dst_port)

    def _copy_dragonfly_files(self, df_dir: Path, dst_port: Path) -> None:
        """Copy dragonfly/ overlay files to the port."""
        for src_file in df_dir.rglob("*"):
            if src_file.is_file():
                rel_path = src_file.relative_to(df_dir)
                dst_file = dst_port / rel_path
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src_file, dst_file)


def merge_port(
    config: Config,
    origin: PortOrigin | str,
    quarterly: str,
    dry_run: bool = False,
) -> MergeResult:
    """
    Convenience function to merge a single port.
    
    Args:
        config: DPorts configuration
        origin: Port origin (string or PortOrigin)
        quarterly: Target quarterly
        dry_run: If True, don't make changes
        
    Returns:
        MergeResult
    """
    if isinstance(origin, str):
        origin = PortOrigin.parse(origin)
    
    merger = PortMerger(config, origin, quarterly, dry_run=dry_run)
    return merger.merge()


def merge_all_ports(
    config: Config,
    quarterly: str,
    dry_run: bool = False,
) -> list[MergeResult]:
    """
    Merge all ports with customizations.
    
    Args:
        config: DPorts configuration
        quarterly: Target quarterly
        dry_run: If True, don't make changes
        
    Returns:
        List of MergeResults for all merged ports
    """
    from dports.overlay import discover_overlays
    
    results = []
    overlay_base = config.paths.delta / "ports"
    
    for overlay in discover_overlays(overlay_base):
        merger = PortMerger(config, overlay.origin, quarterly, dry_run=dry_run)
        result = merger.merge(overlay)
        results.append(result)
    
    return results
