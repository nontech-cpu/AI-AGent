"""
Persistent User Language Preference Test (Phase 7e Step 2)
Tests:
1. set_user_preference tool stores preference in user profile & memories.
2. 10 mixed Hindi/Hinglish/English turns strictly inject the CRITICAL USER PREFERENCE LOCK.
3. Restart-proof (new cache / DB read yields identical result).
4. Switching preference back to Hinglish or Hindi works immediately.
"""
import pytest
from unittest.mock import patch
import db
from agent.tools import execute_tool
from agent.prompt import build_runtime_system_prompt, invalidate_prompt_schedule_cache

@pytest.fixture(autouse=True)
def setup_db():
    from db import engine, text
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id INTEGER UNIQUE NOT NULL,
                consented_at TIMESTAMP,
                state TEXT,
                state_data TEXT,
                persona TEXT,
                language TEXT DEFAULT 'hinglish',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT,
                fact TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    yield

@pytest.mark.asyncio
async def test_persistent_user_preference_e2e():
    chat_id = 998877201
    db.ensure_user(chat_id)
    db.set_user_preference(chat_id, "hinglish", "friendly")

    # Initially is hinglish
    pref_init = db.get_user_preference(chat_id)
    assert pref_init["reply_language"] == "hinglish"

    # Step 1: User explicitly commands English only
    res = await execute_tool(
        chat_id,
        "set_user_preference",
        {
            "reply_language": "english",
            "tone": "concise",
            "request_quote": "talk in english only"
        },
        user_context={"user_text": "now from the conversation talk in english only", "bypass_quote_check": True}
    )
    assert res["status"] == "success"
    assert res["facts"]["reply_language"] == "english"

    # Verify single source of truth in DB
    pref_db = db.get_user_preference(chat_id)
    assert pref_db["reply_language"] == "english"

    # Step 2: 10 Mixed Hindi / Hinglish / English inputs
    mixed_queries = [
        "jodhpur m ky mausam h aaj",
        "what in the u think best movie to watch as of now?",
        "like what the movies from bolywood currenlty in threater?",
        "mere daily reminder set krdo jogging ka 10am ka",
        "kal mere reminder batana",
        "kya haal chaal hai bhai",
        "aaj sham ko 5 baje meeting hai kya",
        "tell me a joke",
        "bhai mujhe raste batao sardarpura se ratanada",
        "show me my notes and facts"
    ]

    for q in mixed_queries:
        prompt = await build_runtime_system_prompt(chat_id, user_text=q)
        assert "CRITICAL USER PREFERENCE LOCK" in prompt
        assert "- LOCKED REPLY LANGUAGE: ENGLISH" in prompt
        assert "MANDATORY: You must generate 100% of your response text in ENGLISH" in prompt

    # Step 3: Restart-proof verification (clear all process caches)
    invalidate_prompt_schedule_cache(chat_id)
    prompt_restart = await build_runtime_system_prompt(chat_id, user_text="kya haal hai?")
    assert "- LOCKED REPLY LANGUAGE: ENGLISH" in prompt_restart

    # Step 4: Switch back to Hinglish
    res_switch = await execute_tool(
        chat_id,
        "set_user_preference",
        {
            "reply_language": "hinglish",
            "tone": "friendly",
            "request_quote": "hinglish me baat karo"
        },
        user_context={"user_text": "ab hinglish me baat karo", "bypass_quote_check": True}
    )
    assert res_switch["status"] == "success"
    assert res_switch["facts"]["reply_language"] == "hinglish"

    prompt_hinglish = await build_runtime_system_prompt(chat_id, user_text="kya chal raha hai?")
    assert "- LOCKED REPLY LANGUAGE: HINGLISH" in prompt_hinglish
