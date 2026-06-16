"""Canonical dops serializer — the inverse of the parser.

The engine parses dops (`text -> lex -> parse -> AST -> plan -> apply`) but
historically had no writer, so every producer (`migration/convert.py`,
`migration/absorb_*.py`, the runner bootstrap, `mass_convert.py`) hand-built
`mk ...` strings with f-strings — four divergent header builders, duplicated
quoting, and an operator->op mapping that drifted (the `!=`->`=` mis-render).

This module is the single home for op syntax, value quoting, and header
format. Builders take raw values (the shape producers actually hold) and
return canonical one-op text with no trailing newline; :func:`overlay`
assembles a full document. The contract is plan-level round-trip: parsing
``overlay(meta, ops)`` and planning it yields the intended ops (AST equality
can't hold — AST nodes carry source spans).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

_ON_MISSING_VALUES = {"error", "warn", "noop"}
_TYPE_VALUES = {"port", "mask", "dport", "lock"}
# Conservative bare-path charset. Tokens (dep specs etc.) are always quoted —
# convert.py's hard-won rule — but relative paths are simple enough to leave
# bare, matching what mass_convert already emits (no churn on `file` ops).
_SAFE_PATH = re.compile(r"^[A-Za-z0-9._/+-]+$")


def quote(value: str) -> str:
    """Render a DSL double-quoted string (the one quoting authority).

    Mirrors the escaping the migration translator used (`_quote`), now
    centralized so every producer escapes identically.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\t", "\\t").replace("\n", "\\n")
    return f'"{escaped}"'


def _path(value: str) -> str:
    """File path position (accepts WORD or STRING): leave bare when it's a
    simple relative path, else quote. Tokens use :func:`quote` directly."""
    return value if _SAFE_PATH.match(value) else quote(value)


def _on_missing(on_missing: str | None) -> str:
    if on_missing is None:
        return ""
    if on_missing not in _ON_MISSING_VALUES:
        raise ValueError(f"on-missing must be one of {_ON_MISSING_VALUES}: {on_missing!r}")
    return f" on-missing {on_missing}"


def _heredoc(recipe: str | Sequence[str]) -> tuple[str, str]:
    """Return (tag, body) for a heredoc, picking a tag that does not collide
    with any recipe line. Body has no trailing newline."""
    lines = recipe.splitlines() if isinstance(recipe, str) else list(recipe)
    body = "\n".join(lines)
    stripped = {ln.strip() for ln in lines}
    tag = "MK"
    n = 0
    while tag in stripped:
        n += 1
        tag = f"MK{n}"
    return tag, body


# --- header directives ---------------------------------------------------

def header(
    *, port: str, type: str, reason: str, target: str = "@any",
    maintainer: str | None = None,
) -> str:
    """Canonical overlay header. Fixed field order (port/type/reason/
    [maintainer/]target) — replaces the four divergent hand-built headers."""
    if type not in _TYPE_VALUES:
        raise ValueError(f"type must be one of {_TYPE_VALUES}: {type!r}")
    lines = [f"port {port}", f"type {type}", f"reason {quote(reason)}"]
    if maintainer is not None:
        lines.append(f"maintainer {quote(maintainer)}")
    lines.append(f"target {target}")
    return "\n".join(lines)


# --- mk scalar ops -------------------------------------------------------

def mk_set(name: str, value: str) -> str:
    return f"mk set {name} {quote(value)}"


def mk_eval(name: str, value: str) -> str:
    return f"mk eval {name} {quote(value)}"


def mk_shell(name: str, value: str) -> str:
    return f"mk shell {name} {quote(value)}"


def mk_unset(name: str, *, on_missing: str | None = None) -> str:
    return f"mk unset {name}{_on_missing(on_missing)}"


# --- mk token ops --------------------------------------------------------

def mk_add(name: str, token: str, *, on_missing: str | None = None) -> str:
    return f"mk add {name} {quote(token)}{_on_missing(on_missing)}"


def mk_remove(name: str, token: str, *, on_missing: str | None = None) -> str:
    return f"mk remove {name} {quote(token)}{_on_missing(on_missing)}"


# --- mk block ops --------------------------------------------------------

def mk_block_set(
    condition: str, recipe: str | Sequence[str], *, contains: str | None = None,
) -> str:
    tag, body = _heredoc(recipe)
    head = f"mk block set condition {quote(condition)}"
    if contains is not None:
        head += f" contains {quote(contains)}"
    return f"{head} <<'{tag}'\n{body}\n{tag}"


def mk_disable_if(
    condition: str, *, contains: str | None = None, on_missing: str | None = None,
) -> str:
    head = f"mk disable-if condition {quote(condition)}"
    if contains is not None:
        head += f" contains {quote(contains)}"
    return head + _on_missing(on_missing)


def mk_replace_if(
    from_condition: str, to_condition: str, *,
    contains: str | None = None, on_missing: str | None = None,
) -> str:
    head = f"mk replace-if from {quote(from_condition)} to {quote(to_condition)}"
    if contains is not None:
        head += f" contains {quote(contains)}"
    return head + _on_missing(on_missing)


# --- mk target ops -------------------------------------------------------

def mk_target_set(name: str, recipe: str | Sequence[str]) -> str:
    tag, body = _heredoc(recipe)
    return f"mk target set {name} <<'{tag}'\n{body}\n{tag}"


def mk_target_append(name: str, recipe: str | Sequence[str]) -> str:
    tag, body = _heredoc(recipe)
    return f"mk target append {name} <<'{tag}'\n{body}\n{tag}"


def mk_target_remove(name: str, *, on_missing: str | None = None) -> str:
    return f"mk target remove {name}{_on_missing(on_missing)}"


def mk_target_rename(old: str, new: str, *, on_missing: str | None = None) -> str:
    return f"mk target rename {old} -> {new}{_on_missing(on_missing)}"


# --- file ops ------------------------------------------------------------

def file_materialize(src: str, dst: str) -> str:
    return f"file materialize {_path(src)} -> {_path(dst)}"


def file_copy(src: str, dst: str) -> str:
    return f"file copy {_path(src)} -> {_path(dst)}"


def file_remove(path: str, *, on_missing: str | None = None) -> str:
    return f"file remove {_path(path)}{_on_missing(on_missing)}"


# --- document assembly ---------------------------------------------------

def overlay(header_text: str, ops: Iterable[str]) -> str:
    """Assemble a full overlay: header, a blank line, the ops, trailing
    newline. `header_text` comes from :func:`header`; `ops` from the builders."""
    op_lines = list(ops)
    body = "\n".join([header_text, "", *op_lines]) if op_lines else header_text
    return body + "\n"
