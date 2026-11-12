import json
from datetime import date, datetime, time as dtime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest

import db
from agent.loop import run_agent_turn
from agent.prompt import build_runtime_system_prompt
from agent.tools import execute_tool
from agent.verifier import extract_evidence_text
from connectors.calendar import CalendarConnector
from core.idempotency import compute_idempotency_key
from core.json_safe import safe_json_dumps, to_json_safe
from llm.provider import get_fake_llm, reset_fake_llm
from timing import IST


def test_to_json_safe_and_safe_json_dumps_handle_all_python_types():
    class _CustomObj:
        def __str__(self):
            return "custom_val"

    payload = {
        "time_obj": dtime(13, 46),
        "date_obj": date(2026, 11, 15),
        "dt_obj": datetime(2026, 11, 15, 13, 46, tzinfo=IST),
        "dec_int": Decimal("42.00"),
        "dec_float": Decimal("12.75"),
        "dec_nan": Decimal("NaN"),
        "nested_tuple": (dtime(9, 15), {"tags": {"a", "b"}}),
        "custom": _CustomObj(),
        123: "numeric_key",
    }

    normalized = to_json_safe(payload)
    # Standard json.dumps must succeed cleanly on normalized output
    dumped_norm = json.dumps(normalized, ensure_ascii=False)
    parsed = json.loads(dumped_norm)

    assert parsed["time_obj"] == "13:46:00"
    assert parsed["date_obj"] == "2026-11-15"
    assert parsed["dt_obj"].startswith("2026-11-15T13:46:00")
    assert parsed["dec_int"] == 42
    assert isinstance(parsed["dec_int"], int)
    assert parsed["dec_float"] == 12.75
    assert parsed["dec_nan"] == "NaN"
    assert parsed["nested_tuple"][0] == "09:15:00"
    assert sorted(parsed["nested_tuple"][1]["tags"]) == ["a", "b"]
    assert parsed["custom"] == "custom_val"
    assert parsed["123"] == "numeric_key"

    # Direct safe_json_dumps on unnormalized payload must also succeed cleanly
    dumped_direct = safe_json_dumps(payload, ensure_ascii=False)
    assert json.loads(dumped_direct)["time_obj"] == "13:46:00"


@pytest.mark.asyncio
async def test_calendar_connector_get_schedule_serializes_raw_due_time():
    conn = CalendarConnector()
    fake_sched = {
        "date": date(2026, 11, 15),
        "calendar_events": [],
        "reminders": [
            {
                "category": "appointments",
                "title": "Test sugar check",
                "amount": None,
                "due_time": dtime(13, 46),
            }
        ],
        "commitments": [],
    }
    with patch("main.get_unified_schedule", new=AsyncMock(return_value=fake_sched)):
        res = await conn.execute(998877820, "get_schedule", {"target_date": "2026-11-15"})

    assert res.ok is True
    assert res.data["reminders"][0]["due_time"] == "13:46:00"
    # Raw json.dumps works on ToolResult.data directly
    assert "13:46:00" in json.dumps(res.data, ensure_ascii=False)


@pytest.mark.asyncio
async def test_execute_tool_boundary_normalizes_non_json_return_values():
    raw_return = {
        "status": "success",
        "due_time": dtime(8, 30),
        "due_date": date(2026, 11, 15),
        "price": Decimal("99.50"),
    }
    with patch("agent.tools._execute_tool_inner", new=AsyncMock(return_value=raw_return)):
        out = await execute_tool(998877821, "list_reminders", {})

    assert out["due_time"] == "08:30:00"
    assert out["due_date"] == "2026-11-15"
    assert out["price"] == 99.5
    # Must serialize with plain json.dumps without default=str
    json.dumps(out, ensure_ascii=False)


@pytest.mark.asyncio
async def test_e2e_run_agent_turn_get_schedule_with_raw_time_object(monkeypatch):
    """Regression test for trace_6cff3898: get_schedule returning datetime.time never crashes run_agent_turn."""
    monkeypatch.setenv("USE_FAKE_LLM", "1")
    reset_fake_llm()
    fake_llm = get_fake_llm()

    chat_id = 998877822
    db.ensure_user(chat_id)
    db.set_user_preference(chat_id, reply_language="english", tone="concise")

    fake_sched = {
        "date": date(2026, 11, 15),
        "calendar_events": [],
        "reminders": [
            {
                "category": "appointments",
                "title": "Test sugar check",
                "amount": None,
                "due_time": dtime(13, 46),
            }
        ],
        "commitments": [],
    }

    fake_llm.queue_poisoned_tool_call("get_schedule", {"target_date": "2026-11-15"})
    fake_llm.queue_response(
        text="On Sunday, you have Test sugar check at 1:46 PM. How are you planning to travel to Udaipur, and what time do you want to leave?"
    )

    with patch("main.get_unified_schedule", new=AsyncMock(return_value=fake_sched)):
        res = await run_agent_turn(chat_id, "i have the go udaipur this sunday for travelling")

    assert "get_schedule" in res["tool_calls_executed"]
    assert "Test sugar check" in res["reply"]
    assert "Udaipur" in res["reply"]
    reset_fake_llm()


@pytest.mark.asyncio
async def test_verifier_idempotency_prompt_and_db_state_handle_python_dates_and_times():
    # 1. Verifier evidence builder
    evidence = extract_evidence_text(
        tool_results=[{"title": "Test sugar check", "due_time": dtime(13, 46), "due_date": date(2026, 11, 15)}]
    )
    assert "13:46:00" in evidence
    assert "2026-11-15" in evidence

    # 2. Idempotency deterministic hashing with raw date/time
    k1 = compute_idempotency_key(998877823, "create_reminder", {"due_time": dtime(13, 46), "due_date": date(2026, 11, 15)})
    k2 = compute_idempotency_key(998877823, "create_reminder", {"due_date": date(2026, 11, 15), "due_time": dtime(13, 46)})
    assert k1 == k2

    # 3. db.set_state with date/time inside state_data dict
    db.ensure_user(998877823)
    db.set_state(998877823, "confirm_step", {"due_time": dtime(13, 46), "due_date": date(2026, 11, 15)})
    st = db.get_state(998877823)
    assert st is not None
    assert st[0] == "confirm_step"
    assert st[1]["due_time"] == "13:46:00"
    db.set_state(998877823, None, None)

    # 4. Prompt pending action serialization with date/time
    with patch("state.pending.get_pending_action", return_value={"action_kind": "create_reminder", "payload": {"due_time": dtime(13, 46)}}):
        prompt = await build_runtime_system_prompt(998877823, "check pending", mem_ctx={"formatted_block": ""})
        assert "13:46:00" in prompt
