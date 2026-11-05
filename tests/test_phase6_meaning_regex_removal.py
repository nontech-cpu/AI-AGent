"""
tests/test_phase6_meaning_regex_removal.py - Verification Suite for Phase 6.

Validates:
1. Zero meaning-determining regexes/keywords in active agent and routing paths.
2. Master Privacy Wipe flows via tool calling (`request_master_privacy_wipe`) + secure OTP flow.
3. Confirmation handling uses LLM classification backed by DB `pending_actions` table.
4. Greeting and acknowledgments route directly to unified agent loop without regex intercepts.
5. Audit script runs and confirms 0 disallowed patterns in active codebase.
"""

import pytest
import os
import subprocess
import sys
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone

import db
import main
from timing import IST
import core.timeutil as timeutil
from state.pending import (
    save_pending_action,
    get_pending_action,
    classify_confirmation_intent_with_llm,
    classify_confirmation_intent,
    execute_pending_action,
    cancel_pending_action
)
from agent.tools import execute_tool
from agent.loop import run_agent_turn
from llm.provider import LLMResponse


@pytest.fixture(autouse=True)
def clean_test_state():
    main.PENDING_WIPE_CODES.clear()
    main.PENDING_GOOGLE_ACTIONS.clear()
    yield
    main.PENDING_WIPE_CODES.clear()
    main.PENDING_GOOGLE_ACTIONS.clear()


def test_audit_regex_meaning_script_passes():
    """Verify scripts/audit_regex_meaning.py passes with 0 disallowed patterns."""
    res = subprocess.run(
        [sys.executable, "scripts/audit_regex_meaning.py"],
        capture_output=True,
        text=True
    )
    assert res.returncode == 0, f"Audit script failed: {res.stdout}\n{res.stderr}"
    assert "AUDIT RESULT: PASS" in res.stdout
    assert "Total DISALLOWED patterns found in active codebase: 0" in res.stdout


@pytest.mark.asyncio
async def test_master_privacy_wipe_tool_sets_pending_action_and_state():
    """Verify request_master_privacy_wipe initiates OTP confirmation via DB pending_actions."""
    chat_id = 998877601
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    res = await execute_tool(chat_id, "request_master_privacy_wipe", {"confirm": True})
    assert res["status"] == "confirmation_required"
    assert "Security & Privacy Warning" in res["message"]
    assert db.get_state(chat_id)[0] == "confirm_master_wipe"

    pending = get_pending_action(chat_id)
    assert pending is not None
    assert pending["action_kind"] == "master_privacy_wipe"


@pytest.mark.asyncio
async def test_confirmation_classification_with_llm_affirmative():
    """Verify free-text confirmation is classified as 'confirm' by LLM without regex."""
    pending = {
        "id": 1,
        "action_kind": "create_meeting",
        "payload": {"title": "Strategy Sync", "start_iso": "2026-11-06T15:00:00+05:30"}
    }

    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text='{"intent": "confirm"}',
            tool_calls=[],
            provider="Gemini",
            model="gemini-3.1-flash-lite-preview"
        )
        intent, extra = await classify_confirmation_intent_with_llm("haan bhai bilkul confirm kar de", pending)
        assert intent == "confirm"
        assert extra is None
        mock_chat.assert_called_once()


@pytest.mark.asyncio
async def test_confirmation_classification_with_llm_cancellation():
    """Verify free-text rejection is classified as 'cancel' by LLM without regex."""
    pending = {
        "id": 2,
        "action_kind": "send_email",
        "payload": {"to": "client@example.com", "subject": "Proposal"}
    }

    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text='{"intent": "cancel"}',
            tool_calls=[],
            provider="Gemini",
            model="gemini-3.1-flash-lite-preview"
        )
        intent, extra = await classify_confirmation_intent_with_llm("rehnde bhai mat bhej", pending)
        assert intent == "cancel"
        assert extra is None
        mock_chat.assert_called_once()


@pytest.mark.asyncio
async def test_confirmation_cross_question_preserves_pending():
    """Verify cross-questioning is recognized and preserves pending action."""
    pending = {
        "id": 3,
        "action_kind": "create_meeting",
        "payload": {"title": "Client Sync", "start_iso": "2026-11-06T15:00:00+05:30"}
    }

    intent, extra = await classify_confirmation_intent_with_llm("kya us time par meri koi aur meeting hai?", pending)
    assert intent == "cross_question"
    assert extra is None


@pytest.mark.asyncio
async def test_deterministic_inline_button_confirmation():
    """Verify deterministic callback buttons execute without needing LLM calls."""
    pending = {
        "id": 4,
        "action_kind": "create_meeting",
        "payload": {"title": "Quick Sync"}
    }

    # Deterministic callback string from Telegram button
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        intent, _ = await classify_confirmation_intent_with_llm("action_confirm", pending)
        assert intent == "confirm"
        mock_chat.assert_not_called()

        intent_cancel, _ = await classify_confirmation_intent_with_llm("action_cancel", pending)
        assert intent_cancel == "cancel"
        mock_chat.assert_not_called()


@pytest.mark.asyncio
async def test_master_privacy_wipe_execution_generates_chat_otp():
    """Verify executing pending master_privacy_wipe generates secure 6-digit OTP."""
    chat_id = 998877602
    db.ensure_user(chat_id)
    save_pending_action(chat_id, "master_privacy_wipe", {})
    pending = get_pending_action(chat_id)
    assert pending is not None

    success, reply = await execute_pending_action(chat_id, pending)
    assert success is True
    assert "Verification Code:" in reply
    assert chat_id in main.PENDING_WIPE_CODES
    otp = main.PENDING_WIPE_CODES[chat_id]["code"]
    assert len(otp) == 6
    assert otp.isdigit()


@pytest.mark.asyncio
async def test_greetings_pass_directly_to_agent_turn():
    """Verify greetings flow through run_agent_turn rather than hardcoded string replies."""
    chat_id = 998877603
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text="Hello! How can I assist you with your schedule today?",
            tool_calls=[],
            provider="Gemini",
            model="gemini-3.1-flash-lite-preview"
        )
        res = await run_agent_turn(chat_id, "hello good morning")
        assert "Hello!" in res["reply"]
        assert res["is_grounded"] is True
        mock_chat.assert_called_once()

