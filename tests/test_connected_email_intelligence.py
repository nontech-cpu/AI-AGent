"""Tests for Autonomous Connected User Gmail Intelligence.

Verifies:
1. Email classification & extraction (Interview, Ticket, Bill, Delivery, Promo rejection).
2. Deduplication using processed_user_emails table.
3. Google user inbox fetching and parsing.
4. Background monitor loop alert dispatching and duplicate avoidance.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

from db import (
    engine,
    text,
    _init_processed_user_emails_table,
    is_user_email_processed,
    record_processed_user_email,
    get_all_connected_google_users,
)
from email_intelligence import analyze_incoming_email, _heuristic_analyze, _format_alert
from google_services import _extract_body_from_gmail_payload, fetch_user_inbox_messages


@pytest.fixture(autouse=True)
def setup_test_db():
    _init_processed_user_emails_table()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM processed_user_emails"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM processed_user_emails"))


def test_processed_user_emails_deduplication():
    chat_id = 998877
    email_id = "msg_abc_123"

    # Initially not processed
    assert is_user_email_processed(chat_id, email_id) is False

    # Record email
    record_processed_user_email(
        chat_id,
        email_id,
        sender="hr@company.com",
        subject="Interview Round 1",
        category="interview",
        is_important=True
    )

    # Now must be processed
    assert is_user_email_processed(chat_id, email_id) is True

    # Recording again does not error out or create duplicate
    record_processed_user_email(
        chat_id,
        email_id,
        sender="hr@company.com",
        subject="Interview Round 1",
        category="interview",
        is_important=True
    )
    assert is_user_email_processed(chat_id, email_id) is True

    # Different email ID for same user is not processed
    assert is_user_email_processed(chat_id, "msg_different") is False


def test_heuristic_interview_extraction():
    sender = "recruitment@google.com"
    subject = "Interview Invitation: Senior Software Engineer at Google"
    body = (
        "Dear Candidate, we invite you for a Technical Round on 28th October 2026 at 3:00 PM IST. "
        "Please connect using Google Meet: https://meet.google.com/xyz-uvwx-rst. Best regards."
    )

    res = _heuristic_analyze(sender, subject, body)
    assert res["is_important"] is True
    assert res["category"] == "interview"
    assert "google.com" in res["meet_link"] or "xyz-uvwx-rst" in res["meet_link"]
    assert "Google" in res["company"]

    alert = _format_alert(res)
    assert alert is not None
    assert "Interview / Meeting Scheduled!" in alert
    assert "xyz-uvwx-rst" in alert


def test_heuristic_ticket_booking_extraction():
    sender = "ticketadmin@irctc.co.in"
    subject = "Electronic Reservation Slip (ERS) - PNR 2458910234"
    body = (
        "Your train ticket from New Delhi to Mumbai Central is confirmed. "
        "PNR: 2458910234. Train: 12951 Mumbai Rajdhani. Date of journey: 30 Oct 2026."
    )

    res = _heuristic_analyze(sender, subject, body)
    assert res["is_important"] is True
    assert res["category"] == "ticket_booking"
    assert res["pnr"] == "2458910234"
    assert "IRCTC" in res["carrier"]

    alert = _format_alert(res)
    assert alert is not None
    assert "Ticket / Booking Confirmed!" in alert
    assert "2458910234" in alert


def test_heuristic_credit_card_bill_extraction():
    sender = "alerts@hdfcbank.net"
    subject = "HDFC Bank Credit Card Statement for Oct 2026"
    body = (
        "Dear Customer, your Credit Card statement is generated. "
        "Total Amount Due: Rs. 14,850.00. Payment Due Date: 05 Nov 2026."
    )

    res = _heuristic_analyze(sender, subject, body)
    assert res["is_important"] is True
    assert res["category"] == "bill_payment"
    assert "14,850" in res["amount"]
    assert "05 Nov 2026" in res["due_date"]

    alert = _format_alert(res)
    assert alert is not None
    assert "Bill Payment / Due Date Alert!" in alert
    assert "14,850" in alert
    assert "05 Nov 2026" in alert


def test_heuristic_urgent_delivery_extraction():
    sender = "shipment-update@amazon.in"
    subject = "Your Amazon package is out for delivery today"
    body = "Your order is arriving today by 7 PM. Share delivery OTP: 739102 with the delivery associate."

    res = _heuristic_analyze(sender, subject, body)
    assert res["is_important"] is True
    assert res["category"] == "delivery_urgent"
    assert res["otp"] == "739102"
    assert res["service"] == "Amazon"

    alert = _format_alert(res)
    assert alert is not None
    assert "Urgent Delivery / Order Update!" in alert
    assert "739102" in alert


def test_promotional_rejection_no_hallucination():
    sender = "marketing@myntra.com"
    subject = "Mega Flash Sale! Flat 60% Off on Brands"
    body = "Don't miss our festive sale. Use code FESTIVE60 to shop now. Click here to unsubscribe."

    res = _heuristic_analyze(sender, subject, body)
    assert res["is_important"] is False
    assert res["category"] == "promotional"

    alert = _format_alert(res)
    assert alert is None


@pytest.mark.asyncio
async def test_analyze_incoming_email_async():
    sender = "recruiter@uber.com"
    subject = "Uber | Technical Interview Schedule"
    body = "Join the Zoom interview link: https://uber.zoom.us/j/9876543210 on Friday at 4 PM."

    result = await analyze_incoming_email(sender, subject, body)
    assert result["is_important"] is True
    assert result["category"] == "interview"
    assert result["alert_text"] is not None
    assert "zoom.us" in result["alert_text"]


def test_extract_body_from_gmail_payload():
    import base64

    text_bytes = b"Hello from plain text body"
    encoded_text = base64.urlsafe_b64encode(text_bytes).decode().rstrip("=")

    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": encoded_text}
            }
        ]
    }

    extracted = _extract_body_from_gmail_payload(payload)
    assert extracted == "Hello from plain text body"


@pytest.mark.asyncio
async def test_fetch_user_inbox_messages_mocked():
    chat_id = 12345
    with patch("google_services.access_token_for_chat", AsyncMock(return_value=("fake_access_token", "user@gmail.com"))), \
         patch("google_services._request") as mock_req:

        mock_req.side_effect = [
            # 1. Message list response
            {"messages": [{"id": "m100", "threadId": "t100"}]},
            # 2. Full message details response
            {
                "id": "m100",
                "threadId": "t100",
                "snippet": "Meeting at 4pm",
                "payload": {
                    "headers": [
                        {"name": "Subject", "value": "Interview Scheduled"},
                        {"name": "From", "value": "hr@meta.com"},
                        {"name": "Date", "value": "Thu, 24 Oct 2026 10:00:00 +0000"},
                    ],
                    "mimeType": "text/plain",
                    "body": {"data": "SGVsbG8gV29ybGQ="} # 'Hello World'
                }
            }
        ]

        messages = await fetch_user_inbox_messages(chat_id, max_results=1)
        assert len(messages) == 1
        assert messages[0]["id"] == "m100"
        assert messages[0]["subject"] == "Interview Scheduled"
        assert messages[0]["sender"] == "hr@meta.com"


@pytest.mark.asyncio
async def test_connected_user_gmail_monitor_loop_end_to_end():
    from main import connected_user_gmail_monitor_loop

    chat_id = 771122
    mock_users = [{"telegram_chat_id": str(chat_id), "email": "candidate@gmail.com"}]
    mock_messages = [
        {
            "id": "email_interview_999",
            "sender": "talent@apple.com",
            "subject": "Apple Interview Invitation",
            "body": "Hi, your Technical Interview is confirmed for 29 Oct at 5 PM on Google Meet: https://meet.google.com/app-leco-mpt."
        },
        {
            "id": "email_promo_888",
            "sender": "marketing@store.com",
            "subject": "50% off on all items!",
            "body": "Shop now. Click to unsubscribe."
        }
    ]

    mock_say = AsyncMock()

    sleep_count_1 = 0
    async def fake_sleep_1(secs):
        nonlocal sleep_count_1
        sleep_count_1 += 1
        if sleep_count_1 > 1:
            raise asyncio.CancelledError()

    with patch("main.get_all_connected_google_users", return_value=mock_users), \
         patch("main.fetch_user_inbox_messages", AsyncMock(return_value=mock_messages)), \
         patch("main.say_raw", mock_say), \
         patch("asyncio.sleep", side_effect=fake_sleep_1):

        try:
            await connected_user_gmail_monitor_loop()
        except asyncio.CancelledError:
            pass

    # Verify say_raw was called exactly once (for the interview, NOT for the promo)
    assert mock_say.call_count == 1
    call_chat_id, call_text = mock_say.call_args[0]
    assert call_chat_id == chat_id
    assert "Interview / Meeting Scheduled!" in call_text
    assert "https://meet.google.com/app-leco-mpt" in call_text

    # Verify both emails are now recorded in processed_user_emails
    assert is_user_email_processed(chat_id, "email_interview_999") is True
    assert is_user_email_processed(chat_id, "email_promo_888") is True

    # Now simulate a second run with the same emails
    mock_say.reset_mock()
    sleep_count_2 = 0
    async def fake_sleep_2(secs):
        nonlocal sleep_count_2
        sleep_count_2 += 1
        if sleep_count_2 > 1:
            raise asyncio.CancelledError()

    with patch("main.get_all_connected_google_users", return_value=mock_users), \
         patch("main.fetch_user_inbox_messages", AsyncMock(return_value=mock_messages)), \
         patch("main.say_raw", mock_say), \
         patch("asyncio.sleep", side_effect=fake_sleep_2):

        try:
            await connected_user_gmail_monitor_loop()
        except asyncio.CancelledError:
            pass

    # Nothing should be called because they are already marked processed!
    assert mock_say.call_count == 0


@pytest.mark.asyncio
async def test_search_user_emails_tool():
    from agent.tools import execute_tool

    chat_id = 998877
    mock_messages = [
        {
            "id": "email_tkt_123",
            "sender": "irctc@indianrail.gov.in",
            "subject": "E-Ticket Confirmation",
            "date": "24 Oct 2026",
            "body": "Train 20495 / JU HDP SF EXP. PNR: 2524281551. Route: Jodhpur to Abu Road. 2 passengers. Total fare: Rs 599.0."
        }
    ]

    with patch("main.get_valid_google_connection_by_chat_id", AsyncMock(return_value=(True, "user@gmail.com"))), \
         patch("google_services.fetch_user_inbox_messages", AsyncMock(return_value=mock_messages)):

        res = await execute_tool(chat_id, "search_user_emails", {"query": "ticket"})
        assert res["status"] == "success"
        assert res["count"] == 1
        assert res["emails"][0]["id"] == "email_tkt_123"
        assert res["emails"][0]["is_important"] is True
        assert "2524281551" in (res["emails"][0]["alert_text"] or "")


@pytest.mark.asyncio
async def test_ticket_email_fast_path():
    from agent.loop import run_agent_turn

    chat_id = 998877
    mock_messages = [
        {
            "id": "email_tkt_123",
            "sender": "irctc@indianrail.gov.in",
            "subject": "E-Ticket Confirmation",
            "date": "24 Oct 2026",
            "body": "Train 20495 / JU HDP SF EXP. PNR: 2524281551. Route: Jodhpur to Abu Road. 2 passengers. Total fare: Rs 599.0."
        }
    ]

    call_count = 0
    groq_resp_1 = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_tkt_1",
                    "type": "function",
                    "function": {"name": "search_user_emails", "arguments": '{"query": "ticket"}'}
                }]
            }
        }],
        "usage": {"total_tokens": 100}
    }
    groq_resp_2 = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Aapka Ticket / Booking Confirmed ho gaya hai! PNR: 2524281551, Route: Jodhpur to Abu Road.",
                "tool_calls": None
            }
        }],
        "usage": {"total_tokens": 80}
    }

    async def mock_post(*args, **kwargs):
        is_telegram = any("telegram.org" in str(a) for a in args) or any("telegram.org" in str(v) for v in kwargs.values())
        if is_telegram:
            class MockTg:
                status_code = 200
                def json(self): return {"ok": True}
            return MockTg()
        nonlocal call_count
        call_count += 1
        req_json = kwargs.get("json", {})
        msgs = req_json.get("messages", [])
        sys_prompt = msgs[0].get("content", "") if msgs else ""
        is_claim_checker = "Grounding & Truth Claim-Checker" in sys_prompt
        is_first_turn = (call_count <= 2) and ("tool_choice" in req_json or "tools" in req_json)

        class MockResp:
            status_code = 200
            def json(self):
                if is_claim_checker:
                    return {
                        "choices": [{
                            "message": {
                                "role": "assistant",
                                "content": '{"claims_action_done": false, "which_action": null, "claims_external_fact": false, "claims_route_or_travel_time": false, "sourced_by_tool": true, "reason": "Ticket from search_user_emails"}',
                                "tool_calls": None
                            }
                        }],
                        "usage": {"total_tokens": 50}
                    }
                elif is_first_turn:
                    return groq_resp_1
                else:
                    return groq_resp_2
        return MockResp()

    with patch("httpx.AsyncClient.post", side_effect=mock_post), \
         patch("main.get_valid_google_connection_by_chat_id", AsyncMock(return_value=(True, "user@gmail.com"))), \
         patch("google_services.fetch_user_inbox_messages", AsyncMock(return_value=mock_messages)):

        turn = await run_agent_turn(chat_id, "mere ticket aaye h kuch btao usme se mail pr")
        assert "search_user_emails" in turn["tool_calls_executed"]
        assert "2524281551" in turn["reply"]
        assert "Ticket / Booking Confirmed" in turn["reply"]


@pytest.mark.asyncio
async def test_ticket_past_date_calendar_rejection():
    from agent.loop import run_agent_turn
    from state.active_context import set_active_ticket

    chat_id = 998877
    # Set active ticket with past date (03-Sep-2026)
    set_active_ticket(chat_id, {
        "pnr": "2524281551",
        "carrier": "Indian Railways",
        "route": "JODHPUR JN (JU) to ABU ROAD (ABR)",
        "datetime_str": "03-Sep-2026 22:00",
        "amount": "599.0",
        "passengers": "DEVENDRA, RISHI"
    })

    call_count = 0
    groq_tool_resp = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_past_1",
                    "type": "function",
                    "function": {"name": "validate_ticket_date", "arguments": '{"datetime_str": "03-Sep-2026 22:00"}'}
                }]
            }
        }],
        "usage": {"total_tokens": 100}
    }
    groq_final_resp = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Bhai, ye ticket purani hai (travel date nikal chuki hai), beete hue time ke liye calendar event ya reminder create nahi ho sakta!",
                "tool_calls": None
            }
        }],
        "usage": {"total_tokens": 80}
    }
    groq_final_resp_2 = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "Bhai, 2524281551 ticket purani hai (travel date nikal chuki hai), past date ke liye reminder add nahi ho sakta.",
                "tool_calls": None
            }
        }],
        "usage": {"total_tokens": 80}
    }

    async def mock_post(*args, **kwargs):
        is_telegram = any("telegram.org" in str(a) for a in args) or any("telegram.org" in str(v) for v in kwargs.values())
        if is_telegram:
            class MockTg:
                status_code = 200
                def json(self): return {"ok": True}
            return MockTg()
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 1:
            resp = groq_tool_resp
        elif call_count == 2:
            resp = groq_final_resp
        else:
            resp = groq_final_resp_2
        class MockResp:
            status_code = 200
            def json(self): return resp
        return MockResp()

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        # User asks to add it to calendar
        turn = await run_agent_turn(chat_id, "Mera ticket ko calender mai add kar do")
        assert "validate_ticket_date" in turn["tool_calls_executed"]
        assert "purani hai" in turn["reply"] or "past date" in turn["reply"]
        assert "nikal chuki hai" in turn["reply"] or "beete hue" in turn["reply"]

        # User references PNR: "2524281551 yeah wala"
        turn2 = await run_agent_turn(chat_id, "2524281551 yeah wala")
        assert "validate_ticket_date" in turn2["tool_calls_executed"]
        assert "purani hai" in turn2["reply"] or "past date" in turn2["reply"]


@pytest.mark.asyncio
async def test_ticket_future_date_calendar_scheduling():
    from agent.loop import run_agent_turn
    from state.active_context import set_active_ticket

    chat_id = 998877
    # Set active ticket with future date (25-Dec-2026)
    set_active_ticket(chat_id, {
        "pnr": "2524289999",
        "carrier": "Indian Railways",
        "route": "DELHI to MUMBAI",
        "datetime_str": "25-Dec-2026 18:00",
        "amount": "1200.0",
        "passengers": "DEVENDRA"
    })

    call_count = 0
    groq_tool_resp = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_remind_1",
                    "type": "function",
                    "function": {"name": "create_reminder", "arguments": '{"title": "Train Journey: Indian Railways (DELHI to MUMBAI)", "due_date": "2026-12-25", "due_time": "18:00"}'}
                }]
            }
        }],
        "usage": {"total_tokens": 100}
    }
    groq_final_resp = {
        "choices": [{
            "message": {
                "role": "assistant",
                "content": "✅ Aapki journey ke liye reminder set ho gaya hai! PNR: 2524289999",
                "tool_calls": None
            }
        }],
        "usage": {"total_tokens": 80}
    }

    async def mock_post(*args, **kwargs):
        is_telegram = any("telegram.org" in str(a) for a in args) or any("telegram.org" in str(v) for v in kwargs.values())
        if is_telegram:
            class MockTg:
                status_code = 200
                def json(self): return {"ok": True}
            return MockTg()
        nonlocal call_count
        call_count += 1
        class MockResp:
            status_code = 200
            def json(self): return groq_tool_resp if call_count == 1 else groq_final_resp
        return MockResp()

    with patch("httpx.AsyncClient.post", side_effect=mock_post), \
         patch("main.get_valid_google_connection_by_chat_id", AsyncMock(return_value=(True, "user@gmail.com"))), \
         patch("google_services.create_meeting", AsyncMock(return_value={"id": "cal_123"})):

        turn = await run_agent_turn(chat_id, "Ok laga do")
        assert "create_reminder" in turn["tool_calls_executed"]
        assert "reminder set" in turn["reply"].lower() or "ka reminder" in turn["reply"].lower()
        assert "2524289999" in turn["reply"] or "Train Journey" in turn["reply"]


@pytest.mark.asyncio
async def test_pre_connection_email_filtering():
    """Verifies that emails received BEFORE the user connected their account are NOT alerted to chat."""
    import time
    from main import connected_user_gmail_monitor_loop

    now_ts = time.time()
    chat_id = 998877

    # Mock user connected right now
    mock_users = [{
        "user_id": 1,
        "telegram_chat_id": str(chat_id),
        "email": "test@gmail.com",
        "refresh_token_enc": "dummy_enc",
        "scopes": "scopes",
        "created_at": None,
        "updated_at": None,
    }]

    # Pre-existing emails sitting in the inbox before connection
    mock_emails = [
        {
            "id": "old_msg_1",
            "sender": "airline@indigo.com",
            "subject": "Flight Ticket Confirmation",
            "body": "PNR: ABC123 Flight from Delhi to Mumbai",
            "internal_date_ms": int((now_ts - 7200) * 1000),  # 2 hours ago
        },
        {
            "id": "old_msg_2",
            "sender": "interviews@tech.com",
            "subject": "Interview Scheduled",
            "body": "Google Meet link: https://meet.google.com/abc",
            "internal_date_ms": int((now_ts - 3600) * 1000),  # 1 hour ago
        }
    ]

    with patch("main.get_all_connected_google_users", return_value=mock_users), \
         patch("main.fetch_user_inbox_messages", AsyncMock(return_value=mock_emails)), \
         patch("main.say_raw", AsyncMock()) as mock_say, \
         patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):

        try:
            await connected_user_gmail_monitor_loop()
        except asyncio.CancelledError:
            pass

        # Old pre-connection emails must NOT be alerted to chat!
        assert mock_say.call_count == 0, f"Expected 0 alerts for pre-connection emails, got {mock_say.call_count}"

        # And both emails must be recorded in baseline
        assert is_user_email_processed(chat_id, "old_msg_1") is True
        assert is_user_email_processed(chat_id, "old_msg_2") is True


@pytest.mark.asyncio
async def test_post_connection_new_email_triggers_alert():
    """Verifies that genuinely NEW emails arriving after linking DO trigger alerts."""
    import time
    from datetime import datetime, timezone
    from main import connected_user_gmail_monitor_loop

    now_ts = time.time()
    chat_id = 998877

    # Mark baseline as existing by recording a dummy baseline email
    record_processed_user_email(chat_id, "baseline_email_1", category="initial_baseline")

    # User connected 5 minutes ago
    conn_dt = datetime.fromtimestamp(now_ts - 300, tz=timezone.utc)
    mock_users = [{
        "user_id": 1,
        "telegram_chat_id": str(chat_id),
        "email": "test@gmail.com",
        "refresh_token_enc": "dummy_enc",
        "scopes": "scopes",
        "created_at": conn_dt,
        "updated_at": conn_dt,
    }]

    # A new flight booking email arrives right now (after connection)
    new_email = [
        {
            "id": "new_msg_future_1",
            "sender": "reservations@indigo.com",
            "subject": "Flight Ticket: Delhi to Bangalore",
            "body": "PNR: 6E9999 Flight 6E-204 Date: 25-Nov-2026 14:00 Seats: 12A",
            "internal_date_ms": int((now_ts + 10) * 1000),  # arriving after connection
        }
    ]

    with patch("main.get_all_connected_google_users", return_value=mock_users), \
         patch("main.fetch_user_inbox_messages", AsyncMock(return_value=new_email)), \
         patch("main.say_raw", AsyncMock()) as mock_say, \
         patch("asyncio.sleep", AsyncMock(side_effect=[None, asyncio.CancelledError()])):

        try:
            await connected_user_gmail_monitor_loop()
        except asyncio.CancelledError:
            pass

        # Since this email arrived AFTER connection, it MUST be alerted!
        assert mock_say.call_count == 1, f"Expected 1 alert for new email, got {mock_say.call_count}"
        call_text = mock_say.call_args[0][1]
        assert "6E9999" in call_text or "Flight" in call_text or "Booking" in call_text





