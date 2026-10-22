import pytest
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, AsyncMock

from schedule.intervals import (
    TimeInterval,
    normalize_to_ist,
    parse_activity_duration,
    check_interval_availability,
    find_free_slots
)

IST = ZoneInfo("Asia/Kolkata")

def test_time_interval_overlap_logic():
    # Base event: 5:00 PM to 6:00 PM IST
    start_a = datetime(2026, 10, 22, 17, 0, tzinfo=IST)
    end_a = datetime(2026, 10, 22, 18, 0, tzinfo=IST)
    int_a = TimeInterval(start=start_a, end=end_a, title="Event A", buffer_minutes=0)

    # Event B: 5:30 PM to 6:30 PM (Direct overlap)
    start_b = datetime(2026, 10, 22, 17, 30, tzinfo=IST)
    end_b = datetime(2026, 10, 22, 18, 30, tzinfo=IST)
    int_b = TimeInterval(start=start_b, end=end_b, title="Event B", buffer_minutes=0)

    assert int_a.overlaps(int_b) is True

    # Event C: 6:15 PM to 7:00 PM (No overlap without buffer)
    start_c = datetime(2026, 10, 22, 18, 15, tzinfo=IST)
    end_c = datetime(2026, 10, 22, 19, 0, tzinfo=IST)
    int_c = TimeInterval(start=start_c, end=end_c, title="Event C", buffer_minutes=0)

    assert int_a.overlaps(int_c, use_buffer=False) is False

    # Event C with 20 min travel buffer -> [17:55 to 19:20] overlaps Event A [17:00 to 18:00]
    int_c_buffered = TimeInterval(start=start_c, end=end_c, title="Event C", buffer_minutes=20)
    assert int_a.overlaps(int_c_buffered, use_buffer=True) is True


def test_activity_duration_parsing():
    assert parse_activity_duration("movie") == 180
    assert parse_activity_duration("dinner") == 90
    assert parse_activity_duration("gym") == 60
    assert parse_activity_duration(activity_type="work", text_content="2.5 hours workshop") == 150
    assert parse_activity_duration(activity_type="meeting", text_content="45 minutes call") == 45


def test_utc_to_ist_normalization():
    # UTC 12:30 PM = IST 6:00 PM
    utc_str = "2026-10-22T12:30:00Z"
    dt_ist = normalize_to_ist(utc_str)
    assert dt_ist.tzinfo == IST
    assert dt_ist.hour == 18
    assert dt_ist.minute == 30 or dt_ist.minute == 0
    assert dt_ist.strftime("%I:%M %p") == "06:00 PM"


@pytest.mark.asyncio
async def test_detects_movie_clashing_with_meeting():
    chat_id = 998811
    # Existing 6:00 PM meeting (UTC 12:30)
    mock_sched = {
        "calendar_events": [
            {
                "summary": "Team Sync",
                "start": {"dateTime": "2026-10-22T12:30:00Z"},  # 6:00 PM IST
                "end": {"dateTime": "2026-10-22T13:00:00Z"},
                "meet_url": "https://meet.google.com/test-meet"
            }
        ],
        "reminders": [],
        "commitments": []
    }

    with patch("main.get_unified_schedule", return_value=mock_sched):
        # User wants to go to movie at 5:00 PM
        res = await check_interval_availability(
            chat_id=chat_id,
            start_time_str="5 PM",
            target_date_str="2026-10-22",
            activity_type="movie"
        )

        assert res["is_free"] is False
        assert len(res["conflicts"]) > 0
        assert "Team Sync" in res["conflicts"][0]
        assert "06:00 PM" in res["conflicts"][0]
        assert "https://meet.google.com/test-meet" in res["conflicts"][0]


def test_find_free_slots():
    t_date = date(2026, 10, 22)
    busy = [
        TimeInterval(
            start=datetime(2026, 10, 22, 14, 0, tzinfo=IST),
            end=datetime(2026, 10, 22, 15, 0, tzinfo=IST),
            title="Busy Call"
        )
    ]
    slots = find_free_slots(busy, t_date)
    assert len(slots) >= 2
    assert "10:00 AM – 02:00 PM" in slots[0]
