"""Step 47 Phase 2 — absorb a port's ``diffs/Makefile.diff`` into
``overlay.dops``.

Deterministic subset only: **in-place variable-assignment** hunks
(``-VAR= old`` / ``+VAR= new`` for the same VAR) → ``mk`` ops. Everything
else (pure insertions / placement, recipe-line edits, conditionals,
mixed hunks) is left for the patch agent — :func:`absorb_makefile`
reports it as ``escalated`` with the unhandled hunks as context.

Correctness is enforced two ways:
  1. **Local self-check** — emitted ops applied to the upstream Makefile
     must equal ``patch(upstream, Makefile.diff)`` under the
     content-exact (whitespace-normalized) comparison. mk ops re-render
     the ``=`` separator (tab→space), which is meaningless to make but
     not byte-identical; content-exact tolerates exactly that.
  2. **Compose-parity gate** — :func:`absorb_makefile_gated` only mutates
     the real tree when the absorbed candidate composes content-exact.

A port flips only when *every* hunk is deterministically handled AND the
self-check passes. Otherwise it escalates untouched.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from dportsv3.engine.api import apply_dsl
from dportsv3.migration.parity import check_parity, makefile_whitespace_normalizer

_VAR_RE = re.compile(r"^([+-])\s*([A-Za-z_][A-Za-z0-9_]*)([+:!?]?=)(.*)$")
_REASON = "absorb diffs/Makefile.diff into dops (Step 47 Phase 2)"


@dataclass
class AbsorbMakefileResult:
    origin: str
    ok: bool
    ops: list[str] = field(default_factory=list)
    escalated: bool = False
    escalate_reason: str | None = None
    unhandled_hunks: list[str] = field(default_factory=list)
    error: str | None = None


def parse_hunks(diff_text: str) -> list[list[str]]:
    """Split a unified diff into hunk bodies (lists of prefixed lines)."""
    hunks: list[list[str]] = []
    cur: list[str] | None = None
    for line in diff_text.splitlines():
        if line.startswith("@@"):
            cur = []
            hunks.append(cur)
        elif line.startswith(("+++", "---")):
            continue
        elif cur is not None and (line[:1] in " +-" or line == ""):
            cur.append(line)
    return [h for h in hunks if any(x[:1] in "+-" for x in h)]


def hunk_to_mk_ops(hunk: list[str]) -> list[str] | None:
    """Emit ``mk`` ops for an in-place variable hunk, or ``None`` if the
    hunk is not a clean in-place variable change (caller escalates)."""
    rem = [line for line in hunk if line.startswith("-")]
    add = [line for line in hunk if line.startswith("+")]
    if not rem:
        return None  # pure insertion → placement risk → escalate
    if not all(_VAR_RE.match(line) for line in (*rem, *add)):
        return None  # touches non-variable lines → escalate

    rem_by: dict[str, re.Match] = {}
    add_by: dict[str, re.Match] = {}
    for line in rem:
        m = _VAR_RE.match(line)
        if m.group(2) in rem_by:
            return None
        rem_by[m.group(2)] = m
    for line in add:
        m = _VAR_RE.match(line)
        if m.group(2) in add_by:
            return None
        add_by[m.group(2)] = m
    if set(rem_by) != set(add_by):
        return None  # vars added/removed don't pair up → escalate

    ops: list[str] = []
    for var, rm in rem_by.items():
        am = add_by[var]
        if rm.group(3) != am.group(3):
            return None  # operator changed (= vs += …) → escalate
        old = rm.group(4).split()
        new = am.group(4).split()
        if len(new) > len(old) and new[: len(old)] == old:
            ops.extend(f"mk add {var} {tok}" for tok in new[len(old):])
        elif len(new) < len(old) and old[: len(new)] == new:
            ops.extend(f"mk remove {var} {tok}" for tok in old[len(new):])
        else:
            ops.append(f'mk set {var} "{am.group(4).strip()}"')
    return ops


def _bootstrap_overlay(origin: str, port_dir: Path, ops: list[str]) -> str:
    from dportsv3.migration.absorb_remove import _resolve_type  # noqa: PLC0415

    header = [
        "target @any",
        f"port {origin}",
        f"type {_resolve_type(port_dir)}",
        f'reason "{_REASON}"',
    ]
    return "\n".join([*header, *ops]) + "\n"


def _patch_result(upstream_makefile: Path, diff_path: Path) -> str | None:
    """``patch(upstream, diff)`` text, or None if the diff rejects."""
    with tempfile.TemporaryDirectory() as d:
        work = Path(d) / "Makefile"
        shutil.copy(upstream_makefile, work)
        proc = subprocess.run(
            ["patch", "--batch", "--forward", "-V", "none", "-r", "-",
             "-p0", "-i", str(diff_path)],
            cwd=d, capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return None
        return work.read_text()


def _ops_reproduce(upstream_makefile: Path, origin: str, ops: list[str], goal: str) -> bool:
    """Apply ops to the upstream Makefile and compare content-exact to goal."""
    with tempfile.TemporaryDirectory() as d:
        work = Path(d)
        shutil.copy(upstream_makefile, work / "Makefile")
        overlay = f"target @any\nport {origin}\ntype port\n" + "\n".join(ops) + "\n"
        res = apply_dsl(overlay, port_root=work, target="@main", oracle_profile="off")
        if not res.ok:
            return False
        got = (work / "Makefile").read_text()
    norm = makefile_whitespace_normalizer
    return norm("Makefile", got) == norm("Makefile", goal)


def absorb_makefile(
    port_dir: Path, *, origin: str, upstream_makefile: Path
) -> AbsorbMakefileResult:
    """Translate an all-in-place-variable ``Makefile.diff`` → ``mk`` ops in
    ``overlay.dops`` and delete the diff. Escalates (no mutation) when any
    hunk isn't a clean in-place variable change or the self-check fails."""
    diff_path = port_dir / "diffs" / "Makefile.diff"
    if not diff_path.is_file():
        return AbsorbMakefileResult(origin=origin, ok=True, error="no Makefile.diff")

    hunks = parse_hunks(diff_path.read_text())
    ops: list[str] = []
    for hunk in hunks:
        hunk_ops = hunk_to_mk_ops(hunk)
        if hunk_ops is None:
            return AbsorbMakefileResult(
                origin=origin, ok=True, escalated=True,
                escalate_reason="non-deterministic hunk (recipe/conditional/insertion/mixed)",
                unhandled_hunks=["\n".join(hunk)],
            )
        ops.extend(hunk_ops)

    goal = _patch_result(upstream_makefile, diff_path)
    if goal is None:
        return AbsorbMakefileResult(
            origin=origin, ok=True, escalated=True,
            escalate_reason="Makefile.diff rejects against current upstream (drift)",
        )
    if not _ops_reproduce(upstream_makefile, origin, ops, goal):
        return AbsorbMakefileResult(
            origin=origin, ok=True, escalated=True,
            escalate_reason="emitted ops don't reproduce patch result (content-exact)",
            unhandled_hunks=["\n".join(h) for h in hunks],
        )

    overlay = port_dir / "overlay.dops"
    if overlay.exists():
        existing = overlay.read_text()
        if existing and not existing.endswith("\n"):
            existing += "\n"
        overlay.write_text(existing + "\n".join(ops) + "\n")
    else:
        overlay.write_text(_bootstrap_overlay(origin, port_dir, ops))
    diff_path.unlink()
    diffs_dir = port_dir / "diffs"
    if diffs_dir.is_dir() and not any(diffs_dir.iterdir()):
        diffs_dir.rmdir()

    return AbsorbMakefileResult(origin=origin, ok=True, ops=ops)


def absorb_makefile_gated(
    origin: str,
    *,
    repo_root: Path,
    freebsd_root: Path,
    targets: list[str] | None = None,
    lock_root: Path | None = None,
) -> AbsorbMakefileResult:
    """Absorb ``Makefile.diff`` for ``origin`` only if the result composes
    content-exact on every target. Escalation results pass through
    unchanged (real tree untouched)."""
    targets = targets or ["@main"]
    category, _, name = origin.partition("/")
    src_port = repo_root / "ports" / origin
    upstream_makefile = freebsd_root / origin / "Makefile"
    if not src_port.is_dir() or not upstream_makefile.is_file():
        return AbsorbMakefileResult(origin=origin, ok=False, error="port or upstream Makefile missing")

    with tempfile.TemporaryDirectory(prefix="dp-absorb-mk-") as tmp:
        cand_root = Path(tmp)
        cand_port = cand_root / "ports" / category / name
        cand_port.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(src_port, cand_port, symlinks=True)

        result = absorb_makefile(cand_port, origin=origin, upstream_makefile=upstream_makefile)
        if not result.ok or result.escalated:
            return result

        for target in targets:
            parity = check_parity(
                origin, target, baseline_root=repo_root, candidate_root=cand_root,
                freebsd_root=freebsd_root, lock_root=lock_root,
                normalize=makefile_whitespace_normalizer,
            )
            if not parity.equal:
                return AbsorbMakefileResult(
                    origin=origin, ok=True, escalated=True,
                    escalate_reason=f"compose parity failed on {target}: "
                    + ", ".join(parity.differences or [parity.error or "?"]),
                    ops=result.ops,
                )

    return absorb_makefile(src_port, origin=origin, upstream_makefile=upstream_makefile)
