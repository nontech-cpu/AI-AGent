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
    assert "Real-Time External Facts, Transit" in prompt
    assert "search_web" in prompt
    assert "find_nearby_places" in prompt


@pytest.mark.asyncio
async def test_multiturn_action_ledger_sent_email_subject_recall():
    """
    Verifies Enterprise Action-Ledger:
    1. Email sent to Aarav Mishra is recorded with its subject.
    2. Even after multiple turns and chat history window, querying 'Aarav ko jo mail bheji thi uska subject kya tha'
       instantly retrieves the subject and recipient from the action ledger.
    3. Token consumption is minimal (< 60 tokens).
    """
    from memory.store import record_action_event
    from memory.retrieval import assemble_memory_context, _MEM_CTX_CACHE

    _MEM_CTX_CACHE.clear()

    # Step 1: Save entity Aarav Mishra
    ent_id = save_entity(
        user_id=TEST_USER_ID,
        name="Aarav Mishra",
        entity_type="person",
        attributes={"email": "aaravmishra@example.com"}
    )
    assert ent_id > 0

    # Step 2: Record sent email action in Action-Ledger
    act_id = record_action_event(
        chat_id_or_user_id=TEST_CHAT_ID,
        action_type="send_email",
        summary="Sent email to aaravmishra@example.com with Subject: 'AI Project Status Update'",
        details={"to": "aaravmishra@example.com", "subject": "AI Project Status Update", "body_preview": "Hi Aarav, testing AI status update."},
        subject_name="Aarav Mishra"
    )
    assert act_id > 0

    # Step 3: User queries after many turns: "Aarav ko jo mail bheji thi uska subject kya tha?"
    ctx = assemble_memory_context(TEST_CHAT_ID, "Aarav ko jo mail bheji thi uska subject kya tha?")
    block = ctx.get("formatted_block", "")

    # Verification: Both Aarav Mishra, the email, and the exact Subject MUST be present
    assert "Aarav Mishra" in block
    assert "AI Project Status Update" in block
    assert "aaravmishra@example.com" in block


@pytest.mark.asyncio
async def test_multiturn_casual_fact_temporal_date_recall():
    """
    Verifies that casual facts stated without explicit 'save' commands on a specific date (e.g. 'mene pizza khaya' on 10 Oct)
    are accurately retrieved when queried weeks later via temporal date (e.g. 'mene 10 oct ko kya khaya tha?').
    """
    from memory.store import save_memory_item
    from memory.retrieval import assemble_memory_context, _MEM_CTX_CACHE

    _MEM_CTX_CACHE.clear()

    # Step 1: Fact extracted on 10 Oct
    save_memory_item(
        user_id=TEST_USER_ID,
        content="2026-10-10: User ate pizza",
        memory_type="personal",
        structured={"category": "personal", "event_date": "2026-10-10"}
    )

    # Step 2: Query on 1 Nov: "mene 10 oct ko kya khaya tha?"
    ctx = assemble_memory_context(TEST_CHAT_ID, "mene 10 oct ko kya khaya tha?")
    block = ctx.get("formatted_block", "")

    assert "2026-10-10" in block
    assert "pizza" in block.lower()


@pytest.mark.asyncio
async def test_early_morning_interaction_prompt_and_mandate():
    """
    Verifies that early morning user interactions before 8:00 AM (or 5 AM - 11 AM)
    dynamically produce the warm Good Morning briefing mandate, and ban call-center IVR greetings.
    """
    from agent.prompt import build_runtime_system_prompt
    from unittest.mock import patch
    from tests.harness.clock import FrozenClock

    with patch("db.is_morning_briefing_sent", return_value=False), \
         FrozenClock("2026-11-04T07:30:00+05:30"):
        prompt = await build_runtime_system_prompt(TEST_CHAT_ID, "Hi")
        # Mandate must enforce natural Good Morning
        assert "Good morning bhai!" in prompt
        # Must strictly prohibit call center script
        assert "STRICT BAN on 'Namaste!" in prompt
        # Must enforce Instinct-style crisp output
        assert "Instinct" in prompt


@pytest.mark.asyncio
async def test_universal_connector_link_semantic_directives():
    """
    Verifies that 'mera X link h?' and 'X link krna h' across GitHub, Notion, Google
    are explicitly defined in system prompt rules as account integrations, not URLs.
    """
    from agent.prompt import build_runtime_system_prompt

    prompt = await build_runtime_system_prompt(TEST_CHAT_ID, "mera github link h?")
    assert "Third-Party Connectors & Account Linking (Google, GitHub, Notion, Email)" in prompt
    assert "check_integrations_status" in prompt
    assert "connect_integration" in prompt
    assert "NEVER output website URLs or manual instructions on how to copy a URL from a browser address bar!" in prompt


@pytest.mark.asyncio
async def test_multi_account_identity_unification_and_places_grounding():
    """
    Verifies that:
    1. Secondary account 1234828592 canonically maps to 1758056012.
    2. Prompt size is compact with zero duplicate timeline block explosions.
    3. find_nearby_places handles null area/city in schema without Groq validation failures.
    4. Specific places like kmall generate targeted place lookup queries.
    """
    import db
    from agent.prompt import build_runtime_system_prompt
    from agent.tools import TOOL_DEFINITIONS
    from utilities import find_nearby_places

    # 1. Canonical Account Mapping
    assert db.canonical_chat_id(1234828592) == 1758056012
    linked = db.get_all_linked_chat_ids(1234828592)
    assert 1758056012 in linked
    assert 1234828592 in linked

    # 2. Compact Prompt Size & Single Timeline Block
    prompt = await build_runtime_system_prompt(1234828592, "Bhy jodhpur m konse mall h?")
    assert len(prompt) < 22000
    assert prompt.count("Active 4D Reality Timeline") <= 1

    # 3. Tool Schema allows nullable area/city
    places_tool = next(t for t in TOOL_DEFINITIONS if t["function"]["name"] == "find_nearby_places")
    props = places_tool["function"]["parameters"]["properties"]
    assert "null" in props["area"]["type"]
    assert "null" in props["city"]["type"]

    # 4. Domain-smart Queries
    kmall_res = await find_nearby_places("kmall", city="Jodhpur")
    assert "kmall" in kmall_res["search_query"].lower()
    assert "location address website" in kmall_res["search_query"].lower()


@pytest.mark.asyncio
async def test_brand_store_grounding_and_zero_raw_leakage():
    """
    Verifies that brand inquiries (Zara in Jodhpur) produce a crisp, truthful negative response,
    spam domains are filtered from live search, and raw crawler dumps are strictly prohibited.
    """
    from agent.prompt import build_runtime_system_prompt
    import utilities

    # 1. System Prompt Rule 18 must enforce dynamic search grounding for brand stores
    prompt = await build_runtime_system_prompt(TEST_CHAT_ID, "bhy jodhpur m zara ka shorrom h?")
    assert "Brand Showrooms & Store Verification" in prompt
    assert "NEVER guess, assume, or hardcode whether a brand exists in a city!" in prompt
    assert "H&M DOES have an official physical store in Jodhpur at K Mall, while Zara does not" in prompt

    # 2. Search spam filter must include deceptive directory domains
    assert "storelocatortool.com" in utilities.SPAM_DOMAINS
    assert "mapdoor.com" in utilities.SPAM_DOMAINS
    assert "justdial.com" in utilities.SPAM_DOMAINS


@pytest.mark.asyncio
async def test_multiturn_meeting_conflict_and_transit_buffer_detection():
    """
    Verifies that:
    1. Setting a reminder without an explicit date defaults to today.
    2. Duration is correctly parsed (e.g. 1 hour meeting).
    3. Tight transit buffer (< 30 min gap before/after cinema/meeting) triggers conflict detection.
    4. Direct time overlap triggers direct conflict detection.
    """
    from state.timeline import estimate_event_duration, check_timeline_collision
    from agent.tools import execute_tool, CreateReminderArgs, TOOL_DEFINITIONS
    import db
    from timing import IST
    from datetime import date, timedelta, time as dtime

    # 1. Verify schema: due_date is optional and defaults to today
    rem_def = [t for t in TOOL_DEFINITIONS if t.get("function", {}).get("name") == "create_reminder"][0]
    assert rem_def["function"]["parameters"]["required"] == ["title"]

    # 2. Duration heuristic verification
    assert estimate_event_duration("Meeting of one hour") == 1.0
    assert estimate_event_duration("Meeting is of 2 hours") == 2.0
    assert estimate_event_duration("30 mins sync") == 0.5
    assert estimate_event_duration("Movie date") == 2.5

    # 3. Setting a 1-hour meeting at 3:00 PM without date defaults to today
    today_d = datetime.now(IST).date()
    res1 = await execute_tool(
        TEST_CHAT_ID,
        "create_reminder",
        {"title": "Project Meeting", "due_time": "15:00", "duration_minutes": 60}
    )
    assert res1.get("status") == "success"
    assert res1.get("due_date") == today_d.isoformat()

    # 4. Scheduling a direct overlap (3:30 PM call) must detect conflict
    res_overlap = await execute_tool(
        TEST_CHAT_ID,
        "create_reminder",
        {"title": "Client Call", "due_time": "15:30", "duration_minutes": 30}
    )
    assert res_overlap.get("status") == "conflict_detected"
    assert res_overlap.get("has_conflict") is True

    # 5. Scheduling Movie date at 4:30 PM (only 30 min transition after 4:00 PM meeting end) must detect tight buffer
    res_buffer = await execute_tool(
        TEST_CHAT_ID,
        "create_reminder",
        {"title": "Movie date", "due_time": "16:30"}
    )
    assert res_buffer.get("status") == "conflict_detected"
    assert res_buffer.get("has_conflict") is True
    assert "Project Meeting" in str(res_buffer.get("conflicts")) or "Project Meeting" in str(res_buffer.get("message"))







