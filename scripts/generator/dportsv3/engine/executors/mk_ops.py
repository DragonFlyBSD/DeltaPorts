"""Makefile operation executors."""

from __future__ import annotations

from dportsv3.engine.apply_common import (
    _failed_row,
    _load_makefile,
    _missing_row,
    _on_missing,
    _replace_line_range,
    _resolve_path,
    _success_row,
    _target_block_span,
)
from dportsv3.engine.fsops import FileTransaction
from dportsv3.engine.makefile_cst import (
    AssignmentNode,
    DirectiveElifNode,
    DirectiveIfNode,
    IncludeNode,
    TargetNode,
)
from dportsv3.engine.makefile_rewrite import find_condition
from dportsv3.engine.models import ApplyContext, ApplyOpResult, PlanOp


def exec_mk_var_set(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    name = op.payload.get("name")
    value = op.payload.get("value")
    if not isinstance(name, str) or not isinstance(value, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.var.set requires name and value",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _missing_row(
            op, policy=policy, message="Makefile does not exist", source_path=path
        )

    text, document = loaded
    matches = [
        node
        for node in document.nodes
        if isinstance(node, AssignmentNode) and node.name == name
    ]
    if not matches:
        return _missing_row(
            op, policy=policy, message=f"assignment not found: {name}", source_path=path
        )

    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple assignments found for {name}",
            source_path=path,
        )

    target = matches[0]
    replacement = f"{name}= {value}"
    updated = _replace_line_range(
        text,
        start=target.span.line_start,
        end=target.span.line_end,
        new_lines=[replacement],
    )
    txn.stage_write(path, updated)
    return _success_row(op, "mk-var-set")


def exec_mk_var_unset(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    name = op.payload.get("name")
    if not isinstance(name, str):
        return _failed_row(
            op, code="E_APPLY_INVALID_OPERATION", message="mk.var.unset requires name"
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _missing_row(
            op, policy=policy, message="Makefile does not exist", source_path=path
        )

    text, document = loaded
    matches = [
        node
        for node in document.nodes
        if isinstance(node, AssignmentNode) and node.name == name
    ]
    if not matches:
        return _missing_row(
            op, policy=policy, message=f"assignment not found: {name}", source_path=path
        )

    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple assignments found for {name}",
            source_path=path,
        )

    target = matches[0]
    updated = _replace_line_range(
        text, start=target.span.line_start, end=target.span.line_end, new_lines=[]
    )
    txn.stage_write(path, updated)
    return _success_row(op, "mk-var-unset")


def exec_mk_var_token_add(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    name = op.payload.get("name")
    value = op.payload.get("value")
    if not isinstance(name, str) or not isinstance(value, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.var.token_add requires name and value",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _missing_row(
            op, policy=policy, message="Makefile does not exist", source_path=path
        )

    text, document = loaded
    matches = [
        node
        for node in document.nodes
        if isinstance(node, AssignmentNode) and node.name == name
    ]
    if not matches:
        return _missing_row(
            op, policy=policy, message=f"assignment not found: {name}", source_path=path
        )

    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple assignments found for {name}",
            source_path=path,
        )

    target = matches[0]
    tokens = [token for token in target.value.split() if token]
    if value in tokens:
        return _success_row(op, "mk-token-exists")
    tokens.append(value)
    replacement = f"{name}= {' '.join(tokens)}"
    updated = _replace_line_range(
        text,
        start=target.span.line_start,
        end=target.span.line_end,
        new_lines=[replacement],
    )
    txn.stage_write(path, updated)
    return _success_row(op, "mk-token-added")


def exec_mk_var_token_remove(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    name = op.payload.get("name")
    value = op.payload.get("value")
    if not isinstance(name, str) or not isinstance(value, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.var.token_remove requires name and value",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _missing_row(
            op, policy=policy, message="Makefile does not exist", source_path=path
        )

    text, document = loaded
    matches = [
        node
        for node in document.nodes
        if isinstance(node, AssignmentNode) and node.name == name
    ]
    if not matches:
        return _missing_row(
            op, policy=policy, message=f"assignment not found: {name}", source_path=path
        )

    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple assignments found for {name}",
            source_path=path,
        )

    target = matches[0]
    tokens = [token for token in target.value.split() if token]
    if value not in tokens:
        return _missing_row(
            op, policy=policy, message=f"token not found: {value}", source_path=path
        )
    tokens = [token for token in tokens if token != value]
    replacement = f"{name}= {' '.join(tokens)}" if tokens else ""
    new_lines = [replacement] if replacement else []
    updated = _replace_line_range(
        text,
        start=target.span.line_start,
        end=target.span.line_end,
        new_lines=new_lines,
    )
    txn.stage_write(path, updated)
    return _success_row(op, "mk-token-removed")


def exec_mk_target_set(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    name = op.payload.get("name")
    recipe = op.payload.get("recipe")
    if not isinstance(name, str) or not isinstance(recipe, list):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.target.set requires name and recipe",
        )
    if not all(isinstance(line, str) for line in recipe):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.target.set recipe must be list[str]",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _failed_row(
            op,
            code="E_APPLY_MISSING_SUBJECT",
            message="Makefile does not exist",
            source_path=path,
        )

    text, document = loaded
    matches = [
        index
        for index, node in enumerate(document.nodes)
        if isinstance(node, TargetNode) and node.name == name
    ]
    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple targets found: {name}",
            source_path=path,
        )

    lines = [f"{name}:", *recipe]
    if matches:
        start, end = _target_block_span(document, matches[0])
        updated = _replace_line_range(text, start=start, end=end, new_lines=lines)
    else:
        updated = text
        if updated and not updated.endswith(("\n", "\r\n")):
            updated += "\n"
        updated += "\n".join(lines) + "\n"

    txn.stage_write(path, updated)
    return _success_row(op, "mk-target-set")


def exec_mk_target_append(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    name = op.payload.get("name")
    recipe = op.payload.get("recipe")
    if not isinstance(name, str) or not isinstance(recipe, list):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.target.append requires name and recipe",
        )
    if not all(isinstance(line, str) for line in recipe):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.target.append recipe must be list[str]",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _failed_row(
            op,
            code="E_APPLY_MISSING_SUBJECT",
            message="Makefile does not exist",
            source_path=path,
        )

    text, document = loaded
    matches = [
        index
        for index, node in enumerate(document.nodes)
        if isinstance(node, TargetNode) and node.name == name
    ]
    if not matches:
        return _failed_row(
            op,
            code="E_APPLY_MISSING_SUBJECT",
            message=f"target not found: {name}",
            source_path=path,
        )
    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple targets found: {name}",
            source_path=path,
        )

    start, end = _target_block_span(document, matches[0])
    block_lines = text.splitlines(keepends=False)[start - 1 : end]
    block_lines.extend(recipe)
    updated = _replace_line_range(text, start=start, end=end, new_lines=block_lines)
    txn.stage_write(path, updated)
    return _success_row(op, "mk-target-appended")


def exec_mk_target_remove(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    name = op.payload.get("name")
    if not isinstance(name, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.target.remove requires name",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _missing_row(
            op, policy=policy, message="Makefile does not exist", source_path=path
        )

    text, document = loaded
    matches = [
        index
        for index, node in enumerate(document.nodes)
        if isinstance(node, TargetNode) and node.name == name
    ]
    if not matches:
        return _missing_row(
            op, policy=policy, message=f"target not found: {name}", source_path=path
        )
    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple targets found: {name}",
            source_path=path,
        )

    start, end = _target_block_span(document, matches[0])
    updated = _replace_line_range(text, start=start, end=end, new_lines=[])
    txn.stage_write(path, updated)
    return _success_row(op, "mk-target-removed")


def exec_mk_target_rename(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    old = op.payload.get("old")
    new = op.payload.get("new")
    if not isinstance(old, str) or not isinstance(new, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.target.rename requires old and new",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _missing_row(
            op, policy=policy, message="Makefile does not exist", source_path=path
        )

    text, document = loaded
    matches = [
        node
        for node in document.nodes
        if isinstance(node, TargetNode) and node.name == old
    ]
    if not matches:
        return _missing_row(
            op, policy=policy, message=f"target not found: {old}", source_path=path
        )
    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple targets found: {old}",
            source_path=path,
        )

    target = matches[0]
    replacement = f"{new}:"
    updated = _replace_line_range(
        text,
        start=target.span.line_start,
        end=target.span.line_start,
        new_lines=[replacement],
    )
    txn.stage_write(path, updated)
    return _success_row(op, "mk-target-renamed")


def exec_mk_block_disable(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    condition = op.payload.get("condition")
    contains = op.payload.get("contains")
    if not isinstance(condition, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.block.disable requires condition",
        )
    if contains is not None and not isinstance(contains, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.block.disable contains must be string",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _missing_row(
            op, policy=policy, message="Makefile does not exist", source_path=path
        )

    text, document = loaded
    intent = find_condition(document, expr=condition, contains=contains)
    if not intent.node_indices:
        return _missing_row(
            op,
            policy=policy,
            message=f"condition not found: {condition}",
            source_path=path,
        )
    if intent.ambiguous:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple conditions found: {condition}",
            source_path=path,
        )

    node = document.nodes[intent.node_indices[0]]
    if not isinstance(node, (DirectiveIfNode, DirectiveElifNode)):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="condition node is not .if/.elif",
            source_path=path,
        )

    original_line = text.splitlines(keepends=False)[node.span.line_start - 1]
    replacement = f"# {original_line}" if original_line else f"# {condition}"
    updated = _replace_line_range(
        text,
        start=node.span.line_start,
        end=node.span.line_end,
        new_lines=[replacement],
    )
    txn.stage_write(path, updated)
    return _success_row(op, "mk-block-disabled")


def exec_mk_block_replace_condition(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    from_condition = op.payload.get("from")
    to_condition = op.payload.get("to")
    contains = op.payload.get("contains")
    if not isinstance(from_condition, str) or not isinstance(to_condition, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.block.replace_condition requires from and to",
        )
    if contains is not None and not isinstance(contains, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.block.replace_condition contains must be string",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    policy = _on_missing(op)
    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _missing_row(
            op, policy=policy, message="Makefile does not exist", source_path=path
        )

    text, document = loaded
    intent = find_condition(document, expr=from_condition, contains=contains)
    if not intent.node_indices:
        return _missing_row(
            op,
            policy=policy,
            message=f"condition not found: {from_condition}",
            source_path=path,
        )
    if intent.ambiguous:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple conditions found: {from_condition}",
            source_path=path,
        )

    node = document.nodes[intent.node_indices[0]]
    if isinstance(node, DirectiveIfNode):
        replacement = f".if {to_condition}"
    elif isinstance(node, DirectiveElifNode):
        replacement = f".elif {to_condition}"
    else:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="condition node is not .if/.elif",
            source_path=path,
        )

    updated = _replace_line_range(
        text,
        start=node.span.line_start,
        end=node.span.line_end,
        new_lines=[replacement],
    )
    txn.stage_write(path, updated)
    return _success_row(op, "mk-condition-replaced")


def exec_mk_block_set(
    op: PlanOp, context: ApplyContext, txn: FileTransaction
) -> ApplyOpResult:
    condition = op.payload.get("condition")
    recipe = op.payload.get("recipe")
    contains = op.payload.get("contains")
    if not isinstance(condition, str) or not isinstance(recipe, list):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.block.set requires condition and recipe",
        )
    if contains is not None and not isinstance(contains, str):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.block.set contains must be string",
        )
    if not all(isinstance(line, str) for line in recipe):
        return _failed_row(
            op,
            code="E_APPLY_INVALID_OPERATION",
            message="mk.block.set recipe must be list[str]",
        )

    try:
        path = _resolve_path(context.port_root, None, default="Makefile")
    except ValueError as exc:
        return _failed_row(
            op,
            code="E_APPLY_INVALID_PATH",
            message=str(exc),
            source_path=context.port_root,
        )

    loaded = _load_makefile(txn, path)
    if loaded is None:
        return _failed_row(
            op,
            code="E_APPLY_MISSING_SUBJECT",
            message="Makefile does not exist",
            source_path=path,
        )

    text, document = loaded
    lines = text.splitlines(keepends=False)

    matches: list[tuple[int, int]] = []
    for region_start, region_end in document.directive_regions:
        start_node = document.nodes[region_start]
        if not isinstance(start_node, DirectiveIfNode):
            continue
        if start_node.condition != condition:
            continue
        start_line = start_node.span.line_start
        end_line = document.nodes[region_end].span.line_end
        block_text = "\n".join(lines[start_line - 1 : end_line])
        if contains is not None and contains not in block_text:
            continue
        matches.append((start_line, end_line))

    if len(matches) > 1:
        return _failed_row(
            op,
            code="E_APPLY_AMBIGUOUS_MATCH",
            message=f"multiple .if blocks found: {condition}",
            source_path=path,
        )

    block_lines = [f".if {condition}", *recipe, ".endif"]
    if matches:
        start_line, end_line = matches[0]
        updated = _replace_line_range(
            text,
            start=start_line,
            end=end_line,
            new_lines=block_lines,
        )
        txn.stage_write(path, updated)
        return _success_row(op, "mk-block-replaced")

    insert_before_line: int | None = None
    for node in document.nodes:
        if isinstance(node, IncludeNode) and node.include == "<bsd.port.post.mk>":
            insert_before_line = node.span.line_start
            break

    if insert_before_line is None:
        updated = text
        if updated and not updated.endswith(("\n", "\r\n")):
            updated += "\n"
        updated += "\n".join(block_lines) + "\n"
    else:
        updated = _replace_line_range(
            text,
            start=insert_before_line,
            end=insert_before_line - 1,
            new_lines=[*block_lines, ""],
        )

    txn.stage_write(path, updated)
    return _success_row(op, "mk-block-inserted")
