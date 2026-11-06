import pytest
from unittest.mock import patch, AsyncMock
from datetime import datetime, timezone
import json

import db
from memory.store import (
    record_action_event,
    get_core_profile,
    resolve_user_id,
    _CORE_PROFILE_CACHE
)
from memory.retrieval import assemble_memory_context
from state.pending import execute_pending_action

TEST_CHAT_ID = 998877504

@pytest.fixture(autouse=True)
def setup_user():
    db.ensure_user(TEST_CHAT_ID)
    db.give_consent(TEST_CHAT_ID)
    db.set_persona(TEST_CHAT_ID, "job")
    db.set_state(TEST_CHAT_ID, "active")
    _CORE_PROFILE_CACHE.clear()
    yield
    _CORE_PROFILE_CACHE.clear()


@pytest.mark.asyncio
async def test_email_hook_and_retrieval_flow():
    """
    Phase 7c Item 4: Mock send email -> row with embedding in memories table
    -> 'maine Aarav ko kya mail bheji thi' answered from retrieval.
    """
    uid = resolve_user_id(TEST_CHAT_ID)
    
    # 1. Mock google_services.send_email
    with patch("google_services.send_email", new=AsyncMock(return_value="msg_live_777")):
        action_payload = {
            "to": "aaravmishra619@gmail.com",
            "subject": "Quarterly Project Update",
            "body": "Here is the summary of project progress."
        }
        
        # Execute through confirmed action pipeline
        ok, reply = await execute_pending_action(
            chat_id=TEST_CHAT_ID,
            pending={
                "id": 9901,
                "action_kind": "send_email",
                "payload": action_payload
            }
        )
        assert ok is True
        assert "Email sent successfully" in reply

    # 2. Verify that an event memory row was inserted into memories table with embedding
    with db.engine.connect() as conn:
        rows = conn.execute(db.text("""
            SELECT id, user_id, memory_type, content, structured, importance, source_type, embedding
            FROM memories
            WHERE user_id = :uid AND memory_type = 'event' AND source_type = 'email'
            ORDER BY id DESC LIMIT 1
        """), {"uid": uid}).fetchall()
        
        assert len(rows) == 1, "An event memory row must be written by the email hook"
        row = rows[0]
        assert row[2] == "event"
        assert "aaravmishra619@gmail.com" in row[3]
        assert "Quarterly Project Update" in row[3]
        assert row[5] == 3  # Importance must be 3 (not 4)
        assert row[7] is not None, "Embedding must be generated for the event memory"

    # 3. Retrieve memory context for the exact question
    user_query = "maine Aarav ko kya mail bheji thi"
    mem_ctx = assemble_memory_context(TEST_CHAT_ID, user_query)
    formatted_memory = mem_ctx.get("formatted_block", "")
    
    assert "aarav" in formatted_memory.lower() or "quarterly project update" in formatted_memory.lower()
    assert "sent email" in formatted_memory.lower()

    # 4. Synthesize with FakeLLM to prove end-to-end recall
    from llm.provider import get_fake_llm
    fake_llm = get_fake_llm()
    fake_llm.clear()
    synthesis = "Aapne Aarav Mishra ko 'Quarterly Project Update' subject ke saath email bheja tha."
    fake_llm.queue_response(synthesis)

    prompt = f"{formatted_memory}\nUser: {user_query}"
    resp = await fake_llm.chat(messages=[{"role": "user", "content": prompt}])
    assert "aarav" in resp.text.lower()
    assert "quarterly project update" in resp.text.lower()


def test_record_action_event_idempotency():
    """
    Phase 7c Item 4: record_action_event must be idempotent on (user_id, source_type, source_id).
    """
    uid = resolve_user_id(TEST_CHAT_ID)
    src_type = "reminder"
    src_id = "rem_unique_5544"
    
    # First call
    id1 = record_action_event(
        chat_id_or_user_id=TEST_CHAT_ID,
        action_type="create_reminder",
        summary="Set reminder: 'Doctor Appointment' for tomorrow 10:00 AM",
        details={"title": "Doctor Appointment"},
        source_type=src_type,
        source_id=src_id,
        importance=3
    )
    assert id1 > 0
    
    # Second call with identical (user_id, source_type, source_id)
    id2 = record_action_event(
        chat_id_or_user_id=TEST_CHAT_ID,
        action_type="create_reminder",
        summary="Set reminder: 'Doctor Appointment' for tomorrow 10:00 AM",
        details={"title": "Doctor Appointment"},
        source_type=src_type,
        source_id=src_id,
        importance=3
    )
    assert id1 == id2, "Idempotent call must return existing memory ID without inserting duplicate"


def test_event_rows_excluded_from_get_core_profile():
    """
    Phase 7c Item 4: Event rows must NEVER enter get_core_profile.
    """
    uid = resolve_user_id(TEST_CHAT_ID)
    
    # Normal profile item
    record_action_event(
        chat_id_or_user_id=TEST_CHAT_ID,
        action_type="create_meeting",
        summary="Scheduled Google Calendar meeting 'Design Sprint' for 11:00 AM",
        source_type="calendar",
        source_id="cal_design_sprint_test",
        importance=3
    )
    
    profile = get_core_profile(uid)
    assert "design sprint" not in profile.lower(), "Event row must NOT enter get_core_profile"
