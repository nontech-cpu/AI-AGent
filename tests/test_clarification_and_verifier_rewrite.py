import json
from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

import db
from agent.loop import run_agent_turn
from agent.verifier import verify_and_ground_reply
from llm.provider import LLMResponse, get_fake_llm, reset_fake_llm
from timing import IST


@pytest.mark.asyncio
async def test_open_ended_plan_does_not_force_tool_choice_required(monkeypatch):
    """Open-ended trip/plan messages allow direct clarifying questions without forcing tool_choice='required'."""
    monkeypatch.setenv("USE_FAKE_LLM", "1")
    reset_fake_llm()
    fake_llm = get_fake_llm()

    chat_id = 998877810
    db.ensure_user(chat_id)
    db.set_user_preference(chat_id, reply_language="english", tone="concise")

    fake_llm.queue_response(
        text="How are you planning to travel to Ramdevra on Saturday, and what time do you plan to leave? I can check options or set a reminder once you share."
    )

    res = await run_agent_turn(chat_id, "I have to go ramdevra on this Saturday")
    assert "what time do you plan to leave" in res["reply"].lower()
    assert res["tool_calls_executed"] == []
    # Only 1 LLM call occurred — no forced tool_choice='required' retry
    assert len(fake_llm.history) == 1
    assert fake_llm.history[0]["tool_choice"] in (None, "auto")
    reset_fake_llm()


@pytest.mark.asyncio
async def test_stop_to_clarify_blocks_fabricated_default_after_needs_info(monkeypatch):
    """After create_reminder returns needs_info (missing due_time), retrying with invented 08:00 is blocked."""
    monkeypatch.setenv("USE_FAKE_LLM", "1")
    reset_fake_llm()
    fake_llm = get_fake_llm()

    chat_id = 998877811
    db.ensure_user(chat_id)
    db.set_user_preference(chat_id, reply_language="english", tone="concise")

    # Call 0: LLM tries create_reminder without due_time -> pre-validator returns needs_info
    fake_llm.queue_poisoned_tool_call(
        "create_reminder",
        {"title": "Trip to Ramdevra", "due_date": "2026-11-14", "request_quote": "go ramdevra on this Saturday"},
    )
    # Call 1: Even if LLM emits another create_reminder inventing 08:00, stop-to-clarify blocks execution
    fake_llm.queue_poisoned_tool_call(
        "create_reminder",
        {"title": "Trip to Ramdevra", "due_date": "2026-11-14", "due_time": "08:00", "request_quote": "go ramdevra on this Saturday"},
    )
    # Call 2: Clarifying question in English
    fake_llm.queue_response(text="What time are you leaving for Ramdevra on Saturday?")

    with patch("core.timeutil.now", return_value=datetime(2026, 11, 12, 16, 46, tzinfo=IST)):
        res = await run_agent_turn(chat_id, "I have to go ramdevra on this Saturday")

    assert "what time" in res["reply"].lower()
    with db.engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.title LIKE '%Ramdevra%'"
            ),
            {"c": chat_id},
        ).scalar()
        assert count == 0
    reset_fake_llm()


@pytest.mark.asyncio
async def test_evidence_aware_verifier_allows_grounded_search_web_travel_facts():
    """Verified search_web distance/route snippets must not trigger false GPS hijack."""
    draft = "Ramdevra is about 183 km from Jodhpur via NH62 (~3h 50m by bus or ~2h 57m by train). What time do you plan to leave?"
    tool_results = [
        {
            "tool_name": "search_web",
            "action": "search_web",
            "status": "success",
            "message": "Jodhpur to Ramdevra distance is 183 km via NH62; train takes 2h 57m, bus takes 3h 50m.",
            "results": [{"title": "Jodhpur to Ramdevra", "snippet": "183 km via NH62"}],
        }
    ]
    audit_payload = json.dumps({
        "claims_action_done": False,
        "which_action": None,
        "claims_external_fact": True,
        "claims_route_or_travel_time": True,
        "claims_unavailable_capability": False,
        "sourced_by_tool": True,
        "reason": "Grounded by search_web results",
    })
    with patch("llm.provider.chat", new=AsyncMock(return_value=LLMResponse(text=audit_payload, tool_calls=[]))):
        passed, reply, violations = await verify_and_ground_reply(
            draft_reply=draft,
            tool_results=tool_results,
            user_query="I have to go ramdevra on this Saturday",
            user_lang="english",
            explicit_lang_lock=True,
        )
    assert passed is True
    assert violations == []
    assert "183 km" in reply
    assert "Mere paas live GPS navigation" not in reply


@pytest.mark.asyncio
async def test_verifier_rewrites_ungrounded_claim_in_locked_english_language():
    """Explicit English lock keeps both critique-rewrite and deterministic fallback in English (never Hinglish)."""
    draft = "Take NH-62 bypass and you will reach in 22 minutes."
    audit_payload = json.dumps({
        "claims_action_done": False,
        "which_action": None,
        "claims_external_fact": True,
        "claims_route_or_travel_time": True,
        "claims_unavailable_capability": False,
        "sourced_by_tool": False,
        "reason": "No verified tool backed the 22-minute ETA",
    })
    rewritten_english = "I don't have live traffic access to predict the exact drive time, though you can check Google Maps. What time are you leaving on Saturday?"

    with patch(
        "llm.provider.chat",
        new=AsyncMock(side_effect=[
            LLMResponse(text=audit_payload, tool_calls=[]),
            LLMResponse(text=rewritten_english, tool_calls=[]),
        ]),
    ):
        passed, reply, violations = await verify_and_ground_reply(
            draft_reply=draft,
            tool_results=[],
            user_query="I have to go ramdevra on this Saturday",
            user_lang="english",
            explicit_lang_lock=True,
        )
    assert passed is False
    assert violations
    assert "Mere paas" not in reply
    assert "What time are you leaving on Saturday?" in reply


@pytest.mark.asyncio
async def test_single_grounding_pass_logs_real_interception_in_turn_traces(monkeypatch):
    """Phase 5 single authoritative grounding pass preserves real violations rather than re-verifying canned text."""
    monkeypatch.setenv("USE_FAKE_LLM", "1")
    reset_fake_llm()
    fake_llm = get_fake_llm()

    chat_id = 998877812
    db.ensure_user(chat_id)
    db.set_user_preference(chat_id, reply_language="english", tone="concise")

    # Call 0: read tool search_memory succeeds
    fake_llm.queue_poisoned_tool_call("search_memory", {"query": "ramdevra"})
    # Call 1 (iteration 1): LLM returns a draft claiming an unexecuted reminder action (caught by LLM claim checker -> rewrite)
    fake_llm.queue_response(text="Your Saturday reminder is all set.")

    # Mock audit_claims_with_llm to flag fake action completion, then rewrite returns cleaned English text
    audit_flag = {
        "claims_action_done": True,
        "which_action": "create_reminder",
        "claims_external_fact": False,
        "claims_route_or_travel_time": False,
        "claims_unavailable_capability": False,
        "sourced_by_tool": False,
        "reason": "Claimed create_reminder without executing it",
    }
    with patch("agent.verifier.audit_claims_with_llm", new=AsyncMock(return_value=audit_flag)), \
         patch("agent.verifier.rewrite_reply_with_grounding", new=AsyncMock(return_value="What time would you like me to schedule your reminder for Saturday?")):
        res = await run_agent_turn(chat_id, "I have to go ramdevra on this Saturday")

    assert res["is_grounded"] is False
    assert res["violations"]
    assert "What time would you like me to schedule" in res["reply"]
    reset_fake_llm()
