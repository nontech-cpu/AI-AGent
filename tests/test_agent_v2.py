import pytest
import os
from unittest.mock import patch, AsyncMock
from datetime import date, datetime
from zoneinfo import ZoneInfo

from agent.tools import execute_tool, TOOL_DEFINITIONS
from agent.loop import run_agent_turn

IST = ZoneInfo("Asia/Kolkata")

@pytest.mark.asyncio
async def test_tool_get_contact_fuzzy():
    chat_id = 998811
    mock_contacts = [
        {
            "id": 5,
            "name": "Ananya Roy",
            "company": "Elevate Ventures",
            "role_title": "Founding Partner",
            "email": "ananya.roy@elevatevc.com",
            "phone": "+91 91234 56789",
            "commitments": "Ananya ko module bhejna hai Tuesday ko",
            "context_notes": "Angel investor"
        }
    ]

    with patch("db.get_contact_memories", return_value=mock_contacts):
        # 1. Exact match
        res1 = await execute_tool(chat_id, "get_contact", {"name_or_alias": "Ananya"})
        assert res1["found"] is True
        assert res1["name"] == "Ananya Roy"
        assert "module bhejna hai" in res1["commitments"]

        # 2. Fast-typed Hinglish typo "annaya"
        res2 = await execute_tool(chat_id, "get_contact", {"name_or_alias": "annaya"})
        assert res2["found"] is True
        assert res2["name"] == "Ananya Roy"

        # 3. Typo "ananay"
        res3 = await execute_tool(chat_id, "get_contact", {"name_or_alias": "ananay"})
        assert res3["found"] is True
        assert res3["name"] == "Ananya Roy"

@pytest.mark.asyncio
async def test_tool_check_availability_detects_conflict():
    chat_id = 998811
    target_d = "2026-10-22"

    mock_sched = {
        "calendar_events": [
            {
                "summary": "Meeting",
                "start": {"dateTime": "2026-10-22T12:30:00Z"},  # 6:00 PM IST
                "end": {"dateTime": "2026-10-22T13:00:00Z"},
                "meet_url": "https://meet.google.com/test-meet"
            }
        ],
        "reminders": [],
        "commitments": []
    }

    with patch("main.get_unified_schedule", return_value=mock_sched):
        # User wants to go to movie at 5 PM (17:00) lasting 180 mins (till 8 PM)
        res = await execute_tool(chat_id, "check_availability", {
            "start_time": "17:00",
            "target_date": target_d,
            "duration_minutes": 180,
            "activity_type": "movie"
        })

        assert res["is_free"] is False
        assert len(res["conflicts"]) > 0
        assert "6:00 PM" in res["conflicts"][0]

@pytest.mark.asyncio
async def test_tool_create_meeting_two_phase_confirmation():
    chat_id = 998811
    start_iso = "2026-10-27T17:00:00+05:30"

    with patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "test@gmail.com")), \
         patch("main.detect_meeting_conflicts", return_value=([], [])):

        res = await execute_tool(chat_id, "create_meeting", {
            "title": "Meeting",
            "start_iso": start_iso,
            "duration_minutes": 30
        })

        # Must return needs_confirmation, NEVER directly claim created without user confirm
        assert res["status"] == "needs_confirmation"
        assert res["action"] == "create_meeting"
        assert res["start_iso"] == start_iso

@pytest.mark.asyncio
async def test_agent_loop_executes_tool_and_answers():
    chat_id = 998811
    mock_contacts = [
        {"id": 5, "name": "Ananya Roy", "company": "Elevate Ventures", "commitments": "Ananya ko module bhejna hai Tuesday ko"}
    ]

    # Mock Groq tool-calling roundtrip:
    # 1. Model asks to call `get_contact` with `name_or_alias="annaya"`
    groq_resp_1 = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_123",
                    "type": "function",
                    "function": {"name": "get_contact", "arguments": '{"name_or_alias": "annaya"}'}
                }]
            }
        }],
        "usage": {"total_tokens": 120}
    }

    # 2. Model receives tool output and gives grounded final answer
    groq_resp_2 = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Bhai, Ananya Roy (Elevate Ventures) ko aapko Tuesday ko module bhejna tha!",
                "tool_calls": None
            }
        }],
        "usage": {"total_tokens": 80}
    }

    call_count = 0
    async def mock_post(url, headers, json):
        nonlocal call_count
        call_count += 1
        class MockResponse:
            status_code = 200
            def json(self):
                return groq_resp_1 if call_count == 1 else groq_resp_2
        return MockResponse()

    with patch("httpx.AsyncClient.post", side_effect=mock_post), \
         patch("db.get_contact_memories", return_value=mock_contacts), \
         patch("db.get_recent_chat_history", return_value=[]), \
         patch("db.log_chat_message"):

        res = await run_agent_turn(chat_id, "bhy annaya ko mujhe ky bhjna th")

        assert "Ananya Roy" in res["reply"]
        assert "module bhejna" in res["reply"]
        assert "get_contact" in res["tool_calls_executed"]
        assert call_count == 2


@pytest.mark.asyncio
async def test_tool_save_contact_and_lookup_typo():
    chat_id = 998822
    # 1. Save contact Ananya Sharma with phone and email
    res_save = await execute_tool(chat_id, "save_contact", {
        "name": "Ananya Sharma",
        "phone": "+91 98765 43210",
        "email": "ananya.sharma@example.com",
        "role": "Lead UI/UX Designer"
    })
    assert res_save["status"] == "success"
    assert res_save["name"] == "Ananya Sharma"

    # 2. Look up contact with typo 'annaya'
    res_lookup = await execute_tool(chat_id, "get_contact", {"name_or_alias": "annaya"})
    assert res_lookup["found"] is True
    assert res_lookup["name"] == "Ananya Sharma"
    assert res_lookup["phone"] == "+91 98765 43210"
    assert res_lookup["email"] == "ananya.sharma@example.com"


@pytest.mark.asyncio
async def test_create_meeting_fails_gracefully_when_google_not_connected():
    chat_id = 771122
    with patch("main.get_valid_google_connection_by_chat_id", return_value=(False, None)):
        res = await execute_tool(chat_id, "create_meeting", {
            "title": "Strategy Sync",
            "start_iso": "2026-10-27T20:00:00+05:30",
            "duration_minutes": 30
        })
        assert res.get("status") == "error"
        assert res.get("error_type") == "not_connected"
        assert "Google Calendar" in res.get("message", "")
        assert "not connected" in res.get("message", "").lower() or "connected nahi hai" in res.get("message", "").lower()


@pytest.mark.asyncio
async def test_connect_integration_google_returns_oauth_url():
    chat_id = 771122
    with patch("google_auth.build_auth_url", return_value="https://test.com/oauth/google"):
        res = await execute_tool(chat_id, "connect_integration", {
            "service": "google",
            "value": "connect"
        })
        assert res.get("status") == "success"
        assert "https://test.com/oauth/google" in res.get("auth_url", "")
        assert "https://test.com/oauth/google" in res.get("message", "")


@pytest.mark.asyncio
async def test_standalone_email_provides_google_oauth_url():
    chat_id = 771122
    with patch("main.get_valid_google_connection_by_chat_id", return_value=(False, None)), \
         patch("google_auth.build_auth_url", return_value="https://test.com/oauth/google"), \
         patch("db.log_chat_message"):
        res = await run_agent_turn(chat_id, "neeravmishrarealme1@gmail.com")
        assert "https://test.com/oauth/google" in res["reply"]
        assert "connect_integration" in res["tool_calls_executed"]

