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
async def test_meeting_update_duration_and_lunch_clash_detection():
    chat_id = 8877665544
    db.ensure_user(chat_id)
    today = date(2026, 11, 5)

    # Clean test items for today
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM memory_items WHERE due_date = :d"), {"d": today.isoformat()})

    # Turn 1: "bhy mere ek meeting reminder lgana 2pm ka"
    res1 = await execute_tool(chat_id, "create_reminder", {
        "title": "Meeting",
        "due_date": today.isoformat(),
        "due_time": "14:00"
    })
    assert res1["status"] == "success"
    assert res1["has_conflict"] is False
    assert res1["title"] == "Meeting"

    # Turn 2: "jo meeting h vo 1hour ke h" -> AI calls update_reminder
    res2 = await execute_tool(chat_id, "update_reminder", {
        "title": "Meeting",
        "duration_minutes": 60
    })
    print("\nRES2 DEBUG:", res2)
    assert res2["status"] == "success"
    assert res2["duration_minutes"] == 60
    assert res2["title"] == "Meeting"

    # Verify DB: Meeting exists, title is clean 'Meeting', duration_minutes is 60
    items = db.items_for_date(chat_id, today)
    assert len(items) == 1
    m_item = items[0]
    assert (m_item.get("title") if isinstance(m_item, dict) else m_item[1]) == "Meeting"
    dur = m_item.get("duration_minutes") if isinstance(m_item, dict) else m_item[5]
    assert int(dur) == 60

    # Turn 3: "bhy merko lucnh pr jana friend k sth reminder set krdena 2:30pm aaj"
    res3 = await execute_tool(chat_id, "create_reminder", {
        "title": "Lunch with friend",
        "due_date": today.isoformat(),
        "due_time": "14:30",
        "duration_minutes": 30
    })
    # MUST DETECT CLASH with Meeting (14:00 - 15:00)!
    assert res3["status"] == "conflict_detected"
    assert res3["has_conflict"] is True
    assert any("Meeting" in c for c in res3["conflicts"])
    print("\n[SUCCESS] Turn 3 correctly detected clash against 1-hour Meeting!")

    # Turn 4: User shifts time to 15:30 (with comfortable transition buffer)
    res4 = await execute_tool(chat_id, "create_reminder", {
        "title": "Lunch with friend",
        "due_date": today.isoformat(),
        "due_time": "15:30",
        "duration_minutes": 30
    })
    assert res4["status"] == "success"
    assert res4["has_conflict"] is False
    print("[SUCCESS] Turn 4 scheduled successfully at 15:30 after Meeting ended with 30m buffer!")

    # Turn 5: User insists on scheduling at 14:30 with force=True
    res5 = await execute_tool(chat_id, "create_reminder", {
        "title": "Quick call with client",
        "due_date": today.isoformat(),
        "due_time": "14:30",
        "duration_minutes": 15,
        "force": True
    })
    assert res5["status"] == "success"
    assert res5["has_conflict"] is True
    print("[SUCCESS] Turn 5 forced scheduling allowed with conflict warning!")
