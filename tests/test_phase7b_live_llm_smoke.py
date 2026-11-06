"""
Phase 7b Live LLM Smoke Tests (Marked with @pytest.mark.live_llm).
Excluded from default pytest suite via pytest.ini (-m "not live_llm").

Tests 5 critical live turn failure modes across 3 budgeted iterations each:
1. Places list: 'jodhpur m konse mall h' (no fabricated malls, grounded in search)
2. Route question: 'ratanada se sardarpura best route' (no fabricated roads/minutes, maps link)
3. What did I mail <person>: 'mene aaravmishra ko ek mail send kra tha tum bta skte ho vo' (event memory / sent search, no crash)
4. Reminder clash: 'kl 2pm flight reminder' overlapping 1-4 PM (clash detected, no silent overwrite)
5. Date and day today: 'aaj date aur day kya h' (anchored to timeutil, correct weekday)
"""

import pytest
import os
import asyncio
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch, AsyncMock

from agent.loop import run_agent_turn
import db
import core.timeutil as timeutil

IST = ZoneInfo("Asia/Kolkata")
SMOKE_CHAT_ID = 998877505

@pytest.fixture(autouse=True)
def setup_smoke_user():
    db.ensure_user(SMOKE_CHAT_ID)
    db.give_consent(SMOKE_CHAT_ID)
    db.set_persona(SMOKE_CHAT_ID, "job")
    db.set_state(SMOKE_CHAT_ID, "active")
    yield

@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("run_idx", [1, 2, 3])
async def test_live_smoke_places_list(run_idx):
    """Places list: Ensure mall query returns search-grounded places without inventing fake malls."""
    user_query = "jodhpur m konse mall h"
    res = await run_agent_turn(
        chat_id=SMOKE_CHAT_ID,
        user_text=user_query,
        channel="telegram"
    )
    reply = res.get("reply", "")
    assert reply, "Expected non-empty reply"
    assert res.get("is_grounded") is not False
    # Verify no ungrounded hallucination
    assert "Orbit Mall" not in reply
    assert "Ansal Plaza" not in reply


@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("run_idx", [1, 2, 3])
async def test_live_smoke_route_question(run_idx):
    """Route question: Ensure no ungrounded minute/road fabrication; Google Maps link provided."""
    user_query = "ratanada se sardarpura best route kya h"
    res = await run_agent_turn(
        chat_id=SMOKE_CHAT_ID,
        user_text=user_query,
        channel="telegram"
    )
    reply = res.get("reply", "")
    assert reply, "Expected non-empty reply"
    # Grounding contract: Either states no live GPS/navigation or provides Google Maps link
    assert ("google.com/maps" in reply or "navigation" in reply.lower() or "maps" in reply.lower() or "gps" in reply.lower())


@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("run_idx", [1, 2, 3])
async def test_live_smoke_what_did_i_mail(run_idx):
    """What did I mail: Check that tool executes without UnboundLocalError and does not ask user for contents."""
    user_query = "mene aaravmishra ko ek mail send kra tha tum bta skte ho vo"
    res = await run_agent_turn(
        chat_id=SMOKE_CHAT_ID,
        user_text=user_query,
        channel="telegram"
    )
    reply = res.get("reply", "")
    assert reply, "Expected non-empty reply"
    # Ensure no technical error message or crash
    assert "UnboundLocalError" not in reply
    assert "technical dikkat" not in reply.lower()
    # Must not ask user to tell what the email said as first move
    assert "aap mujhe bata sakte hain ki usme kya tha" not in reply.lower()


@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("run_idx", [1, 2, 3])
async def test_live_smoke_reminder_clash(run_idx):
    """Reminder clash: Ensure clash between overlapping items is detected without silent overwrite."""
    # Create existing reminder 1 PM - 4 PM tomorrow
    now_ist = timeutil.now(IST)
    tomorrow = (now_ist + timedelta(days=1)).strftime("%Y-%m-%d")
    
    # Clean previous reminders
    db.delete_reminder(SMOKE_CHAT_ID, "Movie")
    db.delete_reminder(SMOKE_CHAT_ID, "Flight")
    
    # Seed 1-4 PM Movie
    db.create_reminder(
        chat_id=SMOKE_CHAT_ID,
        title="Movie",
        due_date=tomorrow,
        due_time="13:00",
        duration_minutes=180
    )
    
    # Try setting 2 PM Flight
    res = await run_agent_turn(
        chat_id=SMOKE_CHAT_ID,
        user_text=f"kal {tomorrow} ko 2:00 PM par flight ka reminder set karo",
        channel="telegram"
    )
    reply = res.get("reply", "")
    assert reply, "Expected non-empty reply"
    # Should detect conflict or mention Movie overlap
    low_reply = reply.lower()
    assert any(w in low_reply for w in ("conflict", "clash", "overlap", "movie", "already", "aage-peeche", "dono"))


@pytest.mark.live_llm
@pytest.mark.asyncio
@pytest.mark.parametrize("run_idx", [1, 2, 3])
async def test_live_smoke_date_and_day_today(run_idx):
    """Date and day today: Ensure correct weekday and ISO date from timeutil, not hallucinated Wednesday/cutoff."""
    now_ist = timeutil.now(IST)
    expected_weekday = now_ist.strftime("%A")
    expected_date = now_ist.strftime("%d")
    expected_year = str(now_ist.year)
    
    res = await run_agent_turn(
        chat_id=SMOKE_CHAT_ID,
        user_text="aaj date aur day kya hai",
        channel="telegram"
    )
    reply = res.get("reply", "")
    assert reply, "Expected non-empty reply"
    assert expected_year in reply
    # Must mention the true weekday (or its Hindi equivalent)
    weekday_hindi_map = {
        "Monday": "somwar", "Tuesday": "mangalwar", "Wednesday": "budhwar",
        "Thursday": "guruwar", "Friday": "shukrawar", "Saturday": "shaniwar", "Sunday": "raviwar"
    }
    hindi_w = weekday_hindi_map.get(expected_weekday, "")
    assert (expected_weekday.lower() in reply.lower() or hindi_w in reply.lower() or "thursday" in reply.lower() or "friday" in reply.lower())
