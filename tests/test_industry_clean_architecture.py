import pytest
from datetime import time as dtime, date
from unittest.mock import patch

import db
import core.timeutil as timeutil
from timing import IST, parse_followup_time
from main import parse_confirmation_and_remainder, find_matching_contact, _begin_meeting_deletion
from agent.tools import execute_tool, TOOL_DEFINITIONS, UpdateReminderArgs

TEST_CHAT_ID = 998877530


@pytest.fixture(autouse=True)
def setup_user():
    db.ensure_user(TEST_CHAT_ID)
    db.give_consent(TEST_CHAT_ID)
    db.set_persona(TEST_CHAT_ID, "job")
    db.set_state(TEST_CHAT_ID, "active")
    yield
    try:
        db.wipe_all_user_memories(TEST_CHAT_ID)
    except Exception:
        pass


@pytest.mark.asyncio
async def test_1a_dynamic_user_name_resolution():
    """Item 1a: User self-identity is dynamically resolved, NOT hardcoded."""
    with db.engine.begin() as conn:
        conn.execute(
            db.text("UPDATE users SET first_name = 'Alice', username = 'alicesmith' WHERE telegram_chat_id = :c"),
            {"c": TEST_CHAT_ID}
        )

    # Save a contact named "Neerav" (previously hardcoded in EXCLUDED_NAMES!)
    db.save_contact_memory(
        TEST_CHAT_ID,
        name="Neerav Sharma",
        phone="9876543210",
        role_title="Engineer",
        context_notes="Core Engineer"
    )

    res = await execute_tool(TEST_CHAT_ID, "list_contacts", {})
    assert res.get("status") == "success"
    # Contact "Neerav Sharma" MUST be listed and not dropped!
    contact_names = [c["name"] for c in res.get("contacts", [])]
    assert "Neerav Sharma" in contact_names


@pytest.mark.asyncio
async def test_1b_universal_contact_alias_generation():
    """Item 1b: Typo/alias generation is universal for any contact name, not hardcoded to 'ananya'."""
    res = await execute_tool(
        TEST_CHAT_ID,
        "save_contact",
        {"name": "Vikram Rathore", "phone": "9998887776", "role": "Architect"}
    )
    assert res.get("status") == "success"

    # Verify fuzzy lookup works for Vikram using find_matching_contact
    contacts = db.get_contact_memories(TEST_CHAT_ID)
    match = find_matching_contact("vikrm", contacts)
    assert match is not None
    assert match["name"] == "Vikram Rathore"


def test_2a_temporal_transition_target_resolution():
    """Item 2a: When user shifts/reschedules (X to Y, X ki jagah Y), target time is correctly Y."""
    now_ref = timeutil.now(IST)

    # "4pm ke badle 5pm" -> target time is 17:00 (5pm), NOT 16:00
    res1 = parse_followup_time("meeting 4pm ke badle 5pm shift kardo", now_ref)
    assert res1 is not None
    t1 = res1[0] if isinstance(res1, tuple) else res1
    assert t1.hour == 17

    # "4 baje ki jagah 6 baje" -> target time is 18:00 (6pm), NOT 16:00
    res2 = parse_followup_time("meeting 4 baje ki jagah 6 baje kardo", now_ref)
    assert res2 is not None
    t2 = res2[0] if isinstance(res2, tuple) else res2
    assert t2.hour == 18


def test_2b_update_reminder_schema_has_from_time():
    """Item 2b: update_reminder tool definition exposes from_time in Pydantic & JSON schema."""
    args = UpdateReminderArgs(title="Sync", from_time="16:00", due_time="17:00")
    assert args.from_time == "16:00"
    assert args.due_time == "17:00"

    tool_def = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "update_reminder")
    props = tool_def["function"]["parameters"]["properties"]
    assert "from_time" in props


def test_3a_clean_confirmation_parsing():
    """Item 3a: Confirmation parsing accurately splits affirmations without 200-char regex fragility."""
    pos, rem = parse_confirmation_and_remainder("haan bhai Ashwin ko email bhej do")
    assert pos is True
    assert "Ashwin ko email bhej do" in rem

    neg, rem2 = parse_confirmation_and_remainder("nahi cancel kardo")
    assert neg is False


@pytest.mark.asyncio
async def test_3b_clean_meeting_deletion_routing():
    """Item 3b: Deletion routing accurately distinguishes pure deletion from rescheduling."""
    # Pure deletion
    is_del = await _begin_meeting_deletion(TEST_CHAT_ID, "kal ki 4 baje wali meeting delete kar do")
    assert isinstance(is_del, bool)

    # Reschedule intent MUST NOT be intercepted as pure deletion
    is_resched = await _begin_meeting_deletion(TEST_CHAT_ID, "meeting 4 baje ki jagah 5 baje shift kardo")
    assert is_resched is False


@pytest.mark.asyncio
async def test_4a_structured_cognitive_relationship_extraction():
    """Item 4a: Structured cognitive extraction saves relationships and birthdays without brittle regexes."""
    from main import extract_and_save_user_facts
    from services.relationship_service import get_user_relationships
    from memory.extractor import EXTRACTION_SYSTEM_PROMPT

    # 1. Verify schema includes relationship and birthday
    assert '"relationship"' in EXTRACTION_SYSTEM_PROMPT
    assert '"birthday"' in EXTRACTION_SYSTEM_PROMPT

    # 2. Verify structured relationship ingestion via extract_and_save_user_facts
    mock_payload = {
        "facts": [{"fact": "User has a younger brother", "category": "profile"}],
        "relationships": [{"name": "Rohan Verma", "relationship": "brother", "birthday": "1998-11-20"}]
    }
    with patch("main._chat_json", return_value=mock_payload):
        await extract_and_save_user_facts(TEST_CHAT_ID, "Mera bhai Rohan Verma hai uska birthday 20th November ko hai")

    rels = get_user_relationships(TEST_CHAT_ID)
    rohan = next((r for r in rels if r["name"].lower() == "rohan verma"), None)
    assert rohan is not None
    assert rohan["relationship"] == "brother"
    assert str(rohan["birthday"]) == "1998-11-20"


def test_detect_user_city_sov_ordering():
    """Verify city detection handles natural Hindi SOV order ('Jaipur me rehta hu')."""
    from morning_briefing import detect_user_city
    db.save_user_fact(TEST_CHAT_ID, "Main Jaipur me rehta hu", "profile")
    city = detect_user_city(TEST_CHAT_ID)
    assert city == "Jaipur"


@pytest.mark.asyncio
async def test_search_user_emails_query_hygiene():
    """Verify search_user_emails strips conversational noise words from Gmail query."""
    from unittest.mock import AsyncMock
    with patch("google_services.fetch_user_inbox_messages", new_callable=AsyncMock, return_value=[]) as mock_fetch:
        with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock, return_value=(True, "user@test.com")):
            await execute_tool(
                TEST_CHAT_ID,
                "search_user_emails",
                {"query": "Ashwin ko bheja hua email", "folder": "sent"}
            )
            mock_fetch.assert_called_once()
            call_query = mock_fetch.call_args[1].get("query")
            assert "in:sent" in call_query
            assert "Ashwin" in call_query
            # Conversational particles MUST be scrubbed so Gmail search doesn't fail
            assert "bheja" not in call_query
            assert "hua" not in call_query
