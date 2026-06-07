"""litellm wrapper with a normalized response shape.

The tokenizers stub (needed on DragonFly) is in dportsv3.agent.__init__,
which runs before any module here is loaded.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    # Cache-read (re-billed prefix) tokens, normalized by litellm across
    # providers as usage.prompt_tokens_details.cached_tokens (e.g. DeepSeek
    # prompt_cache_hit_tokens). Defaults 0 → if a provider/run reports no
    # cache hit, billable_tokens == total and budgeting is unchanged.
    cached_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens
        self.cached_tokens += other.cached_tokens

    @property
    def billable_tokens(self) -> int:
        """New (non-cached) work: uncached prompt + completion. The
        per-attempt budget gates on this so re-sending a cached prefix
        every turn doesn't exhaust the budget for no real work."""
        return max(0, self.prompt_tokens - self.cached_tokens) + self.completion_tokens


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Response:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    # Thinking-mode chain-of-thought (DeepSeek's `reasoning_content`,
    # OpenAI o-series reasoning summaries via the same field name on
    # OpenAI-compat backends). None for non-thinking models.
    reasoning_content: str | None = None
    raw: object = None  # opaque litellm ModelResponse for debugging


def complete(
    messages: list[dict],
    *,
    model: str,
    tools: list[dict] | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    custom_llm_provider: str | None = None,
    timeout: int | None = None,
    temperature: float | None = None,
) -> Response:
    """Call an LLM provider via litellm and return a normalized Response.

    ``messages`` is the OpenAI-style chat list. ``tools`` is the OpenAI-style
    JSON schema list (litellm converts to the native shape per provider).

    ``custom_llm_provider`` forces litellm onto a specific provider's
    client code path regardless of what the model name looks like.
    Important when talking to OpenAI-compatible third-party endpoints
    (opencode.ai/zen, Groq, Together, …) whose model IDs may contain
    a native-provider substring (``deepseek-*``, ``claude-*``) that
    litellm's model→provider heuristic would otherwise mis-route. Pass
    ``custom_llm_provider="openai"`` together with ``api_base`` to pin
    the OpenAI-compat code path.
    """
    import litellm

    kwargs: dict = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key
    if custom_llm_provider:
        kwargs["custom_llm_provider"] = custom_llm_provider
    if timeout is not None:
        kwargs["timeout"] = timeout
    if temperature is not None:
        kwargs["temperature"] = temperature

    completion = litellm.completion(**kwargs)
    choice = completion.choices[0]
    msg = choice.message

    text = msg.content or ""

    tool_calls: list[ToolCall] = []
    raw_calls = getattr(msg, "tool_calls", None) or []
    for call in raw_calls:
        fn = call.function
        arguments = fn.arguments
        if isinstance(arguments, str):
            import json
            try:
                arguments = json.loads(arguments) if arguments else {}
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        tool_calls.append(
            ToolCall(id=call.id, name=fn.name, arguments=arguments or {})
        )

    raw_usage = getattr(completion, "usage", None)
    usage = Usage()
    if raw_usage is not None:
        usage.prompt_tokens = getattr(raw_usage, "prompt_tokens", 0) or 0
        usage.completion_tokens = getattr(raw_usage, "completion_tokens", 0) or 0
        usage.total_tokens = getattr(raw_usage, "total_tokens", 0) or 0
        # litellm normalizes cache-read tokens here across providers
        # (DeepSeek prompt_cache_hit_tokens, Anthropic cache_read_input_tokens,
        # OpenAI cached_tokens). May be absent/None → 0.
        details = getattr(raw_usage, "prompt_tokens_details", None)
        usage.cached_tokens = (getattr(details, "cached_tokens", 0) or 0) if details else 0

    # Some thinking-mode providers (DeepSeek's v4-* models, certain
    # OpenAI-compat relays) expose intermediate chain-of-thought as
    # `reasoning_content` on the message object. The upstream API
    # requires it to be echoed back on multi-turn requests.
    reasoning_content = getattr(msg, "reasoning_content", None) or None

    return Response(
        text=text,
        tool_calls=tool_calls,
        usage=usage,
        reasoning_content=reasoning_content,
        raw=completion,
    )
