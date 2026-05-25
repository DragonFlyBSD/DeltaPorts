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
          "Read up to limit_lines lines from a file starting at zero-indexed "
          "offset_lines. Default 200 lines from start. **Prefer grep first** to "
          "narrow down before reading — whole-file reads on large files (e.g. "
          "Makefile.in, configure) inflate every subsequent turn's prompt by "
          "the file's full size. Returns encoding=text (UTF-8, line-windowed) "
          "or encoding=base64 (binary, capped at 32KB). On truncation, the "
          "result includes total_lines + a hint with the next offset_lines to "
          "request. Use sha256 from this result in put_file's expected_sha256 "
          "to guard stale writes (sha256 is over the FULL file, not the window).",
          {"path": _STR,
           "offset_lines": _INT,
           "limit_lines": _INT},
          ["path"]),
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
          "Recursive POSIX grep -rn over the writable overlay with N lines of "
          "surrounding context (default 3) per match. **Your default tool for "
          "investigating any large file** — returns only the relevant lines + "
          "context, not the whole file. ok=True with empty matches just means "
          "'no matches' (not an error). Use `include` to glob-filter filenames; "
          "set `context=0` to suppress surrounding lines if you only want "
          "match lines themselves.",
          {"pattern": _STR, "path": _STR, "include": _STR,
           "max_bytes": _INT, "context": _INT},
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
    _tool("validate_dops",
          "Run `dportsv3 dsl check` against the port's overlay.dops. Cheap "
          "parse + semantic validation (no compose, no filesystem mutation). "
          "Returns ok=True only when there are zero diagnostics. On failure, "
          "stderr_tail carries diagnostics with line:column and an E_* error "
          "code. Convert flow: call once after writing overlay.dops via "
          "put_file; if not ok, fix the offending line(s) and call again. "
          "Only emit the Conversion Proof after a clean validate_dops.",
          {"origin": _STR}, ["origin"]),
    # Step 25c: edit-intent tools. Opt-in via DP_HARNESS_PATCH_USE_INTENT
    # — gated in patch_tool_names() below so the prompt rewrite (25d)
    # can stage independently. Schemas always live in the registry
    # (so `dispatch()` works for unit tests + future operator-driven
    # invocations); whether the patch LLM sees them is the gate.
    _tool("apply_intent",
          "Apply one declarative edit intent (Step 25 grammar). The "
          "translator validates the intent against its JSON schema, "
          "renders it into substrate ops (compat or dops, resolved "
          "from the port's current overlay state), and applies them "
          "atomically. Returns paths_changed + substrate_diff. Refuses "
          "intents that violate the half-migration invariant or land "
          "on not_in_scope ports. Call `intent_reference(<type>)` for "
          "the per-type schema if you need to look up syntax.",
          {"origin": _STR,
           "intent": {"type": "object",
                      "description": "Intent body as a JSON object with "
                                     "a 'type' field selecting the variant "
                                     "(replace_in_patch, drop_patch, "
                                     "add_patch, add_file, change_makefile, "
                                     "bump_portrevision).",
                      "additionalProperties": True}},
          ["origin", "intent"]),
    _tool("intent_reference",
          "Return the JSON schema for one intent type. Read-only "
          "lookup — use this when you forgot the exact field names "
          "of an intent variant. Listing every known type: pass an "
          "unknown name; the error response carries known_intent_types.",
          {"intent_type": _STR}, ["intent_type"]),
]


# Map tool name → callable in worker module.
_HANDLERS: dict[str, Callable] = {
    spec["function"]["name"]: getattr(worker, spec["function"]["name"])
    for spec in _TOOLS
}


# -----------------------------------------------------------------------------
# Registry accessors
# -----------------------------------------------------------------------------


def schemas(only: set[str] | None = None) -> list[dict]:
    """Return the OpenAI-format tool list to pass to litellm.

    With ``only`` set, restrict the returned schemas to that name
    set — used by the convert flow (Step 20) to drop build-loop
    tools (``extract``, ``dsynth_build``, ``dupe``, ``genpatch``,
    ``install_patches``) it doesn't need, which prevents the model
    from going on source-exploration tangents.
    """
    if only is None:
        return list(_TOOLS)
    return [spec for spec in _TOOLS if spec["function"]["name"] in only]


def names() -> list[str]:
    return [spec["function"]["name"] for spec in _TOOLS]


# Tools the convert flow needs. Deliberately omits the build-loop
# tools that turn out to be tar pits for a port-overlay rewriter:
# extract / dsynth_build / dupe / genpatch / install_patches.
#
# materialize_dports is ALSO excluded — it's the verification step
# the *handler* runs after the agent emits the proof. If the agent
# can call it, the resulting compose output at
# /work/artifacts/compose/.../ becomes another tree the agent
# wanders into.
CONVERT_TOOL_NAMES: frozenset[str] = frozenset({
    "env_verify",
    "list_dir",
    "get_file",
    "put_file",
    "grep",
    "dops_reference",
    "validate_dops",
})


# Step 25c: edit-intent tools are gated behind
# DP_HARNESS_PATCH_USE_INTENT so 25c can land without disturbing
# the production patch agent's behavior. Once 25d (prompt swap)
# ships, the gate retires and intents become the default.
_INTENT_TOOL_NAMES: frozenset[str] = frozenset({
    "apply_intent",
    "intent_reference",
})


def patch_use_intent_enabled() -> bool:
    """Read the ``DP_HARNESS_PATCH_USE_INTENT`` gate.

    Truthy values: ``1`` ``true`` ``yes`` ``on`` (case-insensitive).
    Anything else (including unset / empty) means OFF — default
    production behavior, no intent tools, no 25g lifecycle hooks.

    Shared helper so the gate is checked the same way everywhere
    (tool registry filter, patch-flow lifecycle wiring in 25d-1,
    future 25d-2 prompt selector).
    """
    import os as _os  # noqa: PLC0415
    return (_os.environ.get("DP_HARNESS_PATCH_USE_INTENT") or "").lower() \
        in ("1", "true", "yes", "on")


def patch_tool_names() -> frozenset[str]:
    """Patch-agent's tool list, with the Step 25c intent tools
    conditionally included based on ``DP_HARNESS_PATCH_USE_INTENT``.

    Default (env var unset or '' / '0' / 'false'): all current
    patch tools EXCEPT the intent ones — behavior identical to
    pre-Step-25 production.

    Opt-in (env var = '1' / 'true' / 'yes'): all current patch
    tools PLUS the intent ones. The prompt rewrite (25d-2) is
    when we drop port-subtree put_file from this set; until then
    both surfaces coexist so the prompt can be staged.
    """
    all_names = set(names())
    if patch_use_intent_enabled():
        return frozenset(all_names)
    return frozenset(all_names - _INTENT_TOOL_NAMES)


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
