"""
Automated Industrial Test Suite: Dynamic UPI Payment & Settlement Engine (NPCI Standard)
Verifies:
1. NPCI URL schema compliance and Pillow QR payment card rendering.
2. User UPI ID preference storage and format validation.
3. Multi-user dispatch: Generating QR code and auto-forwarding invoice to tagged payer.
4. Settlement lifecycle: Atomically completing invoice and dispatching real-time dual notifications.
5. Auto-clearing contact commitments / debt memory upon settlement.
6. FastAPI endpoints: Status inspection, manual confirmation, and gateway webhook callbacks.
7. Agent loop tool integration and semantic routing.
"""

import os
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

import db
from core.payment_engine import (
    build_upi_payment_url,
    generate_styled_qr_card,
    create_and_dispatch_payment_request,
    settle_and_notify_payment
)


@pytest.fixture(autouse=True)
def init_clean_db():
    db._init_payment_tables()
    yield


def test_build_upi_payment_url_npci_compliance():
    url = build_upi_payment_url(
        upi_id="nirav@okhdfcbank",
        payee_name="Nirav",
        amount=500.0,
        note="Dinner",
        ref_id="PAY-20261114-1234"
    )
    assert url.startswith("upi://pay?")
    assert "pa=nirav@okhdfcbank" in url
    assert "pn=Nirav" in url
    assert "am=500.00" in url
    assert "cu=INR" in url
    assert "tn=PAY-20261114-1234%3ADinner" in url or "tn=PAY-20261114-1234:Dinner" in url


def test_generate_styled_qr_card_creates_valid_png():
    upi_url = "upi://pay?pa=test@upi&pn=Tester&am=250.00&cu=INR&tn=PAY-001:Test"
    png_bytes = generate_styled_qr_card(
        upi_url=upi_url,
        amount=250.0,
        payee_name="Tester",
        upi_id="test@upi",
        note="Test Note",
        ref_id="PAY-001"
    )
    assert png_bytes is not None
    assert len(png_bytes) > 1000  # High-res PNG
    # Check PNG signature: 89 50 4E 47 0D 0A 1A 0A
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_set_and_get_user_upi_id():
    chat_id = 99887711
    # Invalid UPI format
    ok, msg = db.set_user_upi_id(chat_id, "invalid_upi_no_at_symbol")
    assert not ok
    assert "Invalid UPI ID" in msg

    # Valid UPI format
    ok, msg = db.set_user_upi_id(chat_id, "myaccount@okhdfcbank")
    assert ok
    retrieved = db.get_user_upi_id(chat_id)
    assert retrieved == "myaccount@okhdfcbank"


@pytest.mark.asyncio
async def test_create_and_dispatch_payment_request_and_settle():
    sender_chat = 77112233
    payer_chat = 77112244

    # Setup accounts
    db.ensure_user(sender_chat)
    db.claim_username(sender_chat, "sender_nirav")
    db.set_user_upi_id(sender_chat, "nirav@icici")

    db.ensure_user(payer_chat)
    db.claim_username(payer_chat, "payer_rahul")
    db.set_user_phone_number(payer_chat, "9461089998")

    # Also record a contact commitment for Rahul
    db.save_contact_memory(
        sender_chat,
        name="Rahul",
        phone="9461089998",
        commitments="Rahul se 500 lena hai dinner ka"
    )

    with patch("main.send_file", new_callable=AsyncMock) as mock_send_file, \
         patch("main.send_telegram_text", new_callable=AsyncMock) as mock_send_text:

        mock_send_file.return_value = {"ok": True}
        mock_send_text.return_value = {"ok": True}

        # 1. Create Payment Request
        res = await create_and_dispatch_payment_request(
            sender_chat_id=sender_chat,
            amount=500.0,
            note="Dinner Contribution",
            payer_handle="@payer_rahul"
        )

        assert res["status"] == "success"
        ref_id = res["ref_id"]
        assert ref_id.startswith("PAY-")
        assert res["amount"] == 500.0
        assert res["payer_notified"] is True

        # Verify QR was sent to both sender and payer
        assert mock_send_file.call_count >= 2

        # Verify Database state
        req = db.get_payment_request(ref_id)
        assert req is not None
        assert req["status"] == "pending"
        assert float(req["amount"]) == 500.0

        # 2. Settle the payment
        settle_res = await settle_and_notify_payment(
            ref_id=ref_id,
            verified_by="user",
            receipt_proof="UTR_1234567890"
        )
        assert settle_res["status"] == "success"
        assert settle_res["payer_notified"] is True

        # Verify dual Telegram notifications were dispatched
        assert mock_send_text.call_count >= 2

        # Verify DB status updated to completed
        updated_req = db.get_payment_request(ref_id)
        assert updated_req["status"] == "completed"
        assert updated_req["completed_at"] is not None

        # Verify contact commitment was updated
        contacts = db.get_contact_memories(sender_chat)
        rahul_contact = next((c for c in contacts if c.get("name") == "Rahul"), None)
        assert rahul_contact is not None
        assert "settled" in (rahul_contact.get("commitments") or "").lower()


@pytest.mark.asyncio
async def test_future_scheduled_payment_does_not_disturb_payer_today():
    sender_chat = 77119900
    payer_chat = 77119911

    db.ensure_user(sender_chat)
    db.set_user_upi_id(sender_chat, "nirav@paytm")
    db.ensure_user(payer_chat)
    db.claim_username(payer_chat, "devender_friend")

    with patch("main.send_file", new_callable=AsyncMock) as mock_send_file:
        mock_send_file.return_value = {"ok": True}

        # Request payment collection for a future date (2026-12-01)
        res = await create_and_dispatch_payment_request(
            sender_chat_id=sender_chat,
            amount=750.0,
            note="Dinner Bill",
            payer_handle="@devender_friend",
            due_date="2026-12-01"
        )

        assert res["status"] == "success"
        assert res["is_scheduled"] is True
        assert res["payer_notified"] is False  # Payer is NOT disturbed today!
        # QR is sent only to the sender for their confirmation
        assert mock_send_file.call_count == 1

        # Check DB state
        req = db.get_payment_request(res["ref_id"])
        assert req["status"] == "scheduled"
        assert req["due_date"] == "2026-12-01"



def test_api_payment_endpoints():
    from main import app
    client = TestClient(app)

    # 1. Create a dummy request directly in DB
    ref_id = "PAY-TEST-9988"
    db.create_payment_request(
        ref_id=ref_id,
        creator_chat_id=11112222,
        upi_id="merchant@upi",
        payee_name="Merchant",
        amount=1200.0,
        note="Consultation"
    )

    # 2. GET status
    resp = client.get(f"/api/v1/payments/{ref_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["payment"]["status"] == "pending"

    # 3. POST confirmation
    conf_resp = client.post(
        f"/api/v1/payments/{ref_id}/confirm",
        json={"utr": "SBI123456789"}
    )
    assert conf_resp.status_code == 200
    conf_data = conf_resp.json()
    assert conf_data["status"] == "success"

    # 4. Verify status is now completed
    resp2 = client.get(f"/api/v1/payments/{ref_id}")
    assert resp2.json()["payment"]["status"] == "completed"

    # 5. Webhook endpoint test
    webhook_ref = "PAY-TEST-WH01"
    db.create_payment_request(
        ref_id=webhook_ref,
        creator_chat_id=11113333,
        upi_id="merchant2@upi",
        payee_name="Merchant 2",
        amount=850.0,
        note="Order #42"
    )
    wh_resp = client.post(
        "/api/v1/payments/webhook",
        json={"ref_id": webhook_ref, "utr": "HDFC99887766"}
    )
    assert wh_resp.status_code == 200
    wh_data = wh_resp.json()
    assert wh_data["status"] == "success"
    assert wh_data["ref_id"] == webhook_ref


def test_agent_tool_routing_for_payments():
    from agent.tools import route_tools
    tools = route_tools("Rahul se 500 rupaye lene hain upi qr code bna do")
    tool_names = [t["function"]["name"] for t in tools]
    assert "generate_payment_qr" in tool_names
    assert "settle_payment_request" in tool_names
