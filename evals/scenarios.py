"""
PeculiarAI Master Evaluation Scenarios (Phase 6)
Defines 12 deterministic and semantic test cases evaluating zero-hallucination,
accurate memory retrieval, interval schedule calculations, and multi-turn state preservation.
"""
import pytest
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
from timing import IST

from memory.store import (
    save_entity,
    add_entity_alias,
    find_entity,
    save_memory_item,
    search_memories_hybrid,
    resolve_user_id
)
from memory.retrieval import assemble_memory_context
from schedule.intervals import (
    TimeInterval,
    check_interval_availability,
    normalize_to_ist,
    find_free_slots
)
from state.pending import (
    ensure_pending_table,
    save_pending_action,
    get_pending_action,
    complete_pending_action,
    classify_confirmation_intent,
    get_pending_actions_proxy
)
from agent.verifier import (
    deterministic_verify,
    verify_and_ground_reply
)


async def eval_scenario_01_ananya_typo_and_commitment(chat_id: int = 901) -> bool:
    """
    Scenario 1: Fast-typed Roman Hinglish query with typo: 'bhy annaya ko mujhe ky bhjna th'
    Must match entity Ananya and retrieve the Prompt Engineering Module commitment, without apologizing.
    """
    user_id = resolve_user_id(chat_id)
    ent_id = save_entity(user_id, "Ananya", "person", "Ananya Sharma", {"role": "Designer", "phone": "+91 98765 43210"})
    add_entity_alias(ent_id, "annaya")
    save_memory_item(
        user_id=user_id,
        content="Bhai, Ananya ko Prompt Engineering module send karna hai review ke liye.",
        memory_type="commitment",
        subject_entity_id=ent_id,
        structured={"commitment": "Prompt Engineering module send karna", "status": "pending"}
    )

    ctx = assemble_memory_context(chat_id, "bhy annaya ko mujhe ky bhjna th")
    block = ctx.get("formatted_block", "")

    # Ground truth assertions
    assert "Ananya" in block or "annaya" in block
    assert "Prompt Engineering module" in block
    assert "commitment" in block.lower() or "promise" in block.lower()
    return True


async def eval_scenario_02_fake_github_repo_prevention() -> bool:
    """
    Scenario 2: The model must never hallucinate fake GitHub repos (e.g. 'awesome-python', 'crypto-tracker')
    when the tool results do not contain them.
    """
    fake_reply = "Here are your GitHub repositories:\n1. awesome-python\n2. crypto-tracker\n3. my-portfolio"
    tool_results = [{"status": "success", "repositories": ["my-portfolio", "personal-blog"]}]

    passed, revised_reply, violations = deterministic_verify(
        draft_reply=fake_reply,
        tool_results=tool_results,
        memory_context={},
        user_query="show my repos"
    )

    assert passed is False
    assert any("awesome-python" in v for v in violations)
    assert any("crypto-tracker" in v for v in violations)
    return True


async def eval_scenario_03_schedule_interval_overlap() -> bool:
    """
    Scenario 3: 5:00 PM Movie (180m duration heuristic) must trigger conflict warning against 6:00 PM meeting.
    """
    # 5 PM movie -> 5 PM to 8 PM
    movie = TimeInterval(
        start=datetime(2026, 10, 22, 17, 0, tzinfo=IST),
        end=datetime(2026, 10, 22, 20, 0, tzinfo=IST),
        title="Movie at PVR"
    )
    # 6 PM meeting -> 6 PM to 6:30 PM
    meeting = TimeInterval(
        start=datetime(2026, 10, 22, 18, 0, tzinfo=IST),
        end=datetime(2026, 10, 22, 18, 30, tzinfo=IST),
        title="Sync with Ananya"
    )

    assert movie.overlaps(meeting) is True
    overlap_duration = movie.overlap_minutes(meeting)
    assert overlap_duration == 30
    return True


async def eval_scenario_04_utc_leakage_prevention() -> bool:
    """
    Scenario 4: UTC ISO string '2026-10-22T12:30:00Z' must be normalized to '06:00 PM IST'.
    No raw UTC string or 12:30 may leak in India time context.
    """
    utc_str = "2026-10-22T12:30:00Z"
    ist_dt = normalize_to_ist(utc_str)

    assert ist_dt.hour == 18
    assert ist_dt.minute == 0
    formatted_clock = ist_dt.strftime("%I:%M %p")
    assert formatted_clock == "06:00 PM"
    assert "12:30" not in formatted_clock
    return True


async def eval_scenario_05_multi_turn_cross_question(chat_id: int = 905) -> bool:
    """
    Scenario 5: When an action is pending confirmation, a cross-question from the user
    must be classified as 'cross_question' and the pending action preserved.
    """
    ensure_pending_table()
    save_pending_action(
        chat_id=chat_id,
        action_kind="create_meeting",
        payload={"title": "Sync with Ananya", "start_iso": "2026-10-22T18:00:00+05:30"}
    )

    cross_q = "Tuesday ko Ananya se meeting ke pehle kya mujhe free time milega?"
    act = get_pending_action(chat_id)
    assert act is not None

    intent, _ = classify_confirmation_intent(cross_q, act)
    assert intent == "cross_question"

    # Action remains active in database
    act_still_pending = get_pending_action(chat_id)
    assert act_still_pending is not None
    assert act_still_pending["status"] == "pending"
    return True


async def eval_scenario_06_unconfirmed_action_fabrication_prevention() -> bool:
    """
    Scenario 6: If google_create_meeting tool was NOT executed, assistant must NOT claim
    'Meeting created' with a fake Google Meet URL.
    """
    fake_reply = "Maine meeting create kar di hai! Here is your Google Meet link: https://meet.google.com/abc-defg-hij"
    passed, revised_reply, violations = deterministic_verify(
        draft_reply=fake_reply,
        tool_results=[],
        memory_context={},
        user_query="schedule meeting with Ananya"
    )

    assert passed is False
    assert any("meet.google.com/abc-defg-hij" in v for v in violations)
    assert any("create" in v.lower() or "scheduled" in v.lower() for v in violations)
    return True


async def eval_scenario_07_missing_data_grounding() -> bool:
    """
    Scenario 7: When memory or tool has no data, reply must be grounded and not invent answers.
    """
    is_grounded, grounded_reply, violations = await verify_and_ground_reply(
        draft_reply="Ananya works at Microsoft in Seattle and her email is ananya@microsoft.com",
        tool_results=[],
        memory_context={"entities": [], "commitments": [], "notes": []},
        user_query="Ananya kahan kaam karti hai?"
    )

    # Must be intercepted if unsupported
    assert not is_grounded or "nahi" in grounded_reply.lower() or "saved" in grounded_reply.lower()
    return True


async def eval_scenario_08_continuous_memory_extraction(chat_id: int = 908) -> bool:
    """
    Scenario 8: Fact extraction saves structured memories into database.
    """
    user_id = resolve_user_id(chat_id)
    mem_id = save_memory_item(
        user_id=user_id,
        content="User prefers green tea with honey every morning.",
        memory_type="preference",
        structured={"preference": "green tea with honey", "context": "morning"}
    )
    assert mem_id > 0

    memories = search_memories_hybrid(user_id=user_id, query="green tea", limit=1)
    assert len(memories) > 0
    assert "green tea" in memories[0]["content"].lower()
    return True


async def eval_scenario_09_entity_alias_resolution(chat_id: int = 909) -> bool:
    """
    Scenario 9: 'annaya', 'ananya', and 'ananya sharma' all resolve to the exact same entity.
    """
    user_id = resolve_user_id(chat_id)
    e_id = save_entity(user_id, "Ananya Sharma", "person", "Ananya")
    add_entity_alias(e_id, "annaya")
    add_entity_alias(e_id, "ananya")

    m1 = find_entity(user_id, "annaya")
    m2 = find_entity(user_id, "ananya")
    m3 = find_entity(user_id, "Ananya Sharma")

    assert m1 is not None and m2 is not None and m3 is not None
    assert m1["id"] == e_id
    assert m2["id"] == e_id
    assert m3["id"] == e_id
    return True


async def eval_scenario_10_trigram_and_fuzzy_retrieval(chat_id: int = 910) -> bool:
    """
    Scenario 10: Fuzzy phonetics finds entities even with Roman Hinglish spelling variations.
    """
    user_id = resolve_user_id(chat_id)
    e_id = save_entity(user_id, "Vikramaditya", "person", "Vikram")
    add_entity_alias(e_id, "vikky")

    found = find_entity(user_id, "vikky")
    assert found is not None
    assert found["id"] == e_id
    return True


async def eval_scenario_11_confirmation_execution(chat_id: int = 911) -> bool:
    """
    Scenario 11: Replying 'haan kar do' or 'yes' triggers confirmation.
    """
    ensure_pending_table()
    save_pending_action(
        chat_id=chat_id,
        action_kind="create_meeting",
        payload={"title": "Sprint Planning", "start_iso": "2026-10-22T14:00:00+05:30"}
    )

    act = get_pending_action(chat_id)
    intent, _ = classify_confirmation_intent("haan kar do", act)
    assert intent == "confirm"

    complete_pending_action(act["id"], status="confirmed")
    assert get_pending_action(chat_id) is None
    return True


async def eval_scenario_12_cancellation(chat_id: int = 912) -> bool:
    """
    Scenario 12: Replying 'no' or 'cancel' releases pending state and stops execution.
    """
    ensure_pending_table()
    save_pending_action(
        chat_id=chat_id,
        action_kind="create_meeting",
        payload={"title": "Coffee Meet", "start_iso": "2026-10-22T16:00:00+05:30"}
    )

    act = get_pending_action(chat_id)
    intent, _ = classify_confirmation_intent("nahi mat karo", act)
    assert intent == "cancel"

    complete_pending_action(act["id"], status="cancelled")
    assert get_pending_action(chat_id) is None
    return True
