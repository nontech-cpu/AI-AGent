import pytest
from datetime import date, time as dtime
from sqlalchemy import text
import db
from agent.tools import execute_tool
from agent.verifier import deterministic_verify

@pytest.fixture(autouse=True)
def init_test_db():
    from db import engine
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id INTEGER UNIQUE NOT NULL,
                consented_at TIMESTAMP,
                state TEXT,
                state_data TEXT,
                persona TEXT,
                categories TEXT,
                language TEXT DEFAULT 'en',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                amount REAL,
                due_date DATE,
                due_time TIME,
                duration_minutes INTEGER DEFAULT 30,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                remind_at TIMESTAMP NOT NULL,
                label TEXT NOT NULL,
                sent INTEGER DEFAULT 0
            )
        """))
    yield

@pytest.mark.asyncio
async def test_full_scenario_clash_and_multi_meeting():
    from tests.harness.clock import FrozenClock
    with FrozenClock("2026-11-05T10:00:00+05:30"):
        chat_id = 99112233
        db.ensure_user(chat_id)
        db.give_consent(chat_id)
        today = date(2026, 11, 5)

        # 1. User creates 2pm meeting with 1-hour duration
        res1 = await execute_tool(chat_id, "create_reminder", {
            "title": "Meeting",
            "due_date": today.isoformat(),
            "due_time": "14:00",
            "duration_minutes": "60 mins"  # Tests _safe_int flexibility!
        }, user_context={"bypass_quote_check": True})
        assert res1["status"] == "success"
        assert res1["action"] == "create_reminder"
        assert res1["tool_name"] == "create_reminder"

        # Verify verifier does not intercept success
        ok, final_text, violations = deterministic_verify(
            draft_reply="Done bhai! 'Meeting' ka reminder set kar diya hai.",
            tool_results=[res1],
            memory_context={"formatted_block": ""},
            user_query="bhy mere ek meeting reminder lgana 2pm ka"
        )
        assert ok is True
        assert "Maine koshish ki lekin" not in final_text

        # 2. User tries to create 2:30pm lunch with friend -> CLASH DETECTED!
        res2 = await execute_tool(chat_id, "create_reminder", {
            "title": "Lunch with friend",
            "due_date": today.isoformat(),
            "due_time": "14:30",
            "duration_minutes": 30
        }, user_context={"bypass_quote_check": True})
        assert res2["status"] == "conflict_detected"
        assert res2["has_conflict"] is True
        assert any("Meeting" in c for c in res2["conflicts"])
        assert "Time Conflict" in res2["message"]

        # Verify verifier allows reporting conflict without flagging fake success
        ok, final_text, violations = deterministic_verify(
            draft_reply="Time Conflict: 02:30 PM par already Meeting scheduled hai. Kya aap confirm karna chahenge?",
            tool_results=[res2],
            memory_context={"formatted_block": ""},
            user_query="bhy merko lucnh pr jana friend k sth reminder set krdena 2:30pm aaj"
        )
        assert ok is True
        assert "Maine koshish ki lekin" not in final_text

        # 3. User creates a SEPARATE 4pm meeting -> MUST NOT OVERWRITE 2pm meeting!
        res3 = await execute_tool(chat_id, "create_reminder", {
            "title": "Meeting",
            "due_date": today.isoformat(),
            "due_time": "16:00",
            "duration_minutes": "30,"  # Tests string with comma!
        }, user_context={"user_text": "Bhy mere 4pm meeting h remisner set krdnea"})
        assert res3["status"] == "success"
        assert res3.get("rescheduled") is False  # Must NOT be marked as rescheduled/overwritten!

        # Verify both 2pm and 4pm meetings exist in DB
        items = db.items_for_date(chat_id, today)
        assert len(items) == 2, f"Expected 2 separate meetings, found {len(items)}"
        time_list = [str(it[3]) for it in items]
        assert any("14:00" in t for t in time_list), f"14:00 meeting missing: {time_list}"
        assert any("16:00" in t for t in time_list), f"16:00 meeting missing: {time_list}"

        # 4. User updates 4pm meeting duration to 1 hour
        res4 = await execute_tool(chat_id, "update_reminder", {
            "title": "Meeting",
            "due_time": "16:00",
            "duration_minutes": 60
        }, user_context={"user_text": "new wale meeting one hour ke"})
        assert res4["status"] == "success"
        assert res4["action"] == "update_reminder"
        assert res4["duration_minutes"] == 60

        # 5. User tries to set 4:30pm shopping reminder -> MUST CLASH with 4:00 PM - 5:00 PM Meeting!
        res5 = await execute_tool(chat_id, "create_reminder", {
            "title": "Shopping",
            "due_date": today.isoformat(),
            "due_time": "16:30",
            "duration_minutes": 30
        }, user_context={"user_text": "bhy mere 4:30pm shopping k kiye jana h iska b remind set krdena"})
        assert res5["status"] == "conflict_detected"
        assert res5["has_conflict"] is True
        assert any("Meeting" in c for c in res5["conflicts"])
        assert "16:30" in res5["due_time"] or "16:30" in str(res5)
