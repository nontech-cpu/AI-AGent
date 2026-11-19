"""
tests/test_brain_dump_service.py
--------------------------------
Comprehensive unit and integration tests for Chaotic Voice & Text Brain Dump Auto-Structuring.
Verifies decomposition, resilient execution, schema validation, and zero regression across the agent.
"""

import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, date
from timing import IST
import core.timeutil as timeutil
import db
from services.brain_dump_service import (
    DecomposedItem,
    BrainDumpDecomposition,
    decompose_brain_dump,
    execute_brain_dump_item,
    format_brain_dump_summary,
    process_and_execute_brain_dump
)


@pytest.fixture(autouse=True)
def setup_test_user():
    chat_id = 998877881
    db.ensure_user(chat_id)
    return chat_id


@pytest.mark.asyncio
async def test_brain_dump_decomposition_mocked_llm(setup_test_user):
    """Verifies that decompose_brain_dump parses LLM JSON into typed Pydantic models."""
    mock_llm_json = """
    {
        "is_multi_intent": true,
        "summary_intent": "Scheduled service, call, expense, and saved idea",
        "items": [
            {
                "category": "calendar",
                "title": "Car Service",
                "details": "Annual checkup",
                "target_date": "2026-11-20",
                "target_time": "10:00",
                "duration_minutes": 60
            },
            {
                "category": "reminder",
                "title": "Call Rohit",
                "details": null,
                "target_date": "2026-11-20",
                "target_time": "12:00"
            },
            {
                "category": "expense",
                "title": "Electricity Bill",
                "amount": 1500.0,
                "merchant": "State Electricity Board",
                "details": "Online bill payment"
            },
            {
                "category": "idea",
                "title": "AI Personal Branding App",
                "details": "Platform for creators to manage their AI presence",
                "tags": ["#startup", "#ai"]
            },
            {
                "category": "reading",
                "title": "Future of AI Agents",
                "url": "https://example.com/ai-agents",
                "details": "Must read tech blog"
            }
        ]
    }
    """
    
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_resp = AsyncMock()
        mock_resp.text = mock_llm_json
        mock_chat.return_value = mock_resp

        res = await decompose_brain_dump("kal car service 10 baje, rohit ko call 12 baje, electricity bill 1500, startup idea, aur link padhna hai")
        
        assert res.is_multi_intent is True
        assert len(res.items) == 5
        
        # Validate items
        cat_map = {it.category: it for it in res.items}
        assert "calendar" in cat_map
        assert cat_map["calendar"].title == "Car Service"
        assert cat_map["calendar"].target_time == "10:00"
        
        assert "expense" in cat_map
        assert cat_map["expense"].amount == 1500.0
        
        assert "idea" in cat_map
        assert "#startup" in cat_map["idea"].tags


@pytest.mark.asyncio
async def test_execute_brain_dump_item_resilience_and_fallbacks(setup_test_user):
    """
    Verifies that execute_brain_dump_item gracefully executes each category:
    - Calendar item falls back to local reminder if Google Calendar is disconnected.
    - Expense is logged into DB.
    - Idea is saved into notes/vault.
    """
    chat_id = setup_test_user

    # 1. Calendar item (Google not connected -> local fallback)
    cal_item = DecomposedItem(
        category="calendar",
        title="Dentist Visit",
        target_date="2026-11-25",
        target_time="11:30"
    )
    res_cal = await execute_brain_dump_item(chat_id, cal_item)
    assert res_cal["status"] == "success"
    assert res_cal["category"] == "calendar"
    assert res_cal["title"] == "Dentist Visit"

    # Verify a reminder or calendar item was saved in DB
    user_items = db.items_for_date(chat_id, date(2026, 11, 25))
    assert any("Dentist" in (it[1] or "") for it in user_items)

    # 2. Expense item
    exp_item = DecomposedItem(
        category="expense",
        title="Dinner at Mainland China",
        amount=2450.0,
        merchant="Mainland China"
    )
    res_exp = await execute_brain_dump_item(chat_id, exp_item)
    assert res_exp["status"] == "success"
    assert res_exp["amount"] == 2450.0

    # 3. Idea item
    idea_item = DecomposedItem(
        category="idea",
        title="Micro-SaaS for Podcasters",
        details="Auto-edit video reels",
        tags=["#saas", "#ideas"]
    )
    res_idea = await execute_brain_dump_item(chat_id, idea_item)
    assert res_idea["status"] == "success"

    # Verify fact stored in DB
    facts = db.get_user_facts(chat_id)
    assert any("Micro-SaaS for Podcasters" in f["fact"] for f in facts)


def test_format_brain_dump_summary():
    """Verifies format_brain_dump_summary renders grouped categories cleanly."""
    mock_results = [
        {"category": "calendar", "status": "success", "title": "Car Service", "date": "2026-11-20", "time": "10:00", "note": ""},
        {"category": "reminder", "status": "success", "title": "Call Rohit", "date": "2026-11-20", "time": "12:00"},
        {"category": "expense", "status": "success", "title": "Electricity Bill", "amount": 1500.0, "merchant": "Discom"},
        {"category": "idea", "status": "success", "title": "AI SaaS", "tags": ["#startup", "#ai"]}
    ]

    summary = format_brain_dump_summary(mock_results, summary_intent="Daily tasks", user_lang="hinglish")
    
    assert "Brain Dump Sort & Organize Ho Gaya!" in summary
    assert "Car Service" in summary
    assert "Call Rohit" in summary
    assert "₹1,500.00" in summary
    assert "AI SaaS" in summary


@pytest.mark.asyncio
async def test_agent_tool_process_brain_dump_integration(setup_test_user):
    """Verifies that execute_tool('process_brain_dump') dispatches smoothly from agent layer."""
    from agent.tools import execute_tool

    chat_id = setup_test_user
    mock_llm_json = """
    {
        "is_multi_intent": true,
        "summary_intent": "Organized tasks",
        "items": [
            {
                "category": "reminder",
                "title": "Drink Water",
                "target_date": "2026-11-20",
                "target_time": "14:00"
            },
            {
                "category": "idea",
                "title": "Write newsletter about AGY",
                "details": "Draft key points",
                "tags": ["#writing"]
            }
        ]
    }
    """

    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_resp = AsyncMock()
        mock_resp.text = mock_llm_json
        mock_chat.return_value = mock_resp

        res = await execute_tool(
            chat_id,
            "process_brain_dump",
            {"raw_text": "paani peena hai 2 baje aur newsletter likhna hai AGY ke baare me"}
        )

        assert res["status"] == "success"
        assert res["items_count"] == 2
        assert "Drink Water" in res["formatted_summary"]
        assert "Write newsletter" in res["formatted_summary"]
