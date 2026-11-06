"""
tests/test_phase7c_staging_and_typing.py

Unit tests for Phase 7c review requirements:
1. Staging isolation startup guard: refuses to start if ENV=staging and DATABASE_URL matches PROD_DB_FINGERPRINT.
2. FORCE_PROVIDER_FAIL in STAGING_MODE=1: simulates provider failure and triggers failover.
3. Typing indicator: fires after 2.0s, is cancelled immediately on fast turns, and adds zero LLM calls.
"""

import asyncio
import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from main import validate_startup_environment, handle_active
from llm.provider import chat, GeminiProvider, GroqProvider, LLMResponse


def test_staging_guard_refuses_prod_database():
    """Startup guard must raise RuntimeError if ENV=staging and DATABASE_URL contains PROD_DB_FINGERPRINT."""
    env_overrides = {
        "ENV": "staging",
        "DATABASE_URL": "postgresql://neerav:secret@ep-delicate-lake-12345.us-east-2.aws.neon.tech/neondb",
        "PROD_DB_FINGERPRINT": "ep-delicate-lake-12345",
        "GEMINI_API_KEY": "test_key",
    }
    with patch.dict(os.environ, env_overrides, clear=False):
        with pytest.raises(RuntimeError, match="CRITICAL STAGING GUARD"):
            validate_startup_environment()


def test_staging_guard_allows_separate_database():
    """Startup guard allows startup if staging database has a distinct fingerprint."""
    env_overrides = {
        "ENV": "staging",
        "DATABASE_URL": "postgresql://neerav:secret@ep-staging-lake-99999.us-east-2.aws.neon.tech/staging_db",
        "PROD_DB_FINGERPRINT": "ep-delicate-lake-12345",
        "GEMINI_API_KEY": "test_key",
    }
    with patch.dict(os.environ, env_overrides, clear=False):
        # Must not raise
        validate_startup_environment()


@pytest.mark.asyncio
async def test_force_provider_fail_in_staging_mode():
    """STAGING_MODE=1 with FORCE_PROVIDER_FAIL=gemini must force failover to fallback provider."""
    mock_gemini_resp = LLMResponse(text="Gemini reply", provider="Gemini", model="gemini-3.1-flash-lite-preview")
    mock_groq_resp = LLMResponse(text="Groq fallback reply", provider="Groq", model="openai/gpt-oss-120b")

    with patch.dict(os.environ, {"STAGING_MODE": "1", "FORCE_PROVIDER_FAIL": "gemini"}, clear=False):
        with patch.object(GeminiProvider, "is_configured", return_value=True), \
             patch.object(GroqProvider, "is_configured", return_value=True), \
             patch.object(GeminiProvider, "chat", new_callable=AsyncMock) as mock_gemini_chat, \
             patch.object(GroqProvider, "chat", new_callable=AsyncMock) as mock_groq_chat:
            
            mock_gemini_chat.return_value = mock_gemini_resp
            mock_groq_chat.return_value = mock_groq_resp

            resp = await chat(messages=[{"role": "user", "content": "hello"}])
            
            # Gemini must NOT have been called due to simulated failure
            mock_gemini_chat.assert_not_called()
            # Groq fallback must have been called
            mock_groq_chat.assert_called_once()
            assert resp.provider == "Groq"
            assert resp.text == "Groq fallback reply"
            assert resp.fallback_used is True


@pytest.mark.asyncio
async def test_typing_indicator_cancelled_on_fast_turn():
    """Fast turn (<2.0s) must cancel typing task before it sends any chatAction, adding zero LLM calls."""
    chat_id = 999888111
    with patch("main.tg", new_callable=AsyncMock) as mock_tg, \
         patch("main.say", new_callable=AsyncMock) as mock_say, \
         patch("main.has_consent", return_value=True), \
         patch("agent.loop.run_agent_turn", new_callable=AsyncMock) as mock_turn, \
         patch("llm.provider.chat", new_callable=AsyncMock) as mock_llm_chat:
        
        # Fast turn taking 0.05 seconds
        async def fast_turn(*args, **kwargs):
            await asyncio.sleep(0.05)
            return {"reply": "Fast response!"}
        
        mock_turn.side_effect = fast_turn
        
        await handle_active(chat_id, "hello fast")
        
        # Assert say sent the reply
        mock_say.assert_called_once_with(chat_id, "Fast response!")
        
        # Assert tg was NEVER called with sendChatAction typing
        typing_calls = [c for c in mock_tg.call_args_list if c[0][0] == "sendChatAction"]
        assert len(typing_calls) == 0, f"Expected 0 typing calls on fast turn, got {len(typing_calls)}"
        # Assert typing indicator did not add any LLM calls
        mock_llm_chat.assert_not_called()


@pytest.mark.asyncio
async def test_typing_indicator_fires_on_slow_turn():
    """Slow turn (>2.0s) must trigger sendChatAction 'typing' after 2.0s."""
    chat_id = 999888222
    with patch("main.tg", new_callable=AsyncMock) as mock_tg, \
         patch("main.say", new_callable=AsyncMock) as mock_say, \
         patch("main.has_consent", return_value=True), \
         patch("agent.loop.run_agent_turn", new_callable=AsyncMock) as mock_turn:
        
        # Slow turn taking 2.2 seconds
        async def slow_turn(*args, **kwargs):
            await asyncio.sleep(2.2)
            return {"reply": "Slow response!"}
        
        mock_turn.side_effect = slow_turn
        
        await handle_active(chat_id, "hello slow")
        
        # Assert tg was called with sendChatAction typing
        typing_calls = [c for c in mock_tg.call_args_list if c[0][0] == "sendChatAction"]
        assert len(typing_calls) >= 1, f"Expected at least 1 typing call on slow turn, got {len(typing_calls)}"
        assert typing_calls[0][0][1] == {"chat_id": chat_id, "action": "typing"}
