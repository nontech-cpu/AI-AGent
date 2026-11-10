import pytest
import asyncio
from datetime import datetime, date, time as dtime, timedelta
from unittest.mock import patch, AsyncMock
from timing import IST
import db
import utilities
import google_services
from agent.tools import execute_tool

TEST_CHAT_ID = 99881122
LINKED_CHAT_ID = 99881123


@pytest.fixture(autouse=True)
def setup_gold_standard_fixtures():
    # Setup linked identities for testing
    db.link_user_identities(TEST_CHAT_ID, LINKED_CHAT_ID, "same_user_iphone")
    yield


# ==============================================================================
# PILLAR 1: SEMANTIC LOCATION STATE MACHINE (Zero-Regex, Dynamic Global Context)
# ==============================================================================

def test_semantic_location_state_crud_and_cross_device_sync():
    """Verifies that user location state is stored cleanly without regex and syncs across devices."""
    # 1. Set location for primary device
    ok = db.set_user_location_state(
        TEST_CHAT_ID,
        current_city="Jodhpur",
        region="Rajasthan",
        country="India",
        travel_status="home",
        source="conversational_context"
    )
    assert ok is True

    # 2. Check primary device
    loc1 = db.get_user_location_state(TEST_CHAT_ID)
    assert loc1 is not None
    assert loc1["current_city"] == "Jodhpur"
    assert loc1["region"] == "Rajasthan"
    assert loc1["country"] == "India"
    assert loc1["travel_status"] == "home"

    # 3. Check linked secondary device receives identical state
    loc2 = db.get_user_location_state(LINKED_CHAT_ID)
    assert loc2 is not None
    assert loc2["current_city"] == "Jodhpur"
    assert loc2["region"] == "Rajasthan"


def test_dynamic_travel_update_to_udaipur():
    """Verifies that when a user travels, the location state seamlessly updates to the visiting city."""
    db.set_user_location_state(
        TEST_CHAT_ID,
        current_city="Udaipur",
        region="Rajasthan",
        country="India",
        travel_status="visiting",
        source="conversation_travel"
    )
    loc = db.get_user_location_state(TEST_CHAT_ID)
    assert loc["current_city"] == "Udaipur"
    assert loc["travel_status"] == "visiting"


@pytest.mark.asyncio
async def test_weather_geocoding_never_escapes_to_russia():
    """
    CRITICAL REGRESSION TEST:
    Ensures that 'Is Jodhpur', 'Jodhpur', or messy location strings resolve strictly to Jodhpur, India,
    and NEVER to 'Is, Russia' or other unrelated countries.
    """
    # Test messy Hinglish 'Is Jodhpur'
    w_is = await utilities.get_weather("Is Jodhpur")
    assert w_is["found"] is True
    assert "Jodhpur" in w_is["location"]
    assert "India" in w_is["location"]
    assert w_is["temperature"] > 10.0  # Sunny Indian desert weather, not negative Russia freezing cold

    # Test clean 'Jodhpur'
    w_jod = await utilities.get_weather("Jodhpur")
    assert w_jod["found"] is True
    assert "Jodhpur" in w_jod["location"]
    assert "India" in w_jod["location"]

    # Test international city 'Dubai'
    w_dub = await utilities.get_weather("Dubai")
    assert w_dub["found"] is True
    assert "Dubai" in w_dub["location"]
    assert "United Arab Emirates" in w_dub["location"]


# ==============================================================================
# PILLAR 2: ORTHOGONAL WORKSPACE PRIMITIVES (OpenAI MCP Standard)
# ==============================================================================

@pytest.mark.asyncio
async def test_google_services_create_calendar_event_without_meet_link():
    """Verifies that create_calendar_event creates a standard event without video links when is_video_meeting=False."""
    fake_access = ("test_access_token", "neerav@test.com")
    fake_api_res = {
        "id": "cal_evt_12345",
        "summary": "Meeting",
        "start": {"dateTime": "2026-11-10T17:00:00+05:30"},
        "end": {"dateTime": "2026-11-10T18:00:00+05:30"},
        "htmlLink": "https://calendar.google.com/event?eid=123"
    }

    with patch("google_services.access_token_for_chat", return_value=fake_access), \
         patch("google_services._request", return_value=fake_api_res) as mock_req:
        res = await google_services.create_calendar_event(
            TEST_CHAT_ID,
            summary="Meeting",
            start_time="2026-11-10T17:00:00+05:30",
            duration_minutes=60,
            is_video_meeting=False
        )
        assert res["id"] == "cal_evt_12345"
        assert res["meet_url"] == ""  # No Google Meet link generated

        # Inspect payload sent to Google Calendar API
        called_args = mock_req.call_args
        event_payload = called_args[1]["json"]
        assert "conferenceData" not in event_payload  # No conferenceData attached!


@pytest.mark.asyncio
async def test_schedule_event_tool_syncs_both_channels_without_video_meeting():
    """
    Verifies that the unified schedule_event primitive creates both a Telegram alarm and Google Calendar event
    without forcing Google Meet confirmation.
    """
    fake_cal_res = {
        "id": "evt_9988",
        "summary": "Meeting",
        "start": {"dateTime": "2026-11-10T17:00:00"},
        "end": {"dateTime": "2026-11-10T18:00:00"},
        "htmlLink": "https://calendar.google.com/test",
        "meet_url": ""
    }

    with patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "test@test.com")), \
         patch("google_services.create_calendar_event", return_value=fake_cal_res):
        res = await execute_tool(
            TEST_CHAT_ID,
            "schedule_event",
            {
                "title": "Meeting",
                "start_iso": "2026-11-10T17:00:00",
                "duration_minutes": 60,
                "sync_calendar": True,
                "sync_telegram": True,
                "is_video_meeting": False
            }
        )
        assert res["status"] == "success"
        assert res["synced_calendar"] is True
        assert res["synced_telegram"] is True
        assert "Google Calendar aur Telegram dono par" in res["message"]


# ==============================================================================
# PILLAR 3: EVENT-DRIVEN SCHEDULE CURVE (Adaptive Morning Engine)
# ==============================================================================

@pytest.mark.asyncio
async def test_early_bird_wake_window_4am_to_8am_triggers_and_suppresses_cron():
    """
    Verifies that when a user messages between 4:00 AM and 8:00 AM, the morning briefing
    is immediately delivered and marks briefing_sent=True, preventing 8:00 AM cron disruption.
    """
    from morning_briefing import process_user_morning_briefing
    today_str = "2026-11-10"

    # 1. Clean slate for today
    db._init_morning_briefing_table()
    with db.engine.begin() as conn:
        conn.execute(
            db.text("DELETE FROM daily_morning_briefings WHERE chat_id IN (:c1, :c2) AND briefing_date = :d"),
            {"c1": TEST_CHAT_ID, "c2": LINKED_CHAT_ID, "d": today_str}
        )

    assert db.is_morning_briefing_sent(TEST_CHAT_ID, today_str) is False

    # 2. Simulate early wake-up at 7:46 AM IST
    now_746am = datetime(2026, 11, 10, 7, 46, tzinfo=IST)
    with patch("core.timeutil.now", return_value=now_746am), \
         patch("morning_briefing.build_morning_briefing_message", return_value=("🌅 Good Morning Neerav! Schedule...", "early_wake_kickoff")), \
         patch("morning_briefing.send_telegram_message"):
        
        # When user sends early message
        from agent.loop import run_agent_turn
        # Mock LLM chat returning friendly greeting
        from llm.provider import LLMResponse
        fake_llm_res = LLMResponse(text="Hello Nirav! Kaise ho bhai?", provider="gemini", model="gemini-flash")
        with patch("llm.provider.chat", return_value=fake_llm_res):
            turn_res = await run_agent_turn(TEST_CHAT_ID, "Hi", channel="telegram")

            # Morning briefing card was attached to the early wake reply!
            assert "Good Morning" in turn_res["reply"]

    # 3. Briefing is now recorded as sent for both linked chat IDs
    assert db.is_morning_briefing_sent(TEST_CHAT_ID, today_str) is True
    assert db.is_morning_briefing_sent(LINKED_CHAT_ID, today_str) is True

    # 4. When 8:00 AM cron arrives, it MUST be skipped (zero double notification!)
    now_8am = datetime(2026, 11, 10, 8, 0, tzinfo=IST)
    cron_result = await process_user_morning_briefing(TEST_CHAT_ID, {"chat_id": TEST_CHAT_ID}, now_8am)
    assert cron_result is False  # Suppressed!


@pytest.mark.asyncio
async def test_late_night_guard_before_4am_never_triggers_morning():
    """Verifies that messages sent before 4:00 AM (e.g. 2:00 AM) are treated as late-night, NOT morning."""
    from agent.prompt import build_runtime_system_prompt
    now_2am = datetime(2026, 11, 10, 2, 0, tzinfo=IST)
    with patch("core.timeutil.now", return_value=now_2am):
        prompt = await build_runtime_system_prompt(TEST_CHAT_ID, "Hi")
        assert "- FIRST MORNING INTERACTION MANDATE (Active:" not in prompt
        assert "Regular Turn" in prompt
