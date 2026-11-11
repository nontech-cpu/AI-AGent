"""
llm package root.
Exports unified provider adapter as well as modern lightweight backward-compatibility utilities.
"""

import httpx

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

from timing import IST, parse_followup_time, _WORD_NUMBERS, is_time_ambiguous, ParsedFollowupTime

from .compat import (
    _chat_json,
    classify_yes_no_any_language,
    is_sensitive,
    SENSITIVE,
    is_explicit_calendar_action,
    _explicit_calendar_action,
    _meeting_title,
    extract,
    understand,
    guess_persona,
    answer_conversation_memory,
    INTENTS,
    ITEM_RULES,
)
