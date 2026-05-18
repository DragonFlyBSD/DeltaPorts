"""Budget-bounded retry loop around tool_loop for the patch flow.

One ``run(...)`` is one patch *job*: up to ``tier.max_iterations``
attempts, each itself a full multi-turn tool_loop conversation. Each
attempt starts fresh from [system, user] — we do **not** extend the
prior attempt's growing history, because tool-call traces compound
fast and the budget would melt by attempt 3 otherwise. Between
attempts we append a small failure-context message describing what
went wrong, so the LLM knows it's on a retry.

Stops when:
- the LLM emits Rebuild Proof JSON with ``rebuild_ok=true`` → success
- ``usage.total_tokens >= tier.max_tokens`` → budget-exhausted
- ``attempt == tier.max_iterations`` without success → needs-help
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import llm, prompts, tool_loop
from .llm import Usage

log = logging.getLogger(__name__)


@dataclass
class AttemptInfo:
    attempt: int  # 1-indexed
    tokens: int
    rebuild_ok: bool
    proof: dict | None = None  # parsed Rebuild Proof JSON for this attempt


@dataclass
class PatchResult:
    status: str  # "success" | "needs-help" | "budget-exhausted"
    final_text: str
    usage: Usage = field(default_factory=Usage)
    attempts: list[AttemptInfo] = field(default_factory=list)
    proof: dict | None = None  # the final/winning Rebuild Proof JSON (if any)


_PROOF_BLOCK_RE = re.compile(
    r"##\s*Rebuild Proof\s*\(JSON\)\s*\n+```(?:json)?\s*\n(.*?)\n```",
    re.IGNORECASE | re.DOTALL,
)


def _parse_rebuild_proof(text: str) -> dict | None:
    """Extract the final ``## Rebuild Proof (JSON)`` block, if present."""
    matches = _PROOF_BLOCK_RE.findall(text)
    if not matches:
        return None
    raw = matches[-1].strip()
    try:
        proof = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("attempt_loop: rebuild_proof JSON parse failed: %s", exc)
        return None
    if not isinstance(proof, dict):
        return None
    return proof


def _failure_context_message(attempt_idx: int, prev_text: str) -> dict:
    """Build the user message that nudges the LLM into a retry."""
    snippet = prev_text[-2000:] if len(prev_text) > 2000 else prev_text
    return {
        "role": "user",
        "content": (
            f"Previous attempt #{attempt_idx} did not succeed.\n\n"
            f"Tail of your prior response:\n"
            f"```\n{snippet}\n```\n\n"
            "Inspect what went wrong, adjust your approach, and try again. "
            "If you've tried the same idea twice and it failed both times, "
            "describe the obstacle in your Patch Log and stop — don't burn "
            "the budget thrashing."
        ),
    }


def run(
    payload: str,
    *,
    tier,  # dportsv3.agent.policy.Tier
    env: str,
    model: str,
    api_base: str | None = None,
    api_key: str | None = None,
    custom_llm_provider: str | None = None,
    timeout: int = 600,
    max_tool_turns: int = 12,
) -> PatchResult:
    """Run the patch flow for one bundle, returning a structured PatchResult."""
    base_messages = [
        {"role": "system", "content": prompts.PATCH_SYSTEM},
        {"role": "user", "content": payload},
    ]

    total_usage = Usage()
    attempts: list[AttemptInfo] = []
    prev_text = ""
    final_text = ""
    winning_proof: dict | None = None

    iterations = max(1, int(getattr(tier, "max_iterations", 1) or 1))
    budget = int(getattr(tier, "max_tokens", 0) or 0)

    for attempt_idx in range(1, iterations + 1):
        if attempt_idx == 1:
            messages = list(base_messages)
        else:
            messages = list(base_messages) + [_failure_context_message(attempt_idx - 1, prev_text)]

        # Remaining tokens this attempt is allowed to consume.
        remaining = (budget - total_usage.total_tokens) if budget else 0
        log.info(
            "attempt_loop: starting attempt %d/%d (tokens used so far: %d / %d, remaining %d)",
            attempt_idx, iterations, total_usage.total_tokens, budget, remaining,
        )

        if budget and remaining <= 0:
            log.warning("attempt_loop: budget already exhausted before attempt %d", attempt_idx)
            return PatchResult(
                status="budget-exhausted",
                final_text=final_text,
                usage=total_usage,
                attempts=attempts,
                proof=None,
            )

        response, attempt_usage = tool_loop.run(
            messages,
            model=model,
            env=env,
            api_base=api_base,
            api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            timeout=timeout,
            max_turns=max_tool_turns,
            max_tokens=remaining,
        )
        total_usage.add(attempt_usage)
        prev_text = response.text or ""
        final_text = prev_text

        proof = _parse_rebuild_proof(prev_text)
        rebuild_ok = bool(proof and proof.get("rebuild_ok") is True)

        attempts.append(
            AttemptInfo(
                attempt=attempt_idx,
                tokens=attempt_usage.total_tokens,
                rebuild_ok=rebuild_ok,
                proof=proof,
            )
        )

        if rebuild_ok:
            log.info("attempt_loop: success on attempt %d", attempt_idx)
            winning_proof = proof
            return PatchResult(
                status="success",
                final_text=final_text,
                usage=total_usage,
                attempts=attempts,
                proof=winning_proof,
            )

        if budget and total_usage.total_tokens >= budget:
            log.warning(
                "attempt_loop: budget exhausted after attempt %d (%d >= %d)",
                attempt_idx, total_usage.total_tokens, budget,
            )
            return PatchResult(
                status="budget-exhausted",
                final_text=final_text,
                usage=total_usage,
                attempts=attempts,
                proof=proof,
            )

    log.info("attempt_loop: needs-help after %d attempts", iterations)
    return PatchResult(
        status="needs-help",
        final_text=final_text,
        usage=total_usage,
        attempts=attempts,
        proof=attempts[-1].proof if attempts else None,
    )
