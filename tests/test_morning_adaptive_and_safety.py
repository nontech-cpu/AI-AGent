import pytest
from datetime import datetime, date, time as dtime, timedelta
from unittest.mock import patch, AsyncMock
from zoneinfo import ZoneInfo

from timing import IST
import db
from agent.verifier import deterministic_verify, verify_and_ground_reply
from morning_briefing import detect_early_morning_event, build_morning_briefing_message, process_user_morning_briefing
from state.active_context import set_active_ticket, clear_active_ticket, parse_ticket_datetime


TEST_CHAT_ID = 8877665544


def test_fix1_greeting_does_not_trigger_master_wipe_warning():
    """
    Verifies that a casual greeting ('Hi') does NOT trigger the Security & Privacy
    Master Wipe Warning, even if previous context or draft reply mentioned 'delete kar diye'.
    """
    user_query = "Hi"
    draft_reply = "Good morning! Aaj koi reminder nahi hai, pehle ke notes delete kar diye the."
    tool_results = [{
        "action": "list_reminders",
        "status": "success",
        "count": 0,
        "reminders": [],
        "tool_name": "list_reminders"
    }]

    passed, revised, violations = deterministic_verify(
        draft_reply=draft_reply,
        tool_results=tool_results,
        memory_context=None,
        user_query=user_query,
        recent_history=[]
    )

    # Must NOT inject the Master Privacy Wipe confirmation modal
    assert "Kya aap sach me apni saari memories" not in (revised or "")
    assert "Security & Privacy Warning" not in (revised or "")


@pytest.mark.asyncio
async def test_fix1_explicit_wipe_request_triggers_safety_warning():
    """
    Verifies that an explicit data wipe request ('mera saara data delete kar do')
    DOES trigger the Security & Privacy Warning if no verified wipe tool executed.
    """
    user_query = "mera saara data delete kar do"
    draft_reply = "Done! Maine aapki saari memory aur reminders delete kar diye hain."
    tool_results = []

    is_grounded, revised, violations = await verify_and_ground_reply(
        draft_reply=draft_reply,
        tool_results=tool_results,
        memory_context=None,
        user_query=user_query,
        recent_history=[],
        allow_llm=False
    )

    assert is_grounded is False
    assert "Security & Privacy Warning" in revised
    assert "Kya aap sach me apni saari memories" in revised


def test_fix2a_parse_ticket_datetime_robustness():
    """
    Verifies that diverse airline and railway ticket date formats parse cleanly.
    """
    dt1 = parse_ticket_datetime("10 Nov 2026, 06:00 AM")
    assert dt1 is not None
    assert dt1.day == 10 and dt1.month == 11 and dt1.year == 2026
    assert dt1.hour == 6 and dt1.minute == 0

    dt2 = parse_ticket_datetime("2026-11-10T06:00:00")
    assert dt2 is not None
    assert dt2.hour == 6


@pytest.mark.asyncio
async def test_fix2b_early_flight_adaptive_wakeup_and_checklist():
    """
    Verifies that if user has a flight at 6:00 AM on 10 Nov 2026:
    1. detect_early_morning_event detects it as an early flight.
    2. Adaptive trigger time is set ~2 hours earlier (~4:00 AM).
    3. build_morning_briefing_message synthesizes flight alert & required documents checklist.
    """
    target_d = date(2026, 11, 10)
    set_active_ticket(TEST_CHAT_ID, {
        "carrier": "IndiGo 6E-204",
        "route": "Delhi to Mumbai",
        "datetime_str": "10 Nov 2026, 06:00 AM",
        "pnr": "6E99X1",
        "seats": "12A",
        "summary": "Flight booking confirmed"
    })

    try:
        early = detect_early_morning_event(TEST_CHAT_ID, target_d)
        assert early is not None
        assert early["is_flight"] is True
        assert early["event_time"].hour == 6
        # 2 hours before flight: trigger time is 4:00 AM
        assert early["trigger_time"].hour == 4

        user_info = {"first_name": "Neerav"}
        msg, trigger_kind = await build_morning_briefing_message(
            TEST_CHAT_ID, user_info, target_d, early_event=early
        )

        assert trigger_kind == "early_flight"
        assert "Early Travel Alert" in msg
        assert "IndiGo 6E-204" in msg
        assert "6E99X1" in msg
        assert "Departure & Required Documents Checklist" in msg
        assert "Govt Photo ID" in msg
        assert "Boarding Pass" in msg
        assert "Airport Timing" in msg

    finally:
        clear_active_ticket(TEST_CHAT_ID)


@pytest.mark.asyncio
async def test_fix2b_early_flight_triggers_at_5am_not_8am():
    """
    Verifies that process_user_morning_briefing will trigger at 4:30 AM / 5:00 AM
    when a 6:00 AM flight is scheduled, rather than waiting until 8:00 AM.
    """
    target_d = date(2026, 11, 10)
    set_active_ticket(TEST_CHAT_ID, {
        "carrier": "Air India AI-101",
        "route": "BOM to DEL",
        "datetime_str": "10 Nov 2026, 06:00 AM",
        "pnr": "AI8899",
        "summary": "Morning Flight"
    })

    try:
        # At 05:00 AM IST on Nov 10, process_user_morning_briefing must trigger
        now_5am = datetime(2026, 11, 10, 5, 0, tzinfo=IST)
        user_info = {"first_name": "Neerav", "chat_id": TEST_CHAT_ID}

        with patch("morning_briefing.send_telegram_message", AsyncMock(return_value=True)) as mock_send:
            triggered = await process_user_morning_briefing(TEST_CHAT_ID, user_info, now_5am)
            assert triggered is True
            mock_send.assert_called_once()
            call_text = mock_send.call_args[0][1]
            assert "Early Travel Alert" in call_text
            assert "Air India AI-101" in call_text

    finally:
        clear_active_ticket(TEST_CHAT_ID)


def test_guardrail_allows_file_generation_without_false_positive():
    """
    Verifies that when execute_python_code produces a file artifact (files_sent),
    check_fake_success_guard recognizes it as confirmed dispatch and does NOT
    hijack the reply with 'Aapka request note kar liya gaya hai...'.
    """
    from connectors.guardrails import check_fake_success_guard

    draft_reply = "Glucose report generate kar di hai aur PDF bhej di hai."
    tool_results = [{
        "tool_name": "execute_python_code",
        "status": "success",
        "output": "PDF generated",
        "files_sent": ["Glucose_Report_Nirav.pdf"]
    }]

    is_safe, err = check_fake_success_guard(draft_reply, tool_results)
    assert is_safe is True
    assert err is None


@pytest.mark.asyncio
async def test_pure_feedback_fastpath_bypasses_grounding_refusal():
    """
    Verifies that conversational feedback ('Good', 'Mast', 'Thanks') does not get
    erroneously intercepted by external fact verifier with 'verified information nahi hai'.
    """
    user_query = "Good"
    draft_reply = "Bas bhai, sab badhiya! Kuch kaam ho toh batana."
    tool_results = []

    is_grounded, revised, violations = await verify_and_ground_reply(
        draft_reply=draft_reply,
        tool_results=tool_results,
        memory_context=None,
        user_query=user_query,
        recent_history=[],
        allow_llm=False
    )

    assert is_grounded is True
    assert "verified information nahi hai" not in revised
    assert revised == draft_reply


def test_semantic_routing_domain_isolation():
    """
    Verifies that route_tools cleanly isolates casual conversation from action tools,
    loading domain tools only when semantic intent warrants it (zero hardcoded word lists).
    """
    from agent.tools import route_tools

    # Pure conversational check-ins -> route_tools returns empty (no action tools)
    assert len(route_tools("aur bhy kesa h")) == 0
    assert len(route_tools("hi")) == 0
    assert len(route_tools("ho")) == 0
    assert len(route_tools("sab badhiya")) == 0

    # Real tasks -> route_tools activates relevant domains
    notion_tools = route_tools("FastAPI series delete kar do")
    notion_names = {t["function"]["name"] for t in notion_tools}
    assert "delete_notion_page" in notion_names

    email_tools = route_tools("email bhejo rahul ko")
    email_names = {t["function"]["name"] for t in email_tools}
    assert "send_email" in email_names



