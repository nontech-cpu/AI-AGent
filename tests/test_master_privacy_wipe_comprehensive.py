import os
import json
import time
import pytest
from unittest.mock import patch, AsyncMock
import db
from main import handle_user_text, handle_mail, PENDING_WIPE_CODES, is_master_wipe_request
from agent.tools import execute_tool

@pytest.fixture(autouse=True)
def setup_test_db():
    PENDING_WIPE_CODES.clear()
    yield
    PENDING_WIPE_CODES.clear()


@pytest.mark.asyncio
async def test_wipe_with_typo_chat_otp_verification():
    """Test user saying 'dlete all memory u have about me' and verifying via chat OTP directly."""
    chat_id = 998877101
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.save_user_fact(chat_id, "User prefers Python programming", category="work")
    db.save_memory(chat_id, "travel", "Flight ticket to Mumbai", amount=4500, due_date="2026-11-01", due_time="10:00:00")

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append((cid, text))

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(False, None)):

        # Step 1: User sends typo phrase
        await handle_user_text(chat_id, "dlete all memory u have about me")
        assert any("Security & Privacy Warning" in msg[1] for msg in sent_messages)
        assert db.get_state(chat_id)[0] == "confirm_master_wipe"

        # Step 2: User confirms
        sent_messages.clear()
        await handle_user_text(chat_id, "haan bilkul")
        assert chat_id in PENDING_WIPE_CODES
        otp = PENDING_WIPE_CODES[chat_id]["code"]
        assert len(otp) == 6
        assert any(otp in msg[1] for msg in sent_messages)
        assert db.get_state(chat_id)[0] == "awaiting_wipe_chat_otp"

        # Step 3: User replies with the 6-digit OTP in chat
        sent_messages.clear()
        await handle_user_text(chat_id, otp)
        assert chat_id not in PENDING_WIPE_CODES
        assert any("Privacy Wipe Complete" in msg[1] for msg in sent_messages)

        # Verification: all data wiped
        facts = db.get_user_facts(chat_id)
        assert len(facts) == 0
        items = db.list_memories(chat_id)
        assert len(items) == 0


@pytest.mark.asyncio
async def test_wipe_with_second_typo_phrase_and_email_verification():
    """Test user saying 'delete all meory u have seaved' and verifying via connected email."""
    chat_id = 998877102
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.save_user_fact(chat_id, "Prefers window seat", category="travel")

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append((cid, text))

    connected_email = "testuser@gmail.com"

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(True, connected_email)):

        # Step 1: User sends second typo phrase
        await handle_user_text(chat_id, "delete all meory u have seaved")
        assert any("Security & Privacy Warning" in msg[1] for msg in sent_messages)
        assert db.get_state(chat_id)[0] == "confirm_master_wipe"

        # Step 2: User confirms with "yes"
        sent_messages.clear()
        await handle_user_text(chat_id, "yes")
        assert chat_id in PENDING_WIPE_CODES
        otp = PENDING_WIPE_CODES[chat_id]["code"]
        assert any(otp in msg[1] for msg in sent_messages)

        # Step 3: User sends email with the code
        sent_messages.clear()
        mail_payload = {
            "sender": connected_email,
            "subject": f"Wipe OTP: {otp}",
            "body": f"Please verify wipe with code {otp}",
            "authenticated": True
        }
        await handle_mail(mail_payload)
        assert any("Master Privacy Wipe Confirmed" in msg[1] for msg in sent_messages)

        # Verification: data wiped
        facts = db.get_user_facts(chat_id)
        assert len(facts) == 0


@pytest.mark.asyncio
async def test_wipe_connected_email_user_can_still_verify_in_chat():
    """Even if user has a connected email, they can verify by simply typing the OTP in chat!"""
    chat_id = 998877103
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.save_user_fact(chat_id, "Allergic to peanuts", category="health")

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append((cid, text))

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "user@example.com")):

        await handle_user_text(chat_id, "sab kuch delete kar do")
        await handle_user_text(chat_id, "yes")
        otp = PENDING_WIPE_CODES[chat_id]["code"]

        # Send OTP directly in chat
        sent_messages.clear()
        await handle_user_text(chat_id, f"Here is my code {otp}")
        assert any("Privacy Wipe Complete" in msg[1] for msg in sent_messages)
        assert len(db.get_user_facts(chat_id)) == 0


@pytest.mark.asyncio
async def test_wipe_cancellation():
    """Test user aborting wipe."""
    chat_id = 998877104
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.save_user_fact(chat_id, "Important project data", category="work")

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append((cid, text))

    with patch("main.say", side_effect=mock_say):
        await handle_user_text(chat_id, "delete all my memory")
        assert db.get_state(chat_id)[0] == "confirm_master_wipe"

        sent_messages.clear()
        await handle_user_text(chat_id, "cancel")
        assert any("cancel" in msg[1].lower() for msg in sent_messages)
        assert db.get_state(chat_id)[0] is None
        assert len(db.get_user_facts(chat_id)) == 1


@pytest.mark.asyncio
async def test_agent_tool_request_master_privacy_wipe():
    """Test that agent tool request_master_privacy_wipe transitions to confirm_master_wipe state."""
    chat_id = 998877105
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    res = await execute_tool(chat_id, "request_master_privacy_wipe", {"confirm": True})
    assert res.get("status") == "confirmation_required"
    assert "Security & Privacy Warning" in res.get("message", "")
    assert db.get_state(chat_id)[0] == "confirm_master_wipe"
