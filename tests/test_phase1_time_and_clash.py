"""
tests/test_phase1_time_and_clash.py - Verification tests for Phase 1 Time & Clash Logic.

Covers:
(a) Model sends 2025-01-24 -> tool rejects, model corrects to today, reminder saved for today.
(b) Clash check correctly detects 16:00-17:00 meeting vs 16:30 movie.
(c) list_reminders with no date returns today's items, not empty.
"""
import pytest
from datetime import date, time as dtime
from zoneinfo import ZoneInfo
from unittest.mock import patch, AsyncMock, MagicMock
from agent.tools import execute_tool
from tests.harness.clock import FrozenClock
import db

IST = ZoneInfo("Asia/Kolkata")


@pytest.mark.asyncio
async def test_poisoned_cutoff_date_rejected():
    """
    Verify that when model sends a pre-training cutoff date (e.g. 2025-01-24 or 2024-01-24),
    create_reminder strictly rejects execution and returns a structured tool error.
    """
    chat_id = 998877301
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        # 1. Model sends poisoned date 2025-01-24
        res = await execute_tool(chat_id, "create_reminder", {
            "title": "Movie with friends",
            "due_date": "2025-01-24",
            "due_time": "16:30"
        })
        
        # Tool MUST NOT execute or save to DB; returns structured error
        assert res["status"] == "error"
        assert res["error"] == "invalid_date_or_in_past"
        assert "2025-01-24" in res["message"]
        assert "2026-11-05" in res["message"]

        # Verify nothing was saved in DB for 2025-01-24 or 2026-11-05
        items_past = db.items_for_date(chat_id, date(2025, 1, 24))
        assert len(items_past) == 0


@pytest.mark.asyncio
async def test_agent_loop_self_corrects_poisoned_date():
    """
    End-to-end agent loop test:
    Model initially emits 2025-01-24 -> tool rejects -> model sees error with today's date ->
    model self-corrects to 2026-11-05 -> reminder saved for today.
    """
    from agent.loop import run_agent_turn
    chat_id = 998877302
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.set_persona(chat_id, "job")
    db.set_state(chat_id, "active")

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        # Step 1 response: poisoned date tool call
        mock_resp_1 = MagicMock()
        mock_resp_1.status_code = 200
        mock_resp_1.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_poisoned_1",
                        "type": "function",
                        "function": {
                            "name": "create_reminder",
                            "arguments": '{"title": "Project Review", "due_date": "2025-01-24", "due_time": "15:00"}'
                        }
                    }]
                }
            }],
            "usage": {"total_tokens": 80}
        }

        # Step 2 response: model self-corrects to today 2026-11-05
        mock_resp_2 = MagicMock()
        mock_resp_2.status_code = 200
        mock_resp_2.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call_correct_2",
                        "type": "function",
                        "function": {
                            "name": "create_reminder",
                            "arguments": '{"title": "Project Review", "due_date": "2026-11-05", "due_time": "15:00"}'
                        }
                    }]
                }
            }],
            "usage": {"total_tokens": 120}
        }

        # Step 3 response: final confirmation reply
        mock_resp_3 = MagicMock()
        mock_resp_3.status_code = 200
        mock_resp_3.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Done bhai! Project Review ka reminder aaj 3:00 PM ke liye set kar diya hai."
                }
            }],
            "usage": {"total_tokens": 40}
        }

        call_count = 0
        async def mock_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_resp_1
            elif call_count == 2:
                return mock_resp_2
            return mock_resp_3

        with patch("httpx.AsyncClient.post", side_effect=mock_post):
            res = await run_agent_turn(chat_id, "Aaj 3 baje project review ka reminder laga dena")
            assert "Project Review" in res["reply"]

        # Verify DB item was saved on today (2026-11-05) and NOT 2025-01-24
        today_items = db.items_for_date(chat_id, date(2026, 11, 5))
        past_items = db.items_for_date(chat_id, date(2025, 1, 24))
        assert len(today_items) >= 1
        assert len(past_items) == 0
        assert today_items[0][1] == "Project Review"


@pytest.mark.asyncio
async def test_clash_detection_meeting_vs_movie():
    """
    Verify clash check correctly detects 16:00-17:00 meeting vs 16:30 movie.
    """
    chat_id = 998877303
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        # 1. User has an existing meeting 16:00-17:00 (duration=60 min)
        res1 = await execute_tool(chat_id, "create_reminder", {
            "title": "Client Sync Meeting",
            "due_date": "2026-11-05",
            "due_time": "16:00",
            "duration_minutes": 60
        })
        assert res1["status"] == "success"

        # 2. User tries to schedule "Movie" at 16:30 (duration=120 min)
        res2 = await execute_tool(chat_id, "create_reminder", {
            "title": "Movie with friends",
            "due_date": "2026-11-05",
            "due_time": "16:30",
            "duration_minutes": 120
        })

        # MUST detect conflict
        assert res2["has_conflict"] is True
        assert res2["status"] == "conflict_detected"
        assert "conflicts" in res2
        assert any("Client Sync Meeting" in c for c in res2["conflicts"])
        # Should not overwrite meeting or create conflicting reminder without force
        items = db.items_for_date(chat_id, date(2026, 11, 5))
        assert len(items) == 1
        assert items[0][1] == "Client Sync Meeting"


@pytest.mark.asyncio
async def test_list_reminders_missing_date_returns_today():
    """
    Verify that list_reminders with no date (missing or empty) returns today's items, not empty.
    """
    chat_id = 998877304
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        # Save a reminder for today
        db.save_memory(chat_id, "appointments", "Doctor Appointment", amount=None, due_date=date(2026, 11, 5), due_time=dtime(18, 0))

        # Call list_reminders with no date_str
        res = await execute_tool(chat_id, "list_reminders", {})
        print("ACTUAL RES:", res)
        assert res["date"] == "2026-11-05"
        assert res["count"] >= 1
        assert any(r["title"] == "Doctor Appointment" for r in res["reminders"])

        # Call list_reminders with None date_str
        res_none = await execute_tool(chat_id, "list_reminders", {"date_str": None})
        assert res_none["date"] == "2026-11-05"
        assert res_none["count"] >= 1



@pytest.mark.asyncio
async def test_read_tools_accept_past_date_without_clamping():
    """
    Verify READ tools (list_reminders, get_schedule) accept any valid historical date
    without clamping to today, and return full date metadata ('relative', 'today', 'is_today').
    """
    chat_id = 998877305
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        past_target = "2024-05-10"
        # Save a historical memory item directly for that past date
        db.save_memory(chat_id, "appointments", "Doctor visit last year", None, date(2024, 5, 10), dtime(11, 0), 30)

        res = await execute_tool(
            chat_id,
            "list_reminders",
            {"date_str": past_target},
            user_context={"user_text": "pichle saal ka check karo"}
        )

        # Must not clamp to today!
        assert res["date"] == "2024-05-10"
        assert res["today"] == "2026-11-05"
        assert res["is_today"] is False
        assert "days ago" in res["relative"]
        assert res["count"] >= 1
        assert res["reminders"][0]["title"] == "Doctor visit last year"
        assert res["reminders"][0]["status"] == "PAST"


@pytest.mark.asyncio
async def test_write_tools_reject_past_datetime():
    """
    Verify WRITE tools strictly reject past dates without silently rewriting,
    returning structured error feedback.
    """
    chat_id = 998877306
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        res = await execute_tool(
            chat_id,
            "create_reminder",
            {"title": "Past event", "due_date": "2025-01-24", "due_time": "16:30"},
            user_context={"user_text": "set reminder"}
        )

        assert res["status"] == "error"
        assert res["error"] == "invalid_date_or_in_past"
        assert res["today"] == "2026-11-05"
        # DB should not have this item
        items = db.items_for_date(chat_id, date(2025, 1, 24))
        assert len(items) == 0
