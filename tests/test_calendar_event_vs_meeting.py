"""
Calendar Event vs Google Meet Meeting Tests (Phase 7e Step 5)
Tests:
1. create_calendar_event sets is_video_meeting=False, has_video_link=False, no attendees needed.
2. create_meeting sets is_video_meeting=True, has_video_link=True.
3. Multi-turn decline with replacement intent ("meeting nahi, sirf reminder calendar pe"):
   Clears pending action and processes remainder instruction in the same turn without dead-ending.
"""
import pytest
from datetime import datetime, date, time as dtime
from unittest.mock import patch, AsyncMock
from timing import IST
import core.timeutil as timeutil
import db
from agent.tools import execute_tool
from state.pending import save_pending_action, get_pending_action

@pytest.fixture(autouse=True)
def setup_db():
    from db import engine, text
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id INTEGER UNIQUE NOT NULL,
                consented_at TIMESTAMP,
                state TEXT,
                state_data TEXT,
                persona TEXT,
                language TEXT DEFAULT 'en',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT,
                title TEXT,
                amount REAL,
                due_date DATE,
                due_time TIME,
                duration_minutes INTEGER DEFAULT 30,
                recurrence TEXT DEFAULT 'none',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    yield
    timeutil.set_frozen_clock(None)

@pytest.mark.asyncio
async def test_create_calendar_event_has_no_video_link_or_attendees():
    chat_id = 998877401
    db.ensure_user(chat_id)

    future_now = datetime(2026, 11, 10, 8, 0, 0, tzinfo=IST)
    timeutil.set_frozen_clock(future_now)

    with patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "user@gmail.com")), \
         patch("main.detect_meeting_conflicts", return_value=([], [])):

        res = await execute_tool(
            chat_id,
            "create_calendar_event",
            {
                "title": "Jogging",
                "start_iso": "2026-11-10T10:00:00+05:30",
                "duration_minutes": 30,
                "request_quote": "joggin wala reminder calender pr b lga do"
            },
            user_context={"user_text": "bhy joggin wala reminder calender pr b lga do", "bypass_quote_check": True}
        )

        assert res["status"] == "needs_confirmation"
        assert res["action"] == "create_calendar_event"
        assert res["title"] == "Jogging"
        assert res["is_video_meeting"] is False
        assert res["has_video_link"] is False
        assert "attendees" not in res or not res["attendees"]

@pytest.mark.asyncio
async def test_create_meeting_has_video_link():
    chat_id = 998877402
    db.ensure_user(chat_id)

    future_now = datetime(2026, 11, 10, 8, 0, 0, tzinfo=IST)
    timeutil.set_frozen_clock(future_now)

    with patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "user@gmail.com")), \
         patch("main.detect_meeting_conflicts", return_value=([], [])):

        res = await execute_tool(
            chat_id,
            "create_meeting",
            {
                "title": "Client Sync",
                "start_iso": "2026-11-10T15:00:00+05:30",
                "duration_minutes": 45,
                "attendees": ["client@acme.com"],
                "request_quote": "meeting"
            },
            user_context={"user_text": "create client sync meeting at 3pm", "bypass_quote_check": True}
        )

        assert res["status"] == "needs_confirmation"
        assert res["action"] == "create_meeting"
        assert res["is_video_meeting"] is True
        assert res["has_video_link"] is True
        assert "client@acme.com" in res["attendees"]

@pytest.mark.asyncio
async def test_decline_with_replacement_intent_does_not_dead_end():
    """
    User scenario:
    Bot asks: "Please confirm — create this Google Calendar meeting with a Google Meet link?"
    User replies: "meeting nahi banani sirf jogging ka reminder lagana hai"
    System MUST:
    1. Detect decline/cancel of the video meeting.
    2. NOT return a dead-end 'Action cancel kar diya gaya hai'.
    3. Cancel pending action and allow remainder to be processed.
    """
    chat_id = 998877403
    db.ensure_user(chat_id)

    save_pending_action(chat_id, "create_meeting", {
        "kind": "create_meeting",
        "title": "Jogging",
        "start_iso": "2026-11-10T10:00:00+05:30",
        "duration_minutes": 30
    })

    from agent.loop import run_agent_turn
    from state.pending import get_pending_action

    user_reply = "meeting nahi banani sirf jogging ka reminder lagana hai"

    with patch("state.pending.classify_confirmation_intent_with_llm", return_value=("cancel", {})):
        # Run agent turn
        with patch("llm.provider.chat") as mock_chat:
            mock_resp = AsyncMock()
            mock_resp.text = "Understood, cancelled the video meeting. Jogging reminder is preserved."
            mock_resp.tool_calls = []
            mock_resp.model = "mock-model"
            mock_resp.provider = "mock"
            mock_resp.fallback_used = False
            mock_resp.usage = {"prompt_tokens": 10, "completion_tokens": 10}
            mock_chat.return_value = mock_resp

            turn_res = await run_agent_turn(chat_id, user_reply)

            # Assert pending meeting is cancelled
            assert get_pending_action(chat_id) is None
            # Assert reply is NOT the hardcoded dead-end string
            assert "Action cancel kar diya gaya hai" not in turn_res["reply"]
            assert len(turn_res["reply"]) > 0
