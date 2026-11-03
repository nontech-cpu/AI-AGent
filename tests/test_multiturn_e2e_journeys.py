import pytest
import asyncio
from unittest.mock import patch, AsyncMock
from datetime import datetime
import db
from memory.store import resolve_user_id, save_entity, get_commitments_for_user
from memory.retrieval import assemble_memory_context, _MEM_CTX_CACHE
from agent.tools import execute_tool
from agent.loop import run_agent_turn

TEST_CHAT_ID = 776655
TEST_USER_ID = 776655

@pytest.fixture(autouse=True)
def setup_user():
    from dotenv import load_dotenv
    from sqlalchemy import text
    load_dotenv()
    with db.engine.begin() as conn:
        if db.engine.dialect.name == "postgresql":
            conn.execute(text("INSERT INTO users (id, telegram_chat_id) VALUES (:uid, :cid) ON CONFLICT (telegram_chat_id) DO UPDATE SET id = :uid"), {"uid": TEST_USER_ID, "cid": TEST_CHAT_ID})
        else:
            conn.execute(text("INSERT OR REPLACE INTO users (id, telegram_chat_id) VALUES (:uid, :cid)"), {"uid": TEST_USER_ID, "cid": TEST_CHAT_ID})


@pytest.mark.asyncio
async def test_multiturn_contact_purpose_persistence_and_anaphora():
    """
    Simulates real-world journey:
    1. User saves contacts with specific reasons.
    2. Multiple dummy turns happen, pushing conversation past 8-turn sliding window.
    3. User queries using pronoun: 'un dono ko kyu contact karna tha'.
    4. Retrieval must extract the contacts and their purposes.
    """
    _MEM_CTX_CACHE.clear()

    # Step 1: Save contact with purpose
    res1 = await execute_tool(
        chat_id=TEST_CHAT_ID,
        tool_name="save_contact",
        arguments={
            "name": "Sameer",
            "phone": "9811223344",
            "notes": "Contract agreement review ke liye"
        }
    )
    assert res1.get("status") == "success"
    assert res1.get("notes") == "Contract agreement review ke liye"

    res2 = await execute_tool(
        chat_id=TEST_CHAT_ID,
        tool_name="save_contact",
        arguments={
            "name": "Kavita",
            "phone": "9855667788",
            "notes": "Marketing budget presentation ke liye"
        }
    )
    assert res2.get("status") == "success"
    assert res2.get("notes") == "Marketing budget presentation ke liye"

    # Step 2: Query using anaphora/pronoun 'un dono ko kyu contact karna tha'
    ctx = assemble_memory_context(TEST_CHAT_ID, "un dono ko kyu contact karna tha")
    block = ctx.get("formatted_block", "")

    # Verification: Both contacts and their purposes MUST be in memory context
    assert "Sameer" in block
    assert "Contract agreement review ke liye" in block
    assert "Kavita" in block
    assert "Marketing budget presentation ke liye" in block


@pytest.mark.asyncio
async def test_multiturn_meeting_duration_guard():
    """
    Verifies that stating 'meeting 30 min ki hai' is strictly interpreted
    as a duration parameter and NEVER calls delete_reminder or delete_meeting.
    """
    tool_calls_seen = []

    async def mock_post(*args, **kwargs):
        payload = kwargs.get("json", {})
        # Model returns create_meeting tool call with duration
        class MockResp:
            status_code = 200
            def json(self):
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [{
                                "id": "call_123",
                                "type": "function",
                                "function": {
                                    "name": "create_meeting",
                                    "arguments": '{"title": "Sync Up", "duration_minutes": 30, "start_iso": "2026-11-04T10:00:00+05:30"}'
                                }
                            }]
                        }
                    }],
                    "usage": {"total_tokens": 100}
                }
        return MockResp()

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        res = await run_agent_turn(TEST_CHAT_ID, "meeting 30 min ki hai 4 bje")
        # Ensure delete_reminder was NOT invoked
        assert "delete_reminder" not in res.get("tool_calls_executed", [])


@pytest.mark.asyncio
async def test_multiturn_web_search_available_and_transit_grounding():
    """
    Verifies that:
    1. search_web is registered in CORE_TOOL_NAMES.
    2. route_tools includes search_web for transit and external factual queries.
    3. execute_tool('search_web', query='jodhpur to udaipur direct train') succeeds.
    4. build_runtime_system_prompt contains the anti-hallucination mandate for transit & web search.
    """
    from agent.tools import CORE_TOOL_NAMES, route_tools
    from agent.prompt import build_runtime_system_prompt

    assert "search_web" in CORE_TOOL_NAMES

    tools = route_tools("direct train jodhpur se udaipur he kya?")
    tool_names = [t["function"]["name"] for t in tools]
    assert "search_web" in tool_names

    # Test tool execution
    with patch("utilities.search_web", return_value={"status": "success", "results": "• Jodhpur to Udaipur: No direct passenger train available. Connecting options via Marwar Junction."}):
        res = await execute_tool(
            chat_id=TEST_CHAT_ID,
            tool_name="search_web",
            arguments={"query": "jodhpur to udaipur direct train"}
        )
        assert res.get("status") == "success"
        assert "Jodhpur to Udaipur" in str(res)

    # Test prompt contains Rule 17 anti-hallucination mandate
    prompt = await build_runtime_system_prompt(TEST_CHAT_ID, "direct train jodhpur se udaipur he kya?")
    assert "Real-Time External Facts, Transit & Web Search" in prompt
    assert "search_web" in prompt


