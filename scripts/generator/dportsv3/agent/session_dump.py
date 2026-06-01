"""Optional dump of an attempt's full LLM conversation to the bundle.

Gated by ``DP_HARNESS_DUMP_SESSION``. When enabled, each LLM-driven
attempt's ``messages`` list — the literal sequence sent to the
model: system prompt, user payload, assistant turns (text +
tool_calls), and tool result content — is serialized to gzipped
JSONL and persisted as a bundle artifact.

Relpath: ``analysis/sessions/<job_id>.attempt<N>.jsonl.gz``.

Self-contained on purpose: no imports from runner/worker/etc. The
caller passes ``put_artifact`` so this module never reaches into
the artifact store directly — easier to test, no cycles, no hidden
dependency on which side of the tracker we're on.

Off by default because per-tool-call file content lands in
``messages`` (a 200 KB Makefile.in becomes a 200 KB tool message)
and a session can run to MB. Operators enable it when investigating
a specific class of failure; production runs leave it off.

Per-tool-result message content is head+tail truncated so the
artifact stays bounded even when the LLM saw a big file. Cap is
configurable via ``DP_HARNESS_DUMP_SESSION_CAP`` (bytes, default
16 KB per tool message).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
from typing import Callable

log = logging.getLogger(__name__)


_DEFAULT_TOOL_CONTENT_CAP = 16 * 1024  # bytes per tool-result message
_TRUTHY = frozenset({"1", "true", "yes", "on"})


PutArtifact = Callable[[str, str, bytes, str | None], bool]


def enabled() -> bool:
    """Check the gate without other side effects. Callers can use
    this to skip building the closure entirely when the dump is
    disabled — saves a few cycles on the hot path."""
    return os.environ.get("DP_HARNESS_DUMP_SESSION", "").strip().lower() in _TRUTHY


def _content_cap() -> int:
    raw = os.environ.get("DP_HARNESS_DUMP_SESSION_CAP", "")
    if not raw:
        return _DEFAULT_TOOL_CONTENT_CAP
    try:
        return max(1024, int(raw))
    except ValueError:
        return _DEFAULT_TOOL_CONTENT_CAP


def _truncate_head_tail(text: str, cap: int) -> str:
    if cap <= 0 or len(text) <= cap:
        return text
    half = max(512, cap // 2)
    head = text[:half]
    tail = text[-half:]
    elided = len(text) - len(head) - len(tail)
    return (
        head
        + f"\n[... session_dump elided {elided} of {len(text)} chars ...]\n"
        + tail
    )


def _redact_message(msg: dict, cap: int) -> dict:
    """Return a dump-ready copy of one message. Tool-result content
    (``role == 'tool'``) gets head+tail truncation; assistant / user
    / system content passes through unchanged (those are typically
    LLM-emitted text, not file dumps, and the LLM's own tokens are
    the signal we want to see clean).
    """
    out = dict(msg)
    if out.get("role") == "tool":
        content = out.get("content")
        if isinstance(content, str):
            out["content"] = _truncate_head_tail(content, cap)
    return out


def dump_attempt(
    *,
    bundle_id: str | None,
    job_id: str,
    attempt_idx: int,
    messages: list[dict],
    put_artifact: PutArtifact,
) -> bool:
    """Persist one attempt's full conversation to the bundle as
    gzipped JSONL. Best-effort: returns True on success, False on
    any failure (including the gate being off). Logs at WARN on
    serialization or write failure so operators see why a dump they
    expected didn't land.
    """
    if not enabled():
        return False
    if not bundle_id:
        return False
    cap = _content_cap()
    try:
        lines = [
            json.dumps(_redact_message(m, cap), ensure_ascii=False)
            for m in messages
        ]
        raw = ("\n".join(lines) + "\n").encode("utf-8")
        blob = gzip.compress(raw)
    except Exception as exc:
        log.warning(
            "session_dump: serialize failed (bundle=%s job=%s attempt=%d): %s",
            bundle_id, job_id, attempt_idx, exc,
        )
        return False
    relpath = f"analysis/sessions/{job_id}.attempt{attempt_idx}.jsonl.gz"
    try:
        return bool(put_artifact(bundle_id, relpath, blob, "gzip"))
    except Exception as exc:
        log.warning(
            "session_dump: put_artifact failed (bundle=%s relpath=%s): %s",
            bundle_id, relpath, exc,
        )
        return False


def make_dumper(
    *,
    bundle_id: str | None,
    job_id: str,
    put_artifact: PutArtifact,
):
    """Construct the per-attempt callback the loops invoke. Returns
    ``None`` when the gate is off so call sites can short-circuit
    without building the closure. Callers pass the return value
    straight to ``attempt_loop.run(session_dump=...)`` /
    ``triage.run(session_dump=...)``.
    """
    if not enabled():
        return None
    if not bundle_id:
        return None

    def _dump(attempt_idx: int, messages: list[dict]) -> None:
        dump_attempt(
            bundle_id=bundle_id,
            job_id=job_id,
            attempt_idx=attempt_idx,
            messages=messages,
            put_artifact=put_artifact,
        )

    return _dump
