import pytest
from datetime import date, datetime
from unittest.mock import patch, AsyncMock
from main import get_unified_schedule, detect_meeting_conflicts, show_range
from timing import IST

@pytest.mark.asyncio
async def test_unified_schedule_detects_contact_commitment():
    chat_id = 999999
    # Tuesday date
    target_date = date(2026, 10, 27)

    mock_contacts = [
        {
            "name": "Ananya Roy",
            "company": "Elevate Ventures",
            "role_title": "Founding Partner",
            "commitments": "Ananya ko module bhejna hai Tuesday ko",
            "context_notes": "Met regarding investment",
            "email": "ananya@elevatevc.com",
            "phone": "+91 91234 56789"
        }
    ]

    with patch("main.items_for_date", return_value=[]), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(False, None)), \
         patch("main.get_contact_memories", return_value=mock_contacts), \
         patch("main.get_board_cards", return_value={"queue": [], "this_week": [], "today": [], "done": []}):

        sched = await get_unified_schedule(chat_id, target_date)
        assert sched["weekday"] == "tuesday"
        assert len(sched["commitments"]) == 1
        assert sched["commitments"][0]["name"] == "Ananya Roy"
        assert "module bhejna hai" in sched["commitments"][0]["commitment"]


@pytest.mark.asyncio
async def test_show_range_free_query_with_commitment():
    chat_id = 999999
    target_date = date(2026, 10, 27)

    mock_contacts = [
        {
            "name": "Ananya Roy",
            "company": "Elevate Ventures",
            "commitments": "Ananya ko module bhejna hai Tuesday ko",
            "context_notes": "",
            "role_title": "",
            "email": "",
            "phone": ""
        }
    ]

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append(text)

    with patch("main.say", side_effect=mock_say), \
         patch("main.items_for_date", return_value=[]), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(False, None)), \
         patch("main.get_contact_memories", return_value=mock_contacts), \
         patch("main.get_board_cards", return_value={"queue": [], "this_week": [], "today": [], "done": []}), \
         patch("main.items_between", return_value=[]):

        await show_range(chat_id, target_date, target_date, query_text="ky m tuesday ko free hu")

        assert len(sent_messages) == 1
        reply = sent_messages[0]
        assert "poori tarah free nahi ho" in reply
        assert "Ananya Roy" in reply
        assert "module bhejna hai" in reply


@pytest.mark.asyncio
async def test_show_range_free_query_when_completely_free():
    chat_id = 999999
    # Wednesday date with no commitments
    target_date = date(2026, 10, 28)

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append(text)

    with patch("main.say", side_effect=mock_say), \
         patch("main.items_for_date", return_value=[]), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(False, None)), \
         patch("main.get_contact_memories", return_value=[]), \
         patch("main.get_board_cards", return_value={"queue": [], "this_week": [], "today": [], "done": []}), \
         patch("main.items_between", return_value=[]):

        await show_range(chat_id, target_date, target_date, query_text="kya main wednesday ko free hu?")

        assert len(sent_messages) == 1
        reply = sent_messages[0]
        assert "bilkul free ho" in reply


@pytest.mark.asyncio
async def test_detect_meeting_conflicts_google_calendar():
    chat_id = 999999
    start_iso = "2026-10-27T17:00:00+05:30"

    mock_calendar_events = [
        {
            "summary": "Existing Team Sync",
            "start": {"dateTime": "2026-10-27T17:00:00+05:30"},
            "end": {"dateTime": "2026-10-27T17:30:00+05:30"}
        }
    ]

    with patch("main.get_valid_google_connection_by_chat_id", return_value=(True, None)), \
         patch("main.google_list_calendar_events", new_callable=AsyncMock, return_value=mock_calendar_events), \
         patch("main.items_for_date", return_value=[]), \
         patch("main.get_contact_memories", return_value=[]), \
         patch("main.get_board_cards", return_value={"queue": [], "this_week": [], "today": [], "done": []}):

        conflicts, commitments = await detect_meeting_conflicts(chat_id, start_iso, 30)
        assert len(conflicts) == 1
        assert "Existing Team Sync" in conflicts[0]
