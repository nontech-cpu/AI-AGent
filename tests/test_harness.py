"""
Verification tests for Phase 0 Test Harness.
"""
import pytest
from datetime import datetime, date
from zoneinfo import ZoneInfo
from tests.harness.clock import FrozenClock, get_now, get_today
from tests.harness.fake_llm import FakeLLM
from tests.harness.test_db import create_isolated_engine
from sqlalchemy import text
import agent.tools
import core.timeutil as timeutil

IST = ZoneInfo("Asia/Kolkata")

def test_frozen_clock_anchoring():
    with FrozenClock("2026-11-05T13:30:00", tz=IST) as clock:
        now_dt = get_now(IST)
        assert now_dt.year == 2026
        assert now_dt.month == 11
        assert now_dt.day == 5
        assert now_dt.hour == 13
        assert now_dt.minute == 30
        assert get_today(IST) == date(2026, 11, 5)

        # Check central clock authority via timeutil
        mod_now = agent.tools.timeutil.now(IST)
        assert mod_now.year == 2026
        assert mod_now.hour == 13

        # Advance clock by 2 hours
        clock.advance(hours=2)
        assert get_now(IST).hour == 15
        assert agent.tools.timeutil.now(IST).hour == 15

@pytest.mark.asyncio
async def test_fake_llm_scripted_responses():
    llm = FakeLLM()
    llm.queue_poisoned_reminder(title="Movie", due_date="2025-01-24", due_time="16:30")
    
    resp = await llm.mock_post("https://api.groq.com/chat/completions")
    data = resp.json()
    tool_call = data["choices"][0]["message"]["tool_calls"][0]
    assert tool_call["function"]["name"] == "create_reminder"
    
    import json
    args = json.loads(tool_call["function"]["arguments"])
    assert args["title"] == "Movie"
    assert args["due_date"] == "2025-01-24"
    assert args["due_time"] == "16:30"

def test_isolated_db_engine():
    engine = create_isolated_engine()
    with engine.connect() as conn:
        res = conn.execute(text("SELECT count(*) FROM sqlite_master WHERE type='table'")).scalar()
        assert res > 5
        # Verify user_facts and memories tables exist
        tables = conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        t_names = [t[0] for t in tables]
        assert "users" in t_names
        assert "memory_items" in t_names
        assert "user_facts" in t_names
        assert "memories" in t_names
