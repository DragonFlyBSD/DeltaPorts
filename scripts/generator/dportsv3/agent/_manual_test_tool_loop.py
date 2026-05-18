"""Manual smoke test for dportsv3.agent.tool_loop against a real LLM.

Not a unit test, not committed for CI — temporary verification script.
Delete after step 4 lands and the patch flow has its own E2E smoke.

Usage:
    export DP_TEST_MODEL='openai/some-model'           # litellm model string
    export DP_TEST_API_BASE='https://endpoint/v1'      # optional; only for custom endpoints
    export DP_TEST_API_KEY='your-key'                  # optional; falls back to provider's standard env
    export DP_TEST_PROVIDER='openai'                   # optional; pass through to litellm's custom_llm_provider
    export DP_TEST_ENV='2026Q2'                        # dev-env name; default 2026Q2
    ./scripts/generator/.venv/bin/python -m dportsv3.agent._manual_test_tool_loop

DP_TEST_PROVIDER notes:
- Leave unset for native providers (litellm routes from the model
  prefix: anthropic/, deepseek/, nvidia_nim/, ...).
- Set to 'openai' when talking to an OpenAI-compatible third-party
  endpoint (Groq, Together, opencode.ai/zen, ...) whose model ID
  contains a substring litellm would otherwise route natively (e.g.
  any 'deepseek-*' model name on opencode.ai/zen needs this).
- Set to any other litellm-supported provider name to force that path.

What it does:
- Drives the LLM with a tiny system prompt that asks it to call
  env_verify, then get_file on a known port's Makefile, then report
  the PORTNAME.
- Prints per-turn debug info from tool_loop.
- Prints the final text + token totals.
"""

from __future__ import annotations

import logging
import os
import sys

from dportsv3.agent import tool_loop


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(name)s %(message)s")
    logging.getLogger("dportsv3.agent.tool_loop").setLevel(logging.DEBUG)

    model = os.environ.get("DP_TEST_MODEL")
    if not model:
        print("error: set DP_TEST_MODEL (e.g. openai/gpt-5-nano)", file=sys.stderr)
        return 2
    api_base = os.environ.get("DP_TEST_API_BASE") or None
    api_key = os.environ.get("DP_TEST_API_KEY") or None
    provider = os.environ.get("DP_TEST_PROVIDER") or None
    env = os.environ.get("DP_TEST_ENV", "2026Q2")

    messages = [
        {
            "role": "system",
            "content": (
                "You have tools to operate on a DragonFly ports dev-env. "
                "Call env_verify first, then read "
                "/work/DeltaPorts/ports/devel/readline/Makefile with get_file. "
                "The Makefile is base64-encoded in the result's content field. "
                "Tell me what the PORTNAME= line says."
            ),
        },
        {"role": "user", "content": "Go."},
    ]

    final, usage = tool_loop.run(
        messages,
        model=model,
        env=env,
        api_base=api_base,
        api_key=api_key,
        custom_llm_provider=provider,
        timeout=120,
        max_turns=8,
    )

    print("---")
    print(f"final text:\n{final.text}")
    print(f"tokens (prompt/completion/total): "
          f"{usage.prompt_tokens}/{usage.completion_tokens}/{usage.total_tokens}")
    print(f"messages in conversation history: {len(messages)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
