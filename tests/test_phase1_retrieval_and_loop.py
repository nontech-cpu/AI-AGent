import pytest
import datetime
from memory.retrieval import extract_potential_entity_tokens, assemble_memory_context
from memory.store import (
    save_entity,
    find_entity,
    save_memory_item,
    search_memories_hybrid
)
from agent.tools import route_tools, CORE_TOOL_NAMES, TOOL_DEFINITIONS

TEST_USER_ID = 887755
TEST_CHAT_ID = 887755

@pytest.fixture(autouse=True)
def setup_test_user():
    import db
    from sqlalchemy import text
    with db.engine.begin() as conn:
        if db.engine.dialect.name == "postgresql":
            conn.execute(text("INSERT INTO users (id, telegram_chat_id) VALUES (:uid, :cid) ON CONFLICT (id) DO NOTHING"), {"uid": TEST_USER_ID, "cid": TEST_CHAT_ID})
        else:
            conn.execute(text("INSERT OR IGNORE INTO users (id, telegram_chat_id) VALUES (:uid, :cid)"), {"uid": TEST_USER_ID, "cid": TEST_CHAT_ID})


def test_devanagari_and_short_name_entity_lookup():
    # 1. Devanagari tokens extraction
    dev_tokens = extract_potential_entity_tokens("राहुल को कल कॉल करना है")
    assert "राहुल" in dev_tokens

    # 2. Short names extraction (e.g. Bo, Al, Om)
    short_tokens = extract_potential_entity_tokens("meet with Bo and Al at the office")
    assert "Bo" in short_tokens or "bo" in [t.lower() for t in short_tokens]
    assert "Al" in short_tokens or "al" in [t.lower() for t in short_tokens]

    # 3. Save Devanagari and Short-Name entities
    eid_dev = save_entity(TEST_USER_ID, "राहुल शर्मा", entity_type="person")
    eid_short = save_entity(TEST_USER_ID, "Bo", entity_type="person")

    # 4. Lookup verification
    found_dev = find_entity(TEST_USER_ID, "राहुल")
    assert found_dev is not None
    assert found_dev["id"] == eid_dev

    found_short = find_entity(TEST_USER_ID, "bo")
    assert found_short is not None
    assert found_short["id"] == eid_short


def test_hybrid_retrieval_and_min_score_threshold():
    # Store test memories
    m1 = save_memory_item(
        user_id=TEST_USER_ID,
        content="Ananya is the principal UI designer at Apex Studios",
        memory_type="semantic"
    )
    m2 = save_memory_item(
        user_id=TEST_USER_ID,
        content="User loves drinking black coffee with cardamom in the morning",
        memory_type="preference"
    )

    # Relevant query should match
    res = search_memories_hybrid(TEST_USER_ID, "who is UI designer Ananya", limit=3, min_score=0.015)
    assert len(res) >= 1
    assert "Ananya" in res[0]["content"]
    assert res[0]["score"] > 0.015

    # Completely unrelated query with strict score should return empty (nothing passes -> inject nothing)
    unrelated = search_memories_hybrid(TEST_USER_ID, "quantum particle entanglement astrophysics", limit=3, min_score=0.6)
    assert len(unrelated) == 0


def test_memory_block_capping():
    # Store long text in episodic memory
    long_note = "Detail item note. " * 200  # ~3600 characters
    save_memory_item(
        user_id=TEST_USER_ID,
        content=long_note,
        memory_type="notes"
    )

    ctx = assemble_memory_context(TEST_CHAT_ID, "Detail item note")
    block = ctx.get("formatted_block", "")
    assert len(block) <= 2450
    if len(long_note) > 2400:
        assert "</memory>" in block


def test_semantic_tool_routing():
    # 1. Core tools must ALWAYS be included in any turn
    routed_tools = route_tools("aaj ka din kaisa hai")
    routed_names = {t["function"]["name"] for t in routed_tools}
    for core_tool in CORE_TOOL_NAMES:
        assert core_tool in routed_names, f"Core tool '{core_tool}' missing from routed tools!"

    # 2. Weather query routes get_weather
    w_tools = route_tools("is it raining in Manali today?")
    w_names = {t["function"]["name"] for t in w_tools}
    assert "get_weather" in w_names

    # 3. GitHub query routes github tools
    gh_tools = route_tools("list all repositories and issues on github")
    gh_names = {t["function"]["name"] for t in gh_tools}
    assert any("github" in n for n in gh_names)


@pytest.mark.asyncio
async def test_agent_loop_repeat_guard_and_timeout():
    from unittest.mock import patch, AsyncMock
    import json
    from agent.loop import run_agent_turn

    repeat_call_count = 0

    class MockGroqResp:
        status_code = 200
        def __init__(self, is_second=False):
            self.is_second = is_second
        def json(self):
            nonlocal repeat_call_count
            repeat_call_count += 1
            if repeat_call_count <= 2:
                # Return identical tool call twice in a row
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "tool_calls": [{
                                "id": "call_rep_1",
                                "type": "function",
                                "function": {
                                    "name": "get_schedule",
                                    "arguments": json.dumps({"target_date": "2026-10-31"})
                                }
                            }]
                        }
                    }],
                    "usage": {"total_tokens": 50}
                }
            else:
                return {
                    "choices": [{
                        "message": {
                            "role": "assistant",
                            "content": "Schedule check ho gaya bhai."
                        }
                    }],
                    "usage": {"total_tokens": 50}
                }

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MockGroqResp()
        # Should detect repeated identical call and not infinite loop
        res = await run_agent_turn(TEST_CHAT_ID, "aaj ka schedule dikha")
        assert res is not None
        assert "reply" in res
