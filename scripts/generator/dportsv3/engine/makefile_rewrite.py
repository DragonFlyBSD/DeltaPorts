"""Deterministic Makefile CST rewrite/query primitives."""

from __future__ import annotations

from dataclasses import dataclass, field

from dportsv3.engine.makefile_cst import (
    AssignmentNode,
    DirectiveElifNode,
    DirectiveIfNode,
    MakefileDocument,
    TargetNode,
)


@dataclass(frozen=True)
class EditIntent:
    """Non-executing rewrite intent produced by CST primitives."""

    action: str
    node_indices: list[int] = field(default_factory=list)
    payload: dict[str, object] = field(default_factory=dict)
    ambiguous: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "node_indices": list(self.node_indices),
            "payload": dict(self.payload),
            "ambiguous": self.ambiguous,
        }


def find_var_assignments(document: MakefileDocument, name: str) -> list[AssignmentNode]:
    """Find assignment nodes for a variable name in source order."""
    indices = document.assignment_index.get(name, [])
    return [
        document.nodes[idx]
        for idx in indices
        if isinstance(document.nodes[idx], AssignmentNode)
    ]


def set_var(document: MakefileDocument, name: str, value: str) -> EditIntent:
    """Create intent to set/replace variable assignment."""
    indices = document.assignment_index.get(name, [])
    return EditIntent(
        action="set_var",
        node_indices=list(indices),
        payload={"name": name, "value": value},
        ambiguous=len(indices) > 1,
    )


def unset_var(document: MakefileDocument, name: str) -> EditIntent:
    """Create intent to remove variable assignment(s)."""
    indices = document.assignment_index.get(name, [])
    return EditIntent(
        action="unset_var",
        node_indices=list(indices),
        payload={"name": name},
        ambiguous=len(indices) > 1,
    )


def token_add(document: MakefileDocument, name: str, token: str) -> EditIntent:
    """Create intent to add token to variable assignment."""
    indices = document.assignment_index.get(name, [])
    return EditIntent(
        action="token_add",
        node_indices=list(indices),
        payload={"name": name, "token": token},
        ambiguous=len(indices) > 1,
    )


def token_remove(document: MakefileDocument, name: str, token: str) -> EditIntent:
    """Create intent to remove token from variable assignment."""
    indices = document.assignment_index.get(name, [])
    return EditIntent(
        action="token_remove",
        node_indices=list(indices),
        payload={"name": name, "token": token},
        ambiguous=len(indices) > 1,
    )


def find_target(document: MakefileDocument, name: str) -> list[TargetNode]:
    """Find target nodes by name in source order."""
    indices = document.target_index.get(name, [])
    return [
        document.nodes[idx]
        for idx in indices
        if isinstance(document.nodes[idx], TargetNode)
    ]


def target_set(
    document: MakefileDocument, name: str, recipe_lines: list[str]
) -> EditIntent:
    """Create intent to replace target block recipe."""
    indices = document.target_index.get(name, [])
    return EditIntent(
        action="target_set",
        node_indices=list(indices),
        payload={"name": name, "recipe_lines": list(recipe_lines)},
        ambiguous=len(indices) > 1,
    )


def target_append(
    document: MakefileDocument, name: str, recipe_lines: list[str]
) -> EditIntent:
    """Create intent to append to target recipe."""
    indices = document.target_index.get(name, [])
    return EditIntent(
        action="target_append",
        node_indices=list(indices),
        payload={"name": name, "recipe_lines": list(recipe_lines)},
        ambiguous=len(indices) > 1,
    )


def target_remove(document: MakefileDocument, name: str) -> EditIntent:
    """Create intent to remove target block."""
    indices = document.target_index.get(name, [])
    return EditIntent(
        action="target_remove",
        node_indices=list(indices),
        payload={"name": name},
        ambiguous=len(indices) > 1,
    )


def target_rename(document: MakefileDocument, old: str, new: str) -> EditIntent:
    """Create intent to rename target label."""
    indices = document.target_index.get(old, [])
    return EditIntent(
        action="target_rename",
        node_indices=list(indices),
        payload={"old": old, "new": new},
        ambiguous=len(indices) > 1,
    )


def find_condition(
    document: MakefileDocument,
    expr: str,
    contains: str | None = None,
) -> EditIntent:
    """Create intent for conditional lookup by expression and optional anchor text."""
    matched: list[int] = []
    for idx, node in enumerate(document.nodes):
        if isinstance(node, DirectiveIfNode) and node.condition == expr:
            if contains is None or contains in node.raw_text:
                matched.append(idx)
        elif isinstance(node, DirectiveElifNode) and node.condition == expr:
            if contains is None or contains in node.raw_text:
                matched.append(idx)

    return EditIntent(
        action="find_condition",
        node_indices=matched,
        payload={"expr": expr, "contains": contains},
        ambiguous=len(matched) > 1,
    )
