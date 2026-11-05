"""
PeculiarAI Test Harness: Isolated Test DB Fixture
Guarantees clean, isolated database state for tests without ever touching live PostgreSQL Neon data.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool
import db

def create_isolated_engine():
    """Creates a dedicated in-memory SQLite engine with StaticPool."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool
    )
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
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS memory_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                category TEXT DEFAULT 'appointments',
                title TEXT NOT NULL,
                amount REAL,
                due_date DATE,
                due_time TIME,
                duration_minutes INTEGER DEFAULT 30,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id INTEGER NOT NULL,
                remind_at TIMESTAMP NOT NULL,
                label TEXT,
                sent INTEGER DEFAULT 0,
                FOREIGN KEY (item_id) REFERENCES memory_items(id)
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS memories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                memory_type TEXT DEFAULT 'semantic',
                content TEXT NOT NULL,
                structured JSON,
                tsv TEXT,
                embedding TEXT,
                status TEXT DEFAULT 'active',
                importance INTEGER DEFAULT 3,
                valid_to TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS entities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                entity_type TEXT DEFAULT 'person',
                attributes JSON,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS commitments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                entity_id INTEGER,
                commitment TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                due_date TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
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
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                channel TEXT DEFAULT 'telegram',
                role TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS turn_traces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trace_id TEXT,
                chat_id INTEGER,
                message TEXT,
                detected_connector TEXT,
                tools_called TEXT,
                tool_results TEXT,
                guard_results TEXT,
                latency_ms REAL,
                tokens INTEGER,
                reply_path TEXT,
                is_suspect INTEGER DEFAULT 0,
                suspect_reason TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """))
    return engine

