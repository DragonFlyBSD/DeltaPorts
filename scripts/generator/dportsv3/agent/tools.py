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

_STR = {"type": "string"}
_INT = {"type": "integer"}


def _tool(name: str, desc: str, props: dict | None = None, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {
                "type": "object",
                "properties": props or {},
                "required": required or [],
            },
        },
    }


_TOOLS: list[dict] = [
    _tool("env_verify",
          "Confirm the dev-env is ready. Call first."),
    _tool("list_dir",
          "List a directory's entries in the writable overlay.",
          {"path": _STR, "max_entries": _INT}, ["path"]),
    _tool("get_file",
          "Read a file. Returns encoding=text (UTF-8) or encoding=base64 (binary). "
          "Use sha256 from this result in put_file's expected_sha256 to guard stale writes.",
          {"path": _STR}, ["path"]),
    _tool("put_file",
          "Write a file. encoding='text' (UTF-8, default) or 'base64' (binary). "
          "expected_sha256 is an optimistic lock — pass the sha256 from a prior get_file.",
          {"path": _STR, "content": _STR,
           "encoding": {"type": "string", "enum": ["text", "base64"]},
           "expected_sha256": _STR},
          ["path", "content"]),
    _tool("emit_diff",
          "Working-tree diff for ports/<origin>/<relpath> in DeltaPorts (read-only).",
          {"origin": _STR, "relpath": _STR}, ["origin", "relpath"]),
    _tool("grep",
          "Recursive POSIX grep -rn over the writable overlay. "
          "ok=True with empty matches just means 'no matches' (not an error).",
          {"pattern": _STR, "path": _STR, "include": _STR, "max_bytes": _INT},
          ["pattern", "path"]),
    _tool("materialize_dports",
          "Propagate DeltaPorts edits into the buildable DPorts tree for one origin. "
          "Call after put_file/install_patches edits and before extract/dsynth_build.",
          {"origin": _STR}, ["origin"]),
    _tool("extract",
          "Run `make extract` for a port (after materialize_dports). Returns wrkdir + wrksrc.",
          {"origin": _STR}, ["origin"]),
    _tool("dupe",
          "Snapshot a WRKSRC file with a .orig backup so genpatch can later diff against it.",
          {"path": _STR}, ["path"]),
    _tool("genpatch",
          "Produce a unified diff for a duped+edited file. Output: /work/genpatch-out/patch-*.",
          {"path": _STR}, ["path"]),
    _tool("install_patches",
          "Copy patches from /work/genpatch-out/ into DeltaPorts/ports/<origin>/dragonfly/. "
          "Then call materialize_dports.",
          {"origin": _STR, "patches": {"type": "array", "items": {"type": "string"}}},
          ["origin"]),
    _tool("dsynth_build",
          "Run dsynth -S -y build <origin>. rebuild_ok=true means rc==0. "
          "On failure, call dsynth_log(origin) — the actual build error is in the per-port log, "
          "not in this tool's stdout_tail.",
          {"origin": _STR}, ["origin"]),
    _tool("dsynth_log",
          "Read the tail of dsynth's per-port build log "
          "(/work/dsynth/logs/<origin-with-underscores>.log). Call after dsynth_build failure.",
          {"origin": _STR, "tail_lines": _INT}, ["origin"]),
    _tool("dops_reference",
          "Return a condensed quick-reference for the dops DSL (overlay.dops "
          "syntax: mk set/add/remove, mk replace-if, mk target set/append, "
          "text replace-once, file copy/remove, patch apply). On-demand — "
          "call ONCE only if (a) overlay.dops does NOT exist for this origin "
          "and (b) you are about to write one. Skip otherwise; the reference "
          "is large and re-reading wastes tokens.",
          {}, []),
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
