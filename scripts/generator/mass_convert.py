#!/usr/bin/env python
"""Step 48 — standalone, one-time compat→dops mass-convert driver.

THROWAWAY: not part of the dportsv3 package; delete after the migration.

Runs the deterministic converter (`migration.convert.convert_record`)
over the compat inventory, host-side, no build env. Ship bar is
ENGINE-VALID (status=converted + deterministic_ok) — faithfulness is the
steady-state build loop's job, not verified here.

Safe by default:
- DRY-RUN unless --apply (mutating thousands of ports is irreversible-ish).
- Only flips ports where `Makefile.DragonFly` is the SOLE compat artifact.
  Ports that also carry `diffs/` or `dragonfly/` are DEFERRED — flipping
  them (writing overlay.dops) would suppress the compat path and silently
  drop those un-absorbed artifacts. They go to the diffs-absorption /
  dragonfly-materialize / LLM tail.
- Resumable: judgements append to a JSONL log; a re-run skips origins
  already recorded (and ports that already have overlay.dops).

Usage:
  python mass_convert.py --repo /path/to/DeltaPorts [--apply] [--limit N]
                         [--log mass_convert.jsonl]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dportsv3.agent.convert import read_status_port_type
from dportsv3.engine.api import build_plan, check_dsl, parse_dsl
from dportsv3.migration.convert import (
    _drop_legacy_status,
    _parse_makefile_dragonfly,
    convert_record,
)


def _dragonfly_materialize_ops(port_dir: Path) -> tuple[list[str], str | None]:
    """`file materialize dragonfly/X -> dragonfly/X` for each payload
    file (preserving the dragonfly/ prefix — matches what the compat
    path produces). Returns (ops, escalate_reason). Escalates on
    target-scoped subdirs (dragonfly/@xxx/), which need scoped ops."""
    dfly = port_dir / "dragonfly"
    ops: list[str] = []
    for f in sorted(p for p in dfly.rglob("*") if p.is_file()):
        parts = f.relative_to(dfly).parts
        if parts and parts[0].startswith("@"):
            return [], "target-scoped dragonfly payload"
        rel = f.relative_to(port_dir).as_posix()
        ops.append(f"file materialize {rel} -> {rel}")
    return ops, None


def _diff_file_names(port_dir: Path) -> list[str]:
    d = port_dir / "diffs"
    return [f.name for f in d.rglob("*") if f.is_file()] if d.is_dir() else []


def _render_and_validate(origin: str, port_dir: Path, reason: str, ops: list[str]):
    from dportsv3.migration.convert import _quote  # noqa: PLC0415,F401 (parity w/ converter)
    ptype = read_status_port_type(port_dir) or "port"
    src = "\n".join([
        f"port {origin}", f"type {ptype}", f'reason "{reason}"', "target @any", "",
        *ops, "",
    ])
    ok = (parse_dsl(src, port_dir / "overlay.dops").ok
          and check_dsl(src, port_dir / "overlay.dops").ok
          and build_plan(src, port_dir / "overlay.dops").ok)
    return src, ok


def convert_neither(origin: str, repo: Path, dry_run: bool) -> dict:
    """Deterministic absorb for a diffs-bearing port WITHOUT pkg-plist /
    distinfo: Makefile.DragonFly→mk, dragonfly→materialize, Makefile.diff
    (in-place variable hunks only)→mk, REMOVE→file remove. Escalates if
    any artifact isn't deterministically handleable. Engine-valid bar.
    dragonfly/ retained; Makefile.DragonFly + the absorbed diffs +
    STATUS dropped."""
    from dportsv3.migration.absorb_makefile import hunk_to_mk_ops, parse_hunks  # noqa: PLC0415
    from dportsv3.migration.absorb_remove import (  # noqa: PLC0415
        _is_safe_relative, _read_remove_entries,
    )

    port_dir = repo / "ports" / origin
    KNOWN = {"Makefile.diff", "REMOVE"}
    unhandled = [n for n in _diff_file_names(port_dir) if n not in KNOWN]
    if unhandled:
        return {"origin": origin, "verdict": "deferred",
                "reason": f"unhandled diff type: {unhandled[0]}"}

    ops: list[str] = []
    mk = port_dir / "Makefile.DragonFly"
    if mk.exists():
        mk_ops, errs = _parse_makefile_dragonfly(mk)
        if errs:
            return {"origin": origin, "verdict": "blocked", "reason": errs}
        ops += mk_ops
    if (port_dir / "dragonfly").is_dir():
        dfly_ops, esc = _dragonfly_materialize_ops(port_dir)
        if esc is not None:
            return {"origin": origin, "verdict": "deferred", "reason": esc}
        ops += dfly_ops
    md = port_dir / "diffs" / "Makefile.diff"
    if md.is_file():
        for hunk in parse_hunks(md.read_text()):
            hops = hunk_to_mk_ops(hunk)
            if hops is None:
                return {"origin": origin, "verdict": "deferred",
                        "reason": "Makefile.diff non-variable hunk"}
            ops += hops
    rm = port_dir / "diffs" / "REMOVE"
    if rm.is_file():
        for entry in _read_remove_entries(port_dir):
            if _is_safe_relative(entry):
                ops.append(f"file remove {entry} on-missing noop")
    if not ops:
        return {"origin": origin, "verdict": "blocked", "reason": ["no ops produced"]}

    src, ok = _render_and_validate(
        origin, port_dir, "auto-converted from compat (fragment + diffs/)", ops)
    if not ok:
        return {"origin": origin, "verdict": "failed", "reason": ["overlay not engine-valid"]}

    if not dry_run:
        (port_dir / "overlay.dops").write_text(src)
        for f in port_dir.glob("Makefile.DragonFly*"):
            f.unlink()
        for n in ("Makefile.diff", "REMOVE"):
            (port_dir / "diffs" / n).unlink(missing_ok=True)
        diffs_dir = port_dir / "diffs"
        if diffs_dir.is_dir() and not any(diffs_dir.iterdir()):
            diffs_dir.rmdir()
        _drop_legacy_status(port_dir)
        # dragonfly/ kept — materialize source.
    return {"origin": origin, "verdict": "flipped"}


def convert_combined(origin: str, repo: Path, dry_run: bool) -> dict:
    """Convert a port carrying `dragonfly/` (+ optional Makefile.DragonFly,
    no `diffs/`): mk ops from the fragment + materialize ops for the
    dragonfly payload, in one overlay. Engine-valid bar. dragonfly/ is
    KEPT (it's the materialize source); Makefile.DragonFly + STATUS are
    dropped."""
    port_dir = repo / "ports" / origin
    ops: list[str] = []

    mk = port_dir / "Makefile.DragonFly"
    if mk.exists():
        mk_ops, errs = _parse_makefile_dragonfly(mk)
        if errs:
            return {"origin": origin, "verdict": "blocked", "reason": errs}
        ops += mk_ops

    dfly_ops, esc = _dragonfly_materialize_ops(port_dir)
    if esc is not None:
        return {"origin": origin, "verdict": "deferred", "reason": esc}
    ops += dfly_ops
    if not ops:
        return {"origin": origin, "verdict": "blocked", "reason": ["no ops produced"]}

    ptype = read_status_port_type(port_dir) or "port"
    header = [
        f"port {origin}",
        f"type {ptype}",
        'reason "auto-converted from compat (Makefile.DragonFly + dragonfly/)"',
        "target @any",
        "",
    ]
    src = "\n".join(header + ops + [""])

    if not (parse_dsl(src, port_dir / "overlay.dops").ok
            and check_dsl(src, port_dir / "overlay.dops").ok
            and build_plan(src, port_dir / "overlay.dops").ok):
        return {"origin": origin, "verdict": "failed", "reason": ["overlay not engine-valid"]}

    if not dry_run:
        (port_dir / "overlay.dops").write_text(src)
        for f in port_dir.glob("Makefile.DragonFly*"):
            f.unlink()
        _drop_legacy_status(port_dir)
        # dragonfly/ stays — it's the materialize source.
    return {"origin": origin, "verdict": "flipped"}


def _artifacts(port_dir: Path) -> dict:
    mkdf = any(port_dir.glob("Makefile.DragonFly*"))
    diffs = (port_dir / "diffs").is_dir()
    dragonfly = (port_dir / "dragonfly").is_dir()
    return {"mkdf": mkdf, "diffs": diffs, "dragonfly": dragonfly}


def _enumerate_compat(repo: Path):
    ports_root = repo / "ports"
    for cat in sorted(p for p in ports_root.iterdir() if p.is_dir()):
        for port in sorted(p for p in cat.iterdir() if p.is_dir()):
            if (port / "overlay.dops").exists():
                continue  # already dops
            art = _artifacts(port)
            if not (art["mkdf"] or art["diffs"] or art["dragonfly"]):
                continue  # not a compat port
            yield str(port.relative_to(ports_root)), art


def _tally(counts: dict, verdict: str) -> None:
    key = "deferred_other_artifacts" if verdict == "deferred" else verdict
    counts[key] = counts.get(key, 0) + 1


def _load_done(log: Path) -> set[str]:
    done: set[str] = set()
    if log.is_file():
        for line in log.read_text().splitlines():
            try:
                done.add(json.loads(line)["origin"])
            except (json.JSONDecodeError, KeyError):
                continue
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mass_convert")
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--apply", action="store_true", help="mutate the tree (default: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="process at most N ports (0=all)")
    ap.add_argument("--log", type=Path, default=Path("mass_convert.jsonl"))
    args = ap.parse_args(argv)

    repo = args.repo.resolve()
    done = _load_done(args.log)
    counts = {
        "flipped": 0, "would_flip": 0, "blocked": 0, "failed": 0,
        "deferred_other_artifacts": 0, "skipped_done": 0,
    }
    processed = 0

    with args.log.open("a") as logf:
        for origin, art in _enumerate_compat(repo):
            if origin in done:
                counts["skipped_done"] += 1
                continue
            if args.limit and processed >= args.limit:
                break
            processed += 1

            # diffs/ bearing: pkg-plist / distinfo are the special tail
            # (Step 47) — defer. Everything else → deterministic neither
            # absorb (Makefile.diff-allvar / REMOVE / fragment / dragonfly).
            if art["diffs"]:
                names = _diff_file_names(repo / "ports" / origin)
                special = any(("pkg-plist" in n or "pkg-list" in n or "distinfo" in n)
                              for n in names)
                if special:
                    rec = {"origin": origin, "verdict": "deferred",
                           "reason": "has pkg-plist/distinfo (Step 47)"}
                    _tally(counts, "deferred")
                    logf.write(json.dumps(rec) + "\n")
                    continue
                rec = convert_neither(origin, repo, dry_run=not args.apply)
                v = rec["verdict"]
                if v == "flipped" and not args.apply:
                    v = rec["verdict"] = "would_flip"
                _tally(counts, v)
                logf.write(json.dumps(rec) + "\n")
                continue

            if art["dragonfly"]:
                # dragonfly/ (+ optional Makefile.DragonFly): combined convert.
                rec = convert_combined(origin, repo, dry_run=not args.apply)
                v = rec["verdict"]
                if v == "flipped" and not args.apply:
                    v = rec["verdict"] = "would_flip"
                _tally(counts, v)
                logf.write(json.dumps(rec) + "\n")
                continue

            result = convert_record(
                {"origin": origin, "bucket": "auto-safe"},
                repo_root=repo, dry_run=not args.apply,
            )
            status = result.get("status")
            det_ok = bool(result.get("deterministic_ok"))
            if status == "converted" and det_ok:
                verdict = "flipped" if args.apply else "would_flip"
                _tally(counts, verdict)
                rec = {"origin": origin, "verdict": verdict}
            elif status == "blocked":
                _tally(counts, "blocked")
                rec = {"origin": origin, "verdict": "blocked",
                       "reason": result.get("errors") or []}
            else:
                _tally(counts, "failed")
                rec = {"origin": origin, "verdict": "failed",
                       "status": status, "reason": result.get("errors") or []}
            logf.write(json.dumps(rec) + "\n")

    print(("APPLY" if args.apply else "DRY-RUN") + f" — processed {processed} (log: {args.log})")
    for k, v in counts.items():
        print(f"  {k:26} {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
