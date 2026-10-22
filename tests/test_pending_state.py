"""
Unit tests for PeculiarAI Persistent Multi-Turn State (Phase 5).
Validates DB persistence across restarts, cross-question preservation, and 100% backward-compatibility.
"""
import pytest
from datetime import datetime, timezone, timedelta
from state.pending import (
    ensure_pending_table,
    save_pending_action,
    get_pending_action,
    complete_pending_action,
    cancel_pending_action,
    classify_confirmation_intent,
    PersistentPendingDict,
    get_pending_actions_proxy,
)


@pytest.fixture(autouse=True)
def setup_state():
    ensure_pending_table()
    proxy = get_pending_actions_proxy()
    proxy.clear()
    yield
    proxy.clear()


def test_save_and_retrieve_pending_action():
    chat_id = 777101
    action_id = save_pending_action(
        chat_id=chat_id,
        action_kind="create_meeting",
        payload={
            "title": "Sync with Ananya",
            "start_iso": "2026-10-22T18:00:00+05:30",
            "duration_minutes": 30,
            "attendees": ["ananya@example.com"]
        }
    )
    assert action_id is not None

    action = get_pending_action(chat_id)
    assert action is not None
    assert action["action_kind"] == "create_meeting"
    assert action["payload"]["title"] == "Sync with Ananya"
    assert action["status"] == "pending"


def test_persistence_across_simulated_restart():
    chat_id = 777102
    proxy = get_pending_actions_proxy()
    proxy[chat_id] = {
        "kind": "send_email",
        "to": "client@example.com",
        "subject": "Proposal",
        "body": "Here is the proposal."
    }

    # Simulate process death: clear in-memory dictionary completely
    dict.clear(proxy)

    # State should be reloaded from DB
    assert chat_id in proxy
    loaded = proxy.get(chat_id)
    assert loaded is not None
    assert loaded["to"] == "client@example.com"


def test_classify_confirmation_intent():
    pending = {
        "action_kind": "create_meeting",
        "payload": {"title": "Meeting with Ananya", "start_iso": "2026-10-22T18:00:00+05:30"}
    }

    # Affirmative
    for phrase in ["haan", "yes", "kar do", "bilkul", "create it", "haanji", "sure"]:
        intent, _ = classify_confirmation_intent(phrase, pending)
        assert intent == "confirm", f"Failed for affirmative phrase: {phrase}"

    # Negative
    for phrase in ["no", "cancel", "mat karo", "rehne do", "nahi", "galat", "stop"]:
        intent, _ = classify_confirmation_intent(phrase, pending)
        assert intent == "cancel", f"Failed for negative phrase: {phrase}"

    # Cross-question (User asks for schedule/free time/information without confirming or rejecting)
    cross_queries = [
        "Tuesday ko Ananya se meeting ke pehle kya mujhe free time milega?",
        "kya us samay koi aur meeting hai?",
        "Ananya ka email address kya hai?",
        "what free time do i have before 6pm?",
        "mera schedule batao"
    ]
    for q in cross_queries:
        intent, _ = classify_confirmation_intent(q, pending)
        assert intent == "cross_question", f"Failed for cross question: {q}"


def test_cross_question_preserves_pending_action():
    chat_id = 777103
    save_pending_action(
        chat_id=chat_id,
        action_kind="create_meeting",
        payload={"title": "Client Sync", "start_iso": "2026-10-22T18:00:00+05:30"}
    )

    # User asks a cross-question
    cross_q = "Tuesday ko is meeting ke pehle kya main free hu?"
    act = get_pending_action(chat_id)
    assert act is not None

    intent, _ = classify_confirmation_intent(cross_q, act)
    assert intent == "cross_question"

    # Pending action must NOT be deleted or marked cancelled
    act_after = get_pending_action(chat_id)
    assert act_after is not None
    assert act_after["status"] == "pending"

    # Now user confirms
    intent_confirm, _ = classify_confirmation_intent("haan kar do", act_after)
    assert intent_confirm == "confirm"
    complete_pending_action(act_after["id"], status="confirmed")

    # Completed action is no longer pending
    assert get_pending_action(chat_id) is None


def test_superseding_older_action():
    chat_id = 777104
    save_pending_action(chat_id, "meeting", {"title": "Meeting 1"})
    save_pending_action(chat_id, "meeting", {"title": "Meeting 2"})

    act = get_pending_action(chat_id)
    assert act is not None
    assert act["payload"]["title"] == "Meeting 2"


def test_persistent_dict_proxy_isolation():
    proxy = get_pending_actions_proxy()
    user1 = 8881
    user2 = 8882

    proxy[user1] = {"title": "User 1 Meeting"}
    proxy[user2] = {"title": "User 2 Meeting"}

    # Pop user 1
    u1_act = proxy.pop(user1, None)
    assert u1_act["title"] == "User 1 Meeting"

    # User 2 must still exist
    assert user2 in proxy
    assert proxy[user2]["title"] == "User 2 Meeting"
