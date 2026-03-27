"""File/text/patch operation executors."""

from __future__ import annotations

from pathlib import Path

from dportsv3.engine.apply_common import (
    _failed_row,
    _line_ending,
    _missing_row,
    _on_missing,
    _replace_line_range,
    _resolve_path,
    _success_row,
    _text_to_lines,
)
from dportsv3.engine.fsops import FileTransaction
from dportsv3.engine.models import ApplyContext, ApplyOpResult, PlanOp


def exec_file_copy(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    src = op.payload.get("src")
    dst = op.payload.get("dst")
    if not isinstance(src, str) or not isinstance(dst, str):
        return _failed_row(
            op, code="E_APPLY_INVALID_PATH", message="file.copy requires src and dst"
        )

    try:
        src_path = _resolve_path(context.port_root, src)
        dst_path = _resolve_path(context.port_root, dst)
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    try:
        content = txn.read_text(src_path)
    except FileNotFoundError:
        return _failed_row(
            op,
            code="E_APPLY_MISSING_SUBJECT",
            message=f"copy source does not exist: {src}",
            source_path=src_path,
        )

    txn.stage_write(dst_path, content)
    return _success_row(op, "copied")


def exec_file_materialize(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    src = op.payload.get("src")
    dst = op.payload.get("dst")
    if not isinstance(src, str) or not isinstance(dst, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message="file.materialize requires src and dst",
        )

    try:
        src_path = _resolve_path(context.source_root, src)
        dst_path = _resolve_path(context.port_root, dst)
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    try:
        content = src_path.read_text()
    except FileNotFoundError:
        return _failed_row(
            op,
            code="E_APPLY_MISSING_SUBJECT",
            message=f"materialize source does not exist: {src}",
            source_path=src_path,
        )

    txn.stage_write(dst_path, content)
    return _success_row(op, "materialized")


def exec_file_remove(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    path_value = op.payload.get("path")
    if not isinstance(path_value, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message="file.remove requires path",
        )
    try:
        target = _resolve_path(context.port_root, path_value)
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    if target.exists():
        txn.stage_remove(target)
        return _success_row(op, "removed")

    return _missing_row(
        op,
        policy=policy,
        message=f"file does not exist: {path_value}",
        source_path=target,
    )


def exec_text_line_remove(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    file_path = op.payload.get("file")
    exact = op.payload.get("exact")
    if not isinstance(file_path, str) or not isinstance(exact, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="text.line_remove requires file and exact",
        )

    try:
        target = _resolve_path(context.port_root, file_path)
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    try:
        text = txn.read_text(target)
    except FileNotFoundError:
        return _missing_row(
            op,
            policy=policy,
            message=f"file does not exist: {file_path}",
            source_path=target,
        )

    lines = _text_to_lines(text)
    removed = False
    out: list[str] = []
    for line in lines:
        if not removed and line == exact:
            removed = True
            continue
        out.append(line)

    if not removed:
        return _missing_row(
            op,
            policy=policy,
            message=f"line not found in {file_path}: {exact}",
            source_path=target,
        )

    txn.stage_write(
        target,
        "\n".join(out) + ("\n" if text.endswith(("\n", "\r\n")) and out else ""),
    )
    return _success_row(op, "line-removed")


def exec_text_line_insert_after(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    file_path = op.payload.get("file")
    anchor = op.payload.get("anchor")
    line_value = op.payload.get("line")
    if (
        not isinstance(file_path, str)
        or not isinstance(anchor, str)
        or not isinstance(line_value, str)
    ):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="text.line_insert_after requires file, anchor, and line",
        )

    try:
        target = _resolve_path(context.port_root, file_path)
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    try:
        text = txn.read_text(target)
    except FileNotFoundError:
        return _missing_row(
            op,
            policy=policy,
            message=f"file does not exist: {file_path}",
            source_path=target,
        )

    lines = _text_to_lines(text)
    for idx, current in enumerate(lines):
        if current != anchor:
            continue
        lines.insert(idx + 1, line_value)
        newline = _line_ending(text)
        trailing = text.endswith(("\n", "\r\n"))
        rebuilt = newline.join(lines)
        if trailing or lines:
            rebuilt += newline
        txn.stage_write(target, rebuilt)
        return _success_row(op, "line-inserted")

    return _missing_row(
        op,
        policy=policy,
        message=f"anchor not found in {file_path}: {anchor}",
        source_path=target,
    )


def exec_text_replace_once(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    file_path = op.payload.get("file")
    from_text = op.payload.get("from")
    to_text = op.payload.get("to")
    if (
        not isinstance(file_path, str)
        or not isinstance(from_text, str)
        or not isinstance(to_text, str)
    ):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="text.replace_once requires file, from, and to",
        )

    try:
        target = _resolve_path(context.port_root, file_path)
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    try:
        text = txn.read_text(target)
    except FileNotFoundError:
        return _missing_row(
            op,
            policy=policy,
            message=f"file does not exist: {file_path}",
            source_path=target,
        )

    hits = text.count(from_text)
    if hits == 0:
        return _missing_row(
            op,
            policy=policy,
            message=f"pattern not found in {file_path}",
            source_path=target,
        )
    if hits > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple replacements found in {file_path}",
            source_path=target,
        )

    updated = text.replace(from_text, to_text, 1)
    txn.stage_write(target, updated)
    return _success_row(op, "text-replaced")
