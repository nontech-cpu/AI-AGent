"""
Regression test for live incident resolution:
1. Two-way Google Calendar deletion: deleting a reminder cleans up the synced Google Calendar event and recurring series.
2. Status question protection: informational queries like 'ky tumn firecraker wala reminder htaa diya caldner se bhi?' are never hijacked by fake-success guard.
3. Language adaptability: explicit requests to stop English ('english mt kro baat') are routed cleanly via set_user_preference.
"""
import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, date, time as dtime
import db
from agent.tools import execute_tool
from connectors.guardrails import check_fake_success_guard

@pytest.fixture(autouse=True)
def setup_db():
    db._init_memory_items_status_column()
    db._init_memory_items_recurrence_column()
    db._init_memory_items_calendar_id_column()
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
    yield

@pytest.mark.asyncio
async def test_two_way_calendar_deletion_on_reminder_delete():
    chat_id = 998877991
    db.ensure_user(chat_id)
    
    # 1. Insert an active reminder with a synced recurring calendar event
    with db.engine.begin() as conn:
        res = conn.execute(db.text("""
            INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status, google_calendar_event_id)
            VALUES ((SELECT id FROM users WHERE telegram_chat_id = :cid), 'reminder', 'Firecracker reminder', '2026-11-10', '20:00', 'daily', 'active', '8kh5vv16ntosao1dgec43j3lho_20261110T200000Z')
        """), {"cid": chat_id})
        rem_id = res.lastrowid

    # Mock google_services delete_calendar_event and list_calendar_events
    deleted_cal_ids = []
    async def mock_delete_cal_event(cid, event_id):
        deleted_cal_ids.append(event_id)
        return True

    async def mock_list_cal_events(cid, query=None, max_results=10):
        if query and "firecracker" in query.lower():
            return [{"id": "8kh5vv16ntosao1dgec43j3lho_20261110T200000Z", "summary": "Firecracker reminder"}]
        return []

    with patch("google_services.delete_calendar_event", side_effect=mock_delete_cal_event), \
         patch("google_services.list_calendar_events", side_effect=mock_list_cal_events):
        
        # Execute confirmed deletion of reminder via pending action handler
        del_res = await execute_tool(
            chat_id,
            "delete_reminder",
            {
                "target_ids": [rem_id],
                "confirmed": True
            },
            user_context={"user_text": "confirm", "_from_pending_executor": True, "bypass_quote_check": True}
        )

        assert del_res["status"] == "success"
        assert del_res["facts"]["deleted_count"] == 1

        # Verify Google Calendar event was deleted (base recurring ID extracted)
        assert len(deleted_cal_ids) >= 1
        assert "8kh5vv16ntosao1dgec43j3lho" in deleted_cal_ids

        # Verify database row was soft-deleted
        with db.engine.connect() as conn:
            row = conn.execute(db.text("SELECT status FROM memory_items WHERE id = :id"), {"id": rem_id}).fetchone()
            assert row[0] == "deleted"


def test_status_question_not_intercepted_by_guardrail():
    # User asks if calendar was cleaned up:
    user_query = "ky tumn firecraker wala reminder htaa diya caldner se bhi?"
    draft_reply = "Haan, maine firecracker reminder calendar se remove kar diya hai."

    # Must pass without being flagged as fake success claim!
    ok, err = check_fake_success_guard(draft_reply, [], user_query=user_query)
    assert ok is True
    assert err is None


@pytest.mark.asyncio
async def test_explicit_language_switch_request():
    chat_id = 998877992
    db.ensure_user(chat_id)
    # Set to English initially
    db.set_user_preference(chat_id, "english", "concise")

    from agent.prompt import build_runtime_system_prompt
    prompt = await build_runtime_system_prompt(chat_id, user_text="english mt kro baat")

    # Prompt must contain guidance to call set_user_preference
    assert "set_user_preference" in prompt
    assert "english mt kro baat" in prompt

    # Model executes set_user_preference to switch to Hinglish
    switch_res = await execute_tool(
        chat_id,
        "set_user_preference",
        {
            "reply_language": "hinglish",
            "request_quote": "english mt kro baat"
        },
        user_context={"user_text": "english mt kro baat", "bypass_quote_check": True}
    )
    assert switch_res["status"] == "success"
    assert switch_res["facts"]["reply_language"] == "hinglish"

    # Verify updated in DB
    pref = db.get_user_preference(chat_id)
    assert pref["reply_language"] == "hinglish"


def test_multiturn_quote_validation_across_recent_turns():
    chat_id = 998877993
    db.ensure_user(chat_id)

    # Simulate Turn 1: user asked to set reminder
    db.log_chat_message(chat_id, "user", "set up a remidner for me at 2pm today")
    db.log_chat_message(chat_id, "assistant", "What would you like the reminder to be about?")

    from agent.tools import validate_request_quote

    # Turn 2 user text: "it should the be about meeting"
    turn2_ctx = {"user_text": "it should the be about meeting"}

    # Model provides quote from Turn 1
    assert validate_request_quote(chat_id, "set up a remidner for me at 2pm today", user_context=turn2_ctx) is True

    # Model provides quote from Turn 2
    assert validate_request_quote(chat_id, "it should the be about meeting", user_context=turn2_ctx) is True

    # Model provides combined multi-turn quote
    assert validate_request_quote(chat_id, "set up a remidner for me at 2pm today it should the be about meeting", user_context=turn2_ctx) is True

