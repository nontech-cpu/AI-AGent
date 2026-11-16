"""
PeculiarAI - Comprehensive Self-Learning & Auto-Correction Architecture Tests
Validates the 4 Pillars of Continuous Self-Learning:
1. In-turn execution of learn_user_correction tool with instant cache invalidation.
2. Executive Steering Directive in system prompt with mandatory override weight.
3. Turn-Pair reflection in background cognitive extractor.
4. Database lifecycle: deduplication, updating, and cleanup of learned rules.
"""
import pytest
import datetime
from unittest.mock import AsyncMock, patch

import db
from agent.tools import execute_tool
from agent.prompt import build_runtime_system_prompt, invalidate_prompt_schedule_cache
from memory.extractor import EXTRACTION_SYSTEM_PROMPT, extract_and_persist_turn


def test_db_save_and_deduplicate_user_corrections():
    """Verify Pillar 4: save_user_correction saves, deduplicates, and updates rules."""
    chat_id = 888777111
    db.ensure_user(chat_id)

    # 1. Save new correction
    ok = db.save_user_correction(
        chat_id=chat_id,
        wrong_concept="Replying in Devanagari Hindi font",
        correct_rule="Always use Roman Hinglish script for Indian languages, never Devanagari Hindi",
        category="communication_style"
    )
    assert ok is True

    rules = db.get_user_corrections(chat_id)
    assert len(rules) == 1
    assert "Roman Hinglish" in rules[0]["correction_rule"]

    # 2. Re-save updated version of same rule (should deduplicate and update, NOT create second row)
    ok_upd = db.save_user_correction(
        chat_id=chat_id,
        wrong_concept="Replying in Devanagari Hindi font",
        correct_rule="STRICT: Always use Roman Hinglish script, never Devanagari Hindi font",
        category="communication_style"
    )
    assert ok_upd is True
    rules_after = db.get_user_corrections(chat_id)
    assert len(rules_after) == 1
    assert "STRICT: Always use Roman Hinglish" in rules_after[0]["correction_rule"]

    # 3. Format into executive string
    formatted = db.format_user_corrections(chat_id)
    assert "[MANDATORY RULE]" in formatted
    assert "Past Mistake to Avoid" in formatted
    assert "Replying in Devanagari Hindi font" in formatted

    # 4. Clean up / delete
    del_count = db.delete_user_correction(chat_id, "Devanagari")
    assert del_count >= 1
    assert len(db.get_user_corrections(chat_id)) == 0


@pytest.mark.asyncio
async def test_in_turn_learn_user_correction_tool_execution():
    """Verify Pillar 1: In-turn tool execution records rule and invalidates prompt cache."""
    chat_id = 888777222
    db.ensure_user(chat_id)
    invalidate_prompt_schedule_cache()

    args = {
        "wrong_concept": "Auto-saving receipt without confirmation",
        "correct_rule": "Always present confirmation card before logging expenses from photos",
        "category": "tool_behavior"
    }

    res = await execute_tool(chat_id, "learn_user_correction", args)
    assert res["status"] == "success"
    assert "Understood and permanently recorded" in res["message"]
    assert res["wrong_concept"] == "Auto-saving receipt without confirmation"

    # Verify rule is in DB
    rules = db.get_user_corrections(chat_id)
    assert len(rules) >= 1
    assert any("Always present confirmation card" in r["correction_rule"] for r in rules)

    # Clean up
    db.delete_user_correction(chat_id, "confirmation card")


@pytest.mark.asyncio
async def test_executive_steering_directive_prompt_injection():
    """Verify Pillar 2: Learned rules are injected as Top-Priority Executive Directive."""
    chat_id = 888777333
    db.ensure_user(chat_id)
    invalidate_prompt_schedule_cache()

    # When no rules exist, directive is empty (zero token waste)
    prompt_clean = await build_runtime_system_prompt(chat_id, "hello")
    assert "CRITICAL USER CORRECTIONS & PERMANENT LEARNED RULES" not in prompt_clean

    # Add a learned rule
    db.save_user_correction(
        chat_id=chat_id,
        wrong_concept="Calling phone numbers directly",
        correction_rule="Never claim to make phone calls, only send messages or offer links",
        category="capabilities"
    )
    invalidate_prompt_schedule_cache(chat_id)

    # Prompt must now prominently feature the executive directive
    prompt_with_rule = await build_runtime_system_prompt(chat_id, "hello")
    assert "### 🧠 CRITICAL USER CORRECTIONS & PERMANENT LEARNED RULES (MANDATORY OVERRIDE)" in prompt_with_rule
    assert "[MANDATORY RULE]: Never claim to make phone calls" in prompt_with_rule
    assert "VIOLATING ANY OF THESE RULES IS A CRITICAL SYSTEM FAILURE" in prompt_with_rule

    # Clean up
    db.delete_user_correction(chat_id, "phone calls")


def test_cognitive_extractor_has_correction_rule():
    """Verify Pillar 3: Cognitive extractor prompt explicitly mandates Rule 9 for user corrections."""
    assert "9. USER CORRECTIONS & BEHAVIORAL FEEDBACK (CRITICAL)" in EXTRACTION_SYSTEM_PROMPT
    assert '"corrections": [' in EXTRACTION_SYSTEM_PROMPT
    assert '"wrong_concept"' in EXTRACTION_SYSTEM_PROMPT
    assert '"correction_rule"' in EXTRACTION_SYSTEM_PROMPT
