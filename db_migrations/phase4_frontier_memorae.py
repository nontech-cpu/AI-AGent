"""
Phase 4 Migration: Frontier Memorae Architecture
- Structured Memory Bubbles (everything, work, travel, health, finance)
- Memories table extension for Multimodal Assets, OCR text, and pgvector cosine search
- User MCP Servers Registry table
Idempotent across Neon PostgreSQL and SQLite.
"""
import os
import sys
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///peculiar.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(DATABASE_URL)

def log(msg):
    print(msg, flush=True)

DEFAULT_BUBBLES = [
    ("everything", "Everything", "#6366F1", "Inbox"),
    ("work", "Work & Projects", "#3B82F6", "Briefcase"),
    ("travel", "Travel & Transit", "#EC4899", "Plane"),
    ("health", "Health & Fitness", "#10B981", "Heart"),
    ("finance", "Finance & Bills", "#F59E0B", "CreditCard"),
]


def run_migration():
    log(f"Connecting to database: {engine.url.render_as_string(hide_password=True)}")
    is_postgres = "postgresql" in engine.url.drivername

    with engine.begin() as conn:
        log("1. Creating memory_bubbles table...")
        if is_postgres:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS memory_bubbles (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    slug VARCHAR(64) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    color_hex VARCHAR(16) DEFAULT '#6366F1',
                    icon_name VARCHAR(64) DEFAULT 'Folder',
                    created_at TIMESTAMPTZ DEFAULT now(),
                    CONSTRAINT unique_user_bubble_slug UNIQUE(user_id, slug)
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_memory_bubbles_user ON memory_bubbles(user_id);"))
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS memory_bubbles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    color_hex TEXT DEFAULT '#6366F1',
                    icon_name TEXT DEFAULT 'Folder',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, slug)
                );
            """))

        log("2. Extending memories table with multimodal asset fields...")
        columns_to_add = [
            ("bubble_slug", "VARCHAR(64) DEFAULT 'everything'" if is_postgres else "TEXT DEFAULT 'everything'"),
            ("title", "TEXT"),
            ("url", "TEXT"),
            ("image_url", "TEXT"),
            ("ocr_extracted_text", "TEXT"),
            ("ai_summary", "TEXT"),
            ("tags", "JSONB DEFAULT '[]'::jsonb" if is_postgres else "TEXT DEFAULT '[]'"),
        ]

        for col_name, col_type in columns_to_add:
            try:
                conn.execute(text(f"ALTER TABLE memories ADD COLUMN IF NOT EXISTS {col_name} {col_type};"))
                log(f"   Added or verified column '{col_name}' in memories")
            except Exception as e:
                # SQLite fallback
                try:
                    conn.execute(text(f"ALTER TABLE memories ADD COLUMN {col_name} {col_type};"))
                    log(f"   Added column '{col_name}' in memories (fallback)")
                except Exception:
                    pass

        if is_postgres:
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_bubble_user ON memories(user_id, bubble_slug);"))
            try:
                # Creating HNSW on existing populated table can take time if huge, so let's check
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);"))
            except Exception as e:
                log(f"   HNSW index note: {e}")

        log("3. Creating user_mcp_servers table...")
        if is_postgres:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_mcp_servers (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    server_name VARCHAR(128) NOT NULL,
                    server_url TEXT NOT NULL,
                    auth_token TEXT,
                    is_active BOOLEAN DEFAULT true,
                    tools_cache JSONB DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    CONSTRAINT unique_user_mcp_server UNIQUE(user_id, server_name)
                );
            """))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_user_mcp_user ON user_mcp_servers(user_id);"))
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_mcp_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    server_name TEXT NOT NULL,
                    server_url TEXT NOT NULL,
                    auth_token TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    tools_cache TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, server_name)
                );
            """))

        log("4. Seeding default memory bubbles for existing users...")
        try:
            if is_postgres:
                conn.execute(text("""
                    INSERT INTO memory_bubbles (user_id, slug, name, color_hex, icon_name)
                    SELECT u.id, d.slug, d.name, d.color_hex, d.icon_name
                    FROM users u
                    CROSS JOIN (
                        VALUES 
                            ('everything', 'Everything', '#6366F1', 'Inbox'),
                            ('work', 'Work & Projects', '#3B82F6', 'Briefcase'),
                            ('travel', 'Travel & Transit', '#EC4899', 'Plane'),
                            ('health', 'Health & Fitness', '#10B981', 'Heart'),
                            ('finance', 'Finance & Bills', '#F59E0B', 'CreditCard')
                    ) AS d(slug, name, color_hex, icon_name)
                    ON CONFLICT (user_id, slug) DO NOTHING;
                """))
                log("   Bulk seeded default bubbles for users in PostgreSQL.")
            else:
                users = conn.execute(text("SELECT id FROM users")).fetchall()
                for (u_id,) in users:
                    for slug, name, color, icon in DEFAULT_BUBBLES:
                        conn.execute(text("""
                            INSERT OR IGNORE INTO memory_bubbles (user_id, slug, name, color_hex, icon_name)
                            VALUES (:u, :s, :n, :c, :i);
                        """), {"u": u_id, "s": slug, "n": name, "c": color, "i": icon})
                log(f"   Seeded default bubbles for {len(users)} users (SQLite).")
        except Exception as e:
            log(f"   Bubble seeding note: {e}")

    log("Phase 4 Frontier Memorae migration complete!")


if __name__ == "__main__":
    run_migration()
