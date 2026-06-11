"""Converter MVP for legacy overlay artifacts to overlay.dops."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from dportsv3.engine.api import build_plan, check_dsl, parse_dsl

_ASSIGN_RE = re.compile(r"^([A-Z0-9_]+)\s*(\+?=|\?=|:=|!=)\s*(.*)$")
_TARGET_LINE_RE = re.compile(r"^([A-Za-z0-9_.-]+):\s*$")


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\t", "\\t").replace("\n", "\\n")
    return f'"{escaped}"'


def _references_var(value: str, name: str) -> bool:
    """True when `value` expands `name` — `${name}`, `${name:mod}`, `$(name)`."""
    return bool(re.search(r"\$[{(]" + re.escape(name) + r"[):}]", value))


def _parse_makefile_dragonfly(path: Path) -> tuple[list[str], list[str]]:
    """Return generated dops ops and unsupported reasons."""
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        return [], [f"read_error:{exc}"]

    ops: list[str] = []
    errors: list[str] = []
    i = 0
    heredoc_index = 0

    while i < len(lines):
        raw = lines[i]
        line = raw.strip()
        i += 1

        if not line or line.startswith("#"):
            continue

        if (
            line.startswith(".if")
            or line.startswith(".elif")
            or line.startswith(".else")
        ):
            errors.append("conditional_block_present")
            continue

        assign = _ASSIGN_RE.match(line)
        if assign:
            name = assign.group(1)
            op = assign.group(2)
            value = assign.group(3).strip()
            if op in {"=", "?=", ":=", "!="}:
                if _references_var(value, name):
                    # Self-referential assignment (`VAR:= ${VAR:mod}`,
                    # prepend, etc.). A plain `mk set` would render a fatal
                    # recursive `=`; emit `mk eval`, which appends a verbatim
                    # immediate `:=` line — faithful for every bmake modifier.
                    ops.append(f"mk eval {name} {_quote(value)}")
                else:
                    ops.append(f"mk set {name} {_quote(value)}")
            elif op == "+=":
                if value:
                    # Always quote — `mk add` accepts a STRING token, and a
                    # bare token can carry chars that aren't valid unquoted
                    # DSL words (`>`, `:`, embedded `"` — e.g. dependency
                    # specs `foo>0:cat/foo`, `-DX:STRING="y"`), which the
                    # lexer rejects. Quoting is what `mk set` already does.
                    ops.append(f"mk add {name} {_quote(value)}")
            else:
                errors.append(f"unsupported_assignment_op:{op}")
            continue

        target = _TARGET_LINE_RE.match(line)
        if target:
            target_name = target.group(1)
            recipe: list[str] = []
            while i < len(lines) and (
                lines[i].startswith("\t") or not lines[i].strip()
            ):
                recipe.append(lines[i])
                i += 1
            heredoc_index += 1
            tag = f"MK{heredoc_index}"
            ops.append(f"mk target set {target_name} <<'{tag}'")
            ops.extend(recipe)
            ops.append(tag)
            continue

        errors.append(f"unsupported_line:{line}")

    return ops, errors


def _render_dops(
    origin: str,
    ops: list[str],
    *,
    port_type: str = "port",
    reason: str = "auto-converted from Makefile.DragonFly",
) -> str:
    # `target @any`: the deterministic translator only runs on ports
    # whose source is an UNSCOPED Makefile.DragonFly (the classifier
    # in overlay_state.assess_overlay refuses to mark a port
    # auto_safe_pending if a `Makefile.DragonFly.@xxx` variant exists
    # — those need judgment, not deterministic conversion). An
    # unscoped legacy artifact is target-agnostic by definition; the
    # dops translation must preserve that semantic, otherwise the
    # converted overlay is silently dead on every env whose target
    # ≠ the hardcoded scope. The prior hardcoded `@main` caused
    # exactly that bug on archivers/liblz4 2026-05-26 (env @2026Q2,
    # every op skipped with I_APPLY_TARGET_MISMATCH).
    header = [
        f"port {origin}",
        f"type {port_type}",
        f'reason "{reason}"',
        "target @any",
        "",
    ]
    return "\n".join(header + ops + [""])


def _detect_port_type(port_path: Path) -> str:
    """Resolve a port's plan type the way compat does (STATUS token, then
    a ``newport/`` directory). Returns ``port``/``dport``/``mask``/``lock``.

    The deterministic translator historically rendered ``type port`` for
    every record, which silently mis-typed DragonFly-only ports: compat
    sources a ``dport`` wholly from ``newport/`` and ignores
    ``Makefile.DragonFly`` entirely, so translating that dead file into
    ops produced overlays that diverged from the working compat output.
    """
    status_file = port_path / "STATUS"
    if status_file.is_file():
        try:
            lines = status_file.read_text().splitlines()
        except OSError:
            lines = []
        if lines:
            token = lines[0].strip().split()[0].upper() if lines[0].strip() else ""
            if token in {"PORT", "MASK", "DPORT", "LOCK"}:
                return token.lower()
    if (port_path / "newport").is_dir():
        return "dport"
    return "port"


def _drop_legacy_status(port_path: Path) -> None:
    """Remove ``STATUS`` after a successful deterministic conversion.

    The legacy ``STATUS`` file is part of the compat-overlay artifact
    set (alongside ``Makefile.DragonFly``); once a port carries a valid
    ``overlay.dops`` it's dead metadata that, if left behind, keeps the
    port looking half-migrated. Mirrors the LLM convert path, which
    drops ``STATUS`` via the handler's ``files_removed`` cleanup.

    Guard: the deterministic translator always renders ``type port``
    (see :func:`_render_dops`). If ``STATUS`` declares a non-default
    role (``MASK``/``DPORT``/``LOCK``), deleting it would silently
    switch the port's behavior to the rendered ``port`` — so leave it
    in place. (In that case the rendered ``type port`` is itself wrong;
    the retained ``STATUS`` surfaces the half-migration for review
    rather than hiding it.)
    """
    status_path = port_path / "STATUS"
    if not status_path.is_file():
        return
    try:
        first = status_path.read_text().splitlines()[0].strip()
    except (OSError, IndexError):
        first = ""
    token = first.split()[0].upper() if first else ""
    if token in {"MASK", "DPORT", "LOCK"}:
        return
    status_path.unlink()


def convert_record(
    record: dict[str, Any],
    *,
    repo_root: Path,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Attempt conversion for one classified record."""
    origin = str(record.get("origin", ""))
    port_path = repo_root / "ports" / origin
    dops_path = port_path / "overlay.dops"
    bucket = str(record.get("bucket", ""))

    result: dict[str, Any] = {
        "origin": origin,
        "bucket": bucket,
        "status": "blocked",
        "parse_ok": False,
        "check_ok": False,
        "plan_ok": False,
        "deterministic_ok": False,
        "classified": bool(bucket),
        "errors": [],
        "dry_run": dry_run,
        "output_path": str(dops_path),
    }

    if bucket == "stale":
        result["status"] = "stale"
        return result
    if bucket == "fallback-only":
        result["status"] = "fallback"
        return result
    if bucket != "auto-safe":
        result["status"] = "blocked"
        result["errors"].append("bucket_not_auto_safe")
        return result

    mk_path = port_path / "Makefile.DragonFly"

    if dops_path.exists():
        source = dops_path.read_text()
        parsed = parse_dsl(source, dops_path)
        checked = check_dsl(source, dops_path)
        planned = build_plan(source, dops_path)
        planned_again = build_plan(source, dops_path)
        planned_dict = planned.plan.to_dict() if planned.plan is not None else None
        planned_again_dict = (
            planned_again.plan.to_dict() if planned_again.plan is not None else None
        )
        result["parse_ok"] = parsed.ok
        result["check_ok"] = checked.ok
        result["plan_ok"] = planned.ok
        result["deterministic_ok"] = (
            planned.ok
            and planned_again.ok
            and planned_dict is not None
            and planned_again_dict is not None
            and planned_dict == planned_again_dict
        )
        result["status"] = "converted"
        result["errors"] = [
            d.code
            for d in parsed.diagnostics + checked.diagnostics + planned.diagnostics
        ]
        # The legacy Makefile.DragonFly keeps `classify_dops` returning
        # `auto_safe_pending` forever, which loops the runner. A
        # valid overlay.dops is the migrated form — drop the source.
        if not dry_run and mk_path.exists():
            mk_path.unlink()
        if not dry_run:
            _drop_legacy_status(port_path)
        return result

    port_type = _detect_port_type(port_path)
    if port_type in {"dport", "mask"}:
        # DragonFly-only / masked port: the plan type alone is the whole
        # overlay — compat sources a dport wholly from `newport/` and a
        # mask removes the port, ignoring `Makefile.DragonFly` in both
        # cases. Emit a header-only overlay of the correct type rather
        # than translating the dead legacy file into bogus ops.
        reason = (
            "DragonFly-only port; source in newport/"
            if port_type == "dport"
            else "masked on DragonFly"
        )
        source = _render_dops(origin, [], port_type=port_type, reason=reason)
        parsed = parse_dsl(source, dops_path)
        checked = check_dsl(source, dops_path)
        planned = build_plan(source, dops_path)
        result["parse_ok"] = parsed.ok
        result["check_ok"] = checked.ok
        result["plan_ok"] = planned.ok
        result["deterministic_ok"] = planned.ok
        result["errors"] = [
            d.code
            for d in parsed.diagnostics + checked.diagnostics + planned.diagnostics
        ]
        if parsed.ok and checked.ok and planned.ok:
            result["status"] = "converted"
            if not dry_run:
                dops_path.write_text(source)
                if mk_path.exists():
                    mk_path.unlink()
                (port_path / "STATUS").unlink(missing_ok=True)
        else:
            result["status"] = "failed"
        return result
    if port_type == "lock":
        # Lock ports pin a version in STATUS and source from the lock
        # tree — not a deterministic translation; leave for manual review.
        result["status"] = "blocked"
        result["errors"].append("lock_needs_manual")
        return result

    if not mk_path.exists():
        result["status"] = "blocked"
        result["errors"].append("missing_makefile_dragonfly")
        return result

    ops, conversion_errors = _parse_makefile_dragonfly(mk_path)
    if conversion_errors:
        result["status"] = "blocked"
        result["errors"] = conversion_errors
        return result

    source = _render_dops(origin, ops)
    parsed = parse_dsl(source, dops_path)
    checked = check_dsl(source, dops_path)
    planned = build_plan(source, dops_path)
    planned_again = build_plan(source, dops_path)
    planned_dict = planned.plan.to_dict() if planned.plan is not None else None
    planned_again_dict = (
        planned_again.plan.to_dict() if planned_again.plan is not None else None
    )

    result["parse_ok"] = parsed.ok
    result["check_ok"] = checked.ok
    result["plan_ok"] = planned.ok
    result["deterministic_ok"] = (
        planned.ok
        and planned_again.ok
        and planned_dict is not None
        and planned_again_dict is not None
        and planned_dict == planned_again_dict
    )
    result["errors"] = [
        d.code for d in parsed.diagnostics + checked.diagnostics + planned.diagnostics
    ]

    if (
        result["parse_ok"]
        and result["check_ok"]
        and result["plan_ok"]
        and result["deterministic_ok"]
    ):
        result["status"] = "converted"
        if not dry_run:
            dops_path.write_text(source)
            if mk_path.exists():
                mk_path.unlink()
            _drop_legacy_status(port_path)
    else:
        result["status"] = "failed"

    return result
