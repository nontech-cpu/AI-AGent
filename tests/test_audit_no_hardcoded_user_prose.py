"""
Audit Test: No Hardcoded User-Facing Prose in Tools (Phase 7e Step 1)
Validates that tools return purely structured data: {status, facts, next_options}
and do not return hardcoded user-facing conversational prose.
"""
import pytest
from datetime import datetime, date, time as dtime
from unittest.mock import patch, AsyncMock
from timing import IST
import db
from agent.tools import execute_tool, TOOL_DEFINITIONS

FORBIDDEN_PHRASES = [
    "done bhai",
    "telegram reminder set",
    "action cancel kar diya",
    "ka reminder",
    "ke liye set kar diya",
    "successfully delete kar diya",
    "naam ka reminder nahi mila",
    "aapko kaunsa reminder delete karna hai",
    "filhaal aapka koi pending reminder nahi hai"
]

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

@pytest.mark.asyncio
async def test_create_reminder_returns_structured_data_no_prose():
    chat_id = 998877101
    db.ensure_user(chat_id)
    
    future_now = datetime(2026, 11, 10, 8, 0, tzinfo=IST)
    with patch("core.timeutil.now", return_value=future_now), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(False, None)):
        
        res = await execute_tool(
            chat_id,
            "create_reminder",
            {
                "title": "Gym Session",
                "due_date": "2026-11-10",
                "due_time": "20:00",
                "duration_minutes": 60,
                "request_quote": "gym session"
            },
            user_context={"user_text": "set gym session at 8pm", "bypass_quote_check": True}
        )
        assert res.get("status") == "success"
        assert "facts" in res
        facts = res["facts"]
        assert facts["title"] == "Gym Session"
        assert facts["due_time"] == "20:00"
        assert facts["duration_minutes"] == 60
        assert "next_options" in res
        
        # Verify no conversational prose returned in result
        raw_dump = str(res).lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in raw_dump, f"Found forbidden prose '{phrase}' in tool output"

@pytest.mark.asyncio
async def test_update_reminder_returns_structured_data_no_prose():
    chat_id = 998877102
    db.ensure_user(chat_id)
    item_id = db.save_memory(chat_id, "appointments", "Doctor Visit", None, date(2026, 11, 10), dtime(14, 0), 30)

    future_now = datetime(2026, 11, 10, 8, 0, tzinfo=IST)
    with patch("core.timeutil.now", return_value=future_now):
        res = await execute_tool(
            chat_id,
            "update_reminder",
            {
                "reminder_id": item_id,
                "title": "Doctor Visit",
                "due_time": "16:00",
                "request_quote": "doctor visit"
            },
            user_context={"user_text": "shift doctor visit to 4pm", "bypass_quote_check": True}
        )
        assert res.get("status") == "success"
        assert "facts" in res
        assert res["facts"]["due_time"] == "16:00"
        assert "next_options" in res

        raw_dump = str(res).lower()
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in raw_dump, f"Found forbidden prose '{phrase}' in tool output"

@pytest.mark.asyncio
async def test_delete_reminder_returns_structured_data_no_prose():
    chat_id = 998877103
    db.ensure_user(chat_id)
    item_id = db.save_memory(chat_id, "appointments", "Yoga Class", None, date(2026, 11, 10), dtime(9, 0), 30)

    # 1. Unconfirmed request returns needs_confirmation (pure structured data, zero prose)
    res_unconf = await execute_tool(
        chat_id,
        "delete_reminder",
        {
            "reminder_id": item_id,
            "title": "Yoga Class",
            "request_quote": "delete yoga class"
        },
        user_context={"user_text": "delete yoga class", "bypass_quote_check": True}
    )
    assert res_unconf.get("status") == "needs_confirmation"
    assert "facts" in res_unconf
    assert "next_options" in res_unconf

    # 2. Confirmed request returns success with soft-deleted records
    res = await execute_tool(
        chat_id,
        "delete_reminder",
        {
            "target_ids": [item_id],
            "confirmed": True,
            "request_quote": "delete yoga class"
        },
        user_context={"user_text": "delete yoga class", "bypass_quote_check": True}
    )
    assert res.get("status") == "success"
    assert "facts" in res
    assert "Yoga Class" in res["facts"]["deleted_titles"]
    assert "next_options" in res

    raw_dump = str(res).lower()
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in raw_dump, f"Found forbidden prose '{phrase}' in tool output"

@pytest.mark.asyncio
async def test_delete_reminder_missing_target_returns_structured_error():
    chat_id = 998877104
    db.ensure_user(chat_id)

    res = await execute_tool(
        chat_id,
        "delete_reminder",
        {},
        user_context={"user_text": "delete reminder", "bypass_quote_check": True}
    )
    assert res.get("status") == "error"
    assert res.get("error") == "missing_delete_target"
    assert "facts" in res
    assert "next_options" in res

    raw_dump = str(res).lower()
    for phrase in FORBIDDEN_PHRASES:
        assert phrase not in raw_dump, f"Found forbidden prose '{phrase}' in tool output"

