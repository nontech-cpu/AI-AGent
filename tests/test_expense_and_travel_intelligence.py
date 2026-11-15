"""
Unit & Integration Tests for Smart Expense Tracking & Travel Booking Services:
1. Expense Logging, Categorization, & Summary Aggregations
2. Executive-Styled Excel Generation (.xlsx) & Google Drive Cloud Sync
3. Smart Receipt & Bill OCR Extraction
4. Train (IRCTC PNR) & Flight Ticket Extraction
5. Autonomous Google Calendar Journey Block & 2-Hour Departure Alert Scheduling
6. Live Status Checking with RapidAPI / AviationStack & Mock Fallbacks
7. Full execute_tool Integration for all Expense & Travel Tools
8. Two-Phase Pending Action Confirmations for Expense & Travel
"""

import io
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timedelta

import db
from services.expense_service import (
    record_user_expense,
    query_expense_summary,
    build_expenses_excel,
    sync_expenses_to_drive,
    extract_expense_details
)
from services.travel_service import (
    extract_travel_booking_from_text,
    save_and_schedule_travel,
    check_live_travel_status
)
from agent.tools import execute_tool
from state.pending import execute_pending_action


@pytest.mark.asyncio
async def test_expense_logging_and_summary():
    """Verify recording expenses and calculating category summaries."""
    chat_id = 999333111
    db.ensure_user(chat_id)

    # 1. Log three different expenses
    exp1 = record_user_expense(chat_id, amount=450.0, merchant="Starbucks", category="Food & Dining", expense_date="2026-11-15")
    exp2 = record_user_expense(chat_id, amount=1200.0, merchant="Electricity Board", category="Bills & Utilities", expense_date="2026-11-15")
    exp3 = record_user_expense(chat_id, amount=250.0, merchant="Uber", category="Travel & Commute", expense_date="2026-11-15")

    assert exp1["id"] is not None
    assert exp1["amount"] == 450.0

    # 2. Query summary across all categories
    summary = query_expense_summary(chat_id, timeframe="this_month")
    assert summary["count"] >= 3
    assert summary["total_amount"] >= 1900.0
    assert "Food & Dining" in summary["by_category"]
    assert "Bills & Utilities" in summary["by_category"]

    # 3. Query category-filtered summary
    food_summary = query_expense_summary(chat_id, category="Food & Dining", timeframe="this_month")
    assert food_summary["total_amount"] >= 450.0


def test_expense_excel_generation():
    """Verify openpyxl generates a valid formatted .xlsx spreadsheet with formulas and styling."""
    expenses = [
        {"expense_date": "2026-11-15", "merchant": "Starbucks", "category": "Food & Dining", "amount": 450.0, "payment_method": "UPI", "notes": "Cold coffee"},
        {"expense_date": "2026-11-15", "merchant": "Amazon", "category": "Shopping", "amount": 1599.0, "payment_method": "Card", "notes": "Keyboard"}
    ]

    excel_bytes = build_expenses_excel(expenses, title="Test Expense Report")
    assert excel_bytes is not None
    assert len(excel_bytes) > 1000

    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(excel_bytes), data_only=False)
    ws = wb.active
    assert ws.title == "Expenses"
    # Header check
    assert ws["A3"].value == "Date"
    assert ws["B3"].value == "Merchant / Payee"
    assert ws["D3"].value == "Amount (INR)"
    # First record check
    assert ws["B4"].value == "Starbucks"
    assert ws["D4"].value == 450.0
    # Sum formula check
    assert ws["D6"].value == "=SUM(D4:D5)"


@pytest.mark.asyncio
async def test_sync_expenses_to_drive():
    """Verify sync_expenses_to_drive generates Excel and uploads to Google Drive folder."""
    chat_id = 999333111
    with patch("google_services.upload_file_to_drive", new_callable=AsyncMock) as mock_upload:
        mock_upload.return_value = {
            "id": "drive_sheet_001",
            "name": "Expenses_November_2026.xlsx",
            "webViewLink": "https://drive.google.com/file/d/drive_sheet_001/view"
        }

        res = await sync_expenses_to_drive(chat_id, timeframe="this_month", folder_name="Expenses")
        assert res["ok"] is True
        assert res["file_id"] == "drive_sheet_001"
        assert res["folder_name"] == "Expenses"
        assert "Expenses_" in res["filename"]


@pytest.mark.asyncio
async def test_extract_expense_details_llm():
    """Verify LLM extraction from receipt text/OCR."""
    with patch("llm.provider.chat", new_callable=AsyncMock) as mock_chat:
        mock_chat.return_value = '{"is_expense": true, "amount": 850.50, "merchant": "Haldiram\'s", "category": "Food & Dining", "date": "2026-11-15", "payment_method": "UPI", "notes": "Lunch thali"}'

        extracted = await extract_expense_details("Receipt from Haldiram. Total 850.50 paid via UPI on 15 Nov 2026.")
        assert extracted["is_expense"] is True
        assert extracted["amount"] == 850.50
        assert extracted["merchant"] == "Haldiram's"
        assert extracted["category"] == "Food & Dining"


@pytest.mark.asyncio
async def test_travel_booking_and_scheduling():
    """Verify train journey saving, Google Calendar block, and 2-hour departure reminder alert."""
    chat_id = 999333111
    db.ensure_user(chat_id)

    future_dep = (datetime.now() + timedelta(days=2)).replace(hour=14, minute=0, second=0).isoformat()
    future_arr = (datetime.now() + timedelta(days=2)).replace(hour=20, minute=0, second=0).isoformat()

    booking_data = {
        "travel_type": "train",
        "identifier": "2431256789",
        "service_name": "Vande Bharat Express (22436)",
        "origin": "NDLS",
        "destination": "BSB",
        "departure_datetime": future_dep,
        "arrival_datetime": future_arr,
        "seat_info": "Coach C4, Seat 25",
        "status": "Confirmed"
    }

    with patch("google_services.create_calendar_event", new_callable=AsyncMock) as mock_cal:
        mock_cal.return_value = {"id": "cal_evt_travel_101", "summary": "Vande Bharat Express"}

        res = await save_and_schedule_travel(chat_id, booking_data)

        assert res["booking_id"] is not None
        assert res["identifier"] == "2431256789"
        assert res["calendar_scheduled"] is True
        assert res["reminder_scheduled"] is True

        # Check booking in DB
        bookings = db.get_user_travel_bookings(chat_id, identifier="2431256789")
        assert len(bookings) >= 1
        assert bookings[0]["origin"] == "NDLS"
        assert bookings[0]["seat_info"] == "Coach C4, Seat 25"

        # Check reminder created in DB
        travel_date = (datetime.now() + timedelta(days=2)).date()
        reminders = db.items_for_date(chat_id, travel_date)
        assert any("Journey Alert: Vande Bharat Express" in r[1] for r in reminders)


@pytest.mark.asyncio
async def test_live_travel_status_check():
    """Verify live travel status checking with RapidAPI / AviationStack or graceful fallback."""
    # Test Train PNR check
    res_train = await check_live_travel_status("2431256789", travel_type="train")
    assert res_train["ok"] is True
    assert res_train["identifier"] == "2431256789"
    assert "tracking active" in res_train.get("message", "") or res_train.get("current_status")

    # Test Flight check
    res_flight = await check_live_travel_status("6E-204", travel_type="flight")
    assert res_flight["ok"] is True
    assert res_flight["identifier"] == "6E-204"


@pytest.mark.asyncio
async def test_execute_tool_expense_and_travel():
    """Verify tool execution for log_expense, get_expense_summary, export_expenses_to_drive, track_travel_booking."""
    chat_id = 999333111
    db.ensure_user(chat_id)

    # 1. log_expense tool
    res_log = await execute_tool(
        chat_id,
        "log_expense",
        {"amount": 350.0, "merchant": "Cafe Coffee Day", "category": "Food & Dining"}
    )
    assert res_log["status"] == "success"
    assert res_log["amount"] == 350.0
    assert "Cafe Coffee Day" in res_log["message"]

    # 2. get_expense_summary tool
    res_sum = await execute_tool(
        chat_id,
        "get_expense_summary",
        {"category": "Food & Dining"}
    )
    assert res_sum["status"] == "success"
    assert "total_amount" in res_sum["data"]

    # 3. export_expenses_to_drive tool
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_conn:
        mock_conn.return_value = (True, "user@gmail.com")
        with patch("services.expense_service.sync_expenses_to_drive", new_callable=AsyncMock) as mock_sync:
            mock_sync.return_value = {
                "ok": True,
                "filename": "Expenses_November_2026.xlsx",
                "folder_name": "Expenses",
                "webViewLink": "https://drive.google.com/link",
                "total_amount": 350.0,
                "count": 1
            }
            res_exp = await execute_tool(chat_id, "export_expenses_to_drive", {"folder_name": "Expenses"})
            assert res_exp["status"] == "success"
            assert res_exp["filename"] == "Expenses_November_2026.xlsx"
            assert "Google Drive" in res_exp["message"]

    # 4. track_travel_booking tool
    dep_time = (datetime.now() + timedelta(days=3)).replace(hour=10, minute=0, second=0).isoformat()
    with patch("services.travel_service.save_and_schedule_travel", new_callable=AsyncMock) as mock_sched:
        mock_sched.return_value = {
            "booking_id": 12,
            "travel_type": "flight",
            "identifier": "AI-101",
            "service_name": "Air India 101",
            "origin": "DEL",
            "destination": "JFK",
            "departure_datetime": dep_time,
            "seat_info": "12A",
            "calendar_scheduled": True,
            "reminder_scheduled": True
        }
        res_trav = await execute_tool(
            chat_id,
            "track_travel_booking",
            {
                "identifier": "AI-101",
                "travel_type": "flight",
                "service_name": "Air India 101",
                "origin": "DEL",
                "destination": "JFK",
                "departure_datetime": dep_time
            }
        )
        assert res_trav["status"] == "success"
        assert res_trav["identifier"] == "AI-101"
        assert res_trav["calendar_scheduled"] is True


@pytest.mark.asyncio
async def test_pending_action_confirmations():
    """Verify two-phase confirmation execution for save_expense and book_travel."""
    chat_id = 999333111
    db.ensure_user(chat_id)

    # 1. Confirm save_expense
    expense_payload = {
        "kind": "save_expense",
        "amount": 750.0,
        "merchant": "Domino's Pizza",
        "category": "Food & Dining"
    }
    ok_exp, msg_exp = await execute_pending_action(chat_id, expense_payload)
    assert ok_exp is True
    assert "Expense Saved" in msg_exp
    assert "Domino's Pizza" in msg_exp

    # 2. Confirm book_travel
    travel_payload = {
        "kind": "book_travel",
        "travel_type": "train",
        "identifier": "1234599999",
        "service_name": "Rajdhani Express",
        "origin": "Mumbai",
        "destination": "Delhi",
        "departure_datetime": (datetime.now() + timedelta(days=4)).isoformat(),
        "seat_info": "B1-22"
    }
    with patch("google_services.create_calendar_event", new_callable=AsyncMock) as mock_cal:
        mock_cal.return_value = {"id": "cal_evt_rajdhani"}
        ok_trv, msg_trv = await execute_pending_action(chat_id, travel_payload)
        assert ok_trv is True
        assert "Journey Tracked!" in msg_trv
        assert "Rajdhani Express" in msg_trv
