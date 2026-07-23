"""Session-dump viewer rendering: parse the JSONL dump and
structure it into per-turn view data for the HTML session viewer."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .artifacts import resolve_artifact_path, load_tool_trace




# ---------------------------------------------------------------------------
# Session dump rendering (Phase 2: replace gzip-octet-stream download with a
# structured per-turn view of LLM message transcripts).
# ---------------------------------------------------------------------------

# Match a session-dump artifact: analysis/sessions/<filename>.jsonl[.gz]
_SESSION_RELPATH_RE = re.compile(
    r"^analysis/sessions/[^/]+\.jsonl(\.gz)?$"
)


# Parse the attempt number from a session filename. Convention:
# ``<ts>-<target>-<origin>-<pid>[-<role>].job.attempt<N>.jsonl[.gz]``.
# We only need the N to filter tool_trace events for this session's
# attempt — the rest of the components are documentary.
SESSION_ATTEMPT_RE = re.compile(r"\.attempt(\d+)\.jsonl(?:\.gz)?$")


# Split user prompt into ``## heading`` sections so the byte-budget
# of each section is visible. Headings start at column 0.
_SECTION_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)



def is_session_relpath(relpath: str) -> bool:
    """True if ``relpath`` points at a JSONL session dump under
    analysis/sessions/. Both .jsonl and .jsonl.gz match."""
    return bool(_SESSION_RELPATH_RE.match(relpath))



def _split_user_prompt_sections(content: str) -> list[dict[str, Any]]:
    """Break a user prompt into ``## heading`` sections with byte counts.

    Returns a list of ``{name, bytes, body}`` dicts in document order.
    The portion before the first heading (the "Automation Context" preamble
    in practice) is returned under name="(preamble)". Sections are
    intentionally returned with their body so the template can preview
    each one collapsed. Splitting on ``re.split`` with capture preserves
    headings + their bodies in alternating positions.
    """
    if not content:
        return []
    parts = re.split(r"(?m)^##\s+(.+)$", content)
    out: list[dict[str, Any]] = []
    # parts[0] is the preamble, then pairs of (heading, body)
    preamble = parts[0]
    if preamble.strip():
        out.append({
            "name": "(preamble)",
            "bytes": len(preamble),
            "body": preamble,
        })
    for i in range(1, len(parts), 2):
        name = parts[i].strip()
        body = parts[i + 1] if i + 1 < len(parts) else ""
        out.append({
            "name": name,
            "bytes": len(body),
            "body": body,
        })
    return out



def parse_session_records(
    path: Path, *, gzipped: bool | None = None,
) -> list[dict[str, Any]]:
    """Decompress (if needed) + parse a session JSONL into records.

    ``gzipped`` overrides the path-suffix sniff. Required for the
    blob-backend storage: ``resolve_artifact_path`` returns the
    content-addressed path under ``blobstore/objects/sha256/aa/bb/<sha>``
    with NO extension, so ``path.suffix`` doesn't carry the ``.gz``
    marker even though the file IS gzip-compressed. Callers that have
    the relpath should pass ``gzipped=relpath.endswith('.gz')``
    explicitly. When None, fall back to the path-suffix sniff (works
    for fs-backend artifacts whose fs_path preserves the extension).

    Returns the raw message list. Bad lines are skipped (lenient parse)
    so a partial dump still renders. Raises OSError on read failures —
    callers should catch and surface as the rendering error.
    """
    import gzip as _gzip  # noqa: PLC0415
    if gzipped is None:
        gzipped = path.suffix == ".gz"
    if gzipped:
        opener: Any = lambda p: _gzip.open(p, "rt", encoding="utf-8")
    else:
        opener = lambda p: open(p, encoding="utf-8")
    records: list[dict[str, Any]] = []
    with opener(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records



# Per-tool summarizers. Each function takes the parsed result dict and
# a summary dict to mutate. Kept small and explicit — the previous
# heuristic chain was order-sensitive because tool return shapes
# overlap on incidental keys (e.g. extract carries stdout_tail + wrksrc;
# dsynth_log carries rc + log_path + tail). Per-tool dispatch keyed on
# the calling tool's name removes the overwrite footgun.

def _summary_materialize_dports(
    data: dict[str, Any], summary: dict[str, Any],
) -> None:
    """materialize_dports / reapply output. The ``summary: applied=N``
    line is the operator's most-load-bearing signal (misreading it
    drives the visibility-ghost failure mode). Both the summary line
    and any ``top_warning_codes:`` row are captured."""
    tail = data.get("stdout_tail") or ""
    for line in tail.splitlines():
        ls = line.lstrip()
        if ls.startswith("summary:"):
            summary["headline"] = ls
        elif ls.startswith("top_warning_codes:"):
            summary["warnings_line"] = ls



def _summary_make_extract(
    data: dict[str, Any], summary: dict[str, Any],
) -> None:
    """make_extract — surface the wrksrc so the operator knows where
    the extracted source landed."""
    if data.get("wrksrc"):
        summary["headline"] = f"wrksrc={data['wrksrc']}"



def _summary_make_patch(
    data: dict[str, Any], summary: dict[str, Any],
) -> None:
    """make_patch — do-patch ran; surface ok + the first stderr/stdout
    line so a rejecting patch is visible at a glance."""
    if data.get("ok"):
        summary["headline"] = "do-patch applied (files/* + dragonfly/*)"
        return
    for key in ("stderr_tail", "stdout_tail"):
        for line in (data.get(key) or "").splitlines():
            line = line.strip()
            if line:
                summary["headline"] = f"patch failed: {line[:160]}"
                return



def _summary_dsynth_build(
    data: dict[str, Any], summary: dict[str, Any],
) -> None:
    """dsynth_build — rebuild_ok + rc + log path."""
    bits: list[str] = []
    if "rebuild_ok" in data:
        bits.append(f"rebuild_ok={data['rebuild_ok']}")
    if "rc" in data:
        bits.append(f"rc={data['rc']}")
    if data.get("log_path"):
        bits.append(f"log={data['log_path']}")
    if bits:
        summary["headline"] = " ".join(bits)



def _summary_dsynth_log(
    data: dict[str, Any], summary: dict[str, Any],
) -> None:
    """dsynth_log — log_path + size of the tail payload."""
    tail = data.get("tail") or ""
    summary["headline"] = f"log_tail {len(tail)}B"


# Tool name -> per-tool summarizer. Keyed dispatch (was in server.py).
_TOOL_SUMMARIZERS: dict[str, Any] = {
    "materialize_dports": _summary_materialize_dports,
    "materialize_dports_with_report": _summary_materialize_dports,
    "make_extract": _summary_make_extract,
    "make_patch": _summary_make_patch,
    "dsynth_build": _summary_dsynth_build,
    "dsynth_log": _summary_dsynth_log,
}


def _summarize_tool_result(
    content: str, *, tool_name: str | None = None,
) -> dict[str, Any]:
    """Extract the most operator-relevant fields from a tool result.

    Tool results are JSON-stringified worker return dicts. The template
    wants a quick at-a-glance summary (ok pill, key fields, error
    excerpt) before the operator decides to expand the full content.

    Dispatch keyed on ``tool_name`` (matched back via tool_call_id
    during structuring). When ``tool_name`` is unknown or None the
    summary degrades to just ``ok`` + ``error`` — the raw collapsible
    on the card still carries the full content, so no information is
    lost; only the at-a-glance headline is empty.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return {"ok": None, "headline": content[:200]}
    if not isinstance(data, dict):
        return {"ok": None, "headline": str(data)[:200]}
    summary: dict[str, Any] = {"ok": data.get("ok"), "headline": ""}
    err = data.get("error")
    if err:
        summary["error"] = str(err)[:300]
    summarizer = _TOOL_SUMMARIZERS.get(tool_name or "")
    if summarizer is not None:
        summarizer(data, summary)
    return summary



def _structure_session_turns(
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Group raw message records into chronological assistant-turn cards.

    Each output entry is one of:
    - ``{"kind": "system", "content": str, "bytes": int}``
    - ``{"kind": "user", "content": str, "bytes": int, "sections": [...]}``
        — ``sections`` is _split_user_prompt_sections output. Per-turn
        for context (multiple user records can appear, one per attempt).
    - ``{"kind": "assistant", "turn": int, "content": str,
         "reasoning_content": str, "tool_calls": [{name, args}, ...]}``
    - ``{"kind": "tool", "tool_call_id": str|None,
         "tool_name": str|None, "raw_content": str, "summary": {...}}``
        — ``tool_name`` is best-effort: matched back from the preceding
        assistant's tool_calls list by ``tool_call_id``.

    The output preserves the original record order; turn numbers count
    assistant records and are 1-indexed.
    """
    out: list[dict[str, Any]] = []
    # Track pending tool_calls (id -> name) from the most-recent assistant
    # so when their tool results arrive we can tag them with the call name.
    pending_calls: dict[str, str] = {}
    asst_turn = 0
    for rec in records:
        role = rec.get("role")
        if role == "system":
            out.append({
                "kind": "system",
                "content": rec.get("content") or "",
                "bytes": len(rec.get("content") or ""),
            })
        elif role == "user":
            content = rec.get("content") or ""
            out.append({
                "kind": "user",
                "content": content,
                "bytes": len(content),
                "sections": _split_user_prompt_sections(content),
            })
        elif role == "assistant":
            asst_turn += 1
            calls = []
            pending_calls = {}
            for tc in (rec.get("tool_calls") or []):
                try:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or "?"
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        parsed_args = json.loads(raw_args)
                        args_preview = json.dumps(parsed_args, sort_keys=True)[:300]
                    except (json.JSONDecodeError, ValueError):
                        parsed_args = None
                        args_preview = raw_args[:300]
                    tcid = tc.get("id") or ""
                    if tcid:
                        pending_calls[tcid] = name
                    calls.append({
                        "id": tcid,
                        "name": name,
                        "args_preview": args_preview,
                        "args_raw": raw_args,
                    })
                except Exception:
                    calls.append({
                        "id": "",
                        "name": "?",
                        "args_preview": "(unparseable)",
                        "args_raw": "",
                    })
            content = rec.get("content") or ""
            reasoning = rec.get("reasoning_content") or ""
            out.append({
                "kind": "assistant",
                "turn": asst_turn,
                "content": content,
                "content_bytes": len(content),
                "reasoning_content": reasoning,
                "reasoning_bytes": len(reasoning),
                "tool_calls": calls,
            })
        elif role == "tool":
            tcid = rec.get("tool_call_id") or ""
            tname = pending_calls.get(tcid) if tcid else None
            raw_content = rec.get("content") or ""
            out.append({
                "kind": "tool",
                "tool_call_id": tcid,
                "tool_name": tname,
                "raw_content": raw_content,
                "bytes": len(raw_content),
                "summary": _summarize_tool_result(
                    raw_content, tool_name=tname,
                ),
            })
        # Other roles (none expected) are dropped.
    return out



def _build_cumulative_token_map(
    tool_trace: list[dict[str, Any]], attempt: int | None,
) -> dict[int, dict[str, int]]:
    """Index ``llm_turn`` events from a tool_trace into a turn→tokens
    map for the given attempt.

    The runner emits one ``llm_turn`` per assistant message; events
    carry ``prompt_tokens``, ``completion_tokens``, ``total_tokens``,
    and the runner-summed ``cumulative_total_tokens``. Returning a
    dict keyed on the 1-indexed turn number lets the session viewer
    surface "where did the budget bleed?" without re-summing.

    ``attempt=None`` returns an empty map — without an attempt number
    parsed from the session filename we can't filter unambiguously.
    """
    if attempt is None:
        return {}
    out: dict[int, dict[str, int]] = {}
    for ev in tool_trace:
        if ev.get("type") != "llm_turn":
            continue
        if int(ev.get("attempt") or 0) != attempt:
            continue
        turn = ev.get("turn")
        if not isinstance(turn, int):
            continue
        out[turn] = {
            "prompt_tokens": int(ev.get("prompt_tokens") or 0),
            "completion_tokens": int(ev.get("completion_tokens") or 0),
            "total_tokens": int(ev.get("total_tokens") or 0),
            "cumulative_total_tokens": int(
                ev.get("cumulative_total_tokens") or 0
            ),
        }
    return out



def session_view_data(
    artifact_root: Path,
    bundle_id: str,
    relpath: str,
    ref: dict[str, Any],
    *,
    tool_trace_ref: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build the template context for the session dump viewer.

    Returns ``None`` when the artifact file is missing. Otherwise a
    dict the template renders directly.

    ``tool_trace_ref`` (optional) is the artifact_refs row for
    ``analysis/tool_trace.jsonl`` on the same bundle. When provided
    and the session's attempt number is parseable from the filename,
    per-turn cumulative token counts are joined onto each assistant
    item so the TOC can surface budget-bleed turns.
    """
    path = resolve_artifact_path(artifact_root, ref)
    if path is None or not path.exists():
        return None
    error: str | None = None
    items: list[dict[str, Any]] = []
    try:
        # Use the relpath suffix to decide compression — the resolved
        # on-disk path is content-addressed for blob-backend artifacts
        # and won't carry the .gz extension.
        records = parse_session_records(
            path, gzipped=relpath.endswith(".gz"),
        )
        items = _structure_session_turns(records)
    except OSError as exc:
        error = f"failed to read session: {exc}"
    except Exception as exc:  # noqa: BLE001 — surface to template
        error = f"failed to parse session: {exc}"

    # Join per-turn token totals from tool_trace.jsonl on (attempt, turn).
    attempt_match = SESSION_ATTEMPT_RE.search(Path(relpath).name)
    attempt_num = int(attempt_match.group(1)) if attempt_match else None
    if tool_trace_ref is not None:
        tool_trace = load_tool_trace(artifact_root, tool_trace_ref)
        token_map = _build_cumulative_token_map(tool_trace, attempt_num)
        for it in items:
            if it["kind"] == "assistant":
                tokens = token_map.get(it["turn"])
                if tokens:
                    it["cumulative_total_tokens"] = (
                        tokens["cumulative_total_tokens"]
                    )
                    it["prompt_tokens"] = tokens["prompt_tokens"]
    # Aggregate metrics for the top-of-page header.
    n_turns = sum(1 for it in items if it["kind"] == "assistant")
    n_tools = sum(1 for it in items if it["kind"] == "tool")
    n_users = sum(1 for it in items if it["kind"] == "user")
    sys_bytes = sum(it["bytes"] for it in items if it["kind"] == "system")
    user_bytes = sum(it["bytes"] for it in items if it["kind"] == "user")
    reasoning_bytes = sum(
        it["reasoning_bytes"] for it in items if it["kind"] == "assistant"
    )
    tool_bytes = sum(it["bytes"] for it in items if it["kind"] == "tool")
    return {
        "bundle_id": bundle_id,
        "relpath": relpath,
        "ref": ref,
        "filename": Path(relpath).name,
        "size": path.stat().st_size,
        "attempt": attempt_num,
        # Renamed from "items" because Jinja2 attribute access on
        # dicts uses getattr first and finds the dict.items() builtin
        # method before falling through to the key — so `session.items`
        # in a template returns the bound method, not the list.
        "entries": items,
        "n_turns": n_turns,
        "n_tools": n_tools,
        "n_users": n_users,
        "system_bytes": sys_bytes,
        "user_bytes": user_bytes,
        "reasoning_bytes": reasoning_bytes,
        "tool_bytes": tool_bytes,
        "error": error,
    }
