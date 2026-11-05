"""
tests/test_phase2_llm_adapter.py - Phase 2 Verification Suite.
Asserts:
1. Provider 429 rate limit backoff and fallback to next provider.
2. Provider timeout recovery and failover.
3. Malformed JSON & XML tool calling normalization.
4. Empty response handling without crashes.
5. Tool call with invalid/bad arguments returns structured error feedback.
6. Typed exceptions mapped to concise user-facing error messages with trace_id.
7. FakeLLM deterministic test harness.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from llm.provider import (
    chat,
    LLMResponse,
    BaseLLMProvider,
    GeminiProvider,
    GroqProvider,
    FakeLLMProvider,
    get_fake_llm,
    reset_fake_llm,
    LLMError,
    ProviderTimeoutError,
    RateLimitedError,
    ToolExecutionError,
    DatabaseUnavailableError,
    ValidationError,
    parse_xml_tool_calls
)
import main
import db


# ---------------------------------------------------------------------------
# Test 1: Provider 429 Rate Limit Backoff & Fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_429_backoff_and_fallback():
    """
    Asserts that when primary provider (Gemini) returns 429, the adapter
    retries once with backoff, then falls back to secondary provider (Groq),
    recording fallback_used=True and the fallback reason.
    """
    gemini_resp_429 = MagicMock()
    gemini_resp_429.status_code = 429
    gemini_resp_429.text = "Quota exceeded for quota metric 'Queries' and limit 'Queries per minute'"
    gemini_resp_429.headers = {"retry-after": "0.1"}

    groq_resp_200 = MagicMock()
    groq_resp_200.status_code = 200
    groq_resp_200.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": "Groq fallback successful response."}
        }],
        "usage": {"prompt_tokens": 15, "completion_tokens": 8, "total_tokens": 23}
    }

    call_count = {"gemini": 0, "groq": 0}

    async def mock_post(url, *args, **kwargs):
        if "generativelanguage.googleapis.com" in str(url):
            call_count["gemini"] += 1
            return gemini_resp_429
        elif "api.groq.com" in str(url):
            call_count["groq"] += 1
            return groq_resp_200
        return MagicMock(status_code=500, text="Unknown endpoint")

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "fake_gemini_key",
        "GROQ_API_KEY": "fake_groq_key",
        "LLM_PROVIDER_CHAIN": "gemini,groq",
        "USE_FAKE_LLM": "0"
    }), patch("httpx.AsyncClient.post", side_effect=mock_post):
        
        resp = await chat(
            messages=[{"role": "user", "content": "Hello!"}],
            temperature=0.2,
            timeout=5.0,
            max_retries_per_provider=1
        )

        assert resp.provider == "Groq"
        assert resp.text == "Groq fallback successful response."
        assert resp.fallback_used is True
        assert "Gemini" in (resp.fallback_reason or "")
        assert "429" in (resp.fallback_reason or "")
        # Primary was attempted and retried, then Groq succeeded
        assert call_count["gemini"] >= 2
        assert call_count["groq"] >= 1


# ---------------------------------------------------------------------------
# Test 2: Provider Timeout Recovery and Failover
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provider_timeout_failover():
    """
    Asserts that when primary provider times out, the adapter catches the
    timeout and cleanly fails over to the next candidate provider.
    """
    groq_resp_200 = MagicMock()
    groq_resp_200.status_code = 200
    groq_resp_200.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": "Recovered from timeout via Groq."}
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 6, "total_tokens": 16}
    }

    async def mock_post(url, *args, **kwargs):
        if "generativelanguage.googleapis.com" in str(url):
            raise httpx.TimeoutException("Read timed out after 5.0s")
        elif "api.groq.com" in str(url):
            return groq_resp_200
        return MagicMock(status_code=500)

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "fake_gemini_key",
        "GROQ_API_KEY": "fake_groq_key",
        "LLM_PROVIDER_CHAIN": "gemini,groq",
        "USE_FAKE_LLM": "0"
    }), patch("httpx.AsyncClient.post", side_effect=mock_post):

        resp = await chat(
            messages=[{"role": "user", "content": "Check my schedule"}],
            timeout=2.0,
            max_retries_per_provider=0
        )

        assert resp.provider == "Groq"
        assert resp.text == "Recovered from timeout via Groq."
        assert resp.fallback_used is True
        assert "timed out" in (resp.fallback_reason or "").lower()


# ---------------------------------------------------------------------------
# Test 3: Malformed JSON and XML Tool Calling Parser
# ---------------------------------------------------------------------------

def test_xml_tool_call_extraction():
    """
    Asserts that XML-style function calls emitted by smaller fallback models
    are parsed into structured OpenAI-compatible tool calls.
    """
    sample_text = (
        "I will set a reminder for your meeting.\n"
        "<function=create_reminder>{\"title\": \"Dentist appointment\", \"date_str\": \"2026-11-06\", \"time_str\": \"14:00\"}</function>\n"
        "Please confirm if the time works."
    )
    calls = parse_xml_tool_calls(sample_text)
    assert len(calls) == 1
    assert calls[0]["type"] == "function"
    assert calls[0]["function"]["name"] == "create_reminder"
    import json
    args = json.loads(calls[0]["function"]["arguments"])
    assert args["title"] == "Dentist appointment"
    assert args["date_str"] == "2026-11-06"
    assert args["time_str"] == "14:00"


# ---------------------------------------------------------------------------
# Test 4: Empty Response Handling
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_empty_response_normalization():
    """
    Asserts that if a model returns empty text and empty tool calls, the adapter
    returns a normalized LLMResponse without crashing.
    """
    fake = get_fake_llm()
    fake.queue_response(text="", tool_calls=[])

    with patch.dict("os.environ", {"USE_FAKE_LLM": "1"}):
        resp = await chat(messages=[{"role": "user", "content": "hello"}])
        assert resp.text == ""
        assert resp.tool_calls == []
        assert isinstance(resp.usage, dict)
        assert resp.provider == "FakeLLM"

    reset_fake_llm()


# ---------------------------------------------------------------------------
# Test 5: Tool Call with Bad/Invalid Arguments
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_invalid_arguments_structured_error():
    """
    Asserts that invoking a tool with invalid arguments (missing required parameters)
    returns a structured error dictionary rather than raising unhandled exceptions.
    """
    from agent.tools import execute_tool
    chat_id = 88776655

    # Attempt to create reminder without title
    res = await execute_tool(chat_id, "create_reminder", {"date_str": "2026-11-05"})
    assert res["status"] == "error"
    assert "error" in res
    assert "title" in str(res).lower() or "missing" in str(res).lower() or "invalid" in str(res).lower()


# ---------------------------------------------------------------------------
# Test 6: Typed Exceptions Mapped to User-Facing Messages with Trace ID
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_typed_exceptions_produce_clean_user_reply_with_trace_id():
    """
    Asserts that each typed exception produces an accurate user message with [Ref: trace_...],
    offers a retry, and never sends the legacy 'network fluctuation' canned string.
    """
    chat_id = 99881122
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.set_persona(chat_id, "job")
    db.set_state(chat_id, "active")

    test_cases = [
        (ProviderTimeoutError("Gemini timed out"), "timed out"),
        (RateLimitedError("Groq 429"), "high demand"),
        (ToolExecutionError("Failed to dispatch", tool_name="send_email"), "action"),
        (DatabaseUnavailableError("Postgres unreachable"), "Database connection"),
        (ValidationError("Invalid ISO date", field_name="date_str"), "Invalid parameters"),
    ]

    for exc_instance, expected_keyword in test_cases:
        with patch("agent.loop.run_agent_turn", side_effect=exc_instance), \
             patch("main.say", new_callable=AsyncMock) as mock_say, \
             patch.dict("os.environ", {"AGENT_CORE": "v2"}):

            await main.handle_user_text(chat_id, "Schedule my meeting tomorrow at 4 PM")

            mock_say.assert_called_once()
            reply = mock_say.call_args[0][1]

            # 1. No canned network fluctuation lie
            assert "network mein thoda fluctuation" not in reply

            # 2. Contains trace ID reference
            assert "Ref: trace_" in reply

            # 3. Contains accurate error keyword
            assert expected_keyword.lower() in reply.lower()

            # 4. Offers user retry
            assert "retry" in reply.lower() or "try" in reply.lower()


# ---------------------------------------------------------------------------
# Test 7: FakeLLM Test Harness Operation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fake_llm_harness_poisoned_tool_call():
    """
    Asserts that the FakeLLM provider can script poisoned tool calls for
    grounding and date validation tests.
    """
    fake = get_fake_llm()
    fake.queue_poisoned_tool_call(
        tool_name="create_reminder",
        arguments={"title": "Poisoned Task", "date_str": "2024-05-12", "time_str": "10:00"}
    )

    with patch.dict("os.environ", {"USE_FAKE_LLM": "1"}):
        resp = await chat(messages=[{"role": "user", "content": "Set reminder"}])
        assert len(resp.tool_calls) == 1
        tc = resp.tool_calls[0]
        assert tc["function"]["name"] == "create_reminder"
        import json
        args = json.loads(tc["function"]["arguments"])
        assert args["date_str"] == "2024-05-12"

    reset_fake_llm()

