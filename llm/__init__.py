"""
llm package root.
Exports unified provider adapter as well as backward-compatible legacy utilities.
"""

from .provider import (
    chat,
    chat_sync,
    LLMResponse,
    ToolCall,
    UsageInfo,
    BaseLLMProvider,
    GeminiProvider,
    GroqProvider,
    OpenAIProvider,
    AnthropicProvider,
    FakeLLMProvider,
    get_fake_llm,
    reset_fake_llm,
    LLMError,
    ProviderTimeoutError,
    RateLimitedError,
    ToolExecutionError,
    DatabaseUnavailableError,
    ValidationError,
)

# Re-export all legacy symbols (including underscore-prefixed utilities like _WORD_NUMBERS)
try:
    from . import legacy as _legacy
    for _k, _v in _legacy.__dict__.items():
        if not _k.startswith("__"):
            globals()[_k] = _v
except ImportError:
    pass
