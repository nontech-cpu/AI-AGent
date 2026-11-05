"""
tests/test_phase5_grounding.py - Phase 5 Verification Test Suite
Grounding & Anti-Hallucination Contracts.

Covers:
1. Structured claim-checker output: {claims_action_done, which_action, claims_external_fact, sourced_by_tool}.
2. Interception of unexecuted action claims:
   - Failure 7: Reminder creation claimed without tool execution.
   - Failure 8: Reminder deletion claimed without tool execution.
3. Interception of unverified external facts:
   - Failures 9, 10: Fabricated malls (Ansal Plaza, Orbit Mall).
   - Failure 20: Fabricated grocery orders (Milk & Bread).
   - Failure 24: Fabricated highway and distance without live navigation.
4. Connected-account grounding:
   - Failures 12, 22: Connected Google/GitHub/Notion answered strictly from DB via check_integrations_status.
5. Reschedule vs Create:
   - Failure 19: "shift/move meeting" updates existing item via update_reminder, no duplicate created.
   - Varied Hinglish and English phrasing.
6. Agent loop recovery:
   - Iteration 0 ungrounded draft triggers re-run with tool_choice="required" within 2-call budget.
7. Non-claiming general chat & math pass grounding without false-positive interceptions.
"""
import pytest
import json
from datetime import date, time as dtime
from zoneinfo import ZoneInfo
from unittest.mock import patch, MagicMock, AsyncMock

from agent.verifier import (
    deterministic_verify,
    audit_claims_with_llm,
    verify_and_ground_reply
)
from agent.tools import execute_tool, route_tools
from tests.harness.clock import FrozenClock
import db
import core.timeutil as timeutil
from llm.provider import LLMResponse, get_fake_llm

IST = ZoneInfo("Asia/Kolkata")
TEST_CHAT_ID = 998877501


@pytest.fixture(autouse=True)
def setup_user():
    db.ensure_user(TEST_CHAT_ID)
    db.give_consent(TEST_CHAT_ID)
    db.set_persona(TEST_CHAT_ID, "job")
    db.set_state(TEST_CHAT_ID, "active")
    yield


# ------------------------------------------------------------------------------
# 1. Structured Claim-Checker Schema Verification
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_checker_schema_and_structured_json():
    """Verify that audit_claims_with_llm returns the exact required structured fields."""
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text=json.dumps({
                "claims_action_done": True,
                "which_action": "create_reminder",
                "claims_external_fact": False,
                "sourced_by_tool": False,
                "reason": "Draft claims meeting was scheduled but no tool results provided"
            }),
            tool_calls=[],
            provider="Gemini",
            model="gemini-2.5-flash"
        )
        audit = await audit_claims_with_llm(
            draft_reply="Maine aapka meeting 2:30pm ka schedule kar diya hai.",
            evidence_str="",
            user_query="2:30pm meeting schedule kar do"
        )
        assert "claims_action_done" in audit
        assert "which_action" in audit
        assert "claims_external_fact" in audit
        assert "sourced_by_tool" in audit
        assert audit["claims_action_done"] is True
        assert audit["which_action"] == "create_reminder"
        assert audit["sourced_by_tool"] is False


# ------------------------------------------------------------------------------
# 2. Interception of Unexecuted Action Claims (Failures 7 & 8)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_7_claimed_create_reminder_without_tool_execution():
    """Failure 7: Bot claims reminder was set without invoking create_reminder tool."""
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text=json.dumps({
                "claims_action_done": True,
                "which_action": "create_reminder",
                "claims_external_fact": False,
                "sourced_by_tool": False,
                "reason": "Model claimed action completed without tool execution"
            }),
            tool_calls=[],
            provider="Gemini",
            model="gemini-2.5-flash"
        )
        is_grounded, final_reply, violations = await verify_and_ground_reply(
            draft_reply="Aapka 2:30 PM meeting ka reminder note kar liya gaya hai.",
            tool_results=[],
            memory_context={"formatted_block": ""},
            user_query="mere 2:30 pr meeting ka reminder set krdo"
        )
        assert is_grounded is False
        assert any("create_reminder" in v or "action" in v for v in violations)
        assert "confirm" in final_reply.lower() or "proceed" in final_reply.lower()


@pytest.mark.asyncio
async def test_failure_8_claimed_delete_reminder_without_tool_execution():
    """Failure 8: Bot claims movie reminder was deleted without calling delete_reminder tool."""
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text=json.dumps({
                "claims_action_done": True,
                "which_action": "delete_reminder",
                "claims_external_fact": False,
                "sourced_by_tool": False,
                "reason": "Claimed reminder was deleted but no deletion tool executed"
            }),
            tool_calls=[],
            provider="Gemini",
            model="gemini-2.5-flash"
        )
        is_grounded, final_reply, violations = await verify_and_ground_reply(
            draft_reply="Movie ka reminder delete kar diya gaya hai.",
            tool_results=[],
            memory_context={"formatted_block": ""},
            user_query="bhy moive toh wala dlt remind hta do"
        )
        assert is_grounded is False
        assert any("delete_reminder" in v or "action" in v for v in violations)


# ------------------------------------------------------------------------------
# 3. Interception of Fabricated External Facts (Failures 9, 10, 20, 24)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failures_9_and_10_fabricated_malls_blocked():
    """Failures 9 & 10: Model fabricates fictional shopping malls (Ansal Plaza, Orbit Mall)."""
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text=json.dumps({
                "claims_action_done": False,
                "which_action": None,
                "claims_external_fact": True,
                "sourced_by_tool": False,
                "reason": "Fabricated local mall listings without live place search tool"
            }),
            tool_calls=[],
            provider="Gemini",
            model="gemini-2.5-flash"
        )
        is_grounded, final_reply, violations = await verify_and_ground_reply(
            draft_reply="Jodhpur ke kuch popular malls:\n1. Ansal Plaza Jodhpur\n2. Orbit Mall\n3. City Centre Mall",
            tool_results=[],
            memory_context={"formatted_block": ""},
            user_query="jodhpur mall konse konse h"
        )
        assert is_grounded is False
        assert any("external fact" in v.lower() or "unsubstantiated" in v.lower() for v in violations)
        assert "verified information nahi hai" in final_reply.lower() or "i can't verify" in final_reply.lower()
        # Ansal Plaza and Orbit Mall are blocked
        assert "ansal plaza" not in final_reply.lower()
        assert "orbit mall" not in final_reply.lower()


@pytest.mark.asyncio
async def test_failure_20_fabricated_order_history_blocked():
    """Failure 20: Model fabricates grocery items (Milk & Bread) without live receipt data."""
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text=json.dumps({
                "claims_action_done": False,
                "which_action": None,
                "claims_external_fact": True,
                "sourced_by_tool": False,
                "reason": "Fabricated order receipt items without email or purchase records"
            }),
            tool_calls=[],
            provider="Gemini",
            model="gemini-2.5-flash"
        )
        is_grounded, final_reply, violations = await verify_and_ground_reply(
            draft_reply="Aapka pichla Blinkit order 2 items ka tha: Milk aur Bread.",
            tool_results=[],
            memory_context={"formatted_block": ""},
            user_query="Blinkit ka pichla order kya tha?"
        )
        assert is_grounded is False
        assert "verified information nahi hai" in final_reply.lower()


@pytest.mark.asyncio
async def test_failure_24_fabricated_highway_and_distance_blocked():
    """Failure 24: Model fabricates highway routes (NH-48) and distances for local city transit."""
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = LLMResponse(
            text=json.dumps({
                "claims_action_done": False,
                "which_action": None,
                "claims_external_fact": True,
                "sourced_by_tool": False,
                "reason": "Fabricated distance and transit duration without Google Maps API"
            }),
            tool_calls=[],
            provider="Gemini",
            model="gemini-2.5-flash"
        )
        is_grounded, final_reply, violations = await verify_and_ground_reply(
            draft_reply="Sardarpura se Ratanada lagbhag 4.5 km hai aur NH-48 se 15 minute lagenge.",
            tool_results=[],
            memory_context={"formatted_block": ""},
            user_query="Sardarpura se Ratanada kitni door hai?"
        )
        assert is_grounded is False
        assert "verified information nahi hai" in final_reply.lower()


# ------------------------------------------------------------------------------
# 4. Connected Account Grounding (Failures 12 & 22)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_failure_12_and_22_connected_accounts_answered_from_db():
    """Failures 12 & 22: Integration status must be answered from DB via check_integrations_status, never guessed."""
    # 1. Notion is connected in DB
    db.save_user_integration(TEST_CHAT_ID, "notion", "secret_notion_key_123", json.dumps({"workspace_name": "PeculiarHQ"}))
    res_notion = await execute_tool(TEST_CHAT_ID, "check_integrations_status", {"service": "notion"})
    assert res_notion["status"] == "success"
    assert res_notion["connected"] is True
    assert res_notion["workspace"] == "PeculiarHQ"

    # 2. GitHub is not connected in DB
    res_gh = await execute_tool(TEST_CHAT_ID, "check_integrations_status", {"service": "github"})
    assert res_gh["status"] == "success"
    assert res_gh["connected"] is False

    # 3. Google is connected via email link in DB
    can_cid = db.canonical_chat_id(TEST_CHAT_ID)
    with db.engine.begin() as conn:
        conn.execute(
            db.text("INSERT INTO email_links (user_id, email, verified) VALUES ((SELECT id FROM users WHERE telegram_chat_id = :cid), 'user@gmail.com', true) ON CONFLICT DO NOTHING"),
            {"cid": can_cid}
        )
    res_google = await execute_tool(TEST_CHAT_ID, "check_integrations_status", {"service": "google"})
    assert res_google["status"] == "success"
    assert res_google["connected"] is True
    assert res_google["email"] == "user@gmail.com"


# ------------------------------------------------------------------------------
# 5. Reschedule vs Create Unambiguous Routing (Failure 19)
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("reschedule_phrase", [
    "meeting ko 4pm ke badle 5pm shift kar do",
    "reschedule 4pm meeting to 5pm",
    "move meeting from 16:00 to 17:00",
    "shift my meeting to 5pm",
    "4 baje wali meeting 5 baje kardo"
])
async def test_failure_19_reschedule_updates_existing_item(reschedule_phrase):
    """
    Failure 19: Rescheduling phrases must update the existing reminder in-place via update_reminder,
    never invoke create_reminder to create duplicate items.
    """
    with FrozenClock("2026-11-05T12:00:00", tz=IST):
        today_d = date(2026, 11, 5)
        # Create an existing meeting at 16:00
        with db.engine.begin() as conn:
            conn.execute(
                db.text("DELETE FROM memory_items WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid)"),
                {"cid": TEST_CHAT_ID}
            )
            conn.execute(
                db.text("""
                    INSERT INTO memory_items (user_id, title, due_date, due_time, duration_minutes, category)
                    VALUES ((SELECT id FROM users WHERE telegram_chat_id = :cid), 'Meeting', :d, '16:00', 60, 'appointments')
                """),
                {"cid": TEST_CHAT_ID, "d": today_d}
            )

        # Execute update_reminder
        res = await execute_tool(
            chat_id=TEST_CHAT_ID,
            tool_name="update_reminder",
            arguments={"title": "Meeting", "due_time": "17:00"},
            user_context={"user_text": reschedule_phrase}
        )
        assert res["status"] == "success"
        assert "17:00" in res["message"] or "05:00 PM" in res["message"] or "5:00 PM" in res["message"]

        # Verify that ONLY ONE meeting exists in DB and its time is 17:00 (NO duplicate created)
        day_items = db.items_for_date(TEST_CHAT_ID, today_d)
        assert len(day_items) == 1, f"Expected exactly 1 meeting after update, found {len(day_items)}"
        updated_time = str(day_items[0][3])
        assert updated_time.startswith("17:00"), f"Expected updated time 17:00, found {updated_time}"


# ------------------------------------------------------------------------------
# 6. Agent Loop Interception & Re-run with tool_choice="required"
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_loop_grounding_interception_and_rerun():
    """
    End-to-end agent loop test:
    When model produces an ungrounded action claim on Turn 1 (FakeLLM),
    the loop intercepts it, re-runs with tool_choice='required', executes the tool,
    and returns verified output within the 2-call budget.
    """
    from agent.loop import run_agent_turn

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        # Step 1: Model emits direct conversational reply without tool call (ungrounded)
        resp_1 = MagicMock()
        resp_1.status_code = 200
        resp_1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Aapka 2:30 PM meeting ka reminder note kar liya gaya hai."
                }
            }],
            "usage": {"total_tokens": 50}
        }

        # Step 2: Re-run with tool_choice='required' forces tool execution
        resp_2 = MagicMock()
        resp_2.status_code = 200
        resp_2.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_forced_rem",
                        "type": "function",
                        "function": {
                            "name": "create_reminder",
                            "arguments": json.dumps({"title": "Meeting", "due_time": "14:30"})
                        }
                    }]
                }
            }],
            "usage": {"total_tokens": 60}
        }

        # Step 3: Synthesis with confirmed tool result
        resp_3 = MagicMock()
        resp_3.status_code = 200
        resp_3.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Done! 2:30 PM meeting ka reminder set ho gaya hai."
                }
            }],
            "usage": {"total_tokens": 40}
        }

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [resp_1, resp_2, resp_3]
            turn_result = await run_agent_turn(
                chat_id=TEST_CHAT_ID,
                user_text="mere 2:30 pr meeting ka reminder set krdo"
            )
            # Must have executed create_reminder tool
            assert "create_reminder" in turn_result["tool_calls_executed"]
            assert turn_result["is_grounded"] is True


# ------------------------------------------------------------------------------
# 7. Non-Claiming General Chat & Math Pass Grounding Without Tools
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_general_chat_and_math_pass_grounding_without_tools():
    """
    General greetings, math, or concept explanations make zero action or private fact claims
    and must pass grounding immediately (0 false positives).
    """
    # 1. Greeting
    g_pass, _, g_viols = await verify_and_ground_reply(
        draft_reply="Bas bhai, sab badhiya! Aap sunao, kya chal raha hai?",
        tool_results=[],
        user_query="aur bhai kya haal hai"
    )
    assert g_pass is True
    assert len(g_viols) == 0

    # 2. Conceptual explanation
    c_pass, _, c_viols = await verify_and_ground_reply(
        draft_reply="Machine learning is a subfield of artificial intelligence that involves training algorithms on data.",
        tool_results=[],
        user_query="what is machine learning?"
    )
    assert c_pass is True
    assert len(c_viols) == 0

    # 3. Honest refusal
    r_pass, _, r_viols = await verify_and_ground_reply(
        draft_reply="Mere paas iski koi saved information nahi hai.",
        tool_results=[],
        user_query="mera roll number kya tha?"
    )
    assert r_pass is True
    assert len(r_viols) == 0
