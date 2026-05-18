"""Python harness for the agentic build-failure repair loop.

Importing this package runs ``_ensure_tokenizers_importable()`` early
so that any subsequent ``import litellm`` (or our own ``llm`` module)
won't trip over DragonFly's broken ``py311-tokenizers`` shared object.
The stub is a no-op on platforms where the real tokenizers loads.
"""

from __future__ import annotations

import sys
import types


def _ensure_tokenizers_importable() -> None:
    """Pre-empt litellm's `from tokenizers import Tokenizer` chain.

    DragonFly's py311-tokenizers tokenizers.abi3.so ships with missing
    DT_NEEDED entries (libonig, esaxx); loading it fails at import
    time. litellm imports tokenizers transitively for local cost
    calculation, which we don't use (usage totals come from
    response.usage.total_tokens). Inject a no-op stub iff the real
    module can't be loaded.
    """
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
