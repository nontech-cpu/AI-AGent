import pytest
from datetime import datetime, date, time, timedelta, timezone
from unittest.mock import patch, AsyncMock
from zoneinfo import ZoneInfo
from fastapi.testclient import TestClient

from timing import IST
from main import app, detect_meeting_conflicts, handle_mail
import db
from agent.tools import execute_tool
from api_v1 import get_free_weather, get_morning_briefing


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def init_test_db():
    from db import engine
    from sqlalchemy import text
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
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'en'"))
        except Exception:
            pass
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS google_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                email TEXT NOT NULL,
                refresh_token_enc TEXT NOT NULL,
                scopes TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_device_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                phone TEXT,
                device_id TEXT NOT NULL,
                google_email TEXT,
                last_login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, device_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                fact TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS smart_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS smart_list_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                quantity TEXT,
                checked INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    yield


# --------------------------------------------------------------------------
# Pillar 1 Test: Phone-First Device Adoption & Account Continuity
# --------------------------------------------------------------------------
def test_phone_first_device_adoption():
    chat_id = 998877001
    db.ensure_user(chat_id)
    with db.engine.connect() as conn:
        uid = int(conn.execute(db.text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).first()[0])

    # Record initial session on an iPhone
    db.record_device_session(uid, phone="+919876543210", device_id="iphone_18_pro_max", google_email="neeraj@gmail.com")

    # Login later from a Laptop with same phone number
    check = db.check_device_authorization(uid, device_id="laptop_macbook")
    assert check["valid"] is True
    assert check["google_email"] == "neeraj@gmail.com"
    assert check["reason"] in ("valid", "adopted")


# --------------------------------------------------------------------------
# Pillar 2 Test: Inbound Email Ticket Parsing & Proactive Prompt
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_inbound_ticket_parsing_and_prompt():
    chat_id = 998877002
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    mock_mail = {
        "sender": "neeraj_user@gmail.com",
        "subject": "IndiGo Flight Booking Confirmation: DEL to GOA",
        "body": "Dear Passenger, your flight 6E-204 from Delhi to Goa departs on Tuesday at 7:00 AM. PNR: XYZ123.",
        "authenticated": True
    }

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append(text)

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_state", return_value=(None, None)), \
         patch("main.get_link_by_email", return_value=(chat_id, "mock_code", True)):
        await handle_mail(mock_mail)

    assert len(sent_messages) == 1
    msg = sent_messages[0]
    assert "Flight Ticket receive ho gayi hai" in msg
    assert "DEL to GOA" in msg
    assert "is ticket ko memory se hata do" in msg

    # Verify ticket was stored in facts
    facts = db.get_user_facts(chat_id)
    assert any("DEL to GOA" in f["fact"] for f in facts)


# --------------------------------------------------------------------------
# Pillar 2 Test: Memory Deletion for Tickets ('is ticket ko memory se hata do')
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_note_or_fact_tool():
    chat_id = 998877003
    db.ensure_user(chat_id)

    # Save a flight ticket in facts
    db.save_user_fact(chat_id, "Booking/Ticket: IndiGo DEL to GOA Tuesday 7:00 AM PNR XYZ123", category="booking")
    facts_before = db.get_user_facts(chat_id)
    assert len(facts_before) >= 1

    # Execute delete_note_or_fact tool
    result = await execute_tool(chat_id, "delete_note_or_fact", {"query": "ticket"})
    assert result["status"] == "success"
    assert result["deleted"] is True

    # Check that ticket is removed from active facts
    facts_after = db.get_user_facts(chat_id)
    assert not any("XYZ123" in f["fact"] for f in facts_after)


# --------------------------------------------------------------------------
# Pillar 3 Test: Non-Clashing Meeting Boundaries (5:00-5:30 and 5:30-6:00)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_non_clashing_meeting_boundaries():
    chat_id = 998877004

    # Existing meeting: Monday 5:00 PM to 5:30 PM
    mock_sched = {
        "calendar_events": [
            {
                "summary": "Meeting with Devender",
                "start": {"dateTime": "2026-10-26T17:00:00+05:30"},
                "end": {"dateTime": "2026-10-26T17:30:00+05:30"}
            }
        ],
        "reminders": [],
        "commitments": [],
        "tasks": []
    }

    with patch("main.get_unified_schedule", return_value=mock_sched):
        # 1. New meeting at 5:00 PM (30 min) -> Clashes!
        clashes_overlap, _ = await detect_meeting_conflicts(chat_id, "2026-10-26T17:00:00+05:30", 30)
        assert len(clashes_overlap) == 1
        assert "Devender" in clashes_overlap[0]

        # 2. New meeting at 5:30 PM (starts exactly when Devender's ends) -> NO CLASH!
        clashes_adjacent, _ = await detect_meeting_conflicts(chat_id, "2026-10-26T17:30:00+05:30", 30)
        assert len(clashes_adjacent) == 0


# --------------------------------------------------------------------------
# Pillar 6 Test: Adaptive Morning Briefing (Early 7 AM flight -> 6 AM Briefing + UV)
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_adaptive_morning_briefing_with_early_wakeup_and_uv():
    chat_id = 998877005
    db.ensure_user(chat_id)

    # Mock user Google Calendar having a 7:00 AM Goa flight
    mock_events = [
        {
            "summary": "Goa Flight Departure 6E-204",
            "start": {"dateTime": f"{date.today().isoformat()}T07:00:00+05:30"},
            "end": {"dateTime": f"{date.today().isoformat()}T09:30:00+05:30"}
        }
    ]

    mock_weather = {
        "city": "Delhi",
        "temperature": 24.5,
        "max_temp": 32.0,
        "min_temp": 20.0,
        "uv_index_max": 7.5,
        "uv_peak_hours": "11:30 AM - 02:30 PM",
        "uv_alert": "High (Sunscreen recommended during midday)"
    }

    with patch("api_v1.get_free_weather", return_value=mock_weather), \
         patch("api_v1.get_db_user_id", return_value=123), \
         patch("api_v1.get_google_token", return_value={"email": "user@gmail.com"}), \
         patch("api_v1.list_calendar_events", return_value=mock_events):

        briefing = await get_morning_briefing(city="Delhi", chat_id=chat_id)

        # Assert briefing detected the 7:00 AM flight and triggers 1 hour prior (06:00 AM)
        assert briefing["is_early_wake_up"] is True
        assert briefing["target_send_time"] == "06:00 AM"

        # Assert travel packing checklist was included
        assert "Aadhaar Card" in briefing["travel_checklist"]
        assert "Boarding Pass / Flight Ticket" in briefing["travel_checklist"]

        # Assert UV information is present
        assert briefing["weather"]["uv_index_max"] == 7.5
        assert "High" in briefing["weather"]["uv_alert"]


# --------------------------------------------------------------------------
# Pillar 7 Test: Trip & Smart Packing Checklist Tool
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_packing_list_tool():
    chat_id = 998877006
    db.ensure_user(chat_id)

    res = await execute_tool(
        chat_id,
        "create_packing_list",
        {
            "trip_title": "Goa Trip",
            "items": ["Passport", "Aadhaar Card", "Photo", "Swimwear", "Charger"],
            "trip_date": "2026-10-27"
        }
    )
    assert res["status"] == "success"
    assert res["items_count"] == 5
    assert "Goa Trip Packing" in res["list_name"]

    # Check smart list persisted
    lists = db.get_smart_lists(chat_id)
    assert any("Goa Trip Packing" in l["name"] for l in lists)


# --------------------------------------------------------------------------
# Pillar 5 Test: GitHub File Update Tool
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_update_github_file_tool():
    chat_id = 998877007
    db.ensure_user(chat_id)

    mock_resp = (True, "File 'README.md' in 'test-user/ml-project' successfully updated!", "https://github.com/test-user/ml-project/commit/123456")

    with patch("integrations.update_github_file", return_value=mock_resp), \
         patch("db.get_user_integration", return_value="ghp_mock_token"):

        res = await execute_tool(
            chat_id,
            "update_github_file",
            {
                "repo": "ml-project",
                "path": "README.md",
                "content": "# Machine Learning Project\n\nUpdated by PeculiarAI."
            }
        )
        assert res["status"] == "success"
        assert "README.md" in res["message"]
        assert "https://github.com" in res["commit_url"]
