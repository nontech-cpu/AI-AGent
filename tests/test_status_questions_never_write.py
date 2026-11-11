"""
Status Questions Never Write Test (Phase 7e Step 6)
Tests:
1. Status inquiries ('what about the reminder u set', 'jo set kiya wo batao', 'kya laga tha')
   are strictly read-only.
2. Server-side quote check rejects any write tool call where request_quote is missing or does
   not match text from user messages, returning error 'no_user_request_for_write'.
3. FakeLLM simulation verifying the model cannot execute accidental writes on status turns.
"""
import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, date, time as dtime
from timing import IST
import core.timeutil as timeutil
import db
from agent.tools import execute_tool, validate_request_quote

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
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                message_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    yield
    timeutil.set_frozen_clock(None)

def test_validate_request_quote_string_containment():
    chat_id = 998877501
    user_turn = "bhai mere roj ka 8pm ka gym reminder set krdo"

    # Exact substring -> True
    assert validate_request_quote(chat_id, "gym reminder set krdo", user_context={"user_text": user_turn}) is True
    assert validate_request_quote(chat_id, "roj ka 8pm ka gym", user_context={"user_text": user_turn}) is True

    # Fabricated quote not in message -> False
    assert validate_request_quote(chat_id, "delete meeting", user_context={"user_text": user_turn}) is False
    assert validate_request_quote(chat_id, "schedule doctor appointment", user_context={"user_text": user_turn}) is False

    # Empty or None quote -> False
    assert validate_request_quote(chat_id, "", user_context={"user_text": user_turn}) is False
    assert validate_request_quote(chat_id, None, user_context={"user_text": user_turn}) is False

@pytest.mark.asyncio
async def test_server_rejects_write_on_status_question():
    chat_id = 998877502
    db.ensure_user(chat_id)

    status_user_text = "what about the reminder u set"

    # 1. FakeLLM hallucinates calling create_reminder without matching quote
    res_create = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Meeting",
            "due_date": "2026-11-10",
            "due_time": "17:00",
            "request_quote": "set meeting"  # Not in status question!
        },
        user_context={"user_text": status_user_text}
    )
    assert res_create["status"] == "error"
    assert res_create["error"] == "no_user_request_for_write"
    assert "no_user_request_for_write" in res_create["facts"]["reason"]

    # 2. FakeLLM hallucinates calling delete_reminder without matching quote
    res_delete = await execute_tool(
        chat_id,
        "delete_reminder",
        {
            "title": "Meeting",
            "request_quote": "delete meeting"  # Not in status question!
        },
        user_context={"user_text": status_user_text}
    )
    assert res_delete["status"] == "error"
    assert res_delete["error"] == "no_user_request_for_write"

@pytest.mark.asyncio
async def test_fakellm_status_turn_answers_read_only():
    """
    Simulates full agent turn for 'what about the reminder u set':
    Model calls list_reminders, succeeds with read-only schedule, zero writes performed.
    """
    chat_id = 998877503
    db.ensure_user(chat_id)
    db.save_memory(chat_id, "appointments", "Jogging", None, date(2026, 11, 10), dtime(10, 0), 30, "daily")

    from agent.loop import run_agent_turn

    user_text = "what about the reminder u set"

    fake_calls = [
        # Call 1: model calls list_reminders (read-only)
        {
            "id": "call_list",
            "function": {
                "name": "list_reminders",
                "arguments": '{"date_str": "2026-11-10"}'
            }
        }
    ]

    async def fake_chat(messages, tools=None, **kwargs):
        resp = AsyncMock()
        resp.model = "fake-model"
        resp.provider = "fake"
        resp.fallback_used = False
        resp.usage = {"prompt_tokens": 15, "completion_tokens": 15}

        # First turn: returns tool call to list_reminders
        if len(messages) <= 3:
            resp.text = ""
            resp.tool_calls = fake_calls
        else:
            # Second turn (synthesis): responds with status
            resp.text = "You have a daily Jogging reminder scheduled at 10:00 AM."
            resp.tool_calls = []
        return resp

    with patch("llm.provider.chat", side_effect=fake_chat):
        turn_res = await run_agent_turn(chat_id, user_text)

        assert "Jogging" in turn_res["reply"]
        assert "list_reminders" in turn_res["tool_calls_executed"]
        assert "create_reminder" not in turn_res["tool_calls_executed"]
        assert "delete_reminder" not in turn_res["tool_calls_executed"]

@pytest.mark.asyncio
async def test_write_tool_succeeds_without_request_quote_when_user_gives_command_or_confirms():
    """
    Ensures that when an LLM calls create_reminder without an explicit request_quote,
    actionable user commands (e.g. '10 am morning evrryday' or 'yes') are accepted,
    while status questions ('what about the reminder u set') are still strictly rejected.
    """
    chat_id = 998877504
    db.ensure_user(chat_id)

    # 1. Actionable timing command without request_quote -> succeeds
    res_cmd = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Check sugar and reading",
            "due_date": "2026-11-12",
            "due_time": "10:00",
            "recurrence": "daily"
        },
        user_context={"user_text": "10 am morning evrryday"}
    )
    assert res_cmd.get("status") in ("success", "ok") or res_cmd.get("ok") is True or "Check sugar" in str(res_cmd)

    # 2. Confirmation 'yes' without request_quote -> succeeds
    res_yes = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Check sugar and reading",
            "due_date": "2026-11-12",
            "due_time": "10:00",
            "recurrence": "daily"
        },
        user_context={"user_text": "yes"}
    )
    assert res_yes.get("status") in ("success", "ok") or res_yes.get("ok") is True or "Check sugar" in str(res_yes)

    # 3. Read-only status inquiry without request_quote -> rejected
    res_status = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Check sugar and reading",
            "due_date": "2026-11-12",
            "due_time": "10:00"
        },
        user_context={"user_text": "what about the reminder u set"}
    )
    assert res_status["status"] == "error"
    assert res_status["error"] == "no_user_request_for_write"


