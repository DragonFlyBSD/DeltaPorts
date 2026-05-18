"""Tool registry for the patch agent.

Each entry maps an OpenAI-style tool name to (a worker function, a JSON
schema). The schemas are what the LLM sees; the ``env`` argument is
bound by the caller (``patch.run``) and is not exposed to the LLM.

``dispatch(name, arguments, env)`` is the single entry point used by
``tool_loop``: it looks up the function, calls it with ``env`` plus
the LLM-supplied arguments, and returns a result dict. Any exception
from the worker is caught and surfaced as
``{"ok": False, "error": "..."}`` so the LLM can recover on the next
turn rather than aborting the attempt.

Schemas are hand-written (not auto-derived from signatures) because
``description`` strings materially affect tool-selection quality.
"""

from __future__ import annotations

import inspect
import json
import traceback
from typing import Callable

from . import worker


# -----------------------------------------------------------------------------
# JSON schemas (OpenAI tool format)
# -----------------------------------------------------------------------------

_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "env_verify",
            "description": (
                "Verify the dev-env is ready for tool calls. Call this first; "
                "if it fails, stop and report — no other tool will work."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file",
            "description": (
                "Read a file from the env's writable overlay (paths under /work/...). "
                "Returns the content directly when the file is UTF-8 text "
                "(encoding='text', the common case for source/Makefiles/patches/docs) "
                "or base64-encoded when binary (encoding='base64'). sha256 is over the "
                "raw bytes — pass it to put_file's expected_sha256 to guard against "
                "stale writes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute in-chroot path under /work/ (e.g. /work/DPorts/devel/readline/Makefile).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "put_file",
            "description": (
                "Write content to a file in the env's writable overlay. "
                "Use encoding=text for source/Makefiles (UTF-8); encoding=base64 "
                "for binary. Optional expected_sha256 is an optimistic lock — pass "
                "the sha256 from a prior get_file to fail if the file changed "
                "underneath you."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "In-chroot path under /work/."},
                    "content": {"type": "string", "description": "File contents."},
                    "encoding": {
                        "type": "string",
                        "enum": ["text", "base64"],
                        "description": "Default: text.",
                    },
                    "expected_sha256": {
                        "type": "string",
                        "description": "Optional optimistic-lock check.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "emit_diff",
            "description": (
                "Return the working-tree diff for ports/<origin>/<relpath> in DeltaPorts. "
                "Pure read — never commits. Use to inspect what your edits look like "
                "before calling materialize_dports + dsynth_build."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Port origin like 'devel/readline'."},
                    "relpath": {"type": "string", "description": "File path relative to ports/<origin>/."},
                },
                "required": ["origin", "relpath"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grep",
            "description": (
                "Run ripgrep across files in the env's writable overlay. "
                "Output is capped at max_bytes (default 8192); set higher for "
                "wider surveys but expect truncation on busy trees."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "rg pattern (regex)."},
                    "path": {"type": "string", "description": "In-chroot path under /work/ to search."},
                    "include": {"type": "string", "description": "Optional rg glob filter."},
                    "max_bytes": {"type": "integer", "description": "Output cap (bytes). Default 8192."},
                },
                "required": ["pattern", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "materialize_dports",
            "description": (
                "Propagate DeltaPorts edits into the buildable DPorts tree for one origin. "
                "Wraps the env's `reapply` helper (= dportsv3 compose --origin). "
                "Call after put_file / install_patches edits, before extract or dsynth_build."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Port origin like 'devel/readline'."},
                },
                "required": ["origin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "extract",
            "description": (
                "Run `make extract` for a port (after materialize_dports). "
                "Returns wrkdir + wrksrc — the wrksrc is where the extracted "
                "source lives; dupe/genpatch operate on files inside it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Port origin."},
                },
                "required": ["origin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dupe",
            "description": (
                "Clone a WRKSRC source file with a .orig backup, so a later genpatch "
                "can produce a unified diff against the unmodified original. "
                "Run before editing the file via put_file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Absolute in-chroot path under WRKSRC."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "genpatch",
            "description": (
                "Generate a unified diff for a previously-duped file (file vs file.orig). "
                "Output lands in /work/genpatch-out/patch-* and is returned in the "
                "result's `patches` field for install_patches to pick up."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Same path used with dupe."},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_patches",
            "description": (
                "Copy generated patches from /work/genpatch-out/ into "
                "DeltaPorts/ports/<origin>/dragonfly/. Without `patches`, installs all "
                "patch-* files found. After this, call materialize_dports to "
                "propagate the new patches into DPorts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Port origin."},
                    "patches": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional explicit list of patch filenames.",
                    },
                },
                "required": ["origin"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dsynth_build",
            "description": (
                "Build a port with dsynth. Wraps the env's `dbuild` helper. "
                "rebuild_ok=true (rc==0) means the build succeeded; otherwise inspect "
                "stderr_tail/stdout_tail for the failure and iterate."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin": {"type": "string", "description": "Port origin."},
                },
                "required": ["origin"],
            },
        },
    },
]


# Map tool name → callable in worker module.
_HANDLERS: dict[str, Callable] = {
    spec["function"]["name"]: getattr(worker, spec["function"]["name"])
    for spec in _TOOLS
}


# -----------------------------------------------------------------------------
# Registry accessors
# -----------------------------------------------------------------------------


def schemas() -> list[dict]:
    """Return the OpenAI-format tool list to pass to litellm."""
    return list(_TOOLS)


def names() -> list[str]:
    return [spec["function"]["name"] for spec in _TOOLS]


# -----------------------------------------------------------------------------
# Dispatch
# -----------------------------------------------------------------------------


def dispatch(name: str, arguments: dict | None, *, env: str) -> dict:
    """Invoke the tool ``name`` with ``arguments`` (env bound by caller).

    Worker exceptions are caught and surfaced as
    ``{"ok": False, "error": "..."}`` so the LLM can recover on its
    next turn rather than aborting the attempt. Errors that signal a
    bug (wrong tool name, missing required arg) are surfaced the same
    way — never raised — for the same reason.
    """
    handler = _HANDLERS.get(name)
    if handler is None:
        return {"ok": False, "error": f"unknown tool: {name!r}"}

    args = arguments or {}
    if not isinstance(args, dict):
        return {"ok": False, "error": f"arguments must be a JSON object, got {type(args).__name__}"}

    # Filter to declared kwargs only; reject required-but-missing.
    sig = inspect.signature(handler)
    params = list(sig.parameters.values())
    if not params or params[0].name != "env":
        return {"ok": False, "error": f"tool {name} has no env parameter (bug)"}

    accepted = {p.name for p in params[1:]}
    rejected = [k for k in args if k not in accepted]
    if rejected:
        return {
            "ok": False,
            "error": f"tool {name}: unexpected argument(s): {rejected}",
        }
    required = {
        p.name for p in params[1:]
        if p.default is inspect.Parameter.empty and p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }
    missing = [k for k in required if k not in args]
    if missing:
        return {"ok": False, "error": f"tool {name}: missing required argument(s): {missing}"}

    try:
        result = handler(env, **args)
    except Exception as exc:  # noqa: BLE001 — intentional broad catch; surface to LLM
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=4),
        }

    # Workers already return {ok: bool, ...} or raise; if a worker returned
    # something else (e.g. dict without 'ok'), pass through unchanged.
    if isinstance(result, dict) and "ok" not in result:
        result = {"ok": True, **result}
    return result
