"""
db_migrations/init_postgres_schema.py

Idempotent schema initialization for PostgreSQL.
Creates all required application tables, extensions, constraints, and indexes
with IF NOT EXISTS, enabling clean initialization of a fresh staging or production database.
"""

import os
import sys
import logging
from sqlalchemy import create_engine, text

logger = logging.getLogger("db_migrations")
logging.basicConfig(level=logging.INFO)

DDL_STATEMENTS = [
    # Extensions
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",

    # 1. users
    """CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        telegram_chat_id BIGINT UNIQUE NOT NULL,
        persona TEXT,
        categories TEXT[] DEFAULT '{}'::text[],
        language TEXT DEFAULT 'en',
        consented_at TIMESTAMPTZ,
        state TEXT,
        state_data TEXT,
        first_name TEXT,
        username TEXT,
        timezone TEXT DEFAULT 'Asia/Kolkata',
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_users_chat_id ON users(telegram_chat_id)",

    # 2. memory_items (Core schedule & reminders anchor)
    """CREATE TABLE IF NOT EXISTS memory_items (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        category TEXT NOT NULL,
        title TEXT NOT NULL,
        amount NUMERIC,
        due_date DATE,
        due_time TIME,
        duration_minutes INTEGER DEFAULT 30,
        raw_text TEXT,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memory_items_user_due ON memory_items(user_id, due_date)",

    # 3. reminders (Delivery alerts for memory_items)
    """CREATE TABLE IF NOT EXISTS reminders (
        id SERIAL PRIMARY KEY,
        item_id INTEGER NOT NULL REFERENCES memory_items(id) ON DELETE CASCADE,
        remind_at TIMESTAMPTZ NOT NULL,
        due_at TIMESTAMPTZ,
        sent_at TIMESTAMPTZ,
        claimed_by TEXT,
        claimed_at TIMESTAMPTZ,
        label TEXT NOT NULL,
        sent BOOLEAN NOT NULL DEFAULT false
    )""",
    "CREATE INDEX IF NOT EXISTS idx_reminders_remind_sent ON reminders(remind_at, sent)",

    # 4. google_tokens
    """CREATE TABLE IF NOT EXISTS google_tokens (
        id SERIAL PRIMARY KEY,
        user_id INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        email TEXT,
        refresh_token_enc TEXT NOT NULL,
        scopes TEXT,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 5. entities (Cognitive Memory Graph)
    """CREATE TABLE IF NOT EXISTS entities (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        entity_type VARCHAR(64) NOT NULL DEFAULT 'person',
        primary_alias VARCHAR(255),
        attributes JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT unique_user_entity_name UNIQUE(user_id, name)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_entities_user ON entities(user_id, entity_type)",
    "CREATE INDEX IF NOT EXISTS idx_entities_name_trgm ON entities USING gin (name gin_trgm_ops)",

    # 6. entity_aliases
    """CREATE TABLE IF NOT EXISTS entity_aliases (
        id SERIAL PRIMARY KEY,
        entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
        alias VARCHAR(255) NOT NULL,
        alias_clean VARCHAR(255) NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT unique_entity_alias UNIQUE(entity_id, alias_clean)
    )""",
    "CREATE INDEX IF NOT EXISTS idx_entity_aliases_clean_trgm ON entity_aliases USING gin (alias_clean gin_trgm_ops)",

    # 7. memories (Single Source of Truth cognitive memory)
    """CREATE TABLE IF NOT EXISTS memories (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        memory_type VARCHAR(32) NOT NULL DEFAULT 'episodic',
        subject_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
        content TEXT NOT NULL,
        structured JSONB DEFAULT '{}'::jsonb,
        status VARCHAR(32) DEFAULT 'active',
        source_type VARCHAR(32) DEFAULT 'chat',
        source_id TEXT,
        confidence NUMERIC DEFAULT 1.0,
        importance INTEGER DEFAULT 3,
        valid_from TIMESTAMPTZ DEFAULT now(),
        valid_to TIMESTAMPTZ,
        embedding vector(384),
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        last_accessed_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_memories_user_type ON memories(user_id, memory_type, status)",
    "CREATE INDEX IF NOT EXISTS idx_memories_content_trgm ON memories USING gin (content gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS idx_memories_subject_entity ON memories(subject_entity_id)",
    "CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops)",

    # 8. identity_links (Multi-chat identity unification)
    """CREATE TABLE IF NOT EXISTS identity_links (
        id SERIAL PRIMARY KEY,
        source_chat_id BIGINT UNIQUE NOT NULL,
        canonical_chat_id BIGINT NOT NULL,
        label TEXT DEFAULT 'linked_account',
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_identity_links_source ON identity_links(source_chat_id)",

    # 9. embedding_retry_queue
    """CREATE TABLE IF NOT EXISTS embedding_retry_queue (
        id SERIAL PRIMARY KEY,
        memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        attempts INTEGER DEFAULT 0,
        last_error TEXT,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 10. commitments
    """CREATE TABLE IF NOT EXISTS commitments (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        target_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
        due_date DATE,
        due_time TIME,
        status TEXT NOT NULL DEFAULT 'pending',
        nudge_count INTEGER DEFAULT 0,
        max_nudges INTEGER DEFAULT 3,
        last_nudged_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_commitments_user ON commitments(user_id, status)",

    # 11. conversation_summaries
    """CREATE TABLE IF NOT EXISTS conversation_summaries (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        session_date DATE NOT NULL,
        summary TEXT NOT NULL,
        turn_count INTEGER DEFAULT 0,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 12. turn_traces
    """CREATE TABLE IF NOT EXISTS turn_traces (
        id SERIAL PRIMARY KEY,
        trace_id VARCHAR(64) UNIQUE NOT NULL,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        session_id VARCHAR(64),
        message TEXT,
        tools_called JSONB DEFAULT '[]'::jsonb,
        tool_results JSONB DEFAULT '[]'::jsonb,
        duration_ms NUMERIC,
        input_tokens INTEGER DEFAULT 0,
        output_tokens INTEGER DEFAULT 0,
        total_tokens INTEGER DEFAULT 0,
        model VARCHAR(64),
        provider VARCHAR(32),
        error_type VARCHAR(64),
        error_message TEXT,
        llm_calls JSONB DEFAULT '[]'::jsonb,
        memory_status VARCHAR(64),
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_turn_traces_user ON turn_traces(user_id, created_at DESC)",

    # 13. pending_actions
    """CREATE TABLE IF NOT EXISTS pending_actions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        action_type VARCHAR(64) NOT NULL,
        payload JSONB NOT NULL,
        status VARCHAR(32) DEFAULT 'pending',
        created_at TIMESTAMPTZ DEFAULT now(),
        expires_at TIMESTAMPTZ
    )""",
    "CREATE INDEX IF NOT EXISTS idx_pending_actions_user ON pending_actions(user_id, status)",

    # 14. active_contexts
    """CREATE TABLE IF NOT EXISTS active_contexts (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        context_data JSONB DEFAULT '{}'::jsonb,
        updated_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 15. chat_logs
    """CREATE TABLE IF NOT EXISTS chat_logs (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        role TEXT NOT NULL,
        message TEXT NOT NULL,
        channel TEXT DEFAULT 'telegram',
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    "CREATE INDEX IF NOT EXISTS idx_chat_logs_user ON chat_logs(user_id, created_at DESC)",

    # 16. user_facts (Legacy compatibility & migration anchor)
    """CREATE TABLE IF NOT EXISTS user_facts (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        fact TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        status TEXT DEFAULT 'active',
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 17. user_integrations
    """CREATE TABLE IF NOT EXISTS user_integrations (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        service TEXT NOT NULL,
        api_key_enc TEXT NOT NULL,
        config_data TEXT DEFAULT '{}',
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT unique_user_service UNIQUE(user_id, service)
    )""",

    # 18. contact_memories
    """CREATE TABLE IF NOT EXISTS contact_memories (
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
    )""",

    # 19. user_device_sessions
    """CREATE TABLE IF NOT EXISTS user_device_sessions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        phone TEXT,
        device_id TEXT NOT NULL,
        google_email TEXT,
        last_login_at TIMESTAMPTZ DEFAULT now(),
        created_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT unique_user_device UNIQUE(user_id, device_id)
    )""",

    # 20. processed_user_emails
    """CREATE TABLE IF NOT EXISTS processed_user_emails (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        email_id TEXT NOT NULL,
        subject TEXT,
        sender TEXT,
        processed_at TIMESTAMPTZ DEFAULT now(),
        action_taken TEXT,
        CONSTRAINT unique_chat_email UNIQUE(chat_id, email_id)
    )""",

    # 21. daily_morning_briefings
    """CREATE TABLE IF NOT EXISTS daily_morning_briefings (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        briefing_date DATE NOT NULL,
        sent_at TIMESTAMPTZ DEFAULT now(),
        trigger_kind TEXT DEFAULT 'standard_8am',
        CONSTRAINT unique_chat_briefing_date UNIQUE(chat_id, briefing_date)
    )""",

    # 22. user_corrections
    """CREATE TABLE IF NOT EXISTS user_corrections (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        wrong_concept TEXT NOT NULL,
        correction_rule TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        created_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 23. board_cards
    """CREATE TABLE IF NOT EXISTS board_cards (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        description TEXT,
        status TEXT NOT NULL DEFAULT 'queue',
        priority TEXT NOT NULL DEFAULT 'medium',
        due_date DATE,
        created_at TIMESTAMPTZ DEFAULT now(),
        updated_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 24. smart_lists & items
    """CREATE TABLE IF NOT EXISTS smart_lists (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        category TEXT DEFAULT 'general',
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS smart_list_items (
        id SERIAL PRIMARY KEY,
        list_id INTEGER NOT NULL REFERENCES smart_lists(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        quantity TEXT,
        checked BOOLEAN NOT NULL DEFAULT false,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 25. user_rules
    """CREATE TABLE IF NOT EXISTS user_rules (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        rule_text TEXT NOT NULL,
        is_active BOOLEAN NOT NULL DEFAULT true,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 26. memory_bubbles & members
    """CREATE TABLE IF NOT EXISTS memory_bubbles (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        slug VARCHAR(64) NOT NULL,
        name VARCHAR(128) NOT NULL,
        color_hex VARCHAR(16) DEFAULT '#6366F1',
        icon_name VARCHAR(32) DEFAULT 'Folder',
        created_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT unique_user_bubble_slug UNIQUE(user_id, slug)
    )""",
    """CREATE TABLE IF NOT EXISTS shared_bubble_members (
        id SERIAL PRIMARY KEY,
        bubble_id INTEGER NOT NULL REFERENCES memory_bubbles(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        permission VARCHAR(32) DEFAULT 'read_write',
        joined_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 27. user_mcp_servers
    """CREATE TABLE IF NOT EXISTS user_mcp_servers (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        server_name VARCHAR(128) NOT NULL,
        server_url TEXT NOT NULL,
        api_key_enc TEXT,
        status VARCHAR(32) DEFAULT 'active',
        created_at TIMESTAMPTZ DEFAULT now(),
        CONSTRAINT unique_user_mcp UNIQUE(user_id, server_name)
    )""",

    # 28. measurements & failed_extractions
    """CREATE TABLE IF NOT EXISTS measurements (
        id SERIAL PRIMARY KEY,
        trace_id VARCHAR(64),
        metric_name VARCHAR(64) NOT NULL,
        metric_value NUMERIC NOT NULL,
        tags JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS failed_extractions (
        id SERIAL PRIMARY KEY,
        raw_text TEXT NOT NULL,
        error_reason TEXT,
        source VARCHAR(32) DEFAULT 'chat',
        created_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 29. working_contexts, projects, memory_versions
    """CREATE TABLE IF NOT EXISTS working_contexts (
        user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
        content TEXT NOT NULL,
        updated_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS projects (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name VARCHAR(255) NOT NULL,
        status VARCHAR(32) DEFAULT 'active',
        metadata JSONB DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS memory_versions (
        id SERIAL PRIMARY KEY,
        memory_id INTEGER NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
        previous_content TEXT NOT NULL,
        changed_at TIMESTAMPTZ DEFAULT now()
    )""",

    # 30. email_links, pending_followups, user_relationships, user_subscriptions
    """CREATE TABLE IF NOT EXISTS email_links (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        email TEXT NOT NULL,
        code TEXT NOT NULL,
        verified BOOLEAN DEFAULT false,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS pending_followups (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        query TEXT NOT NULL,
        due_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS user_relationships (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        person_name TEXT NOT NULL,
        relationship TEXT NOT NULL,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
    """CREATE TABLE IF NOT EXISTS user_subscriptions (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        plan TEXT NOT NULL DEFAULT 'free',
        active BOOLEAN DEFAULT true,
        created_at TIMESTAMPTZ DEFAULT now()
    )""",
]


def init_postgres_schema(engine=None, schema: str = "public") -> list[str]:
    """
    Executes idempotent DDL statements against the specified PostgreSQL database and schema.
    Returns list of successfully created/verified tables.
    """
    if engine is None:
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            raise RuntimeError("DATABASE_URL environment variable is required.")
        engine = create_engine(db_url)

    created = []
    with engine.begin() as conn:
        if schema != "public":
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            conn.execute(text(f"SET search_path TO {schema}, public"))

        for stmt in DDL_STATEMENTS:
            clean_stmt = stmt.strip()
            conn.execute(text(clean_stmt))

    logger.info("All 38 tables, extensions, and indexes verified/created successfully.")
    return created


if __name__ == "__main__":
    init_postgres_schema()
    print("Postgres schema initialization complete!")

