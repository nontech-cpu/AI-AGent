"""
PeculiarAI Phase 4 Test Suite: Prompt & Tool Selection
Verifies:
1. Static system prompt principles (<= 4KB static part, zero scenario-specific examples, zero city/brand facts).
2. Dynamic prompt assembly and dynamic placeholder resolution (Time, Schedule, Memory, Integrations, Pending Actions).
3. Principled semantic tool routing (core tools always safe baseline, domain tool group semantic routing, multi-turn context inheritance).
4. Short-term context & database persistence for pending actions across simulated server restarts.
5. Multi-turn anaphora resolution via conversation history.
"""
import pytest
import os
import json
from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy import text
import db
from timing import IST
import core.timeutil as timeutil
from agent.prompt import RUNTIME_SYSTEM_PROMPT_TEMPLATE, build_runtime_system_prompt
from agent.tools import route_tools, TOOL_DEFINITIONS, CORE_TOOL_NAMES
from state.pending import (
    save_pending_action,
    get_pending_action,
    complete_pending_action,
    cancel_pending_action,
    classify_confirmation_intent,
    _PENDING_ACTION_CACHE
)

TEST_CHAT_ID = 887700


@pytest.fixture(autouse=True)
def setup_phase4_user():
    """Ensure test user exists and clear state caches."""
    _PENDING_ACTION_CACHE.clear()
    with db.engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO users (telegram_chat_id)
            VALUES (:cid)
            ON CONFLICT (telegram_chat_id) DO NOTHING
        """), {"cid": TEST_CHAT_ID})
        conn.execute(text("DELETE FROM pending_actions WHERE chat_id = :cid"), {"cid": TEST_CHAT_ID})
    yield
    _PENDING_ACTION_CACHE.clear()


# ------------------------------------------------------------------------------
# 1. Static Prompt Principle & Size Verification
# ------------------------------------------------------------------------------

def test_static_prompt_size_under_4kb():
    raw_bytes = RUNTIME_SYSTEM_PROMPT_TEMPLATE.encode("utf-8")
    assert len(raw_bytes) <= 4096, f"Static prompt template exceeded 4KB: {len(raw_bytes)} bytes"


def test_static_prompt_contains_core_principles():
    template = RUNTIME_SYSTEM_PROMPT_TEMPLATE
    # Identity and persona
    assert "PeculiarAI" in template
    # General problem-solving loop
    assert "Problem-Solving Loop" in template
    assert "Work out the user's goal" in template
    assert "What is needed" in template
    assert "What do I already know" in template
    assert "What is missing" in template
    assert "ask ONE short" in template
    assert "ACT immediately" in template
    # Grounding & Truth contract
    assert "Grounding & Truth Contract" in template
    assert "NEVER invent" in template
    assert "NEVER claim an action was performed" in template
    # Temporal grounding
    assert "Temporal Correctness" in template
    assert "{TODAY_ISO}" in template
    assert "{CURRENT_YEAR}" in template


def test_static_prompt_zero_scenario_examples_and_brand_facts():
    template = RUNTIME_SYSTEM_PROMPT_TEMPLATE.lower()
    # No specific test-fixture names or cities
    banned_words = [
        "k mall", "mohankall", "mohan mall", "ratan mall", "madhav complex",
        "aditya 9034898498", "sardarpura", "ratanada", "nh-48",
        "shopping at 4:30pm", "fasting glucose was 88", "zara", "h&m"
    ]
    for banned in banned_words:
        assert banned not in template, f"Banned scenario-specific term found in prompt: '{banned}'"


# ------------------------------------------------------------------------------
# 2. Dynamic Runtime Prompt Assembly
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dynamic_runtime_prompt_assembly():
    now_ist = timeutil.now(IST)
    prompt = await build_runtime_system_prompt(
        chat_id=TEST_CHAT_ID,
        user_text="What are my meetings today?",
        mem_ctx={"formatted_block": "<memory>[Profile]: User is an engineer</memory>"}
    )
    # Check placeholders are resolved
    assert "{NOW_TIME}" not in prompt
    assert "{TODAY_ISO}" not in prompt
    assert "{CURRENT_YEAR}" not in prompt
    assert "{MEMORY_CONTEXT_BLOCK}" not in prompt
    assert "{SCHEDULE_CONTEXT_BLOCK}" not in prompt

    # Real values present
    assert str(now_ist.year) in prompt
    assert "[Profile]: User is an engineer" in prompt


# ------------------------------------------------------------------------------
# 3. Principled Semantic Tool Routing
# ------------------------------------------------------------------------------

def test_route_tools_always_includes_core_tools():
    tools = route_tools("Hello there, how are you?")
    tool_names = {t["function"]["name"] for t in tools}
    # All core baseline tools must always be present
    for core_tool in CORE_TOOL_NAMES:
        assert core_tool in tool_names, f"Core tool '{core_tool}' missing from baseline routing"


def test_route_tools_attaches_domain_tools_for_domain_queries():
    # 1. Developer / GitHub query
    gh_tools = route_tools("Show me the readme file from my github repo")
    gh_names = {t["function"]["name"] for t in gh_tools}
    assert "get_github_file" in gh_names or "list_github_repos" in gh_names

    # 2. Workspace / Notion query
    notion_tools = route_tools("Create a new notion page with meeting notes")
    notion_names = {t["function"]["name"] for t in notion_tools}
    assert "create_notion_page" in notion_names

    # 3. Email communication query
    email_tools = route_tools("Send an email to john about the budget review")
    email_names = {t["function"]["name"] for t in email_tools}
    assert "send_email" in email_names

    # 4. Utilities / Weather query
    weather_tools = route_tools("What is the weather forecast today?")
    weather_names = {t["function"]["name"] for t in weather_tools}
    assert "get_weather" in weather_names


def test_route_tools_multi_turn_context_inheritance():
    # User says a short follow-up like "yes" or "proceed" with prior email draft context
    tools = route_tools("yes proceed please", context_text="Drafted email to Aarav with subject Meeting Notes")
    names = {t["function"]["name"] for t in tools}
    assert "send_email" in names


def test_casual_chat_does_not_pollute_specialized_tools():
    # A casual chat turn with no domain context should not attach heavy developer tools
    tools = route_tools("kya haal hai bhai, sab badhiya?")
    names = {t["function"]["name"] for t in tools}
    # Core tools present
    assert "create_reminder" in names
    assert "list_reminders" in names
    # Notion writing should not be randomly attached to casual chat
    assert "create_notion_page" not in names


# ------------------------------------------------------------------------------
# 4. Short-Term Context & Database Persistence Across Restarts
# ------------------------------------------------------------------------------

def test_pending_action_database_persistence_across_restarts():
    payload = {
        "title": "Review Design Tokens",
        "start_iso": "2026-11-06T15:00:00+05:30",
        "duration_minutes": 45
    }
    # 1. Save pending action to database
    action_id = save_pending_action(TEST_CHAT_ID, "create_meeting", payload, expires_minutes=15)
    assert action_id is not None

    # 2. Simulate complete server restart / process termination (clear in-memory caches)
    _PENDING_ACTION_CACHE.clear()

    # 3. Retrieve from database after restart
    recovered = get_pending_action(TEST_CHAT_ID)
    assert recovered is not None
    assert recovered["id"] == action_id
    assert recovered["action_kind"] == "create_meeting"
    assert recovered["payload"]["title"] == "Review Design Tokens"
    assert recovered["payload"]["duration_minutes"] == 45

    # 4. Confirm action and verify database state transition
    complete_pending_action(action_id, status="confirmed")
    _PENDING_ACTION_CACHE.clear()

    # Must no longer be pending
    assert get_pending_action(TEST_CHAT_ID) is None


def test_cross_question_preserves_pending_action():
    payload = {"recipient": "boss@company.com", "subject": "Status Report", "body": "All on track."}
    save_pending_action(TEST_CHAT_ID, "send_email", payload)

    pending = get_pending_action(TEST_CHAT_ID)
    assert pending is not None

    # User asks a cross-question instead of saying yes/no
    intent, _ = classify_confirmation_intent("Wait, did you get my earlier message?", pending)
    assert intent == "cross_question"

    # Pending action must remain intact in DB
    intact = get_pending_action(TEST_CHAT_ID)
    assert intact is not None
    assert intact["action_kind"] == "send_email"
    assert intact["payload"]["subject"] == "Status Report"


# ------------------------------------------------------------------------------
# 5. Multi-Turn Anaphora Resolution via Chat History
# ------------------------------------------------------------------------------

def test_multi_turn_anaphora_history_persistence():
    # Persist chat turns in DB
    db.log_chat_message(TEST_CHAT_ID, "user", "Set a reminder for Doctor appointment tomorrow at 10am")
    db.log_chat_message(TEST_CHAT_ID, "assistant", "Done! Reminder set for Doctor appointment tomorrow at 10:00 AM.")
    db.log_chat_message(TEST_CHAT_ID, "user", "Isko 11am kar do")

    # Retrieve recent chat history from DB
    history = db.get_recent_chat_history(TEST_CHAT_ID, limit=5)
    assert len(history) >= 3
    messages_content = [h.get("text") or h.get("content") for h in history]
    assert any("Doctor appointment" in m for m in messages_content)
    assert any("Isko 11am kar do" in m for m in messages_content)
