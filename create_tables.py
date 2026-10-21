from sqlalchemy import text
from db import engine

with engine.begin() as conn:
    # 1. Base users table
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_chat_id BIGINT UNIQUE NOT NULL,
            persona TEXT,
            categories TEXT[],
            language TEXT,
            consented_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))

    # 2. Dynamic Task Boards (Queue, This Week, Today, Done)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS board_cards (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'queue',
            priority TEXT NOT NULL DEFAULT 'medium',
            due_date DATE,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """))

    # 3. Persistent Smart Lists
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS smart_lists (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))

    # 4. Smart List Items (Checklist items with checked/unchecked state)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS smart_list_items (
            id SERIAL PRIMARY KEY,
            list_id INTEGER NOT NULL REFERENCES smart_lists(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            quantity TEXT,
            checked BOOLEAN NOT NULL DEFAULT false,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))

    # 5. Behavioral Rule Learning (AI Preferences)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS user_rules (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            rule_text TEXT NOT NULL,
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))

    # 6. Relationship & Networking Memory (Enfolo feature)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS contact_memories (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            company TEXT,
            role_title TEXT,
            email TEXT,
            phone TEXT,
            met_at TEXT,
            context_notes TEXT,
            commitments TEXT,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))

    # 7. Persistent Chat Logs (Full Conversation History in PostgreSQL)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS chat_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_chat_logs_user ON chat_logs(user_id, created_at DESC)"))

    # 8. User Long-Term Semantic Facts & Life Memory (ChatGPT style memory)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS user_facts (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            fact TEXT NOT NULL,
            category TEXT DEFAULT 'general',
            status TEXT DEFAULT 'active',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_facts_user ON user_facts(user_id, status)"))

print("All tables including chat_logs and user_facts created successfully!")