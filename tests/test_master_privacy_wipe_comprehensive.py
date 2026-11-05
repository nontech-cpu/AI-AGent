import os
import json
import time
import pytest
from unittest.mock import patch, AsyncMock
import db
from main import handle_user_text, handle_mail, PENDING_WIPE_CODES
from agent.tools import execute_tool
from llm.provider import LLMResponse

async def fake_wipe_chat(*args, **kwargs):
    messages = kwargs.get("messages", [])
    last_msg = messages[-1] if messages else {}
    last_role = last_msg.get("role")
    last_content = str(last_msg.get("content", ""))

    # If claim verifier calls LLM
    if "audit_claims" in last_content or "is_grounded" in last_content or kwargs.get("response_format", {}).get("type") == "json_object":
        if "confirmation intent" in last_content.lower() or "classify user confirmation" in last_content.lower():
            import re
            m = re.search(r'User message:\s*"([^"]+)"', last_content)
            user_msg = m.group(1).lower() if m else last_content.lower()
            intent = "cancel" if any(w in user_msg for w in ("cancel", "mat karo", "rehne do", "nahi", "no", "abort")) else "confirm"
            return LLMResponse(
                text=json.dumps({"intent": intent}),
                tool_calls=[],
                provider="Gemini",
                model="gemini-3.1-flash-lite-preview"
            )
        return LLMResponse(
            text=json.dumps({
                "claims_action_done": False,
                "which_action": None,
                "claims_external_fact": False,
                "sourced_by_tool": True,
                "is_grounded": True,
                "reason": "Verified"
            }),
            tool_calls=[],
            provider="Gemini",
            model="gemini-3.1-flash-lite-preview"
        )

    # If following tool execution
    if last_role == "tool":
        return LLMResponse(
            text="⚠️ **Security & Privacy Warning**: Kya aap sach me apni saari memories, chat history, notes, reminders permanently delete karna chahte hain? Reply 'yes/haan' to confirm ya 'no/cancel' to abort.",
            tool_calls=[],
            provider="Gemini",
            model="gemini-3.1-flash-lite-preview"
        )

    # If wipe query
    if any(w in last_content.lower() for w in ("dlete", "delete", "wipe", "mita", "saaf", "sab kuch")):
        return LLMResponse(
            text="",
            tool_calls=[{
                "id": "call_wipe",
                "type": "function",
                "function": {"name": "request_master_privacy_wipe", "arguments": "{}"}
            }],
            provider="Gemini",
            model="gemini-3.1-flash-lite-preview"
        )

    return LLMResponse(
        text="Maine note kar liya hai.",
        tool_calls=[],
        provider="Gemini",
        model="gemini-3.1-flash-lite-preview"
    )

@pytest.fixture(autouse=True)
def setup_test_db():
    PENDING_WIPE_CODES.clear()
    from state.pending import cancel_pending_action
    for cid in (998877101, 998877102, 998877103, 998877104, 998877106):
        try:
            db.set_state(cid, None)
            cancel_pending_action(cid)
        except Exception:
            pass
    with patch.dict(os.environ, {"AGENT_CORE": "v2"}), \
         patch("llm.provider.chat", side_effect=fake_wipe_chat):
        yield
    PENDING_WIPE_CODES.clear()
    for cid in (998877101, 998877102, 998877103, 998877104, 998877106):
        try:
            db.set_state(cid, None)
            cancel_pending_action(cid)
        except Exception:
            pass


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


@pytest.mark.asyncio
async def test_cross_question_does_not_block_user_during_wipe_pending():
    """While wipe is pending, asking a normal question should not get locked in an infinite repetitive error loop."""
    chat_id = 998877106
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    sent_messages = []
    async def mock_say(cid, text):
        sent_messages.append((cid, text))

    with patch("main.say", side_effect=mock_say), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(True, "user@example.com")):

        await handle_user_text(chat_id, "delete all my memory")
        await handle_user_text(chat_id, "yes")
        assert db.get_state(chat_id)[0] == "awaiting_wipe_email_verification"

        # User asks a normal question instead of entering code
        sent_messages.clear()
        with patch("main.handle_active", new_callable=AsyncMock) as mock_active:
            await handle_user_text(chat_id, "meri github profile kya hai")
            # Must NOT output repetitive canned rejection message
            assert not any("Aapki memory wipe request pending hai" in msg[1] for msg in sent_messages)
            assert not any("Invalid code" in msg[1] for msg in sent_messages)
            # Must have routed to normal conversational handling
            mock_active.assert_called_once()

