import pytest
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from unittest.mock import patch, AsyncMock
import main
from main import (
    format_event_clock_ist,
    find_matching_contact,
    handle_user_text,
    show_range
)

IST = ZoneInfo("Asia/Kolkata")

def test_format_event_clock_ist():
    # 12:30 UTC = 18:00 IST (6:00 PM)
    assert format_event_clock_ist("2026-10-22T12:30:00Z") == "6:00 PM"
    assert format_event_clock_ist("2026-10-22T12:30:00+00:00") == "6:00 PM"
    assert format_event_clock_ist("2026-10-22T18:00:00+05:30") == "6:00 PM"
    assert format_event_clock_ist(None) == "All day"

def test_find_matching_contact_fuzzy():
    contacts = [
        {"id": 1, "name": "Ananya Roy", "company": "Elevate Ventures", "commitments": "Module bhejna hai"},
        {"id": 2, "name": "Rahul Sharma", "company": "TechNova Solutions", "commitments": "Pitch deck bhejna hai"}
    ]
    # Exact
    assert find_matching_contact("ananya", contacts)["name"] == "Ananya Roy"
    # Common user typos in Hinglish
    assert find_matching_contact("bhy annaya ko kya bhejna tha", contacts)["name"] == "Ananya Roy"
    assert find_matching_contact("ananay ko kuch bhejna hai", contacts)["name"] == "Ananya Roy"
    assert find_matching_contact("ananaya ko module bhejna h", contacts)["name"] == "Ananya Roy"
    assert find_matching_contact("rahul ko bhejo", contacts)["name"] == "Rahul Sharma"
    assert find_matching_contact("raahul ko deck dena", contacts)["name"] == "Rahul Sharma"

@pytest.mark.asyncio
async def test_show_range_movie_conflict_warning():
    chat_id = 998877
    target_d = date(2026, 10, 22)
    sent = []
    async def mock_say(cid, text):
        sent.append(text)

    mock_sched = {
        "calendar_events": [
            {
                "summary": "Meeting",
                "start": {"dateTime": "2026-10-22T12:30:00Z"},  # 6:00 PM IST
                "end": {"dateTime": "2026-10-22T13:00:00Z"},
                "meet_url": "https://meet.google.com/test-meet"
            }
        ],
        "reminders": [],
        "commitments": [],
        "tasks": []
    }

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_unified_schedule", return_value=mock_sched):

        # User asks: "kl mere movie he shmko 5 bje jaunha ky m uss tym free hu?"
        await show_range(chat_id, target_d, target_d, query_text="kl mere movie he shmko 5 bje jaunha ky m uss tym free hu?")

        assert len(sent) == 1
        # It must show 6:00 PM instead of 12:30
        assert "6:00 PM - Meeting" in sent[0]
        assert "12:30" not in sent[0]
        # It should warn about the 5 PM movie clash with the 6 PM meeting
        assert "Schedule Warning" in sent[0]
        assert "6:00 PM" in sent[0]

@pytest.mark.asyncio
async def test_contact_commitment_query_answers_directly():
    chat_id = 998877112
    sent = []
    async def mock_say(cid, text):
        sent.append(text)

    mock_contacts = [
        {
            "id": 5,
            "name": "Ananya Roy",
            "company": "Elevate Ventures",
            "commitments": "Ananya ko module bhejna hai Tuesday ko"
        }
    ]

    from llm.provider import LLMResponse

    async def fake_chat(*args, **kwargs):
        messages = kwargs.get("messages", [])
        user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                user_msg = str(m.get("content", ""))
        user_msg_low = user_msg.lower()
        if "kisko" in user_msg_low or ("rahul" in user_msg_low and "ananya" in user_msg_low):
            reply = "Aapko Rahul ko pitch deck aur Ananya ko module bhejna hai."
        elif "rahul" in user_msg_low:
            reply = "Aapko Rahul ko pitch deck share karni hai."
        else:
            reply = "Aapko Ananya ko module bhejna hai Tuesday ko."
        return LLMResponse(text=reply, provider="Fake", model="test")

    with patch("main.say", side_effect=mock_say), \
         patch("llm.provider.chat", side_effect=fake_chat), \
         patch("main.run_commitment_nudges", new_callable=AsyncMock), \
         patch("main.get_state", return_value=(None, None)), \
         patch("main.has_consent", return_value=True), \
         patch("main.get_persona", return_value="professional"), \
         patch("main.get_language", return_value="en"), \
         patch("main.set_language"), \
         patch("main.log_chat_message"), \
         patch("main.get_contact_memories", return_value=mock_contacts), \
         patch("db.get_contact_memories", return_value=mock_contacts):

        # User query with typo "annaya": "bhy annaya ko mujhe ky bhjna th"
        await handle_user_text(chat_id, "bhy annaya ko mujhe ky bhjna th")
        assert len(sent) == 1
        assert "Ananya" in sent[0]
        assert "module" in sent[0]

        # Second query: "ananya jo h contact m usko ky bhjna th"
        await handle_user_text(chat_id, "ananya jo h contact m usko ky bhjna th")
        assert len(sent) == 2
        assert "Ananya" in sent[1]
        assert "module" in sent[1]

@pytest.mark.asyncio
async def test_generic_contact_commitment_for_any_contact():
    chat_id = 998877113
    sent = []
    async def mock_say(cid, text):
        sent.append(text)

    mock_contacts = [
        {"id": 4, "name": "Rahul Sharma", "company": "TechNova", "commitments": "Rahul ko pitch deck share karni hai"},
        {"id": 5, "name": "Ananya Roy", "company": "Elevate Ventures", "commitments": "Ananya ko module bhejna hai"}
    ]

    from llm.provider import LLMResponse

    async def fake_chat(*args, **kwargs):
        messages = kwargs.get("messages", [])
        user_msg = ""
        for m in messages:
            if m.get("role") == "user":
                user_msg = str(m.get("content", ""))
        user_msg_low = user_msg.lower()
        if "kisko" in user_msg_low or ("rahul" in user_msg_low and "ananya" in user_msg_low):
            reply = "Aapko Rahul ko pitch deck aur Ananya ko module bhejna hai."
        elif "rahul" in user_msg_low:
            reply = "Aapko Rahul ko pitch deck share karni hai."
        else:
            reply = "Aapko Ananya ko module bhejna hai Tuesday ko."
        return LLMResponse(text=reply, provider="Fake", model="test")

    with patch("main.say", side_effect=mock_say), \
         patch("llm.provider.chat", side_effect=fake_chat), \
         patch("main.run_commitment_nudges", new_callable=AsyncMock), \
         patch("main.get_state", return_value=(None, None)), \
         patch("main.has_consent", return_value=True), \
         patch("main.get_persona", return_value="professional"), \
         patch("main.get_language", return_value="en"), \
         patch("main.set_language"), \
         patch("main.log_chat_message"), \
         patch("main.get_contact_memories", return_value=mock_contacts), \
         patch("db.get_contact_memories", return_value=mock_contacts):

        # Generic test for Rahul: "rahul ko kya share karna tha?"
        await handle_user_text(chat_id, "rahul ko kya share karna tha?")
        assert len(sent) == 1
        assert "Rahul" in sent[0]
        assert "pitch deck" in sent[0]

        # General query for all contacts: "kisko kya bhejna tha?"
        await handle_user_text(chat_id, "kisko kya bhejna tha?")
        assert len(sent) == 2
        assert "Rahul" in sent[1]
        assert "Ananya" in sent[1]

