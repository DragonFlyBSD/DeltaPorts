"""Triage flow — single-turn LLM call with snippet rounds folded in-process.

The flow:

    for round in range(max_snippet_rounds):
        call LLM with [system, user (+ accumulated snippets)]
        write the response text to the bundle (so snippet-extractor can read it)
        if response has no `## Snippet Requests` section: stop
        run snippet-extractor for this round
        format the extracted snippets, append to the next user turn
    return classification, confidence, response_text, usage

The bundle's ``analysis/snippets/round_N/`` files appear as the rounds
happen — no cross-job re-enqueue traffic.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import llm, prompts, snippets
from .policy import Confidence


@dataclass
class TriageResult:
    text: str  # final response text
    classification: str
    confidence: Confidence
    snippet_rounds: int = 0
    usage: llm.Usage = field(default_factory=llm.Usage)


_CLASSIFICATION_RE = re.compile(r"^##\s*Classification\s*\n+([^\n#]+)", re.MULTILINE)
_CONFIDENCE_RE = re.compile(r"^##\s*Confidence\s*\n+([^\n#]+)", re.MULTILINE)
_CONFIDENCE_LEAD_RE = re.compile(r"(high|medium|low)\b")
_SNIPPET_SECTION_RE = re.compile(
    r"^##\s*Snippet Requests\s*\n", re.MULTILINE
)


def _normalize_confidence(raw: str) -> Confidence:
    """Coerce the captured Confidence line to the enum.

    The agent is told to emit exactly one of high/medium/low, but may
    disobey and append prose ("high — because ..."). Take the leading
    enum word; a non-conforming value falls back to ``low`` (conservative
    — a prose value used to silently fail the policy floor and downgrade
    the tier to MANUAL; ``low`` is explicit rather than accidental).
    """
    m = _CONFIDENCE_LEAD_RE.match(raw.strip().lower())
    return m.group(1) if m else "low"  # type: ignore[return-value]


def _parse(text: str) -> tuple[str, Confidence]:
    classification = ""
    confidence: Confidence = "low"
    if m := _CLASSIFICATION_RE.search(text):
        classification = m.group(1).strip().lower()
    if m := _CONFIDENCE_RE.search(text):
        confidence = _normalize_confidence(m.group(1))
    return classification, confidence


def _has_snippet_requests(text: str) -> bool:
    return bool(_SNIPPET_SECTION_RE.search(text))


def _write_intermediate_triage(bundle_dir: Path, text: str) -> None:
    """Write ``analysis/triage.md`` so snippet-extractor can read requests from it."""
    out = bundle_dir / "analysis" / "triage.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text.rstrip() + "\n")


def run(
    payload: str,
    *,
    bundle_dir: Path,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    custom_llm_provider: str | None = None,
    timeout: int = 120,
    max_snippet_rounds: int | None = None,
    on_event=None,
    session_dump=None,
) -> TriageResult:
    """Run the triage flow end-to-end for one bundle.

    ``payload`` is the markdown prompt produced by
    ``agent-queue-runner.build_triage_payload``.

    ``bundle_dir`` is the live bundle directory; we write the intermediate
    triage.md after each LLM round so snippet-extractor can parse requests.
    """
    if max_snippet_rounds is None:
        max_snippet_rounds = int(os.environ.get("DP_HARNESS_MAX_SNIPPET_ROUNDS", "5"))

    messages: list[dict] = [
        {"role": "system", "content": prompts.TRIAGE_SYSTEM},
        {"role": "user", "content": payload},
    ]

    total_usage = llm.Usage()
    response_text = ""
    snippet_round = 0
    turn = 0

    while True:
        turn += 1
        response = llm.complete(
            messages,
            model=model,
            api_base=api_base,
            api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            timeout=timeout,
        )
        total_usage.add(response.usage)
        response_text = response.text

        if on_event is not None:
            try:
                on_event({
                    "type": "llm_turn",
                    "phase": "triage",
                    "turn": turn,
                    "snippet_round": snippet_round,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "cumulative_total_tokens": total_usage.total_tokens,
                })
            except Exception:
                pass  # callback must never break the loop

        _write_intermediate_triage(bundle_dir, response_text)

        if snippet_round >= max_snippet_rounds:
            break
        if not _has_snippet_requests(response_text):
            break

        snippet_round += 1
        rc, files = snippets.extract_round(bundle_dir, snippet_round)
        if rc != 0 or not files:
            # extractor failed or produced nothing usable; stop loop
            break

        messages.append({"role": "assistant", "content": response_text})
        messages.append(
            {
                "role": "user",
                "content": snippets.format_for_prompt(bundle_dir, files),
            }
        )

    # Append the last assistant turn so the dump reflects the full
    # conversation (the loop only appends mid-round, leaves the
    # final response un-appended because we don't iterate again).
    if response_text:
        messages.append({"role": "assistant", "content": response_text})
    if session_dump is not None:
        try:
            session_dump(1, messages)
        except Exception:
            # session_dump failures are best-effort; they log
            # internally.
            pass

    classification, confidence = _parse(response_text)
    return TriageResult(
        text=response_text,
        classification=classification,
        confidence=confidence,
        snippet_rounds=snippet_round,
        usage=total_usage,
    )
