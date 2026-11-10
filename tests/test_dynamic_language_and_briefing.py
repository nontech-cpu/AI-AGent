import pytest
import db
from morning_briefing import build_morning_briefing_message
from agent.tools import _execute_tool_inner
from datetime import datetime
from timing import IST

@pytest.mark.asyncio
async def test_set_user_language_tool_updates_db():
    chat_id = 999111222
    # Initial state should be updated to english
    res = await _execute_tool_inner(chat_id, "set_user_language", {"language": "english"})
    assert res["status"] == "success"
    assert res["language"] == "english"

    # Verify directly from DB
    stored = db.get_user_language(chat_id)
    assert stored == "english"

    # Now change to hinglish
    res2 = await _execute_tool_inner(chat_id, "set_user_language", {"language": "hinglish"})
    assert res2["status"] == "success"
    assert db.get_user_language(chat_id) == "hinglish"

@pytest.mark.asyncio
async def test_morning_briefing_localized_english():
    chat_id = 999333444
    db.set_user_language(chat_id, "english")
    target_date = datetime(2026, 11, 10, 8, 0, tzinfo=IST)

    briefing_text, trigger = await build_morning_briefing_message(chat_id, target_date=target_date, user_info={})
    assert "Here is your daily overview" in briefing_text
    assert "Today's Schedule & Tasks" in briefing_text
    assert "No meetings scheduled today" in briefing_text
    assert "Have a wonderful, productive day ahead! Let me know if you need anything added or updated." in briefing_text

@pytest.mark.asyncio
async def test_morning_briefing_hinglish_default():
    chat_id = 999555666
    db.set_user_language(chat_id, "hinglish")
    target_date = datetime(2026, 11, 10, 8, 0, tzinfo=IST)

    briefing_text, trigger = await build_morning_briefing_message(chat_id, target_date=target_date, user_info={})
    assert "yahan hai aapka daily overview" in briefing_text
    assert "Aaj Ka Schedule & Tasks" in briefing_text
    assert "Aaj koi meeting scheduled nahi hai" in briefing_text
