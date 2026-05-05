from __future__ import annotations

import re

from .errors import UsageError


def sanitize_name(value: str) -> str:
    translated = value.translate(str.maketrans({"/": "_", ":": "_", "@": "_", " ": "_"}))
    return re.sub(r"[^A-Za-z0-9._-]", "", translated)


def target_to_branch(target: str) -> str:
    if target == "@main":
        return "main"
    if re.fullmatch(r"@20[0-9][0-9]Q[1-4]", target):
        return target[1:]
    raise UsageError(f"unsupported target: {target}")


def default_env_name(target: str, origin: str | None) -> str:
    base = sanitize_name(target.removeprefix("@"))
    if origin:
        return f"{base}-{sanitize_name(origin)}"
    return base
