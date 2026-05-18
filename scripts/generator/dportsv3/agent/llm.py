"""litellm wrapper with a normalized response shape.

DragonFly's py311-tokenizers package ships a tokenizers.abi3.so with
missing DT_NEEDED entries (libonig, esaxx); loading it fails at import
time. litellm imports tokenizers transitively for its local cost
calculator. We don't need local token counting — usage totals come
from response.usage.total_tokens — so we pre-empt the import with a
small no-op stub iff the real tokenizers can't be loaded. The stub
exposes the surface litellm touches (Tokenizer.from_pretrained,
.encode); calls on the stub return empty results and we never see them
because we don't call cost_calculator.

When the platform's tokenizers works (Linux, macOS), the try block
succeeds and the stub never runs.
"""

from __future__ import annotations

import sys
import types
from dataclasses import dataclass, field


def _ensure_tokenizers_importable() -> None:
    try:
        import tokenizers  # noqa: F401
        return
    except ImportError:
        pass

    fake = types.ModuleType("tokenizers")

    class _Encoding:
        ids: list[int] = []
        tokens: list[str] = []

    class _Tokenizer:
        @classmethod
        def from_pretrained(cls, *args, **kwargs) -> "_Tokenizer":
            return cls()
        @classmethod
        def from_file(cls, *args, **kwargs) -> "_Tokenizer":
            return cls()
        def encode(self, *args, **kwargs) -> _Encoding:
            return _Encoding()

    fake.Tokenizer = _Tokenizer
    fake.Encoding = _Encoding
    sys.modules["tokenizers"] = fake


_ensure_tokenizers_importable()


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


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
    raw: object = None  # opaque litellm ModelResponse for debugging


def complete(
    messages: list[dict],
    *,
    model: str,
    tools: list[dict] | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    timeout: int | None = None,
    temperature: float | None = None,
) -> Response:
    """Call an LLM provider via litellm and return a normalized Response.

    ``messages`` is the OpenAI-style chat list. ``tools`` is the OpenAI-style
    JSON schema list (litellm converts to the native shape per provider).
    """
    import litellm

    kwargs: dict = {"model": model, "messages": messages}
    if tools:
        kwargs["tools"] = tools
    if api_base:
        kwargs["api_base"] = api_base
    if api_key:
        kwargs["api_key"] = api_key
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

    return Response(text=text, tool_calls=tool_calls, usage=usage, raw=completion)
