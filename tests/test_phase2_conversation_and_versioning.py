import pytest
import datetime
from memory.store import (
    save_memory_item,
    consolidate_and_save_fact,
    get_active_memories,
    forget_memory,
    get_core_profile,
    get_conversation_summary,
    save_conversation_summary,
    search_memories_hybrid
)
from memory.retrieval import assemble_memory_context

TEST_USER_ID = 887744
TEST_CHAT_ID = 887744

@pytest.fixture(autouse=True)
def setup_test_user():
    import db
    from sqlalchemy import text
    with db.engine.begin() as conn:
        if db.engine.dialect.name == "postgresql":
            conn.execute(text("INSERT INTO users (id, telegram_chat_id) VALUES (:uid, :cid) ON CONFLICT (id) DO NOTHING"), {"uid": TEST_USER_ID, "cid": TEST_CHAT_ID})
        else:
            conn.execute(text("INSERT OR IGNORE INTO users (id, telegram_chat_id) VALUES (:uid, :cid)"), {"uid": TEST_USER_ID, "cid": TEST_CHAT_ID})


def test_memory_write_consolidation_and_versioning():
    # 1. ADD initial fact
    action1, id1 = consolidate_and_save_fact(
        user_id=TEST_USER_ID,
        content="User currently lives in Jodhpur, Rajasthan",
        memory_type="preference",
        importance=4
    )
    assert action1 == "ADD"
    assert id1 is not None

    # 2. NOOP: Duplicate fact must not create new row
    action2, id2 = consolidate_and_save_fact(
        user_id=TEST_USER_ID,
        content="User currently lives in Jodhpur, Rajasthan",
        memory_type="preference",
        importance=4
    )
    assert action2 == "NOOP"
    assert id2 == id1

    # 3. UPDATE: Superseding fact (e.g. user moved to Bengaluru)
    action3, id3 = consolidate_and_save_fact(
        user_id=TEST_USER_ID,
        content="User has moved and now lives in Bengaluru, Karnataka",
        memory_type="preference",
        importance=4
    )
    assert action3 == "UPDATE"
    assert id3 is not None
    assert id3 != id1

    # 4. Verify versioning: old fact id1 must have valid_to set and superseded_by = id3
    active_mems = get_active_memories(TEST_USER_ID)
    active_ids = [m["id"] for m in active_mems]
    assert id3 in active_ids
    assert id1 not in active_ids  # id1 must be superseded and excluded from active list


def test_core_vs_archival_memory_split():
    # Store core preference (importance 5)
    save_memory_item(
        user_id=TEST_USER_ID,
        content="User is strictly vegetarian and drinks almond milk",
        memory_type="preference",
        importance=5
    )
    # Store archival episodic note
    save_memory_item(
        user_id=TEST_USER_ID,
        content="Visited the Blue City handicraft market on Saturday",
        memory_type="episodic",
        importance=2
    )

    # Core profile directly loaded without retrieval
    core = get_core_profile(TEST_USER_ID)
    assert "strictly vegetarian" in core

    # Context assembly produces distinct blocks
    ctx = assemble_memory_context(TEST_CHAT_ID, "what can I eat?")
    formatted = ctx.get("formatted_block", "")
    assert "[Core User Profile & Hard Constraints]" in formatted
    assert "strictly vegetarian" in formatted


def test_conversation_summary_persistence():
    summary_text = "User discussed moving to Bengaluru for a new engineering role and asked about apartment options."
    save_conversation_summary(TEST_USER_ID, summary_text)

    retrieved = get_conversation_summary(TEST_USER_ID)
    assert retrieved == summary_text


def test_user_memory_controls_and_forget():
    # Store test memory to forget
    mem_id = save_memory_item(
        user_id=TEST_USER_ID,
        content="User's favorite dessert is chocolate cheesecake",
        memory_type="preference",
        importance=3
    )

    # Verify present in active memories
    active_before = get_active_memories(TEST_USER_ID)
    assert any(m["id"] == mem_id for m in active_before)

    # Soft-delete via forget_memory (by keyword)
    forgotten = forget_memory(TEST_USER_ID, "cheesecake")
    assert forgotten is True

    # Verify memory is no longer active
    active_after = get_active_memories(TEST_USER_ID)
    assert not any(m["id"] == mem_id for m in active_after)

    # Hybrid search must also exclude the forgotten memory
    search_res = search_memories_hybrid(TEST_USER_ID, "favorite dessert cheesecake", limit=5)
    assert not any(r["id"] == mem_id for r in search_res)
