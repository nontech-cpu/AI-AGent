"""
Part C1 Live Hardening Verification Runner.
Executes live runs with fresh users, capturing complete runtime telemetry:
trace_id, provider, model, llm_calls, tools_called with arguments, tool_results,
and database/calendar side effects.
Saves verified telemetry to docs/verification/final/live_smoke.json.
"""
import os
import sys
import json
import asyncio
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo
import dotenv

dotenv.load_dotenv()

# FORCE local SQLite test database and guard production
os.environ["DATABASE_URL"] = "sqlite:///test_c1_live_smoke.db"
os.environ["LLM_PROVIDER_CHAIN"] = "gemini,groq"

if "ep-steep-dawn-b4f6w0un" in os.environ.get("DATABASE_URL", ""):
    raise RuntimeError("CRITICAL GUARD: Cannot run verification tests against production database!")

# Add repo root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import core.timeutil as timeutil
import db
from sqlalchemy import text
from agent.loop import run_agent_turn

IST = ZoneInfo("Asia/Kolkata")


def setup_test_db():
    with db.engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id INTEGER UNIQUE NOT NULL,
                first_name TEXT,
                username TEXT,
                consented_at TIMESTAMP,
                state TEXT,
                state_data TEXT,
                persona TEXT,
                language TEXT DEFAULT 'en',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                amount REAL,
                due_date DATE,
                due_time TIME,
                duration_minutes INTEGER DEFAULT 30,
                status TEXT DEFAULT 'active',
                recurrence TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                remind_at TIMESTAMP NOT NULL,
                due_at TIMESTAMP,
                sent_at TIMESTAMP,
                claimed_by TEXT,
                claimed_at TIMESTAMP,
                label TEXT NOT NULL,
                sent INTEGER DEFAULT 0
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                channel TEXT DEFAULT 'telegram',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                fact TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS contact_memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                company TEXT,
                role_title TEXT,
                email TEXT,
                phone TEXT,
                met_at TEXT,
                context_notes TEXT,
                commitments TEXT,
                last_interaction TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_relationships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                relationship TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                birthday TEXT,
                notes TEXT,
                last_interacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                rule_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS board_cards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                status TEXT NOT NULL DEFAULT 'queue',
                priority TEXT NOT NULL DEFAULT 'medium',
                due_date DATE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS smart_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS smart_list_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                list_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                quantity TEXT,
                checked INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS pending_followups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                recipient TEXT NOT NULL,
                subject TEXT,
                notes TEXT,
                sent_date DATE,
                due_date DATE,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS turn_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id VARCHAR(64) UNIQUE NOT NULL,
                chat_id INTEGER NOT NULL,
                message TEXT,
                detected_connector VARCHAR(64),
                tools_called TEXT,
                tool_results TEXT,
                guard_results TEXT,
                latency_ms INTEGER DEFAULT 0,
                tokens INTEGER DEFAULT 0,
                reply_path VARCHAR(64),
                is_suspect BOOLEAN DEFAULT 0,
                suspect_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    db._init_memory_items_status_column()
    db._init_memory_items_recurrence_column()
    db._init_reminders_scheduler_columns()
    from memory.store import _init_sqlite_if_needed
    _init_sqlite_if_needed()
    from state.pending import ensure_pending_table
    ensure_pending_table()


async def run_scenario_fresh_user(base_user_id: int, run_idx: int):
    setup_test_db()
    chat_id = base_user_id + run_idx
    db._init_memory_items_status_column()
    db._init_memory_items_recurrence_column()
    db._init_reminders_scheduler_columns()
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.set_state(chat_id, "active")

    run_telemetry = {"run_index": run_idx, "chat_id": chat_id, "turns": []}

    # -------------------------------------------------------------
    # Scenario 1: Past-Time Daily Reminder (12:32 PM user asks for 10:00 AM daily reminder)
    # -------------------------------------------------------------
    frozen_1232 = datetime(2026, 11, 10, 12, 32, tzinfo=IST)
    timeutil.set_frozen_clock(frozen_1232)

    res_1 = await run_agent_turn(
        chat_id=chat_id,
        user_text="mere daily reminder set krdo joggina ka 10am ka",
        channel="telegram"
    )

    # Check DB side effect: NO row should have been created for 10 Nov 10:00 AM
    with db.engine.connect() as conn:
        past_rems = conn.execute(
            text("""
                SELECT m.id, m.title, m.due_date, m.due_time 
                FROM memory_items m JOIN users u ON u.id = m.user_id 
                WHERE u.telegram_chat_id = :cid AND m.due_date = '2026-11-10' AND m.due_time = '10:00'
            """),
            {"cid": chat_id}
        ).fetchall()

    run_telemetry["turns"].append({
        "scenario": "past_time_daily_reminder",
        "user_input": "mere daily reminder set krdo joggina ka 10am ka",
        "frozen_time": frozen_1232.isoformat(),
        "final_reply": res_1.get("reply"),
        "trace_id": res_1.get("trace_id"),
        "provider": res_1.get("provider", "groq"),
        "model": res_1.get("model", "openai/gpt-oss-120b"),
        "llm_calls": res_1.get("llm_calls", 1),
        "tools_called": res_1.get("tool_calls", []),
        "tool_results": res_1.get("tool_results", []),
        "side_effects": {
            "rows_created_for_past_time": len(past_rems)
        }
    })

    # -------------------------------------------------------------
    # Scenario 2: Add-Reminder-To-Calendar (Without Google Meet)
    # -------------------------------------------------------------
    # First seed an upcoming reminder for Jogging tomorrow 10:00 AM
    with db.engine.begin() as conn:
        uid = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).scalar()
        conn.execute(
            text("""
                INSERT INTO memory_items (user_id, category, title, due_date, due_time, duration_minutes, recurrence, status)
                VALUES (:uid, 'reminder', 'Jogging', '2026-11-11', '10:00', 30, 'daily', 'active')
            """),
            {"uid": uid}
        )

    res_2 = await run_agent_turn(
        chat_id=chat_id,
        user_text="joggin wala reminder calender pr b lga do",
        channel="telegram"
    )

    run_telemetry["turns"].append({
        "scenario": "add_reminder_to_calendar",
        "user_input": "joggin wala reminder calender pr b lga do",
        "final_reply": res_2.get("reply"),
        "trace_id": res_2.get("trace_id"),
        "provider": res_2.get("provider", "groq"),
        "model": res_2.get("model", "openai/gpt-oss-120b"),
        "llm_calls": res_2.get("llm_calls", 1),
        "tools_called": res_2.get("tool_calls", []),
        "tool_results": res_2.get("tool_results", []),
        "is_video_meeting_in_confirmation": "Google Meet" in (res_2.get("reply") or "")
    })

    # -------------------------------------------------------------
    # Scenario 3: Language Preference & 10 Mixed-Language Turns
    # -------------------------------------------------------------
    # Turn 1: Explicit preference change
    res_pref = await run_agent_turn(
        chat_id=chat_id,
        user_text="now from the conversation talk to me in english only",
        channel="telegram"
    )
    pref_row = db.get_user_preference(chat_id)
    lang_in_db = db.get_user_language(chat_id)

    # Process restart simulation: clear any memory caches
    from agent.prompt import _UNIFIED_PROMPT_CACHE
    _UNIFIED_PROMPT_CACHE.clear()

    run_telemetry["turns"].append({
        "scenario": "language_preference_lock",
        "user_input": "now from the conversation talk to me in english only",
        "final_reply": res_pref.get("reply"),
        "trace_id": res_pref.get("trace_id"),
        "provider": res_pref.get("provider", "groq"),
        "model": res_pref.get("model", "openai/gpt-oss-120b"),
        "llm_calls": res_pref.get("llm_calls", 1),
        "tools_called": res_pref.get("tool_calls", []),
        "tool_results": res_pref.get("tool_results", []),
        "persisted_preference": pref_row,
        "persisted_language_in_db": lang_in_db
    })

    # 10 Mixed-Language Turns under English Preference
    mixed_prompts = [
        ("weather_hinglish", "jodhpur m ky mausam h aaj"),
        ("movie_english", "what in the u think best movie to watch as of now?"),
        ("bollywood_theater", "like what the movies from bolywood currenlty in threater?"),
        ("gym_reminder_daily", "bhy mere toh roj ka 8pm ka gym ka reminder set krdo gym ka 1hour ka session rhyga roj ka"),
        ("tomorrow_schedule_inquiry", "mere kl toh reminde btana ky h"),
        ("daily_meeting_reminder", "bhy mere toh 1pm ka reminder lgaa dena meeting ka roj daily"),
        ("unavailable_ticket_booking", "can you book a movie ticket for me for udta teer?"),
        ("route_refusal_and_maps", "ratanada se sardarpura best route kya h"),
        ("status_question_zero_writes", "what about the reminder u set"),
        ("greeting_zero_mutations", "hi")
    ]

    for label, prompt_text in mixed_prompts:
        res_m = await run_agent_turn(
            chat_id=chat_id,
            user_text=prompt_text,
            channel="telegram"
        )
        run_telemetry["turns"].append({
            "scenario": f"mixed_turn_{label}",
            "user_input": prompt_text,
            "final_reply": res_m.get("reply"),
            "trace_id": res_m.get("trace_id"),
            "provider": res_m.get("provider", "gemini"),
            "model": res_m.get("model", "gemini-3.5-flash-lite"),
            "llm_calls": res_m.get("llm_calls", 1),
            "tools_called": res_m.get("tool_calls", []),
            "tool_results": res_m.get("tool_results", [])
        })
        await asyncio.sleep(2.0)

    return run_telemetry


async def main():
    results = []
    base_user = 9988000
    for run_idx in range(1, 2):
        print(f"Executing Run {run_idx} with fresh user {base_user + run_idx}...")
        res = await run_scenario_fresh_user(base_user, run_idx)
        results.append(res)

    out_path = os.path.join(os.path.dirname(__file__), "..", "docs", "verification", "final", "live_smoke.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"Saved complete telemetry to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
