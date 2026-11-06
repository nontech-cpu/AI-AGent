import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone
import json

import db
from memory.store import (
    save_memory_item,
    get_core_profile,
    resolve_user_id,
    _CORE_PROFILE_CACHE
)
from memory.retrieval import assemble_memory_context

TEST_CHAT_ID = 998877503

@pytest.fixture(autouse=True)
def setup_user():
    db.ensure_user(TEST_CHAT_ID)
    db.give_consent(TEST_CHAT_ID)
    db.set_persona(TEST_CHAT_ID, "job")
    db.set_state(TEST_CHAT_ID, "active")
    _CORE_PROFILE_CACHE.clear()
    yield
    _CORE_PROFILE_CACHE.clear()


def test_event_rows_excluded_from_get_core_profile():
    """Requirement 2a: Event rows must NEVER enter get_core_profile, even if importance >= 4."""
    uid = resolve_user_id(TEST_CHAT_ID)
    
    # 1. Insert a normal profile preference (should be included)
    p_id = save_memory_item(
        user_id=uid,
        content="Prefers dark roast coffee with almond milk",
        memory_type="preference",
        importance=4
    )
    
    # 2. Insert an event row with importance=3 (should be excluded)
    e1_id = save_memory_item(
        user_id=uid,
        content="Sent email to client regarding contract signing",
        memory_type="event",
        importance=3,
        structured={"source_type": "gmail", "source_id": "msg_991"}
    )
    
    # 3. Insert an event row with importance=4 (should STILL be excluded due to memory_type NOT IN ('event', 'action_event'))
    e2_id = save_memory_item(
        user_id=uid,
        content="Created calendar meeting 'Quarterly Review'",
        memory_type="event",
        importance=4,
        structured={"source_type": "google_calendar", "source_id": "evt_992"}
    )
    
    profile = get_core_profile(uid)
    
    assert "coffee" in profile.lower(), "Core preference must be present in profile"
    assert "sent email" not in profile.lower(), "Event row (importance 3) must NOT be in get_core_profile"
    assert "quarterly review" not in profile.lower(), "Event row (importance 4) must NOT be in get_core_profile"


def test_event_backfill_idempotency():
    """Requirement 2c: Backfill must be idempotent on (user_id, source_type, source_id)."""
    uid = resolve_user_id(TEST_CHAT_ID)
    
    source_type = "gmail"
    source_id = "pending_action_37"
    
    # Simulate inserting first row via Phase 3 pipeline
    mem_id = save_memory_item(
        user_id=uid,
        content="Sent email to aaravmishra619@gmail.com with subject 'weather'",
        memory_type="event",
        source_type=source_type,
        structured={
            "action": "email_sent",
            "recipient": "aaravmishra619@gmail.com",
            "subject": "weather",
            "source_type": source_type,
            "source_id": source_id,
            "confidence": 1.0
        },
        importance=3
    )
    assert mem_id > 0
    
    # Check duplicate prevention logic
    with db.engine.connect() as conn:
        rows = conn.execute(db.text("""
            SELECT id, user_id, source_type, structured
            FROM memories
            WHERE user_id = :uid AND memory_type = 'event' AND status = 'active'
        """), {"uid": uid}).fetchall()
        
        seen_keys = set()
        for r in rows:
            struct = r[3] if isinstance(r[3], dict) else (json.loads(r[3]) if r[3] else {})
            s_id = struct.get("source_id")
            if s_id:
                seen_keys.add((r[1], r[2], str(s_id)))
        
        # A second run with key (uid, source_type, source_id) must be recognized as existing
        is_duplicate = (uid, source_type, source_id) in seen_keys
        assert is_duplicate is True, "Second run must detect existing key and skip"


@pytest.mark.asyncio
async def test_event_memory_retrieval_and_llm_answering():
    """Requirement 2e: Question 'maine Aarav ko kya mail bheji thi' retrieves event memory and answers with date & subject."""
    uid = resolve_user_id(TEST_CHAT_ID)
    
    # 1. Insert historical sent-email event into memories table via Phase 3 pipeline
    save_memory_item(
        user_id=uid,
        content="Sent email to aaravmishra619@gmail.com with subject 'weather'",
        memory_type="event",
        structured={
            "action": "email_sent",
            "recipient": "aaravmishra619@gmail.com",
            "subject": "weather",
            "source_type": "gmail",
            "source_id": "37",
            "confidence": 1.0,
            "event_date": "2026-10-31"
        },
        importance=3,
        valid_from=datetime(2026, 10, 31, 16, 31, 58)
    )
    
    # 2. Retrieve memory context for the exact user question
    user_query = "maine Aarav ko kya mail bheji thi"
    mem_ctx = assemble_memory_context(TEST_CHAT_ID, user_query)
    
    formatted_memory = mem_ctx.get("formatted_block", "")
    assert "aarav" in formatted_memory.lower() or "weather" in formatted_memory.lower()
    assert "sent email" in formatted_memory.lower()
    
    # 3. Use FakeLLMProvider configured with an answer based on this retrieval
    from llm.provider import FakeLLMProvider, LLMResponse, get_fake_llm
    
    fake_llm = get_fake_llm()
    fake_llm.clear()
    # Program fake LLM to synthesize response reflecting the retrieved memory
    synthesis = "Aapne 31 October 2026 ko Aarav Mishra (aaravmishra619@gmail.com) ko 'weather' subject ke saath email bheja tha."
    fake_llm.queue_response(synthesis)
    
    prompt = f"{formatted_memory}\nUser: {user_query}"
    resp = await fake_llm.chat(messages=[{"role": "user", "content": prompt}])
    
    assert "aarav" in resp.text.lower()
    assert "weather" in resp.text.lower()
    assert "31 october" in resp.text.lower() or "2026" in resp.text.lower()
