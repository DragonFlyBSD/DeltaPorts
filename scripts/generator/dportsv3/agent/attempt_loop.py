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
    parts = [
        f"Previous attempt #{attempt_idx} did not succeed.\n",
        f"Tail of your prior response:\n```\n{snippet}\n```\n",
    ]
    parts.append(
        "Inspect what went wrong, adjust your approach, and try again. "
        "If you've tried the same idea twice and it failed both times, "
        "describe the obstacle in your Patch Log and stop — don't burn "
        "the budget thrashing."
    )
    return {"role": "user", "content": "\n".join(parts)}


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
    on_event=None,
    system_prompt: str | None = None,
    tool_whitelist: set[str] | frozenset[str] | None = None,
    proof_parser=None,
    is_success=None,
    session_dump=None,
) -> PatchResult:
    """Run the patch flow for one bundle, returning a structured PatchResult.

    ``system_prompt`` defaults to ``prompts.PATCH_SYSTEM``. Step 20b's
    convert flow passes ``prompts.CONVERT_SYSTEM`` so the same
    attempt-loop / tool-loop infrastructure drives the conversion
    agent without forking the engine.
    """
    base_messages = [
        {"role": "system", "content": system_prompt or prompts.PATCH_SYSTEM},
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
            messages = list(base_messages) + [
                _failure_context_message(attempt_idx - 1, prev_text)
            ]

        # Remaining tokens this attempt is allowed to consume.
        # Budget on billable (uncached) tokens, not total — re-sending a
        # cached prefix every turn shouldn't burn the budget for no new work.
        remaining = (budget - total_usage.billable_tokens) if budget else 0
        log.info(
            "attempt_loop: starting attempt %d/%d (billable used so far: %d / %d, remaining %d)",
            attempt_idx, iterations, total_usage.billable_tokens, budget, remaining,
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

        if on_event is not None:
            try:
                on_event({
                    "type": "attempt_start",
                    "attempt": attempt_idx,
                    "iterations": iterations,
                    # `tokens_used_so_far` is the number the budget gate
                    # actually enforces on (billable = uncached prompt +
                    # completion). Reporting total here made the display
                    # show the re-billed cached prefix (millions) while
                    # the gate compared billable (thousands) — alarming
                    # and wrong. Carry total + cached alongside for the
                    # UI breakdown.
                    "tokens_used_so_far": total_usage.billable_tokens,
                    "total_tokens_so_far": total_usage.total_tokens,
                    "cached_tokens_so_far": total_usage.cached_tokens,
                    "budget": budget,
                })
            except Exception:
                pass

        response, attempt_usage, rebuild_ok_seen = tool_loop.run(
            messages,
            model=model,
            env=env,
            api_base=api_base,
            api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            timeout=timeout,
            max_turns=max_tool_turns,
            max_tokens=remaining,
            on_event=on_event,
            attempt_idx=attempt_idx,
            tool_whitelist=tool_whitelist,
        )
        total_usage.add(attempt_usage)
        prev_text = response.text or ""
        final_text = prev_text

        # Optional full-session dump (gated by DP_HARNESS_DUMP_SESSION
        # at the callback's construction site). messages is the final
        # state of this attempt's conversation; the callback persists
        # it to the bundle. Best-effort: any failure inside the
        # callback is swallowed so the loop never derails.
        if session_dump is not None:
            try:
                session_dump(attempt_idx, messages)
            except Exception as exc:
                log.warning(
                    "attempt_loop: session_dump failed on attempt %d: %s",
                    attempt_idx, exc,
                )

        # Step 20: the success criterion is configurable. Patch
        # uses _parse_rebuild_proof + proof.rebuild_ok==True;
        # convert passes a Conversion-Proof parser + an existence
        # predicate. Without this, attempt_loop would always retry
        # convert attempts even after a clean proof.
        _parse = proof_parser or _parse_rebuild_proof
        _ok = is_success or (
            lambda p: bool(p and p.get("rebuild_ok") is True)
        )
        proof = _parse(prev_text)
        rebuild_ok = bool(_ok(proof))

        # Proof-block orphan rescue: the LLM may have run out of budget
        # before it could emit ``## Rebuild Proof (JSON)``, even though
        # a ``dsynth_build`` tool call already returned rebuild_ok=true
        # earlier in the attempt. Lift the success from the structured
        # tool result and synthesize a minimal proof dict so downstream
        # writes ``proposed_fix.md`` (the success artifact) instead of
        # ``manual_handoff.md`` (the escalation artifact). Gated on the
        # default success predicate — convert's custom is_success keys
        # on different fields, so the rebuild_ok signal is meaningless
        # there.
        if not rebuild_ok and rebuild_ok_seen and is_success is None:
            log.info(
                "attempt_loop: rebuild_ok=true seen via tool result but no "
                "proof block in assistant text; synthesizing proof for "
                "attempt %d",
                attempt_idx,
            )
            proof = {"rebuild_ok": True, "source": "tool_result"}
            rebuild_ok = True

        attempts.append(
            AttemptInfo(
                attempt=attempt_idx,
                tokens=attempt_usage.total_tokens,
                rebuild_ok=rebuild_ok,
                proof=proof,
            )
        )

        if on_event is not None:
            try:
                on_event({
                    "type": "attempt_end",
                    "attempt": attempt_idx,
                    "rebuild_ok": rebuild_ok,
                    "tokens": attempt_usage.total_tokens,
                })
            except Exception:
                pass

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

        if budget and total_usage.billable_tokens >= budget:
            log.warning(
                "attempt_loop: budget exhausted after attempt %d (%d >= %d billable)",
                attempt_idx, total_usage.billable_tokens, budget,
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
