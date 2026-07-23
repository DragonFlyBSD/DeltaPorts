"""Assemble the fix-review chat's LLM messages from a bundle's artifacts.

Pure assembly, no I/O: given the bundle's metadata, a callable that
reads any of its artifacts as text, the chosen session dump, and the
operator's chat turns, build the OpenAI-style ``messages`` list for a
tools-OFF Q&A call. The tracker endpoint supplies the ``read_artifact``
reader (backed by the artifact store) and makes the ``llm.complete``
call — this module never touches the network, the filesystem, or an LLM.

Substrate note: the chat reads the bundle's **frozen artifacts**, not a
live dev-env. The agent's tools all read a shared, long-lived quarterly
chroot whose tree has moved on from (or never landed) this fix — so the
faithful record of *this* fix is what the job produced, which is exactly
the artifact set assembled here. No tool loop: the meaningful artifacts
(diff, triage, proposed_fix, errors, the rendered session) are small
enough to curate into one context, so there is nothing to iteratively
fetch.

Reader-callable shape mirrors ``proposed_fix.build_proposed_fix_ctx`` so
the assembly is unit-testable off a plain dict of fake artifacts.
"""

from __future__ import annotations

import json
from typing import Any, Callable

# Total byte budget for the assembled context (system message). Suits a
# modern 128K-context model (~96KB ≈ 25K tokens), leaving ample room for
# the chat turns + reply. Operators can shrink it for a smaller model.
DEFAULT_CONTEXT_CAP = 96 * 1024

# Per-artifact clip so one big artifact (a long errors.txt) can't crowd
# out the others; the session transcript gets the remaining budget.
_PER_ARTIFACT_CAP = 16 * 1024

# The session transcript is the richest single source ("why"), so it
# always keeps at least this much of the budget.
_MIN_SESSION_CAP = 24 * 1024

# Curated artifact set, in prompt-priority order. Deliberately excludes:
#   - logs/full.log.gz     (multi-MB raw build log; errors.txt is its
#                           distilled form and the session carries the
#                           dsynth_log output the agent actually saw)
#   - analysis/tool_trace.jsonl (redundant with the rendered session)
#   - analysis/sessions/*  (rendered separately as the transcript)
#   - *_audit.json / *_result.json (numeric/audit, redundant with the md)
_CURATED_ARTIFACTS: list[tuple[str, str]] = [
    ("analysis/proposed_fix.md", "Proposed fix (operator-facing summary)"),
    ("analysis/changes.diff", "The change (unified diff)"),
    ("analysis/triage.md", "Triage diagnosis"),
    ("analysis/patch.md", "Patch-agent narrative"),
    ("logs/errors.txt", "Build errors (distilled from the failed build)"),
]


# ReadArtifact(relpath) -> decoded text, or None if missing/unreadable.
# The caller decompresses .gz transparently before returning.
ReadArtifact = Callable[[str], "str | None"]


def _clip(text: str, limit: int) -> str:
    """Head+tail clip a string to ``limit`` bytes with an elision note.

    Whole-turn / whole-file boundaries are the caller's concern; this is
    the last-resort byte bound so a single oversized block can't blow the
    budget.
    """
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    keep = max(512, (limit - 64) // 2)
    head = data[:keep].decode("utf-8", errors="ignore")
    tail = data[-keep:].decode("utf-8", errors="ignore")
    return (
        head
        + f"\n[… {len(data) - 2 * keep} bytes elided …]\n"
        + tail
    )


def _parse_session_jsonl(text: str) -> list[dict[str, Any]]:
    """Parse decompressed session-dump JSONL into message records.

    Lenient: bad lines are skipped so a partial dump still renders.
    """
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def render_session_transcript(
    records: list[dict[str, Any]], *, cap: int,
) -> tuple[str, bool]:
    """Render a dumped session into a readable transcript for chat context.

    Flattens the OpenAI-shaped message records into prose blocks: the
    task payload, each assistant turn (reasoning + text + which tools it
    called), and each tool result. The original *system* prompt is
    skipped — static loop scaffolding, not part of "why this fix".
    Structured ``tool_calls`` are rendered as text rather than replayed
    as ``tool`` messages, so the outgoing request carries no tool
    plumbing (it runs tools-off) and stays a clean system+user/assistant
    shape across providers.

    Returns ``(text, truncated)``. When over ``cap``, keeps the **head
    and tail** and elides the middle: the head holds the failure + triage
    + first-move reasoning, the tail holds the winning approach + rebuild
    proof — both are what "why" questions hinge on. The repeated middle
    build cycles are the most expendable. Whole-block, so no turn is cut
    mid-sentence (a single oversized block is head+tail clipped first).
    """
    blocks: list[str] = []
    turn = 0
    for rec in records:
        role = rec.get("role")
        content = rec.get("content")
        if role == "system":
            continue
        if role == "user":
            if isinstance(content, str) and content.strip():
                blocks.append("## Task given to the agent\n\n" + content.strip())
        elif role == "assistant":
            turn += 1
            parts: list[str] = [f"## Agent turn {turn}"]
            reasoning = rec.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning.strip():
                parts.append("_Reasoning:_\n" + reasoning.strip())
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
            calls = rec.get("tool_calls") or []
            if calls:
                names = ", ".join(
                    str((c.get("function") or {}).get("name")
                        or c.get("name") or "?")
                    for c in calls
                )
                parts.append(f"_Called tools: {names}_")
            blocks.append("\n\n".join(parts))
        elif role == "tool":
            name = rec.get("name") or "tool"
            if isinstance(content, str) and content.strip():
                blocks.append(f"## Result from `{name}`\n\n{content.strip()}")

    sep = "\n\n"
    if len(sep.join(blocks).encode("utf-8")) <= cap:
        return sep.join(blocks), False

    def _bytes(b: str) -> int:
        return len(b.encode("utf-8")) + len(sep)

    half = cap // 2
    # Clip any single block that alone exceeds half — the head/tail loops
    # below always keep the first block of each end, so an oversized one
    # would otherwise leak the cap.
    blocks = [_clip(b, half) for b in blocks]

    head: list[str] = []
    used = 0
    for b in blocks:
        if used + _bytes(b) > half and head:
            break
        head.append(b)
        used += _bytes(b)
    tail: list[str] = []
    used = 0
    for b in reversed(blocks[len(head):]):
        if used + _bytes(b) > half and tail:
            break
        tail.append(b)
        used += _bytes(b)
    tail.reverse()
    elided = len(blocks) - len(head) - len(tail)
    middle = (
        [f"## … {elided} middle turn(s) elided to fit the context window …"]
        if elided > 0 else []
    )
    return sep.join(head + middle + tail), True


def build_chat_messages(
    *,
    bundle_meta: dict[str, Any],
    read_artifact: ReadArtifact,
    session_relpath: str | None,
    chat_turns: list[dict[str, str]],
    cap: int = DEFAULT_CONTEXT_CAP,
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Assemble the tools-off chat request from a bundle's artifacts.

    ``bundle_meta``: dict with ``origin`` / ``target`` / ``bundle_id``.
    ``read_artifact``: reads one artifact of this bundle as text (None if
    absent). ``session_relpath``: the session dump to render as the
    transcript, or None. ``chat_turns``: the operator's user/assistant
    turns, ending with a user question.

    Returns ``(messages, meta)`` where ``messages`` is
    ``[system, *chat_turns]`` and ``meta`` reports what was folded in
    (``artifacts_included``, ``session_truncated``) for the UI/audit.
    """
    from .prompts import CHAT_SYSTEM  # local import: keep module import-light

    cap = max(8 * 1024, cap)

    # 1. Curate the small text artifacts, clipping each. Stop before the
    #    artifacts alone starve the session of its minimum budget.
    artifact_blocks: list[str] = []
    included: list[str] = []
    used = 0
    for relpath, label in _CURATED_ARTIFACTS:
        text = read_artifact(relpath)
        if not text or not text.strip():
            continue
        block = f"### {label}\n_`{relpath}`_\n\n{_clip(text.strip(), _PER_ARTIFACT_CAP)}"
        nbytes = len(block.encode("utf-8"))
        if artifact_blocks and used + nbytes > cap - _MIN_SESSION_CAP:
            break
        artifact_blocks.append(block)
        included.append(relpath)
        used += nbytes

    # 2. Render the session transcript with whatever budget remains.
    session_cap = max(_MIN_SESSION_CAP, cap - used - 2048)
    transcript = ""
    truncated = False
    if session_relpath:
        raw = read_artifact(session_relpath)
        if raw:
            transcript, truncated = render_session_transcript(
                _parse_session_jsonl(raw), cap=session_cap,
            )

    # 3. Assemble the single system message: instruction + identity +
    #    artifacts + transcript.
    origin = bundle_meta.get("origin") or "(unknown origin)"
    target = bundle_meta.get("target") or "(none)"
    bundle_id = bundle_meta.get("bundle_id") or "(unknown)"

    sections = [
        CHAT_SYSTEM,
        "# The fix under review\n\n"
        + f"- Origin: `{origin}`\n"
        + f"- Target: `{target}`\n"
        + f"- Bundle: `{bundle_id}`",
    ]
    if artifact_blocks:
        sections.append(
            "# Artifacts from this job\n\n"
            "These are the files the job produced — the frozen record of "
            "this fix. Quote them when they answer a question.\n\n"
            + "\n\n".join(artifact_blocks)
        )
    if transcript:
        note = (
            "\n\n_(Transcript truncated to fit the context window; earlier "
            "and final turns are kept, the middle elided. Say so if a "
            "question falls in the cut region.)_"
            if truncated else ""
        )
        sections.append(
            "# Full agent session transcript\n\n" + transcript + note
        )

    system_content = "\n\n".join(sections)
    messages = [{"role": "system", "content": system_content}, *chat_turns]
    meta = {
        "session_relpath": session_relpath,
        "session_truncated": truncated,
        "artifacts_included": included,
    }
    return messages, meta
