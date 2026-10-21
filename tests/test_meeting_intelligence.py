import pytest
from datetime import datetime, date, time
import zoneinfo
from unittest.mock import patch, AsyncMock
from timing import IST
from llm import parse_followup_time
from main import handle_user_text, PENDING_GOOGLE_ACTIONS, prepare_google_action

@pytest.fixture
def fixed_now():
    # Wednesday, Oct 21, 2026, 7:30 PM IST
    return datetime(2026, 10, 21, 19, 30, tzinfo=zoneinfo.ZoneInfo("Asia/Kolkata"))

def test_parse_followup_time_weekdays_and_typos(fixed_now):
    # Tuesday 5pm -> Next Tuesday Oct 27
    t_time, t_date = parse_followup_time("tuesday 5pm", fixed_now)
    assert t_time == time(17, 0)
    assert t_date == date(2026, 10, 27)

    # Typo: tuedday 5pm -> Next Tuesday Oct 27
    t_time, t_date = parse_followup_time("tuedday 5pm", fixed_now)
    assert t_time == time(17, 0)
    assert t_date == date(2026, 10, 27)

    # Monday 5 bje -> Next Monday Oct 26
    t_time, t_date = parse_followup_time("monday 5 bje", fixed_now)
    assert t_time == time(17, 0)
    assert t_date == date(2026, 10, 26)

    # Friday 4pm -> This Friday Oct 23
    t_time, t_date = parse_followup_time("friday 4pm", fixed_now)
    assert t_time == time(16, 0)
    assert t_date == date(2026, 10, 23)

@pytest.mark.asyncio
async def test_pending_action_updates_meeting_time_on_user_reschedule(fixed_now):
    chat_id = 888123
    PENDING_GOOGLE_ACTIONS[chat_id] = {
        'kind': 'create_meeting',
        'title': 'Meeting',
        'start_iso': '2026-10-22T17:00:00+05:30',
        'duration_minutes': 30,
        'attendees': [],
        'created_at': datetime.now(IST)
    }

    sent = []
    async def mock_say(cid, text):
        sent.append(text)

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_state", return_value=(None, None)), \
         patch("main.has_consent", return_value=True), \
         patch("main.get_persona", return_value="professional"), \
         patch("main.get_language", return_value="en"), \
         patch("main.set_language"), \
         patch("main.log_chat_message"), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "test@gmail.com")), \
         patch("main.detect_meeting_conflicts", return_value=([], [])):

        # User changes mind during confirmation: "monday 5 bje"
        await handle_user_text(chat_id, "monday 5 bje")

        # It should update the pending action instead of dropping it
        assert chat_id in PENDING_GOOGLE_ACTIONS
        assert "2026-10-26T17:00:00" in PENDING_GOOGLE_ACTIONS[chat_id]["start_iso"]
        assert len(sent) == 1
        assert "Monday, 26 Oct at 05:00 PM" in sent[0]

@pytest.mark.asyncio
async def test_ask_for_meet_link_returns_real_link():
    chat_id = 888123
    mock_events = [
        {
            "summary": "AI Sync",
            "start": {"dateTime": "2026-10-26T17:00:00+05:30"},
            "meet_url": "https://meet.google.com/abc-defg-hij"
        }
    ]

    sent = []
    async def mock_say(cid, text):
        sent.append(text)

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_state", return_value=(None, None)), \
         patch("main.has_consent", return_value=True), \
         patch("main.get_persona", return_value="professional"), \
         patch("main.get_language", return_value="en"), \
         patch("main.set_language"), \
         patch("main.log_chat_message"), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "test@gmail.com")), \
         patch("main.google_list_calendar_events", new_callable=AsyncMock, return_value=mock_events):

        await handle_user_text(chat_id, "meeting ka link ya de")
        assert len(sent) == 1
        assert "https://meet.google.com/abc-defg-hij" in sent[0]
        assert "AI Sync" in sent[0]

@pytest.mark.asyncio
async def test_create_meeting_defaults_title_when_start_iso_is_present():
    chat_id = 888123
    sent = []
    async def mock_say(cid, text):
        sent.append(text)

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "test@gmail.com")), \
         patch("main.detect_meeting_conflicts", return_value=([], [])):

        # title is None, but start_iso is already given
        await prepare_google_action(chat_id, {
            "intent": "create_meeting",
            "title": None,
            "start_iso": "2026-10-27T17:00:00+05:30",
            "duration_minutes": 30,
            "attendees": []
        })

        # It must NOT ask "Meeting kis din aur kitne baje rakhun?"
        assert chat_id in PENDING_GOOGLE_ACTIONS
        assert PENDING_GOOGLE_ACTIONS[chat_id]["title"] == "Meeting"
        assert len(sent) == 1
        assert "Please confirm" in sent[0]
        assert "2026-10-27T17:00:00+05:30" in sent[0]
