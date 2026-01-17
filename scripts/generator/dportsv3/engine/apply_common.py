"""Shared helpers used by apply executors and pipeline."""

from __future__ import annotations

import difflib
import shutil
import tempfile
from pathlib import Path

from dportsv3.common.validation import normalize_on_missing
from dportsv3.engine.fsops import FileTransaction
from dportsv3.engine.makefile_cst import (
    MakefileDocument,
    RecipeLineNode,
    TargetNode,
    parse_makefile_cst,
)
from dportsv3.engine.models import ApplyDiff, ApplyOpResult, Diagnostic, PlanOp


def _diag(
    *,
    severity: str,
    code: str,
    message: str,
    source_path: Path | None = None,
) -> Diagnostic:
    return Diagnostic(
        severity=severity,  # type: ignore[arg-type]
        code=code,
        message=message,
        source_path=str(source_path) if source_path is not None else None,
        line=None,
        column=None,
    )


def _on_missing(op: PlanOp) -> str:
    value = op.payload.get("on_missing")
    if isinstance(value, str):
        normalized = normalize_on_missing(value)
        if normalized is not None:
            return normalized
    return "error"


def _resolve_path(
    port_root: Path, rel: str | None, *, default: str | None = None
) -> Path:
    raw = rel if rel is not None else default
    if raw is None:
        raise ValueError("missing path in operation payload")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError(f"absolute paths are not allowed: {raw}")
    resolved = (port_root / candidate).resolve()
    try:
        resolved.relative_to(port_root.resolve())
    except ValueError as exc:
        raise ValueError(f"path escapes port root: {raw}") from exc
    return resolved


def _success_row(op: PlanOp, message: str = "applied") -> ApplyOpResult:
    return ApplyOpResult(
        id=op.id,
        kind=op.kind,
        target=op.target,
        status="applied",
        message=message,
        diagnostics=[],
    )


def _failed_row(
    op: PlanOp,
    *,
    code: str,
    message: str,
    source_path: Path | None = None,
) -> ApplyOpResult:
    return ApplyOpResult(
        id=op.id,
        kind=op.kind,
        target=op.target,
        status="failed",
        message=code.lower(),
        diagnostics=[
            _diag(
                severity="error",
                code=code,
                message=message,
                source_path=source_path,
            )
        ],
    )


def _missing_row(
    op: PlanOp,
    *,
    policy: str,
    message: str,
    source_path: Path | None = None,
) -> ApplyOpResult:
    if policy == "warn":
        return ApplyOpResult(
            id=op.id,
            kind=op.kind,
            target=op.target,
            status="skipped",
            message="on-missing-warn",
            diagnostics=[
                _diag(
                    severity="warning",
                    code="W_APPLY_ON_MISSING_WARN",
                    message=message,
                    source_path=source_path,
                )
            ],
        )
    if policy == "noop":
        return ApplyOpResult(
            id=op.id,
            kind=op.kind,
            target=op.target,
            status="skipped",
            message="on-missing-noop",
            diagnostics=[],
        )
    return _failed_row(
        op,
        code="E_APPLY_MISSING_SUBJECT",
        message=message,
        source_path=source_path,
    )


def _line_ending(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def _text_to_lines(text: str) -> list[str]:
    return text.splitlines(keepends=False)


def _lines_to_text(lines: list[str], *, newline: str, trailing_newline: bool) -> str:
    if not lines:
        return ""
    out = newline.join(lines)
    if trailing_newline:
        return out + newline
    return out


def _replace_line_range(
    text: str, *, start: int, end: int, new_lines: list[str]
) -> str:
    lines = _text_to_lines(text)
    lines[start - 1 : end] = new_lines
    return _lines_to_text(
        lines,
        newline=_line_ending(text),
        trailing_newline=text.endswith(("\n", "\r\n")),
    )


def _rel_path(path: Path, port_root: Path) -> str:
    try:
        return path.resolve().relative_to(port_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _unified_diff(*, before: str | None, after: str | None, rel_path: str) -> str:
    before_lines = [] if before is None else before.splitlines(keepends=True)
    after_lines = [] if after is None else after.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )


def _build_staged_diffs(txn: FileTransaction, port_root: Path) -> list[ApplyDiff]:
    rows: list[ApplyDiff] = []
    for path in txn.staged_paths():
        before, after = txn.staged_change_snapshot(path)
        if before == after:
            continue
        rel = _rel_path(path, port_root)
        if before is None and after is not None:
            change_type = "created"
        elif before is not None and after is None:
            change_type = "removed"
        else:
            change_type = "modified"
        rows.append(
            ApplyDiff(
                path=rel,
                change_type=change_type,
                diff=_unified_diff(before=before, after=after, rel_path=rel),
            )
        )
    return rows


def _build_patch_preview_diff(op: PlanOp, port_root: Path) -> ApplyDiff | None:
    rel = op.payload.get("path")
    if not isinstance(rel, str):
        return None
    try:
        patch_path = _resolve_path(port_root, rel)
    except ValueError:
        return None
    try:
        patch_text = patch_path.read_text()
    except FileNotFoundError:
        return None
    return ApplyDiff(
        path=_rel_path(patch_path, port_root),
        change_type="fallback_patch",
        diff=patch_text,
    )


def _rel_under_root(path: Path, root: Path) -> Path | None:
    try:
        return path.resolve().relative_to(root.resolve())
    except ValueError:
        return None


def _materialize_staged_tree(port_root: Path, txn: FileTransaction) -> Path:
    tmp = tempfile.mkdtemp(prefix="dportsv3-oracle-")
    temp_root = Path(tmp)

    if port_root.exists():
        for item in sorted(port_root.iterdir(), key=lambda path: path.name):
            dest = temp_root / item.name
            if item.is_dir():
                shutil.copytree(item, dest, symlinks=True)
            else:
                shutil.copy2(item, dest)

    for path, content in txn.staged_writes().items():
        rel = _rel_under_root(path, port_root)
        if rel is None:
            continue
        target = temp_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    for path in txn.staged_removes():
        rel = _rel_under_root(path, port_root)
        if rel is None:
            continue
        target = temp_root / rel
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

    return temp_root


def _load_makefile(
    txn: FileTransaction, path: Path
) -> tuple[str, MakefileDocument] | None:
    try:
        text = txn.read_text(path)
    except FileNotFoundError:
        return None
    parsed = parse_makefile_cst(text, source_path=path)
    if not parsed.ok or parsed.document is None:
        raise ValueError(
            parsed.diagnostics[0].message
            if parsed.diagnostics
            else "failed to parse Makefile"
        )
    return text, parsed.document


def _target_block_span(document: MakefileDocument, target_idx: int) -> tuple[int, int]:
    node = document.nodes[target_idx]
    if not isinstance(node, TargetNode):
        raise ValueError("target index does not refer to a target node")
    end = node.span.line_end
    cursor = target_idx + 1
    while cursor < len(document.nodes):
        current = document.nodes[cursor]
        if isinstance(current, RecipeLineNode):
            end = current.span.line_end
            cursor += 1
            continue
        break
    return node.span.line_start, end
