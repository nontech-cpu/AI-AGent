"""
Unit and Integration Tests for Proactive Autonomous Morning Briefing Engine.
Covers:
1. Implicit city inference (detect_user_city)
2. Dynamic early event detection (flight, train, meeting before 8:30 AM)
3. Boarding checklist & ticket details generation
4. Weather inclusion (only when city known) vs cleanly omitting when unknown
5. Database deduplication per calendar date
"""
import pytest
from datetime import datetime, date, time as dtime, timedelta
from unittest.mock import patch, MagicMock

from timing import IST
import db
from morning_briefing import (
    detect_user_city,
    detect_early_morning_event,
    build_morning_briefing_message,
    process_user_morning_briefing,
)


def test_detect_user_city():
    test_chat_id = 998877

    # Case 1: No facts saved -> returns None
    with patch("db.get_user_facts", return_value=[]), \
         patch("morning_briefing.get_active_ticket", return_value=None):
        assert detect_user_city(test_chat_id) is None

    # Case 2: Natural expression "I live in Delhi"
    facts_delhi = [{"fact": "User is a software developer and lives in Delhi currently."}]
    with patch("db.get_user_facts", return_value=facts_delhi), \
         patch("morning_briefing.get_active_ticket", return_value=None):
        assert detect_user_city(test_chat_id) == "Delhi"

    # Case 3: Label "City: Mumbai"
    facts_mumbai = [{"fact": "User profile | City: Mumbai | Role: Architect"}]
    with patch("db.get_user_facts", return_value=facts_mumbai), \
         patch("morning_briefing.get_active_ticket", return_value=None):
        assert detect_user_city(test_chat_id) == "Mumbai"

    # Case 4: Route origin e.g. "Route: JODHPUR JN (JU) to ABU ROAD (ABR)"
    facts_route = [{"fact": "Booking/Ticket: IRCTC | PNR: 2524281551 | Route: JODHPUR JN (JU) to ABU ROAD (ABR)"}]
    with patch("db.get_user_facts", return_value=facts_route), \
         patch("morning_briefing.get_active_ticket", return_value=None):
        assert detect_user_city(test_chat_id) == "Jodhpur"

    # Case 5: Stop word like "flat" or "home" -> skipped
    facts_stop = [{"fact": "User is staying in flat."}]
    with patch("db.get_user_facts", return_value=facts_stop), \
         patch("morning_briefing.get_active_ticket", return_value=None):
        assert detect_user_city(test_chat_id) is None


def test_detect_early_flight():
    test_chat_id = 998877
    target_d = date(2026, 10, 27)

    # User has an early morning flight at 06:00 AM IST
    mock_ticket = {
        "carrier": "Air India (AI-802)",
        "route": "Delhi to Bangalore",
        "pnr": "AI789456",
        "datetime_str": "27 Oct 2026, 06:00 AM",
        "seats": "12A",
        "summary": "Flight to Bangalore at 6 AM"
    }

    with patch("morning_briefing.get_active_ticket", return_value=mock_ticket), \
         patch("db.get_user_facts", return_value=[]):
        ev = detect_early_morning_event(test_chat_id, target_d)
        assert ev is not None
        assert ev["is_travel"] is True
        assert ev["is_flight"] is True
        assert ev["kind"] == "flight"
        # 6:00 AM flight -> wake up at 04:00 AM (2 hours before)
        assert ev["event_time"].hour == 6
        assert ev["trigger_time"].hour == 4


def test_detect_no_early_event_for_afternoon_meeting():
    test_chat_id = 998877
    target_d = date(2026, 10, 27)

    unified_sched = {
        "calendar_events": [
            {
                "summary": "Team Sync",
                "start": {"dateTime": "2026-10-27T14:30:00+05:30"}
            }
        ],
        "reminders": [
            {"title": "Evening Gym", "due_time": dtime(18, 0)}
        ]
    }

    with patch("morning_briefing.get_active_ticket", return_value=None), \
         patch("db.get_user_facts", return_value=[]):
        ev = detect_early_morning_event(test_chat_id, target_d, unified_sched=unified_sched)
        assert ev is None


@pytest.mark.asyncio
async def test_briefing_message_with_early_flight_and_city():
    test_chat_id = 998877
    user_info = {"first_name": "Neeraj", "username": "neeraj_dev"}
    target_d = date(2026, 10, 27)

    early_event = {
        "is_travel": True,
        "is_flight": True,
        "kind": "flight",
        "title": "Air India AI-802",
        "event_time": datetime(2026, 10, 27, 6, 0, tzinfo=IST),
        "trigger_time": datetime(2026, 10, 27, 4, 0, tzinfo=IST),
        "ticket_data": {
            "carrier": "Air India AI-802",
            "route": "Delhi to Mumbai",
            "pnr": "AI998822",
            "seats": "14B"
        }
    }

    mock_weather = {
        "found": True,
        "temperature": 26.5,
        "condition": "Clear sky",
        "min_temp": 19.0,
        "max_temp": 30.0,
        "aqi": 120,
        "uv_index": 5.0
    }

    with patch("morning_briefing.detect_user_city", return_value="Delhi"), \
         patch("morning_briefing.get_weather", return_value=mock_weather):
        msg, trigger_kind = await build_morning_briefing_message(
            test_chat_id, user_info, target_d, early_event=early_event
        )

        assert trigger_kind == "early_flight"
        # Contains early wake-up prompt and checklist
        assert "Good Morning Neeraj" in msg
        assert "Air India AI-802" in msg
        assert "AI998822" in msg
        assert "Govt Photo ID / Aadhaar Card" in msg
        assert "Boarding Pass / E-Ticket" in msg
        # Contains Weather since city is known
        assert "Delhi Ka Mausam" in msg
        assert "26.5°C" in msg
        assert "**AQI:** 120" in msg
        assert "**UV Index:** 5.0" in msg


@pytest.mark.asyncio
async def test_briefing_message_without_city_omits_weather():
    test_chat_id = 998877
    user_info = {"first_name": "Rahul", "username": "rahul_k"}
    target_d = date(2026, 10, 27)

    unified_sched = {
        "calendar_events": [
            {
                "summary": "Sprint Planning",
                "start": {"dateTime": "2026-10-27T11:00:00+05:30"}
            }
        ],
        "reminders": [
            {"title": "Pay Electricity Bill", "due_time": dtime(17, 0)}
        ],
        "commitments": [
            {"name": "Ananya", "commitment": "Module complete karke dena hai"}
        ]
    }

    # City unknown -> weather must be omitted, no asking questions
    with patch("morning_briefing.detect_user_city", return_value=None):
        msg, trigger_kind = await build_morning_briefing_message(
            test_chat_id, user_info, target_d, early_event=None, unified_sched=unified_sched
        )

        assert trigger_kind == "standard_8am"
        assert "Good Morning Rahul" in msg
        assert "Sprint Planning" in msg
        assert "Pay Electricity Bill" in msg
        assert "Ananya" in msg
        # Weather block must NOT be present
        assert "Mausam" not in msg
        assert "Weather" not in msg


def test_database_deduplication():
    test_chat_id = 771122
    d_str = "2026-10-27"

    # Initially not sent
    assert db.is_morning_briefing_sent(test_chat_id, d_str) is False

    # Record briefing sent
    db.record_morning_briefing_sent(test_chat_id, d_str, trigger_kind="standard_8am")

    # Now must be True
    assert db.is_morning_briefing_sent(test_chat_id, d_str) is True
