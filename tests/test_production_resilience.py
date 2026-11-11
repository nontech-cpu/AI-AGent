import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, date, time as dtime
import db
import google_services
from timing import IST
from tests.harness.clock import FrozenClock
from agent.loop import run_agent_turn
from agent.tools import execute_tool
from state.active_context import set_active_event, get_active_event, format_active_context

@pytest.mark.asyncio
async def test_groq_429_rate_limit_failover():
    chat_id = 998811
    call_attempts = []
    
    async def mock_post(*args, **kwargs):
        is_telegram = any("telegram.org" in str(a) for a in args) or any("telegram.org" in str(v) for v in kwargs.values())
        if is_telegram:
            class MockTg:
                status_code = 200
                def json(self): return {"ok": True}
            return MockTg()
        payload = kwargs.get("json", {})
        model_used = payload.get("model")
        call_attempts.append(model_used)
        if len(call_attempts) == 1:
            # Primary model returns 429 Too Many Requests
            class Mock429:
                status_code = 429
                text = "Rate limit exceeded: TPM limit hit"
            return Mock429()
        # Fallback model succeeds
        class Mock200:
            status_code = 200
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": "Haan bhai, bilkul ready hu!",
                            "tool_calls": None
                        }
                    }],
                    "usage": {"total_tokens": 50}
                }
        return Mock200()

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        res = await run_agent_turn(chat_id, "kya haal hai")
        assert res is not None
        assert res["reply"] == "Haan bhai, bilkul ready hu!"
        # Verify it attempted multiple models
        assert len(call_attempts) >= 2
        assert call_attempts[1] != call_attempts[0]
        assert any(m in call_attempts for m in ("gemini-3.5-flash", "gemini-3.1-flash-lite-preview", "openai/gpt-oss-120b", "openai/gpt-oss-20b", "qwen/qwen3.8-27b", "llama-3.3-70b-versatile"))

@pytest.mark.asyncio
async def test_user_level_timezone_persistence():
    chat_id = 887766
    # 1. Set user timezone in DB before connecting Google
    db.set_user_timezone(chat_id, "America/New_York")
    saved = db.get_user_timezone(chat_id)
    assert saved == "America/New_York"
    
    # Clear memory cache
    google_services._USER_TZ_CACHE.pop(chat_id, None)
    
    # 2. Verify google_services resolves to this DB timezone when Google is not connected
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock, return_value=(None, None)):
        tz = await google_services.get_user_calendar_timezone(chat_id)
        assert tz == "America/New_York"

    # 3. Test set_user_timezone tool execution
    tool_res = await execute_tool(chat_id, "set_user_timezone", {"timezone": "Europe/London"})
    assert tool_res["status"] == "success"
    assert db.get_user_timezone(chat_id) == "Europe/London"

@pytest.mark.asyncio
async def test_active_event_context_resolution():
    chat_id = 776655
    with FrozenClock("2026-11-04T12:00:00", tz=IST):
        # Simulate user setting a reminder
        set_active_event(chat_id, {
            "title": "Quarterly Budget Planning",
            "due_date": "2026-11-04",
            "due_time": "15:00",
            "type": "meeting"
        })
        
        # Verify active context reflects it
        ctx_str = format_active_context(chat_id)
        assert "Quarterly Budget Planning" in ctx_str
        
        # When user says "calendar me laga do" and create_meeting is invoked with generic title
        with patch("main.detect_meeting_conflicts", new_callable=AsyncMock, return_value=([], [])), \
             patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock, return_value=(True, "test@gmail.com")):
            res = await execute_tool(chat_id, "create_meeting", {
                "title": "Meeting",
                "start_iso": "2026-11-04T15:00:00"
            }, user_context={"user_text": "calendar me laga do"})
            assert res["status"] == "needs_confirmation"
            # Verify title was contextually resolved to the active event!
            assert res["title"] == "Quarterly Budget Planning"

@pytest.mark.asyncio
async def test_strict_grounding_rule_in_prompt():
    from agent.prompt import RUNTIME_SYSTEM_PROMPT_TEMPLATE
    prompt_low = RUNTIME_SYSTEM_PROMPT_TEMPLATE.lower()
    assert "anti-hallucination" in prompt_low or "grounding" in prompt_low
    assert "never output fake confirmation" in prompt_low or "without executing" in prompt_low
