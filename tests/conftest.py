"""
Test configuration and fixtures for PeculiarAI test suite.
Sets up mock environment variables and deterministic test clients.
"""
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

# A5.10 TEST-SUITE ENVIRONMENT GUARD: tests must never touch the live Neon DB.
PROD_DB_FINGERPRINT = os.getenv("PROD_DB_FINGERPRINT", "ep-steep-dawn-b4f6w0un")
_raw_incoming_db = os.environ.get("DATABASE_URL", "")
if PROD_DB_FINGERPRINT in _raw_incoming_db:
    raise RuntimeError(
        "FATAL ENVIRONMENT GUARD: pytest detected DATABASE_URL pointing to production Neon DB! "
        "Tests must NEVER touch the production database. Execution aborted."
    )

# Ensure test environment variables are set before importing application modules
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST_TELEGRAM_BOT_TOKEN"
os.environ["WEBHOOK_SECRET"] = "test_webhook_secret_key_12345678"
os.environ["GROQ_API_KEY"] = "gsk_test_mock_groq_api_key"
os.environ["GROQ_MODEL"] = "openai/gpt-oss-120b"
os.environ["GROQ_STT_MODEL"] = "whisper-large-v3-turbo"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "test_google_client_secret"
os.environ["GOOGLE_TOKEN_KEY"] = "k1cM5_bU4mZJ2tW_jL9qP0rS8uV3wX6yA7bC4dE1fG8="  # Valid 32-byte Fernet key
os.environ["PUBLIC_BASE_URL"] = "https://peculiar-ai-backend.onrender.com"
os.environ["TELEGRAM_OTP_BOT_TOKEN"] = "123456:TEST_OTP_BOT_TOKEN"
os.environ["TELEGRAM_OTP_BOT_USERNAME"] = "PeculiarAI_Verify_Bot"
os.environ["PECULIARAI_WEB_VERIFY_DB"] = "test_web_verify.sqlite3"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["AGENT_CORE"] = "v1"

import main
import llm
import google_auth
import google_services
import multimodal


@pytest.fixture(autouse=True)
def reset_provider_cooldowns_fixture():
    """Ensure LLM provider cooldowns are cleared before and after each test."""
    from llm.provider import reset_provider_cooldowns
    reset_provider_cooldowns()
    yield
    reset_provider_cooldowns()


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient fixture."""
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def init_test_db():
    from db import engine
    from sqlalchemy import text
    if PROD_DB_FINGERPRINT in str(engine.url):
        raise RuntimeError("FATAL: Test suite engine connected to production Neon DB! Aborting immediately.")
    if "sqlite" not in str(engine.url):
        yield
        return
    with engine.begin() as conn:
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
        try:
            conn.execute(text("ALTER TABLE users ADD COLUMN language TEXT DEFAULT 'en'"))
        except Exception:
            pass
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS google_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                email TEXT NOT NULL,
                refresh_token_enc TEXT NOT NULL,
                scopes TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS user_device_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                phone TEXT,
                device_id TEXT NOT NULL,
                google_email TEXT,
                last_login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, device_id)
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
                recurrence TEXT DEFAULT 'none',
                google_calendar_event_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        try:
            conn.execute(text("ALTER TABLE memory_items ADD COLUMN duration_minutes INTEGER DEFAULT 30"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE memory_items ADD COLUMN status TEXT DEFAULT 'active'"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE memory_items ADD COLUMN recurrence TEXT DEFAULT 'none'"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE memory_items ADD COLUMN google_calendar_event_id TEXT"))
        except Exception:
            pass
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
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                channel TEXT DEFAULT 'telegram',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        try:
            conn.execute(text("ALTER TABLE chat_logs ADD COLUMN channel TEXT DEFAULT 'telegram'"))
        except Exception:
            pass
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
            CREATE TABLE IF NOT EXISTS user_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                rule_text TEXT NOT NULL,
                is_active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        try:
            conn.execute(text("ALTER TABLE user_rules ADD COLUMN is_active INTEGER DEFAULT 1"))
        except Exception:
            pass
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
        try:
            conn.execute(text("ALTER TABLE user_relationships ADD COLUMN last_interacted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"))
        except Exception:
            pass
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
            CREATE TABLE IF NOT EXISTS user_subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                service_name TEXT NOT NULL,
                amount REAL,
                billing_cycle TEXT,
                next_renewal_date DATE,
                is_trial INTEGER DEFAULT 0,
                trial_expiry_date DATE,
                cancel_url TEXT,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, service_name)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS memory_bubbles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                slug TEXT NOT NULL,
                name TEXT NOT NULL,
                color_hex TEXT,
                icon_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, slug)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS shared_bubble_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bubble_id INTEGER NOT NULL,
                owner_user_id INTEGER NOT NULL,
                shared_with_chat_id INTEGER NOT NULL,
                permission TEXT DEFAULT 'read',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(bubble_id, shared_with_chat_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS oauth_states (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                state TEXT,
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

    from memory.store import _init_sqlite_if_needed
    _init_sqlite_if_needed()
    from state.pending import ensure_pending_table
    ensure_pending_table()

    main.PENDING_GOOGLE_ACTIONS.clear()
    main.PENDING_WIPE_CODES.clear()
    main.RECENT_DOCS.clear()
    main.RECENT_CHAT_TURNS.clear()
    main.SEEN_UPDATES.clear()
    os.environ["AGENT_CORE"] = "v1"
    yield
    main.PENDING_GOOGLE_ACTIONS.clear()
    main.PENDING_WIPE_CODES.clear()
    main.RECENT_DOCS.clear()
    main.RECENT_CHAT_TURNS.clear()
    main.SEEN_UPDATES.clear()
    os.environ["AGENT_CORE"] = "v1"
