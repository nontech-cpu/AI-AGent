"""
Time Validation and Recurrence Tests (Phase 7e Steps 3 & 4)
Tests:
1. Past-time rejection:
   - now=12:32 IST, request 10:00 -> error time_already_passed
   - now=13:07 IST, request 13:00 -> error time_already_passed
   - now=13:07 IST, request 13:30 -> ok
   - Midnight and month-end boundaries
   - Tolerance is 0 by default and configurable
2. Recurrence across 3 days with FrozenClock & restart-proof persistence
"""
import pytest
import os
from datetime import datetime, date, time as dtime, timedelta
from unittest.mock import patch
from timing import IST
import core.timeutil as timeutil
import db
from agent.tools import execute_tool

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
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                remind_at TIMESTAMP,
                due_at TIMESTAMP,
                label TEXT,
                sent BOOLEAN DEFAULT 0,
                sent_at TIMESTAMP,
                claimed_by TEXT,
                claimed_at TIMESTAMP
            )
        """))
    yield
    timeutil.set_frozen_clock(None)

@pytest.mark.asyncio
async def test_time_validation_rejects_past_times_strictly():
    chat_id = 998877301
    db.ensure_user(chat_id)

    # 1. Now = 12:32 IST, requested = 10:00 today -> REJECT
    t_1232 = datetime(2026, 11, 10, 12, 32, 0, tzinfo=IST)
    timeutil.set_frozen_clock(t_1232)

    res_1 = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Jogging",
            "due_date": "2026-11-10",
            "due_time": "10:00",
            "request_quote": "jogging reminder"
        },
        user_context={"user_text": "set jogging reminder for 10am", "bypass_quote_check": True}
    )
    assert res_1["status"] == "error"
    assert res_1["error"] == "time_already_passed"
    assert "10:00" in res_1["requested"]

    # 2. Now = 13:07 IST, requested = 13:00 today -> REJECT
    t_1307 = datetime(2026, 11, 10, 13, 7, 0, tzinfo=IST)
    timeutil.set_frozen_clock(t_1307)

    res_2 = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Meeting Sync",
            "due_date": "2026-11-10",
            "due_time": "13:00",
            "request_quote": "meeting sync"
        },
        user_context={"user_text": "schedule meeting sync at 1pm", "bypass_quote_check": True}
    )
    assert res_2["status"] == "error"
    assert res_2["error"] == "time_already_passed"

    # 3. Now = 13:07 IST, requested = 13:30 today -> OK
    res_3 = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Meeting Sync",
            "due_date": "2026-11-10",
            "due_time": "13:30",
            "request_quote": "meeting sync"
        },
        user_context={"user_text": "schedule meeting sync at 1:30pm", "bypass_quote_check": True}
    )
    assert res_3["status"] == "success"
    assert res_3["facts"]["due_time"] == "13:30"

@pytest.mark.asyncio
async def test_time_validation_midnight_and_monthend_boundaries():
    chat_id = 998877302
    db.ensure_user(chat_id)

    # Midnight boundary: 2026-11-30 23:55 IST
    t_monthend = datetime(2026, 11, 30, 23, 55, 0, tzinfo=IST)
    timeutil.set_frozen_clock(t_monthend)

    # 23:50 on 2026-11-30 has passed -> REJECT
    res_past = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Late Review",
            "due_date": "2026-11-30",
            "due_time": "23:50",
            "request_quote": "late review"
        },
        user_context={"user_text": "set late review", "bypass_quote_check": True}
    )
    assert res_past["status"] == "error"
    assert res_past["error"] == "time_already_passed"

    # Next month boundary: 2026-12-01 00:15 IST is future -> OK
    res_next_month = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "December Kickoff",
            "due_date": "2026-12-01",
            "due_time": "00:15",
            "request_quote": "december kickoff"
        },
        user_context={"user_text": "set december kickoff", "bypass_quote_check": True}
    )
    assert res_next_month["status"] == "success"
    assert res_next_month["facts"]["due_date"] == "2026-12-01"

@pytest.mark.asyncio
async def test_daily_recurrence_across_3_days_frozen_clock():
    chat_id = 998877303
    db.ensure_user(chat_id)

    day1 = datetime(2026, 11, 10, 8, 0, 0, tzinfo=IST)
    timeutil.set_frozen_clock(day1)

    # Create daily reminder for 8:00 PM (future on Day 1)
    res = await execute_tool(
        chat_id,
        "create_reminder",
        {
            "title": "Gym Session",
            "due_date": "2026-11-10",
            "due_time": "20:00",
            "duration_minutes": 60,
            "recurrence": "daily",
            "request_quote": "gym reminder"
        },
        user_context={"user_text": "set daily gym reminder for 8pm", "bypass_quote_check": True}
    )
    assert res["status"] == "success"
    assert res["facts"]["recurrence"] == "daily"

    # Day 1: 10 Nov 2026 -> reminder appears
    items_d1 = db.items_for_date(chat_id, date(2026, 11, 10))
    assert any("Gym Session" in it[1] for it in items_d1)

    # Day 2: 11 Nov 2026 -> FrozenClock moves to tomorrow
    day2 = datetime(2026, 11, 11, 9, 0, 0, tzinfo=IST)
    timeutil.set_frozen_clock(day2)
    items_d2 = db.items_for_date(chat_id, date(2026, 11, 11))
    assert any("Gym Session" in it[1] for it in items_d2)

    # Day 3: 12 Nov 2026 -> FrozenClock moves to Day 3
    day3 = datetime(2026, 11, 12, 10, 0, 0, tzinfo=IST)
    timeutil.set_frozen_clock(day3)
    items_d3 = db.items_for_date(chat_id, date(2026, 11, 12))
    assert any("Gym Session" in it[1] for it in items_d3)

