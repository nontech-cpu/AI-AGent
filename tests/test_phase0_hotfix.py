import pytest
import datetime
from memory.extractor import is_invalid_or_negative_fact, extract_and_persist_turn
from memory.store import (
    save_entity,
    find_entity,
    save_measurement,
    get_measurements,
    save_memory_item,
    search_memories_hybrid
)
from agent.tools import execute_tool

TEST_USER_ID = 887766
TEST_CHAT_ID = 887766

@pytest.fixture(autouse=True)
def setup_test_user():
    import db
    from sqlalchemy import text
    with db.engine.begin() as conn:
        if db.engine.dialect.name == "postgresql":
            conn.execute(text("INSERT INTO users (id, telegram_chat_id) VALUES (:uid, :cid) ON CONFLICT (id) DO NOTHING"), {"uid": TEST_USER_ID, "cid": TEST_CHAT_ID})
        else:
            conn.execute(text("INSERT OR IGNORE INTO users (id, telegram_chat_id) VALUES (:uid, :cid)"), {"uid": TEST_USER_ID, "cid": TEST_CHAT_ID})

def test_negative_fact_and_assistant_reply_filter():
    # 1. Negative absence statements must be dropped
    assert is_invalid_or_negative_fact("User has not saved yesterday's glucose reading") is True
    assert is_invalid_or_negative_fact("User has not shared glucose reading for yesterday.") is True
    assert is_invalid_or_negative_fact("koi reading nahi hai") is True
    assert is_invalid_or_negative_fact("Mere paas koi glucose reading saved nahi hai") is True
    assert is_invalid_or_negative_fact("There is no reading found in the system.") is True

    # 2. Reminder/scheduling requests must be rejected from memory facts
    assert is_invalid_or_negative_fact("Remind user at 10:00 AM daily to measure sugar") is True
    assert is_invalid_or_negative_fact("Reminder set for 'Follow up on laptop status'") is True

    # 3. Meta-claims and questions must be rejected
    assert is_invalid_or_negative_fact("Assistant told the user that sugar is normal") is True
    assert is_invalid_or_negative_fact("Did the user record blood pressure today?") is True

    # 4. Valid positive facts MUST pass
    assert is_invalid_or_negative_fact("Friend's fasting blood glucose was 84 mg/dL on 2026-10-29.") is False
    assert is_invalid_or_negative_fact("User's fasting sugar reading is 88 mg/dL on 2026-10-30.") is False
    assert is_invalid_or_negative_fact("User lives in Jodhpur.") is False


def test_subject_resolution_friend():
    # Create or resolve a friend entity
    eid = save_entity(TEST_USER_ID, "friend", entity_type="relation")
    assert eid is not None

    found = find_entity(TEST_USER_ID, "friend")
    assert found is not None
    assert found["id"] == eid
    assert found["id"] != TEST_USER_ID


def test_measurements_sql_lookup_and_tool():
    # Insert two test measurements: Day 1 (84) and Day 2 (88)
    d1 = datetime.datetime(2026, 10, 29, 8, 30, tzinfo=datetime.timezone.utc)
    d2 = datetime.datetime(2026, 10, 30, 8, 30, tzinfo=datetime.timezone.utc)

    m1_id = save_measurement(
        user_id=TEST_USER_ID,
        metric="glucose",
        value=84.0,
        unit="mg/dL",
        measured_at=d1,
        context="fasting"
    )
    assert m1_id is not None

    m2_id = save_measurement(
        user_id=TEST_USER_ID,
        metric="glucose",
        value=88.0,
        unit="mg/dL",
        measured_at=d2,
        context="fasting"
    )
    assert m2_id is not None

    # Query measurements directly via get_measurements SQL
    readings = get_measurements(TEST_USER_ID, metric="glucose", limit=5)
    assert len(readings) >= 2
    # Verify latest reading is 88
    latest = readings[0]
    assert float(latest["value"]) == 88.0
    assert "2026-10-30" in str(latest["measured_at"])

    # Query with date filter for day 1
    d1_readings = get_measurements(
        TEST_USER_ID,
        metric="glucose",
        date_from="2026-10-29",
        date_to="2026-10-29 23:59:59"
    )
    assert len(d1_readings) >= 1
    assert float(d1_readings[0]["value"]) == 84.0


@pytest.mark.asyncio
async def test_get_measurements_tool_execution():
    # Tool execution via execute_tool
    res = await execute_tool(
        chat_id=TEST_CHAT_ID,
        tool_name="get_measurements",
        arguments={
            "metric": "sugar",  # Aliased to glucose
            "date_from": "2026-10-30",
            "date_to": "2026-10-30"
        }
    )
    assert res.get("status") == "success"
    readings = res.get("readings", [])
    assert len(readings) >= 1
    assert float(readings[0]["value"]) == 88.0
    assert "88" in readings[0]["summary"]


def test_retrieval_excludes_reminders_and_negative_facts():
    from memory.retrieval import assemble_memory_context

    # Store a reminder in memories (should be filtered out by retrieval)
    save_memory_item(
        user_id=TEST_USER_ID,
        content="Remind user at 10am to measure sugar",
        memory_type="reminder"
    )
    # Store a valid episodic fact
    save_memory_item(
        user_id=TEST_USER_ID,
        content="User checked fasting sugar at 88 mg/dL on 2026-10-30",
        memory_type="episodic"
    )

    ctx = assemble_memory_context(TEST_CHAT_ID, "sugar reading")
    formatted = ctx.get("formatted_block", "")
    # Reminders must NOT be present in past notes
    assert "Remind user at 10am" not in formatted


@pytest.mark.asyncio
async def test_end_to_end_extraction_and_dedupe():
    from unittest.mock import patch, AsyncMock
    import json

    mock_llm_response = {
        "entities": [
            {"name": "friend", "entity_type": "relation"}
        ],
        "commitments": [],
        "measurements": [
            {
                "subject": "friend",
                "metric": "glucose",
                "value": 84.0,
                "unit": "mg/dL",
                "context": "fasting",
                "event_date": "2026-10-29"
            }
        ],
        "facts": [
            {
                "subject": "user",
                "fact": "User has not saved yesterday's glucose reading",
                "event_date": "2026-10-30"
            },
            {
                "subject": "friend",
                "fact": "Friend's fasting blood glucose was 84 mg/dL on 2026-10-29.",
                "event_date": "2026-10-29"
            }
        ]
    }

    class MockResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": json.dumps(mock_llm_response)}}]}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = MockResp()

        # Run extraction turn 1
        await extract_and_persist_turn(TEST_CHAT_ID, "mere friend ka sugar 84 tha kal", "theek hai")

        # Verify friend entity was created
        friend_ent = find_entity(TEST_USER_ID, "friend")
        assert friend_ent is not None
        assert friend_ent["id"] != TEST_USER_ID

        # Verify measurement was saved with friend's entity_id
        meas = get_measurements(TEST_USER_ID, metric="glucose", subject_entity_id=friend_ent["id"])
        assert len(meas) == 1
        assert float(meas[0]["value"]) == 84.0

        # Verify negative fact was DROPPED, and positive fact was SAVED
        memories = search_memories_hybrid(TEST_USER_ID, "glucose reading", limit=10)
        contents = [m["content"] for m in memories]
        assert not any("has not saved" in c for c in contents)
        assert any("84 mg/dL" in c for c in contents)

        # Run extraction turn 2 with identical data to verify deduplication
        await extract_and_persist_turn(TEST_CHAT_ID, "mere friend ka sugar 84 tha kal", "theek hai")
        meas_after = get_measurements(TEST_USER_ID, metric="glucose", subject_entity_id=friend_ent["id"])
        # Should NOT duplicate the row
        assert len(meas_after) == 1
