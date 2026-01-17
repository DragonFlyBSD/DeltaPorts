"""Patch execution and artifact helpers for compose pipeline."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dportsv3.policy import PATCH_TIMEOUT_SECONDS, TREETOP_IDENTITY_RULES


def apply_patch(patch_path: Path, target_dir: Path, dry_run: bool) -> tuple[bool, str]:
    """Apply one unified patch using non-interactive patch flags."""
    cmd = [
        "patch",
        "--batch",
        "--forward",
        "-V",
        "none",
        "-r",
        "-",
        "-p0",
        "-i",
        str(patch_path.resolve()),
    ]
    if dry_run:
        cmd.insert(1, "--dry-run")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(target_dir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=PATCH_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"patch timed out after {PATCH_TIMEOUT_SECONDS}s"
    if proc.returncode == 0:
        return True, ""
    detail = proc.stderr.strip() or proc.stdout.strip() or "patch failed"
    return False, detail


def find_patch_artifacts(root: Path) -> list[Path]:
    """Find patch artifact files leaked into output tree."""
    artifacts: list[Path] = []
    for pattern in ("*.orig", "*.rej"):
        artifacts.extend(sorted(root.rglob(pattern)))
    return artifacts


def inject_identity_entries(
    source_text: str,
    *,
    marker: str,
    injected_lines: tuple[str, ...],
) -> str:
    """Inject static identity entries before marker line once."""
    lines = source_text.splitlines()
    output: list[str] = []
    inserted = False
    for line in lines:
        if not inserted and marker in line:
            output.extend(injected_lines)
            inserted = True
        output.append(line)
    return "\n".join(output) + ("\n" if output else "")


def copy_treetop_identity_files(
    output_path: Path,
    freebsd_root: Path,
    *,
    dry_run: bool,
) -> int:
    """Copy/inject treetop identity files used by special patches."""
    changed = 0
    for filename, (marker, injected_lines) in TREETOP_IDENTITY_RULES.items():
        src = freebsd_root / filename
        dst = output_path / filename
        if dst.exists() or not src.exists() or not src.is_file():
            continue
        content = src.read_text()
        content = inject_identity_entries(
            content,
            marker=marker,
            injected_lines=injected_lines,
        )
        if not dry_run:
            dst.write_text(content)
            stat = src.stat()
            os.utime(dst, (stat.st_atime, stat.st_mtime))
        changed += 1
    return changed
