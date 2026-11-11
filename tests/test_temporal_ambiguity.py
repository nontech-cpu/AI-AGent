import pytest
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from unittest.mock import patch, AsyncMock
from timing import IST, parse_followup_time, is_time_ambiguous

@pytest.fixture
def noon_now():
    """Reference point at 12:00 PM (12:00 IST) on 2026-10-20."""
    return datetime(2026, 10, 20, 12, 0, tzinfo=IST)

def test_10_baje_is_ambiguous_and_never_silently_converted_to_22(noon_now):
    """'10 baje' without AM/PM or morning/evening context must be flagged as ambiguous, not silently converted to 22:00."""
    parsed = parse_followup_time("10 baje", noon_now)
    assert parsed is not None
    assert parsed.is_ambiguous is True
    # Crucial: it must NOT be silently converted to 22:00 (10 PM) just because 10:00 AM passed
    assert parsed[0].hour == 10
    assert parsed[0].minute == 0
    assert is_time_ambiguous("10 baje", noon_now) is True

def test_10_subah_is_unambiguous_am():
    """'10 subah' specifies morning explicitly -> unambiguous 10:00 AM."""
    morning_now = datetime(2026, 10, 20, 8, 0, tzinfo=IST)
    parsed = parse_followup_time("10 subah", morning_now)
    assert parsed is not None
    assert parsed.is_ambiguous is False
    assert parsed[0] == time(10, 0)
    assert parsed[1] == date(2026, 10, 20)
    assert is_time_ambiguous("10 subah", morning_now) is False

    # When 10 AM today has already passed (e.g. at 12 PM), '10 subah' naturally schedules for tomorrow morning
    noon_now = datetime(2026, 10, 20, 12, 0, tzinfo=IST)
    parsed_afternoon = parse_followup_time("10 subah", noon_now)
    assert parsed_afternoon[0] == time(10, 0)
    assert parsed_afternoon[1] == date(2026, 10, 21)
    assert parsed_afternoon.is_ambiguous is False

def test_10_raat_is_unambiguous_pm(noon_now):
    """'10 raat' specifies night explicitly -> unambiguous 22:00 (10:00 PM)."""
    parsed = parse_followup_time("10 raat", noon_now)
    assert parsed is not None
    assert parsed.is_ambiguous is False
    assert parsed[0] == time(22, 0)
    assert parsed[1] == date(2026, 10, 20)
    assert is_time_ambiguous("10 raat", noon_now) is False

def test_10_am_is_unambiguous(noon_now):
    """'10 AM' specifies AM meridian -> unambiguous 10:00 AM."""
    parsed = parse_followup_time("10 AM", noon_now)
    assert parsed is not None
    assert parsed.is_ambiguous is False
    assert parsed[0] == time(10, 0)
    assert is_time_ambiguous("10 AM", noon_now) is False

def test_10_pm_is_unambiguous(noon_now):
    """'10 PM' specifies PM meridian -> unambiguous 22:00 (10:00 PM)."""
    parsed = parse_followup_time("10 PM", noon_now)
    assert parsed is not None
    assert parsed.is_ambiguous is False
    assert parsed[0] == time(22, 0)
    assert is_time_ambiguous("10 PM", noon_now) is False

def test_10_baje_kar_do_is_ambiguous(noon_now):
    """'10 baje kar do' has no meridian/period indicator -> flagged as ambiguous."""
    parsed = parse_followup_time("10 baje kar do", noon_now)
    assert parsed is not None
    assert parsed.is_ambiguous is True
    assert parsed[0].hour == 10
    assert is_time_ambiguous("10 baje kar do", noon_now) is True

def test_kal_10_baje_is_ambiguous(noon_now):
    """'Kal 10 baje' specifies tomorrow but lacks AM/PM -> flagged as ambiguous, target tomorrow."""
    parsed = parse_followup_time("Kal 10 baje", noon_now)
    assert parsed is not None
    assert parsed.is_ambiguous is True
    assert parsed[0].hour == 10
    assert parsed[1] == date(2026, 10, 21)
    assert is_time_ambiguous("Kal 10 baje", noon_now) is True

@pytest.mark.asyncio
async def test_ambiguous_time_during_pending_action():
    """
    When a pending create_meeting action exists and user replies with an ambiguous time
    like 'Meeting ka time 10 baje kar do', the system must ask for AM/PM clarification
    rather than silently updating to 22:00 or canceling the action.
    """
    import main
    import db
    chat_id = 99887711
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.set_persona(chat_id, "professional")

    main.PENDING_GOOGLE_ACTIONS[chat_id] = {
        "kind": "create_meeting",
        "title": "Strategy Sync",
        "start_iso": "2026-10-21T15:00:00+05:30",
        "duration_minutes": 30,
        "attendees": ["test@example.com"],
        "created_at": datetime.now(IST),
    }

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append(text)

    with patch("main.say", side_effect=mock_say):
        await main.handle_user_text(chat_id, "Meeting ka time 10 baje kar do")

    # Action must NOT be removed from pending
    assert chat_id in main.PENDING_GOOGLE_ACTIONS
    # System must ask whether morning or evening
    assert any("subah 10:00 AM ya raat 10:00 PM" in msg for msg in sent_messages)
    # The start_iso must NOT be mutated to 22:00 (10 PM)
    assert "22:00" not in main.PENDING_GOOGLE_ACTIONS[chat_id]["start_iso"]
