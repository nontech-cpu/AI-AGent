"""
Phase 3 Test Suite:
1. Safe AST Calculator (operations, percentages, functions, security guards).
2. Proactive Commitment State Machine & Nudges (max 3 nudges).
3. Web Search & Fetch URL Tools (structured results with citations).
4. Code-Level Grounding Verifier (fake action completion interception, live info checks).
"""
import pytest
import os
import json
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from sqlalchemy import text
import db
from memory.store import (
    resolve_user_id, create_commitment, get_commitments,
    update_commitment_status, get_commitments_due_for_nudge
)
from utilities import calculate
from agent.tools import execute_tool
from agent.verifier import deterministic_verify, verify_and_ground_reply

TEST_CHAT_ID = 887744


@pytest.fixture(autouse=True)
def setup_test_user():
    """Ensures test user exists in database."""
    with db.engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO users (telegram_chat_id)
                VALUES (:cid)
                ON CONFLICT (telegram_chat_id) DO NOTHING
            """),
            {"cid": TEST_CHAT_ID}
        )
    yield


def test_ast_calculator_safe_math():
    """Tests arithmetic operations, parentheses, percentages, and math functions."""
    # 1. Basic arithmetic and precedence
    r1 = calculate("10 + 20 * 3 - 5")
    assert r1["status"] == "success"
    assert r1["result"] == 65

    # 2. Parentheses
    r2 = calculate("(10 + 20) * (3 - 1)")
    assert r2["status"] == "success"
    assert r2["result"] == 60

    # 3. Percentages
    r3 = calculate("18% of 4500")
    assert r3["status"] == "success"
    assert r3["result"] == 810.0

    # 4. Math functions
    r4 = calculate("sqrt(144) + abs(-8)")
    assert r4["status"] == "success"
    assert r4["result"] == 20

    # 5. Rounding
    r5 = calculate("round(10 / 3, 2)")
    assert r5["status"] == "success"
    assert r5["result"] == 3.33

    # 6. Division by zero safety
    r6 = calculate("100 / 0")
    assert r6["status"] == "error"
    assert "Division by zero" in r6["message"]

    # 7. Security guard: Disallow __import__, eval, attributes, or builtins
    r7 = calculate("__import__('os').system('dir')")
    assert r7["status"] == "error"

    r8 = calculate("open('/etc/passwd')")
    assert r8["status"] == "error"

    # 8. Large exponent DoS prevention
    r9 = calculate("2 ** 5000")
    assert r9["status"] == "error"
    assert "Exponent too large" in r9["message"]


@pytest.mark.asyncio
async def test_commitments_state_machine_and_nudges():
    """Tests commitment creation, retrieval, status transition, and max 3 nudges."""
    uid = resolve_user_id(TEST_CHAT_ID)
    due_past = datetime.now() - timedelta(minutes=5)

    # 1. Create commitment
    cid = create_commitment(
        user_id=uid,
        title="Check expense after ₹1000 spend",
        description="User requested follow-up after reaching ₹1000",
        target_date=due_past,
        condition_type="threshold",
        condition_details={"amount": 1000, "metric": "expense"}
    )
    assert cid > 0

    # 2. Get commitments
    comms = get_commitments(user_id=uid)
    matched = [c for c in comms if c["id"] == cid]
    assert len(matched) == 1
    assert matched[0]["status"] == "pending"
    assert matched[0]["nudge_count"] == 0
    assert matched[0]["condition_details"].get("amount") == 1000

    # 3. Check due for nudge
    due_items = get_commitments_due_for_nudge(now_dt=datetime.now())
    due_ids = [d["id"] for d in due_items]
    assert cid in due_ids

    # 4. Simulate Nudge 1
    update_commitment_status(cid, uid, increment_nudge=True)
    comms1 = get_commitments(user_id=uid)
    assert [c for c in comms1 if c["id"] == cid][0]["nudge_count"] == 1

    # 5. Simulate Nudge 2 and Nudge 3
    update_commitment_status(cid, uid, increment_nudge=True)
    update_commitment_status(cid, uid, status="nudged", increment_nudge=True)

    comms3 = get_commitments(user_id=uid)
    item3 = [c for c in comms3 if c["id"] == cid][0]
    assert item3["nudge_count"] == 3
    assert item3["status"] == "nudged"

    # 6. Complete commitment
    update_commitment_status(cid, uid, status="completed")
    comms_done = get_commitments(user_id=uid)
    assert [c for c in comms_done if c["id"] == cid][0]["status"] == "completed"


@pytest.mark.asyncio
async def test_tool_execution_web_search_and_commitment():
    """Tests web_search and manage_commitment tool execution via execute_tool."""
    # 1. manage_commitment tool call
    res_create = await execute_tool(
        TEST_CHAT_ID,
        "manage_commitment",
        {"action": "create", "title": "Submit GST return on 20th"}
    )
    assert res_create["status"] == "success"
    c_id = res_create["commitment_id"]

    res_list = await execute_tool(
        TEST_CHAT_ID,
        "manage_commitment",
        {"action": "list"}
    )
    assert res_list["status"] == "success"
    assert any(c["id"] == c_id for c in res_list["commitments"])

    res_complete = await execute_tool(
        TEST_CHAT_ID,
        "manage_commitment",
        {"action": "complete", "commitment_id": c_id}
    )
    assert res_complete["status"] == "success"

    # 2. web_search mock call
    mock_search_results = {
        "status": "success",
        "query": "Jodhpur airport flights",
        "results": [
            {"title": "Jodhpur Airport Schedule", "url": "https://example.com/flights", "snippet": "Direct flights to Delhi and Mumbai"}
        ],
        "count": 1
    }
    with patch("utilities.search_web", new_callable=AsyncMock) as mock_sw:
        mock_sw.return_value = mock_search_results
        res_ws = await execute_tool(
            TEST_CHAT_ID,
            "web_search",
            {"query": "Jodhpur airport flights"}
        )
        assert res_ws["status"] == "success"
        assert res_ws["count"] == 1


@pytest.mark.asyncio
async def test_code_level_grounding_verifier():
    """Tests that unverified action completion claims and fake links are blocked."""
    # 1. Fake action claim: Model claims reminder was set, but tool results is empty or failed
    is_grounded, revised, violations = deterministic_verify(
        draft_reply="Haan maine aapka reminder set kar diya hai kal subah 9 baje ke liye.",
        tool_results=[],
        user_query="reminder laga do kal 9am"
    )
    assert is_grounded is False
    assert any("Claimed action completed without success" in v for v in violations)
    assert "complete nahi ho paya" in revised

    # 2. Succeeded action claim: Model claims reminder set AND tool result confirms success
    is_grounded_ok, revised_ok, violations_ok = deterministic_verify(
        draft_reply="Aapka reminder set kar diya gaya hai.",
        tool_results=[{"tool_name": "create_reminder", "status": "success", "message": "Reminder created"}],
        user_query="reminder laga do"
    )
    assert is_grounded_ok is True
    assert len(violations_ok) == 0

    # 3. Fake Google Meet link
    is_meet_ok, meet_revised, meet_viols = deterministic_verify(
        draft_reply="Meeting fix ho gayi: https://meet.google.com/abc-defg-hij join kar lena.",
        tool_results=[],
        user_query="meeting rakh lo"
    )
    assert is_meet_ok is False
    assert any("Fabricated Google Meet link" in v for v in meet_viols)
    assert "https://meet.google.com/abc-defg-hij" not in meet_revised
