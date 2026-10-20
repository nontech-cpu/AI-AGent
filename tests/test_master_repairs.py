"""
Regression tests reproducing and verifying the fixes for all 5 Telegram failures
and architectural requirements specified in the Master Repair Prompt.
"""
import json
import pytest
from datetime import datetime, timedelta, time as dtime
from unittest.mock import patch, AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import main
import llm
import google_services

IST = ZoneInfo("Asia/Kolkata")


# --- Failure 1 Regression: Update interpreted as create ---
@pytest.mark.asyncio
async def test_failure_01_update_existing_event_patches_same_id():
    """
    Test that 'PeculiarAI QA Test 01 wali meeting ko 5 PM kar do'
    locates the existing event ID, updates it, preserves Meet link,
    and NEVER creates a new event.
    """
    chat_id = 777101
    existing_event = {
        "id": "event_peculiar_qa_01",
        "summary": "PeculiarAI QA Test 01",
        "start": {"dateTime": "2026-10-21T16:00:00+05:30"},
        "end": {"dateTime": "2026-10-21T16:30:00+05:30"},
        "conferenceData": {
            "entryPoints": [{"entryPointType": "video", "uri": "https://meet.google.com/test-meet-qa01"}]
        },
        "htmlLink": "https://calendar.google.com/event?eid=event_peculiar_qa_01",
    }

    # Step 1: Verify find_calendar_event locates the event by title
    with patch("google_services.list_calendar_events", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [existing_event]
        matches, is_ambig = await google_services.find_calendar_event(
            chat_id=chat_id,
            title_query="PeculiarAI QA Test 01",
        )
        assert len(matches) == 1
        assert matches[0]["id"] == "event_peculiar_qa_01"
        assert is_ambig is False

    # Step 2: Verify update_meeting calls PATCH on existing ID and preserves Meet link
    with patch("google_services.access_token_for_chat", new_callable=AsyncMock) as mock_acc:
        mock_acc.return_value = ("mock_access_token", "user@example.com")
        with patch("google_services.get_calendar_event", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = existing_event
            with patch("google_services._request", new_callable=AsyncMock) as mock_req:
                mock_req.return_value = {
                    "id": "event_peculiar_qa_01",
                    "summary": "PeculiarAI QA Test 01",
                    "start": {"dateTime": "2026-10-21T17:00:00+05:30"},
                    "end": {"dateTime": "2026-10-21T17:30:00+05:30"},
                    "conferenceData": existing_event["conferenceData"],
                    "htmlLink": existing_event["htmlLink"],
                }

                updated = await google_services.update_meeting(
                    chat_id=chat_id,
                    event_id="event_peculiar_qa_01",
                    new_start_iso="2026-10-21T17:00:00+05:30",
                    duration_minutes=30,
                )

                assert updated["id"] == "event_peculiar_qa_01"
                assert updated["meet_url"] == "https://meet.google.com/test-meet-qa01"
                # Ensure PATCH was called, NOT POST
                call_args = mock_req.call_args
                assert call_args[0][0] == "PATCH"
                assert "event_peculiar_qa_01" in call_args[0][1]


@pytest.mark.asyncio
async def test_failure_01_update_nonexistent_event_never_creates():
    """
    If the event is not found, it must report that no matching event exists,
    and NEVER create a new event as a fallback.
    """
    chat_id = 777102
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_conn:
        mock_conn.return_value = (True, "user@example.com")
        with patch("main.google_find_calendar_event", new_callable=AsyncMock) as mock_find:
            mock_find.return_value = ([], False)  # No matches
            with patch("main.say", new_callable=AsyncMock) as mock_say:
                with patch("main.google_create_meeting", new_callable=AsyncMock) as mock_create:
                    await main.prepare_google_action(chat_id, {
                        "intent": "update_meeting",
                        "target_title": "Nonexistent Meeting",
                        "start_iso": "2026-10-21T17:00:00+05:30",
                    })

                    # create_meeting must NEVER be called
                    mock_create.assert_not_called()
                    # User must be told it wasn't found
                    mock_say.assert_called_once()
                    assert "koi meeting nahi mili" in mock_say.call_args[0][1] or "check karein" in mock_say.call_args[0][1]


# --- Failure 2 Regression: Cancellation interpreted as unrelated reminder ---
@pytest.mark.asyncio
async def test_failure_02_cancellation_of_pending_proposal():
    """
    If a meeting proposal is awaiting confirmation and user cancels,
    the pending proposal must be cancelled immediately without searching local reminders.
    """
    chat_id = 777201
    main.PENDING_GOOGLE_ACTIONS[chat_id] = {
        "kind": "create_meeting",
        "title": "PeculiarAI QA Cancel Test",
        "start_iso": "2026-10-21T18:00:00+05:30",
        "duration_minutes": 15,
        "created_at": datetime.now(IST),
    }

    with patch("main.say", new_callable=AsyncMock) as mock_say:
        handled = await main._begin_meeting_deletion(chat_id, "PeculiarAI QA Cancel Test meeting cancel kar do")
        assert handled is True
        assert chat_id not in main.PENDING_GOOGLE_ACTIONS
        mock_say.assert_called_once()
        msg = mock_say.call_args[0][1]
        assert "cancel kar di" in msg
        assert "reminders nahi mila" not in msg


@pytest.mark.asyncio
async def test_failure_02_cancellation_of_existing_google_event():
    """
    If user cancels an already-created Google meeting, it searches Google Calendar
    and prompts for deletion confirmation, rather than querying only local reminders.
    """
    chat_id = 777202
    existing_ev = {
        "id": "cal_del_999",
        "summary": "PeculiarAI QA Cancel Test",
        "start": {"dateTime": "2026-10-21T18:00:00+05:30"},
    }

    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_conn:
        mock_conn.return_value = (True, "user@example.com")
        with patch("main.google_find_calendar_event", new_callable=AsyncMock) as mock_find:
            mock_find.return_value = ([existing_ev], False)
            with patch("main.say", new_callable=AsyncMock) as mock_say:
                handled = await main._begin_meeting_deletion(chat_id, "PeculiarAI QA Cancel Test meeting cancel kar do")
                assert handled is True
                assert chat_id in main.PENDING_GOOGLE_ACTIONS
                assert main.PENDING_GOOGLE_ACTIONS[chat_id]["kind"] == "delete_meeting"
                assert main.PENDING_GOOGLE_ACTIONS[chat_id]["event_id"] == "cal_del_999"


# --- Failure 3 Regression: Context question answered from history ---
@pytest.mark.asyncio
async def test_failure_03_context_memory_question():
    """
    When user asks what project name and meeting title they stated,
    it answers from recent chat turns without invoking Calendar creation.
    """
    chat_id = 777301
    turns = [
        {"role": "user", "text": "Is conversation mein meri test project ka naam PeculiarAI hai. Is test ke liye ek 30-minute meeting ka title 'Memory QA Meeting' rakhna."},
        {"role": "assistant", "text": "Bilkul. Meeting kis din aur kitne baje rakhun?"},
    ]

    mock_answer = "Aapne test project ka naam PeculiarAI aur meeting ka title 'Memory QA Meeting' bataya tha."
    with patch("llm._chat_json", new_callable=AsyncMock) as mock_llm:
        mock_llm.return_value = {"choices": [{"message": {"content": mock_answer}}]}
        with patch("llm.httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"content": mock_answer}}]
            }
            mock_post.return_value = mock_resp

            reply = await llm.answer_conversation_memory(
                user_query="Maine test project ka kya naam bataya tha, aur meeting ka title kya rakhne ko kaha tha?",
                chat_history=turns,
            )
            assert "PeculiarAI" in reply
            assert "Memory QA Meeting" in reply


# --- Failure 4 Regression: Ambiguous 'kal shaam' policy ---
def test_failure_04_kal_shaam_ambiguity_policy():
    """
    When user says 'kal shaam', the parser detects the date (tomorrow)
    but leaves due_time as None because no specific hour was given.
    """
    now = datetime(2026, 10, 20, 10, 0, tzinfo=IST)
    parsed = llm.parse_followup_time("kal shaam", now)
    assert parsed is not None
    due_time, target_date = parsed
    # Time must be None (ambiguous), date is tomorrow
    assert due_time is None
    assert target_date == datetime(2026, 10, 21).date()


# --- Failure 5 Regression: Connected account does not short-circuit tasks ---
@pytest.mark.asyncio
async def test_failure_05_connected_account_does_not_shortcircuit_task():
    """
    A meeting scheduling request containing the word 'google'
    must NOT be short-circuited by 'Your Google account is already connected'.
    """
    chat_id = 777501
    query = "Connect Google Calendar and schedule a 30-minute meeting tomorrow at 4 PM"

    with patch("main.start_google_connection", new_callable=AsyncMock) as mock_connect:
        with patch("main.understand", new_callable=AsyncMock) as mock_understand:
            mock_understand.return_value = {
                "intent": "create_meeting",
                "title": "Meeting",
                "start_iso": "2026-10-21T16:00:00+05:30",
                "duration_minutes": 30,
                "attendees": [],
            }
            with patch("main.prepare_google_action", new_callable=AsyncMock) as mock_prep:
                await main.handle_active(chat_id, query)
                # start_google_connection must NOT have been called directly
                mock_connect.assert_not_called()
                # prepare_google_action must be called for the task
                mock_prep.assert_called_once()


# --- State Trap Prevention & Flexible Conversation Regressions ---
@pytest.mark.asyncio
async def test_create_meeting_details_does_not_trap_greetings():
    """
    When a user was previously asked for meeting details (state='create_meeting_details'),
    sending 'hi' or 'heello' must NOT ask for meeting date/time. It must reset state and greet.
    """
    chat_id = 888101
    state_store = {chat_id: ("create_meeting_details", json.dumps({"title": "Test Sync"}))}

    with patch("main.get_state", side_effect=lambda cid: state_store.get(cid, (None, None))):
        with patch("main.set_state", side_effect=lambda cid, st, data=None: state_store.pop(cid, None) if st is None else state_store.update({cid: (st, data)})):
            with patch("main.say", new_callable=AsyncMock) as mock_say:
                await main.handle_user_text(chat_id, "heello")
                assert state_store.get(chat_id) is None  # State cleared!
                mock_say.assert_called()
                reply_sent = mock_say.call_args[0][1]
                assert "Namaste" in reply_sent or "personal assistant" in reply_sent
                assert "Date/time" not in reply_sent


@pytest.mark.asyncio
async def test_create_meeting_details_does_not_trap_agenda_query():
    """
    When in 'create_meeting_details', asking 'remidner mere aajke' must NOT ask for
    meeting date/time. It must reset state and show today's agenda.
    """
    chat_id = 888102
    state_store = {chat_id: ("create_meeting_details", json.dumps({"title": "Test Sync"}))}

    with patch("main.get_state", side_effect=lambda cid: state_store.get(cid, (None, None))):
        with patch("main.set_state", side_effect=lambda cid, st, data=None: state_store.pop(cid, None) if st is None else state_store.update({cid: (st, data)})):
            with patch("main.understand", new_callable=AsyncMock) as mock_understand:
                mock_understand.return_value = {
                    "intent": "show_agenda",
                    "start": datetime(2026, 10, 20).date(),
                    "end": datetime(2026, 10, 20).date(),
                    "item": None
                }
                with patch("main.show_range", new_callable=AsyncMock) as mock_show_range:
                    with patch("main.say", new_callable=AsyncMock):
                        await main.handle_user_text(chat_id, "remidner mere aajke")
                        assert state_store.get(chat_id) is None
                        mock_show_range.assert_called_once()


@pytest.mark.asyncio
async def test_start_command_with_bot_username_resets_state():
    """
    When user sends '/start@PeculiarAIBot' while in a pending state,
    it must immediately reset the state and send the welcome/help message.
    """
    chat_id = 888103
    state_store = {chat_id: ("create_meeting_details", json.dumps({"title": "Test Sync"}))}

    with patch("main.get_state", side_effect=lambda cid: state_store.get(cid, (None, None))):
        with patch("main.set_state", side_effect=lambda cid, st, data=None: state_store.pop(cid, None) if st is None else state_store.update({cid: (st, data)})):
            with patch("main.say", new_callable=AsyncMock) as mock_say:
                await main.handle_user_text(chat_id, "/start@PeculiarAIBot")
                assert state_store.get(chat_id) is None
                mock_say.assert_called_once()
                assert "Welcome back!" in mock_say.call_args[0][1]


@pytest.mark.asyncio
async def test_create_meeting_details_does_not_trap_casual_chat():
    """
    When in 'create_meeting_details', sending 'aur bhy kesa h' must route to
    conversational chat, not ask for date/time.
    """
    chat_id = 888104
    state_store = {chat_id: ("create_meeting_details", json.dumps({"title": "Test Sync"}))}

    with patch("main.get_state", side_effect=lambda cid: state_store.get(cid, (None, None))):
        with patch("main.set_state", side_effect=lambda cid, st, data=None: state_store.pop(cid, None) if st is None else state_store.update({cid: (st, data)})):
            with patch("main.understand", new_callable=AsyncMock) as mock_understand:
                mock_understand.return_value = {"intent": "chat"}
                with patch("main.handle_conversational_chat", new_callable=AsyncMock) as mock_chat:
                    await main.handle_user_text(chat_id, "aur bhy kesa h")
                    assert state_store.get(chat_id) is None
                    mock_chat.assert_called_once_with(chat_id, "aur bhy kesa h")
