"""Step 47 Phase 1 — absorb a port's ``diffs/REMOVE`` into ``overlay.dops``.

A legacy ``diffs/REMOVE`` lists port-tree files the compat path strips
from the composed port. This translates each safe entry into a
``file remove <path> on-missing noop`` op and deletes ``diffs/REMOVE``.

Important: writing an ``overlay.dops`` flips the port to dops-mode
compose, which suppresses the *entire* compat ``diffs/`` path. So a
port can only be flipped when ``REMOVE`` is its sole delta — otherwise
the other ``diffs/`` artifacts (Makefile.diff, …) would silently stop
applying. The compose-parity gate (:mod:`dportsv3.migration.parity`)
enforces this: :func:`absorb_remove_gated` only mutates the real tree
when the absorbed candidate composes byte-identically.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dportsv3.migration.parity import check_parity

_REASON = "absorb diffs/REMOVE into dops (Step 47 Phase 1)"


@dataclass
class AbsorbResult:
    origin: str
    ok: bool
    overlay_created: bool = False
    entries_absorbed: list[str] = field(default_factory=list)
    entries_skipped_unsafe: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class GateOutcome:
    origin: str
    flipped: bool
    absorb: AbsorbResult | None = None
    differences: dict[str, list[str]] = field(default_factory=dict)
    error: str | None = None


def _read_remove_entries(port_dir: Path) -> list[str]:
    remove_file = port_dir / "diffs" / "REMOVE"
    if not remove_file.is_file():
        return []
    out: list[str] = []
    for line in remove_file.read_text().splitlines():
        item = line.strip()
        if item:
            out.append(item)
    return out


def _is_safe_relative(entry: str) -> bool:
    """Match the compat path's guard: entries that escape the port root
    (absolute, or normalizing to a ``..`` prefix) are *ignored* by
    compat, so we must not emit ops for them (``file.remove`` would
    instead fail on them and diverge)."""
    if os.path.isabs(entry):
        return False
    normalized = os.path.normpath(entry)
    return not (normalized == ".." or normalized.startswith(".." + os.sep))


def _resolve_type(port_dir: Path) -> str:
    """Bootstrap header ``type`` from STATUS, mirroring the compat plan-
    type resolution (PORT/MASK/DPORT/LOCK → lowercase)."""
    status = port_dir / "STATUS"
    if status.is_file():
        lines = status.read_text().splitlines()
        if lines:
            token = lines[0].strip().split()[0].upper() if lines[0].strip() else ""
            if token in {"PORT", "MASK", "DPORT", "LOCK"}:
                return token.lower()
    return "port"


def _render_bootstrap_overlay(origin: str, port_dir: Path, ops: list[str]) -> str:
    header = [
        "target @any",
        f"port {origin}",
        f"type {_resolve_type(port_dir)}",
        f'reason "{_REASON}"',
    ]
    return "\n".join([*header, *ops]) + "\n"


def absorb_remove(port_dir: Path, *, origin: str) -> AbsorbResult:
    """Translate ``diffs/REMOVE`` → ``file remove`` ops in ``overlay.dops``
    and delete ``diffs/REMOVE`` (and ``diffs/`` if it becomes empty).
    Mutates ``port_dir`` in place."""
    entries = _read_remove_entries(port_dir)
    if not entries:
        return AbsorbResult(origin=origin, ok=True, error="no diffs/REMOVE")

    safe: list[str] = []
    unsafe: list[str] = []
    for entry in entries:
        (safe if _is_safe_relative(entry) else unsafe).append(entry)

    ops = [f"file remove {entry} on-missing noop" for entry in safe]

    overlay = port_dir / "overlay.dops"
    overlay_created = not overlay.exists()
    if overlay_created:
        overlay.write_text(_render_bootstrap_overlay(origin, port_dir, ops))
    else:
        existing = overlay.read_text()
        if existing and not existing.endswith("\n"):
            existing += "\n"
        overlay.write_text(existing + "\n".join(ops) + "\n")

    remove_file = port_dir / "diffs" / "REMOVE"
    remove_file.unlink(missing_ok=True)
    diffs_dir = port_dir / "diffs"
    if diffs_dir.is_dir() and not any(diffs_dir.iterdir()):
        diffs_dir.rmdir()

    return AbsorbResult(
        origin=origin,
        ok=True,
        overlay_created=overlay_created,
        entries_absorbed=safe,
        entries_skipped_unsafe=unsafe,
    )


def absorb_remove_gated(
    origin: str,
    *,
    repo_root: Path,
    freebsd_root: Path,
    targets: list[str] | None = None,
    lock_root: Path | None = None,
) -> GateOutcome:
    """Absorb ``REMOVE`` for ``origin`` only if the result composes
    byte-identically on every target. Builds a throwaway candidate
    (the one port, absorbed), gates it against ``repo_root``, and
    applies the same absorption to ``repo_root`` only when all targets
    pass."""
    targets = targets or ["@main"]
    category, _, name = origin.partition("/")
    src_port = repo_root / "ports" / origin
    if not src_port.is_dir():
        return GateOutcome(origin=origin, flipped=False, error="port dir not found")

    with tempfile.TemporaryDirectory(prefix="dp-absorb-") as tmp:
        cand_root = Path(tmp)
        cand_port = cand_root / "ports" / category / name
        cand_port.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_port, cand_port, symlinks=True)

        absorb = absorb_remove(cand_port, origin=origin)
        if not absorb.ok:
            return GateOutcome(origin=origin, flipped=False, absorb=absorb, error=absorb.error)

        differences: dict[str, list[str]] = {}
        for target in targets:
            result = check_parity(
                origin,
                target,
                baseline_root=repo_root,
                candidate_root=cand_root,
                freebsd_root=freebsd_root,
                lock_root=lock_root,
            )
            if not result.equal:
                differences[target] = result.differences or [result.error or "compose failed"]

        if differences:
            return GateOutcome(
                origin=origin, flipped=False, absorb=absorb, differences=differences
            )

    # Gate green on every target — apply to the real tree.
    applied = absorb_remove(src_port, origin=origin)
    return GateOutcome(origin=origin, flipped=applied.ok, absorb=applied)
