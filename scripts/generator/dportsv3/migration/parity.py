"""Compose-parity oracle for the Step 47 `diffs/` absorption program.

Definition of done for absorbing a port's legacy ``diffs/`` lane into
``overlay.dops``: the composed output must be byte-for-byte identical
before and after. This module composes one origin from two delta-root
states (baseline = compat applying ``diffs/``; candidate = dops
absorption) into throwaway trees and reports any divergence in the
port's composed subtree.

Pure / read-only with respect to both delta roots — it only writes to
temp output dirs the caller never sees. The per-phase translators set
up the two roots (e.g. a pristine copy vs a mutated copy) and call
:func:`check_parity` as the gate before flipping a port.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dportsv3.compose import run_compose
from dportsv3.fsutils import diff_tree


@dataclass
class ParityResult:
    """Outcome of a single-origin compose-parity check."""

    origin: str
    target: str
    equal: bool
    baseline_ok: bool
    candidate_ok: bool
    differences: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "origin": self.origin,
            "target": self.target,
            "equal": self.equal,
            "baseline_ok": self.baseline_ok,
            "candidate_ok": self.candidate_ok,
            "differences": self.differences,
            "error": self.error,
        }


def _compose_origin(
    *,
    origin: str,
    target: str,
    delta_root: Path,
    freebsd_root: Path,
    out_dir: Path,
    lock_root: Path | None,
) -> bool:
    """Compose a single origin into ``out_dir``. Returns compose ok.

    Oracle is forced ``off``: the bmake oracle only runs in dops-mode
    compose, so a compat baseline never validates while the dops
    candidate would — an asymmetry that fails the comparison for
    reasons unrelated to output. The gate checks output byte-parity;
    Makefile validation is an orthogonal concern handled elsewhere."""
    result = run_compose(
        target=target,
        output_path=out_dir,
        delta_root=delta_root,
        freebsd_root=freebsd_root,
        lock_root=lock_root,
        selected_origins=[origin],
        dry_run=False,
        strict=False,
        replace_output=True,
        oracle_profile="off",
    )
    return result.ok


def check_parity(
    origin: str,
    target: str,
    *,
    baseline_root: Path,
    candidate_root: Path,
    freebsd_root: Path,
    lock_root: Path | None = None,
) -> ParityResult:
    """Compose ``origin`` from both roots and compare the port subtree.

    ``equal`` is True only when both composes succeed and the composed
    ``<origin>`` subtrees are byte-identical."""
    with (
        tempfile.TemporaryDirectory(prefix="dp-parity-base-") as bdir,
        tempfile.TemporaryDirectory(prefix="dp-parity-cand-") as cdir,
    ):
        baseline_ok = _compose_origin(
            origin=origin,
            target=target,
            delta_root=baseline_root,
            freebsd_root=freebsd_root,
            out_dir=Path(bdir),
            lock_root=lock_root,
        )
        candidate_ok = _compose_origin(
            origin=origin,
            target=target,
            delta_root=candidate_root,
            freebsd_root=freebsd_root,
            out_dir=Path(cdir),
            lock_root=lock_root,
        )
        if not baseline_ok or not candidate_ok:
            return ParityResult(
                origin=origin,
                target=target,
                equal=False,
                baseline_ok=baseline_ok,
                candidate_ok=candidate_ok,
                error="compose failed (see compose report)",
            )

        diffs = diff_tree(Path(bdir) / origin, Path(cdir) / origin)
        return ParityResult(
            origin=origin,
            target=target,
            equal=not diffs,
            baseline_ok=True,
            candidate_ok=True,
            differences=[f"{cls} {path}" for cls, path in diffs],
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dportsv3.migration.parity",
        description="Compose-parity oracle for diffs/ absorption (Step 47).",
    )
    parser.add_argument("origin", help="port origin, e.g. audio/foo")
    parser.add_argument("target", help="compose target, e.g. @main")
    parser.add_argument("--baseline-root", required=True, type=Path)
    parser.add_argument("--candidate-root", required=True, type=Path)
    parser.add_argument("--freebsd-root", required=True, type=Path)
    parser.add_argument("--lock-root", type=Path, default=None)
    args = parser.parse_args(argv)

    result = check_parity(
        args.origin,
        args.target,
        baseline_root=args.baseline_root,
        candidate_root=args.candidate_root,
        freebsd_root=args.freebsd_root,
        lock_root=args.lock_root,
    )

    if result.equal:
        print(f"PARITY OK: {result.origin} @ {result.target}")
        return 0
    print(f"PARITY FAIL: {result.origin} @ {result.target}")
    if result.error:
        print(f"  error: {result.error}")
    for line in result.differences:
        print(f"  {line}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
