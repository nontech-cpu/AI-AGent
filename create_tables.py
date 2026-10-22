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

    # 9. User Integrations & API Tokens (Notion, GitHub, Trello)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS user_integrations (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            service TEXT NOT NULL,
            api_key_enc TEXT NOT NULL,
            config_data TEXT DEFAULT '{}',
            is_active BOOLEAN NOT NULL DEFAULT true,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT unique_user_service UNIQUE(user_id, service)
        )
    """))

    # 10. Phase 2: Layered Cognitive Memory (Entities, Aliases, Multimodal & Vector Memories)
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    # L5: Entity Knowledge Graph
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS entities (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name VARCHAR(255) NOT NULL,
            entity_type VARCHAR(64) NOT NULL DEFAULT 'person',
            primary_alias VARCHAR(255),
            attributes JSONB DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT unique_user_entity_name UNIQUE(user_id, name)
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_entities_user ON entities(user_id, entity_type)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_entities_name_trgm ON entities USING gin (name gin_trgm_ops)"))

    # Entity Aliases for Typo & Phonetic Tolerance ('annaya' -> 'Ananya Roy')
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS entity_aliases (
            id SERIAL PRIMARY KEY,
            entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
            alias VARCHAR(255) NOT NULL,
            alias_clean VARCHAR(255) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            CONSTRAINT unique_entity_alias UNIQUE(entity_id, alias_clean)
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_entity_aliases_clean_trgm ON entity_aliases USING gin (alias_clean gin_trgm_ops)"))

    # Unified Cognitive Memories Table (L1 Working, L2 Summary, L3 Episodic, L4 Semantic, L5 Relations)
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS memories (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            memory_type VARCHAR(32) NOT NULL DEFAULT 'episodic',
            subject_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
            content TEXT NOT NULL,
            structured JSONB DEFAULT '{}'::jsonb,
            status VARCHAR(32) DEFAULT 'active',
            source_type VARCHAR(32) DEFAULT 'chat',
            embedding vector(384),
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            last_accessed_at TIMESTAMPTZ DEFAULT now()
        )
    """))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_user_type ON memories(user_id, memory_type, status)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_content_trgm ON memories USING gin (content gin_trgm_ops)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_subject_entity ON memories(subject_entity_id)"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops)"))

print("All tables including entities, entity_aliases, and memories created successfully!")