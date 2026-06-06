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
          "Call after put_file/install_patches edits and before make_extract/dsynth_build.",
          {"origin": _STR}, ["origin"]),
    _tool("make_extract",
          "Run `make extract` for a port (after materialize_dports): unpacks the "
          "distfile into WRKSRC. PRISTINE upstream — no patches applied. Returns "
          "wrkdir + wrksrc. Use when reading original source or generating a patch "
          "against vanilla upstream. To patch a file that FreeBSD's files/patch-* "
          "also modifies, follow this with make_patch first.",
          {"origin": _STR}, ["origin"]),
    _tool("make_patch",
          "Run `make patch` for a port (after materialize_dports + make_extract): "
          "the do-patch phase make_extract skips. Applies files/patch-* then "
          "dragonfly/* into WRKSRC, leaving it in the real build-time state. Call "
          "BEFORE dupe/genpatch when authoring a dragonfly/ patch that must sit on "
          "top of files/ modifications, so genpatch's baseline matches what the "
          "build sees. On failure, stdout_tail names the rejecting patch.",
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
    # Step 38f: scope-filtered view of overlay.dops. Use INSTEAD of
    # `get_file overlay.dops` when reasoning about what compose will
    # actually apply on the current build — the raw file lists ops
    # for every target, and on multi-target overlays manual
    # scope-filtering is error-prone.
    _tool("get_effective_overlay",
          "Return the dops ops effective for the current build "
          "target. Reads ports/<origin>/overlay.dops, parses via the "
          "engine, filters by the env's target, and returns "
          "structured ops in declaration order with scope tags. "
          "Result fields: `target` (env's compose target), "
          "`effective_ops` (list of {id, kind, target, ...payload} — "
          "what the engine WILL apply), `filtered_out` (same shape "
          "plus `reason` — ops scoped to other build lines). On a "
          "port with no overlay.dops, returns ok=True with empty "
          "lists. Use INSTEAD of `get_file overlay.dops` when "
          "reasoning about what compose will apply; the raw file "
          "lists ops for every target and manual scope-filtering is "
          "error-prone on multi-target overlays.",
          {"origin": _STR}, ["origin"]),
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
    tools (``make_extract``, ``make_patch``, ``dsynth_build``,
    ``dupe``, ``genpatch``, ``install_patches``) it doesn't need,
    which prevents the model from going on source-exploration tangents.
    """
    if only is None:
        return list(_TOOLS)
    return [spec for spec in _TOOLS if spec["function"]["name"] in only]


def names() -> list[str]:
    return [spec["function"]["name"] for spec in _TOOLS]


# Tools the convert flow needs. Deliberately omits the build-loop
# tools that turn out to be tar pits for a port-overlay rewriter:
# make_extract / make_patch / dsynth_build / dupe / genpatch /
# install_patches.
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


def patch_tool_names() -> frozenset[str]:
    """The patch agent's tool list.

    The patch agent edits ``ports/<origin>/overlay.dops`` directly in
    dops DSL — the same surface the convert agent uses (``put_file`` +
    ``validate_dops`` + ``dops_reference``, reading with ``grep`` /
    ``get_file``) — plus the build-loop tools convert doesn't need
    (``make_extract`` / ``make_patch`` / ``dupe`` / ``genpatch`` /
    ``install_patches`` / ``dsynth_build`` / ``dsynth_log`` /
    ``materialize_dports``) and the
    read-only ``emit_diff`` / ``get_effective_overlay`` views. All of
    these live in the registry, so this is just the full tool set.
    """
    return frozenset(names())


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
