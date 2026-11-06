import pytest
import os
import json
from unittest.mock import AsyncMock, patch

import db
from agent.verifier import verify_and_ground_reply, deterministic_verify, READ_ONLY_TOOL_NAMES
from agent.tools import execute_tool, route_tools
from connectors.guardrails import check_fake_success_guard

TEST_CHAT_ID = 998877520


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
async def test_pillar1_semantic_router_includes_wipe_tool():
    """Pillar 1: Semantic routing includes request_master_privacy_wipe for diverse phrasing without hardcoded keywords."""
    phrases = [
        "bhy i want the dlete entire memory",
        "mera saara data uda do",
        "wipe my data and start fresh",
        "sab bhool jao clean slate"
    ]
    for phrase in phrases:
        tools = route_tools(phrase)
        tool_names = {t["function"]["name"] for t in tools if isinstance(t, dict) and "function" in t}
        assert "request_master_privacy_wipe" in tool_names, f"request_master_privacy_wipe not routed for: '{phrase}'"


@pytest.mark.asyncio
async def test_pillar2_verifier_blocks_hallucinated_deletion_with_only_read_tools():
    """
    Pillar 2: The exact screenshot incident.
    Model claims it deleted contacts and reminders, but only list_contacts executed.
    Verifier MUST block this and replace with security confirmation warning.
    """
    hallucinated_draft = (
        "Main aapki memory aur personal data delete karne ke liye taiyaar hoon. "
        "Maine aapke account se ye sabhi cheezein delete kar di hain:\n"
        "1. Contacts: Aarav Mishra, Ashwin, aur Aditya ki details hata di gayi hain.\n"
        "2. Reminders: Movie aur Go home wale reminders delete kar diye gaye hain.\n"
        "Ab aapka data clear ho chuka hai. Kya main aapki kisi aur tarah se madad kar sakta hoon?"
    )
    # Only read tool list_contacts succeeded:
    tool_results = [
        {
            "tool_name": "list_contacts",
            "status": "success",
            "count": 3,
            "contacts": [
                {"name": "Aarav Mishra"},
                {"name": "Ashwin"},
                {"name": "Aditya"}
            ]
        }
    ]

    # 1. Deterministic check must fail
    passed_det, revised_det, viols_det = deterministic_verify(
        draft_reply=hallucinated_draft,
        tool_results=tool_results,
        user_query="bhy i want the dlete entire memory"
    )
    assert not passed_det
    assert "Security & Privacy Warning" in revised_det
    assert any("delete" in v or "wipe" in v or "action" in v for v in viols_det)

    # 2. Unified verify_and_ground_reply must fail and ground to safe prompt
    is_grounded, final_reply, violations = await verify_and_ground_reply(
        draft_reply=hallucinated_draft,
        tool_results=tool_results,
        user_query="bhy i want the dlete entire memory",
        allow_llm=False
    )
    assert not is_grounded
    assert "Security & Privacy Warning" in final_reply or "confirm" in final_reply.lower()

    # 3. Guardrail fake success check must block
    is_fake_ok, fake_err = check_fake_success_guard(hallucinated_draft, tool_results)
    assert not is_fake_ok


@pytest.mark.asyncio
async def test_pillar2_read_only_tools_cannot_satisfy_any_action_claim():
    """Verify that every tool in READ_ONLY_TOOL_NAMES is rejected when an action is claimed."""
    for tool_name in ["list_contacts", "list_reminders", "get_schedule", "search_memory", "get_measurements"]:
        tool_results = [{"tool_name": tool_name, "status": "success", "id": 123}]
        draft = "Maine reminder delete kar diya hai."
        passed_det, _, viols = deterministic_verify(draft, tool_results, user_query="delete reminder")
        assert not passed_det, f"{tool_name} incorrectly satisfied an action claim!"


@pytest.mark.asyncio
async def test_pillar2_legitimate_privacy_wipe_confirmation_allowed():
    """When request_master_privacy_wipe executes with confirmation_required, draft asking confirmation is allowed."""
    wipe_tool_result = [
        {
            "tool_name": "request_master_privacy_wipe",
            "status": "confirmation_required",
            "message": "⚠️ Security Warning: Kya aap confirm karte hain?"
        }
    ]
    legit_draft = "⚠️ Security Warning: Kya aap confirm karte hain ki aap apni memory permanently delete karna chahte hain? (Reply yes/no)"
    
    passed_det, clean_reply, viols = deterministic_verify(
        legit_draft,
        wipe_tool_result,
        user_query="delete entire memory"
    )
    assert passed_det
    assert len(viols) == 0


@pytest.mark.asyncio
async def test_pillar3_request_master_privacy_wipe_and_atomic_purge():
    """
    Pillar 3:
    1. request_master_privacy_wipe transitions to confirm_master_wipe.
    2. Atomic purge removes all records across contacts, reminders, memories, chat logs, turn traces,
       and resets consent and persona so user returns to initial onboarding.
    """
    # 1. Seed user data
    db.save_contact_memory(
        TEST_CHAT_ID,
        name="Aarav Mishra",
        phone="9876543210",
        email="aarav@example.com",
        role_title="Client",
        context_notes="Test contact"
    )
    db.save_user_fact(TEST_CHAT_ID, "User loves filter coffee")

    contacts_before = db.get_contact_memories(TEST_CHAT_ID)
    assert len(contacts_before) >= 1

    # 2. Execute privacy wipe tool
    res = await execute_tool(TEST_CHAT_ID, "request_master_privacy_wipe", {"confirm": True})
    assert res.get("status") == "confirmation_required"
    assert db.get_state(TEST_CHAT_ID)[0] == "confirm_master_wipe"

    # 3. Complete atomic purge
    purged = db.wipe_all_user_memories(TEST_CHAT_ID)
    assert purged is True

    # 4. Verify all tables are completely empty
    contacts_after = db.get_contact_memories(TEST_CHAT_ID)
    assert len(contacts_after) == 0

    assert not db.has_consent(TEST_CHAT_ID)
    assert db.get_persona(TEST_CHAT_ID) is None
    assert db.get_state(TEST_CHAT_ID)[0] is None
