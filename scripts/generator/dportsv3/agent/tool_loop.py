"""Multi-turn LLM-with-tools driver.

One call to ``run(...)`` is a single conversation with the LLM:
- Send messages + tool schemas
- If the LLM emitted ``tool_calls``, dispatch each, append the results
  as ``tool`` messages, and re-call
- Stop when the LLM returns text-only (no tool calls) or when
  ``max_turns`` is hit

The caller (``patch.run`` in step 4) handles attempt-level retries
with fresh failure context; this driver is one inner attempt.
"""

from __future__ import annotations

import json
import logging
import time

from . import llm, tools
from .llm import Response, Usage

log = logging.getLogger(__name__)


def _assistant_message_from(response: Response) -> dict:
    """Reconstruct the assistant message dict that produced ``response``.

    Needed so the next LLM call sees the tool calls the model made on
    the previous turn (otherwise it has amnesia about its own request).
    Thinking-mode providers (DeepSeek v4-*, some OpenAI-compat relays)
    additionally require ``reasoning_content`` to be echoed back, or
    the next request fails with HTTP 400.
    """
    msg: dict = {"role": "assistant", "content": response.text or ""}
    if response.tool_calls:
        msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.arguments or {}),
                },
            }
            for tc in response.tool_calls
        ]
    if response.reasoning_content:
        msg["reasoning_content"] = response.reasoning_content
    return msg


def run(
    messages: list[dict],
    *,
    model: str,
    env: str,
    api_base: str | None = None,
    api_key: str | None = None,
    custom_llm_provider: str | None = None,
    timeout: int = 120,
    max_turns: int = 12,
    max_tokens: int = 0,
    on_event=None,
    attempt_idx: int = 1,
    tool_whitelist: set[str] | frozenset[str] | None = None,
) -> tuple[Response, Usage]:
    """Drive the LLM through tool calls until it returns text-only.

    ``messages`` is mutated to include each assistant + tool turn for
    the duration of the loop. ``env`` is the dev-env name; every tool
    call is bound to it.

    Returns the final text-only ``Response`` and the cumulative
    ``Usage`` across all turns.

    Two safety caps:
    - ``max_turns``: stop after this many LLM round-trips even if the
      model keeps calling tools. Default 12.
    - ``max_tokens``: stop when cumulative usage reaches this many
      tokens. 0 (the default) disables the check — the caller is
      expected to pass the remaining attempt-level budget when one
      exists.
    """
    total = Usage()
    tool_schemas = tools.schemas(only=tool_whitelist)
    final: Response | None = None

    for turn in range(1, max_turns + 1):
        if max_tokens and total.total_tokens >= max_tokens:
            log.warning(
                "tool_loop: token budget exhausted on turn %d (%d >= %d)",
                turn, total.total_tokens, max_tokens,
            )
            return (final if final is not None else Response(text="")), total

        response = llm.complete(
            messages,
            model=model,
            tools=tool_schemas,
            api_base=api_base,
            api_key=api_key,
            custom_llm_provider=custom_llm_provider,
            timeout=timeout,
        )
        total.add(response.usage)
        final = response

        # Per-turn telemetry. Without this, only the per-attempt
        # totals are visible, which makes it hard to see WHERE the
        # tokens went (typically the prompt grows fast because
        # conversation history compounds with every tool result).
        tools_requested = (
            [tc.name for tc in response.tool_calls] if response.tool_calls else []
        )
        if on_event is not None:
            try:
                on_event({
                    "type": "llm_turn",
                    "attempt": attempt_idx,
                    "turn": turn,
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                    "tools_requested": tools_requested,
                    "text_only": not response.tool_calls,
                    "cumulative_total_tokens": total.total_tokens,
                })
            except Exception:
                pass  # callback must never break the loop

        if max_tokens and total.total_tokens >= max_tokens:
            log.warning(
                "tool_loop: token budget exhausted after turn %d (%d >= %d); "
                "stopping before tool dispatch",
                turn, total.total_tokens, max_tokens,
            )
            if on_event is not None:
                try:
                    on_event({
                        "type": "token_budget_exhausted",
                        "attempt": attempt_idx,
                        "turn": turn,
                        "tokens": total.total_tokens,
                        "budget": max_tokens,
                        "phase": "after_llm_turn",
                        "tools_skipped": tools_requested,
                    })
                except Exception:
                    pass
            return response, total

        if not response.tool_calls:
            log.debug("tool_loop: turn %d returned text-only, stopping", turn)
            return response, total

        log.debug(
            "tool_loop: turn %d issued %d tool call(s): %s",
            turn,
            len(response.tool_calls),
            tools_requested,
        )

        # Echo the assistant's tool-call message back into history so
        # the model has continuity on the next turn.
        messages.append(_assistant_message_from(response))

        for call in response.tool_calls:
            t0 = time.monotonic()
            # Defense-in-depth: even though we filtered the schemas
            # the model receives, refuse non-whitelisted tools if the
            # model hallucinates one (or the schema filter has a bug).
            if tool_whitelist is not None and call.name not in tool_whitelist:
                result = {
                    "ok": False,
                    "error": (
                        f"tool {call.name!r} is not allowed in this flow; "
                        f"available tools: {sorted(tool_whitelist)}"
                    ),
                }
            else:
                result = tools.dispatch(
                    call.name, call.arguments, env=env,
                )
            duration_ms = int((time.monotonic() - t0) * 1000)
            if on_event is not None:
                try:
                    on_event({
                        "type": "tool_call",
                        "attempt": attempt_idx,
                        "turn": turn,
                        "tool": call.name,
                        "args": call.arguments or {},
                        "result": result if isinstance(result, dict) else {"value": result},
                        "duration_ms": duration_ms,
                    })
                except Exception:
                    pass  # callback must never break the loop
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(result),
                }
            )
    log.warning(
        "tool_loop: hit max_turns=%d without a text-only response", max_turns
    )
    return (final if final is not None else Response(text="")), total
