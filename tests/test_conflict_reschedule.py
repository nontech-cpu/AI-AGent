import pytest
from datetime import date, time as dtime
from sqlalchemy import text
import db
from agent.tools import execute_tool

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
async def test_smart_reschedule_and_deduplication():
    chat_id = 998877661
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.set_persona(chat_id, "Job")
    today = date(2026, 10, 23)

    # 1. Create 6 PM movie
    res1 = await execute_tool(chat_id, "create_reminder", {
        "title": "Movie time",
        "due_date": today.isoformat(),
        "due_time": "18:00"
    })
    assert res1["status"] == "success"

    # 2. Create 6 PM Devender meeting -> flags conflict with movie
    res2 = await execute_tool(chat_id, "create_reminder", {
        "title": "Meeting with Devender",
        "due_date": today.isoformat(),
        "due_time": "18:00"
    })
    assert res2["status"] == "success"
    assert res2["has_conflict"] is True
    assert any("Movie time" in c for c in res2["conflicts"])

    # 3. Reschedule Devender meeting to 5 PM (17:00)
    res3 = await execute_tool(chat_id, "create_reminder", {
        "title": "Meeting with Devender",
        "due_date": today.isoformat(),
        "due_time": "17:00"
    })
    assert res3["status"] == "success"
    assert res3.get("rescheduled") is True
    # 5 PM and 6 PM do not clash (60 min diff), and it does NOT clash with itself
    assert res3["has_conflict"] is False
    assert len(res3["conflicts"]) == 0

    # 4. Check DB items: exactly 2 items (Movie at 18:00 and Devender at 17:00), no duplicates!
    items = db.items_for_date(chat_id, today)
    assert len(items) == 2
    times = {it[1]: it[3] for it in items}
    assert times["Movie time"] == "18:00:00" or times["Movie time"] == dtime(18, 0)
    assert times["Meeting with Devender"] == "17:00:00" or times["Meeting with Devender"] == dtime(17, 0)
