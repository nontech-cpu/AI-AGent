import pytest
from agent.verifier import deterministic_verify, verify_and_ground_reply

def test_verifier_catches_fabricated_meet_link():
    draft = "Aapka meeting schedule ho gaya hai. Meet link: https://meet.google.com/fake-xyz-123"
    tool_results = [{"status": "needs_confirmation", "action": "create_meeting"}]
    memory_ctx = {"formatted_block": ""}

    passed, revised, violations = deterministic_verify(draft, tool_results, memory_ctx)
    assert passed is False
    assert any("Fabricated Google Meet link" in v for v in violations)
    assert "https://meet.google.com/fake-xyz-123" not in revised


def test_verifier_catches_unconfirmed_action_completion():
    draft = "Monday ko 5 baje ka meeting create kar di hai. Google Calendar invite bhej diya."
    # Tool only prepared confirmation, didn't actually create yet
    tool_results = [{"status": "needs_confirmation", "action": "create_meeting", "start_iso": "2026-10-27T17:00:00+05:30"}]
    memory_ctx = {"formatted_block": ""}

    passed, revised, violations = deterministic_verify(draft, tool_results, memory_ctx)
    assert passed is False
    assert any("Claimed action completed without success" in v for v in violations)
    assert "confirm" in revised.lower()


def test_verifier_catches_fake_github_repositories():
    draft = "Yeh rahe aapke repositories:\n- awesome-python\n- crypto-tracker\n- spamdetection"
    # Evidence only contains spamdetection and birthday
    tool_results = [{
        "status": "success",
        "username": "nontech-cpu",
        "repositories": ["birthday", "spamdetection"]
    }]
    memory_ctx = {"formatted_block": ""}

    passed, revised, violations = deterministic_verify(
        draft,
        tool_results,
        memory_ctx,
        user_query="mera github ke repos btao"
    )
    assert passed is False
    assert any("awesome-python" in v for v in violations)
    assert any("crypto-tracker" in v for v in violations)


def test_verifier_allows_grounded_reply():
    draft = "Ananya ko Tuesday ko module bhejna hai."
    tool_results = [{
        "found": True,
        "name": "Ananya Roy",
        "commitments": "Ananya ko module bhejna hai Tuesday ko"
    }]
    memory_ctx = {
        "formatted_block": "<memory>[Identified Contacts & Entities]: Ananya Roy</memory>"
    }

    passed, revised, violations = deterministic_verify(draft, tool_results, memory_ctx)
    assert passed is True
    assert revised == draft
    assert len(violations) == 0


@pytest.mark.asyncio
async def test_verify_and_ground_reply_full_flow():
    draft = "Bilkul, Ananya ko module bhejna tha Tuesday ko."
    tool_results = [{"found": True, "name": "Ananya Roy", "commitments": "module bhejna hai Tuesday ko"}]
    memory_ctx = {"formatted_block": "• Ananya Roy: module bhejna hai Tuesday ko"}

    is_grounded, final_reply, violations = await verify_and_ground_reply(
        draft_reply=draft,
        tool_results=tool_results,
        memory_context=memory_ctx,
        user_query="ananya ko kya bhejna tha"
    )
    assert is_grounded is True
    assert "module" in final_reply
