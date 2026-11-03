import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime
from zoneinfo import ZoneInfo
import google_services
from agent.tools import _resolve_user_timezone, execute_tool, TOOL_DEFINITIONS

@pytest.mark.asyncio
async def test_dynamic_timezone_resolution_america():
    chat_id = 112233
    with patch("google_services._request", new_callable=AsyncMock) as mock_req, \
         patch("google_services.access_token_for_chat", new_callable=AsyncMock, return_value=("fake_access_token", "fake_email")):
        # Mock Google primary calendar returning America/New_York
        mock_req.return_value = {"timeZone": "America/New_York"}
        
        # Clear cache for this test
        google_services._USER_TZ_CACHE.pop(chat_id, None)
        
        tz_str = await google_services.get_user_calendar_timezone(chat_id)
        assert tz_str == "America/New_York"
        
        tz_obj, resolved_name = await _resolve_user_timezone(chat_id)
        assert resolved_name == "America/New_York"
        assert tz_obj == ZoneInfo("America/New_York")

@pytest.mark.asyncio
async def test_dynamic_timezone_resolution_london():
    chat_id = 445566
    with patch("google_services._request", new_callable=AsyncMock) as mock_req, \
         patch("google_services.access_token_for_chat", new_callable=AsyncMock, return_value=("fake_access_token", "fake_email")):
        mock_req.return_value = {"timeZone": "Europe/London"}
        google_services._USER_TZ_CACHE.pop(chat_id, None)
        
        tz_str = await google_services.get_user_calendar_timezone(chat_id)
        assert tz_str == "Europe/London"
        
        tz_obj, resolved_name = await _resolve_user_timezone(chat_id)
        assert resolved_name == "Europe/London"
        assert tz_obj == ZoneInfo("Europe/London")

@pytest.mark.asyncio
async def test_create_meeting_respects_user_timezone_offset():
    chat_id = 778899
    # Simulate user in New York
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock, return_value=("fake_token", "fake_email")), \
         patch("google_services.get_user_calendar_timezone", new_callable=AsyncMock, return_value="America/New_York"), \
         patch("google_services._request", new_callable=AsyncMock) as mock_req:
        
        mock_req.return_value = {"id": "ev_ny_123", "status": "confirmed"}
        
        res = await google_services.create_meeting(
            chat_id=chat_id,
            title="Team Sync",
            start_iso="2026-11-03T16:00:00",
            duration_minutes=30
        )
        assert res.get("id") == "ev_ny_123"
        
        # Check payload passed to Google Calendar API
        call_args = mock_req.call_args
        posted_json = call_args[1].get("json")
        assert posted_json is not None
        assert posted_json["summary"] == "Team Sync"
        assert posted_json["start"]["timeZone"] == "America/New_York"
        assert posted_json["end"]["timeZone"] == "America/New_York"
        # Verify the ISO string contains the timezone offset (not naive!)
        assert "-0" in posted_json["start"]["dateTime"] or "+0" in posted_json["start"]["dateTime"]
        # Verify duration is exactly 30 minutes (16:00 to 16:30)
        assert "16:00:00" in posted_json["start"]["dateTime"]
        assert "16:30:00" in posted_json["end"]["dateTime"]

@pytest.mark.asyncio
async def test_tool_definitions_zero_hardcoded_offsets():
    # Verify no tool definition hardcodes '+05:30'
    for tool in TOOL_DEFINITIONS:
        f = tool.get("function", {})
        desc = f.get("description", "")
        assert "+05:30" not in desc, f"Tool {f.get('name')} description contains hardcoded +05:30"
        props = f.get("parameters", {}).get("properties", {})
        for prop_name, prop_val in props.items():
            p_desc = prop_val.get("description", "")
            assert "+05:30" not in p_desc, f"Tool {f.get('name')} prop {prop_name} contains hardcoded +05:30"
