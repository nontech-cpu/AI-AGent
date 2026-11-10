"""
Phase 7e Live Telegram Regression Test Suite (Step 8)
Replays each exact turn from the 10 Nov 2026 live Telegram bug report using
FakeLLM + FrozenClock and asserts end-to-end correctness.
"""
import pytest
from datetime import datetime, date, time as dtime
from unittest.mock import patch, AsyncMock
from timing import IST
import core.timeutil as timeutil
import db
from agent.tools import execute_tool
from agent.loop import run_agent_turn
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
                language TEXT DEFAULT 'hinglish',
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
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                message_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT,
                fact TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    yield
    timeutil.set_frozen_clock(None)

@pytest.mark.asyncio
async def test_turn_a_past_time_10am_at_1232pm_rejected():
    """Turn a: 'mere daily reminder set krdo joggina ka 10am ka' sent at 12:32 PM."""
    chat_id = 998877601
    db.ensure_user(chat_id)

    now_1232 = datetime(2026, 11, 10, 12, 32, 0, tzinfo=IST)
    timeutil.set_frozen_clock(now_1232)

    user_text = "mere daily reminder set krdo joggina ka 10am ka"

    res = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Jogging",
            "due_date": "2026-11-10",
            "due_time": "10:00",
            "recurrence": "daily",
            "request_quote": "daily reminder"
        },
        user_context={"user_text": user_text}
    )

    assert res["status"] == "error"
    assert res["error"] == "time_already_passed"
    assert "10:00" in res["requested"]

@pytest.mark.asyncio
async def test_turn_b_gym_8pm_visible_on_kal():
    """Turn b: 'bhy mere toh roj ka 8pm ka gym ka reminder set krdo ... 1hour ka session' sent at 1:07 PM."""
    chat_id = 998877602
    db.ensure_user(chat_id)

    now_1307 = datetime(2026, 11, 10, 13, 7, 0, tzinfo=IST)
    timeutil.set_frozen_clock(now_1307)

    user_text = "bhy mere toh roj ka 8pm ka gym ka reminder set krdo gym ka 1hour ka session rhyga roj ka"

    res = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Gym session",
            "due_date": "2026-11-10",
            "due_time": "20:00",
            "duration_minutes": 60,
            "recurrence": "daily",
            "request_quote": "roj ka 8pm ka gym ka reminder set krdo"
        },
        user_context={"user_text": user_text}
    )

    assert res["status"] == "success"
    assert res["facts"]["recurrence"] == "daily"
    assert res["facts"]["duration_minutes"] == 60

    # Query for tomorrow (11 Nov 2026)
    kal_items = db.items_for_date(chat_id, date(2026, 11, 11))
    assert any("Gym session" in it[1] for it in kal_items)

@pytest.mark.asyncio
async def test_turn_c_add_reminder_to_calendar_no_meet():
    """Turn c: 'joggin wala reminder calender pr b lga do' -> create_calendar_event without Meet."""
    chat_id = 998877603
    db.ensure_user(chat_id)

    # Seed existing jogging reminder
    db.save_memory(chat_id, "appointments", "Jogging", None, date(2026, 11, 11), dtime(10, 0), 30, "daily")

    future_now = datetime(2026, 11, 10, 8, 0, 0, tzinfo=IST)
    timeutil.set_frozen_clock(future_now)

    user_text = "bhy joggin wala reminder calender pr b lga do"

    with patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "user@gmail.com")), \
         patch("main.detect_meeting_conflicts", return_value=([], [])):

        res = await execute_tool(
            chat_id,
            "create_calendar_event",
            {
                "title": "Jogging",
                "start_iso": "2026-11-11T10:00:00+05:30",
                "duration_minutes": 30,
                "request_quote": "joggin wala reminder calender pr b lga do"
            },
            user_context={"user_text": user_text}
        )

        assert res["status"] == "needs_confirmation"
        assert res["action"] == "create_calendar_event"
        assert res["is_video_meeting"] is False
        assert res["has_video_link"] is False

@pytest.mark.asyncio
async def test_turn_d_decline_meeting_with_reminder_correction():
    """Turn d: 'meetinv nhi toh banani h sirf jogging ka reminder lgana h'."""
    chat_id = 998877604
    db.ensure_user(chat_id)

    save_pending_action(chat_id, "create_meeting", {
        "kind": "create_meeting",
        "title": "Jogging",
        "start_iso": "2026-11-10T10:00:00+05:30",
        "duration_minutes": 30
    })

    user_text = "meetinv nhi toh banani h sirf jogging ka reminder lgana h"

    with patch("state.pending.classify_confirmation_intent_with_llm", return_value=("cancel", {})):
        with patch("llm.provider.chat") as mock_chat:
            mock_resp = AsyncMock()
            mock_resp.text = "Video meeting cancel kar di hai. Jogging ka reminder active hai."
            mock_resp.tool_calls = []
            mock_resp.model = "mock"
            mock_resp.provider = "mock"
            mock_resp.fallback_used = False
            mock_resp.usage = {"prompt_tokens": 10, "completion_tokens": 10}
            mock_chat.return_value = mock_resp

            turn = await run_agent_turn(chat_id, user_text)

            assert get_pending_action(chat_id) is None
            assert "Action cancel kar diya gaya hai" not in turn["reply"]

@pytest.mark.asyncio
async def test_turn_e_english_preference_prevents_bollywood_hinglish_regression():
    """Turn e: 'now from the conversation talk to me in english only' locks English for Bollywood question."""
    chat_id = 998877605
    db.ensure_user(chat_id)

    # 1. Set English preference
    res = await execute_tool(
        chat_id,
        "set_user_preference",
        {
            "reply_language": "english",
            "tone": "concise",
            "request_quote": "talk to me in english only"
        },
        user_context={"user_text": "now from the conversation talk to me in english only"}
    )
    assert res["status"] == "success"

    # 2. Subsequent Bollywood query in Hinglish/English
    from agent.prompt import build_runtime_system_prompt
    bollywood_query = "like what the movies from bolywood currenlty in threater?"
    prompt = await build_runtime_system_prompt(chat_id, user_text=bollywood_query)

    assert "- LOCKED REPLY LANGUAGE: ENGLISH" in prompt
    assert "MANDATORY: You must generate 100% of your response text in ENGLISH" in prompt

@pytest.mark.asyncio
async def test_turn_f_status_query_is_read_only():
    """Turn f: 'what about the reminder u set' is strictly read-only."""
    chat_id = 998877606
    db.ensure_user(chat_id)

    status_text = "what about the reminder u set"

    # Write tool attempt without valid quote is blocked
    res = await execute_tool(
        chat_id,
        "delete_reminder",
        {
            "title": "Meeting",
            "request_quote": "delete meeting"
        },
        user_context={"user_text": status_text}
    )
    assert res["status"] == "error"
    assert res["error"] == "no_user_request_for_write"

