"""
PeculiarAI Phase 2 Migration: Memory Versioning and Conversation Summary Tables.
Idempotent and reversible migration for Neon PostgreSQL and SQLite.
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import db
from sqlalchemy import text

def run_migration():
    print("Running Phase 2 Migration: Memory Versioning + Conversation Summaries...")
    is_pg = db.engine.dialect.name == "postgresql"

    with db.engine.begin() as conn:
        if is_pg:
            # 1. Add versioning columns to memories table
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'memories' AND column_name = 'superseded_by') THEN
                        ALTER TABLE memories ADD COLUMN superseded_by INTEGER REFERENCES memories(id) ON DELETE SET NULL;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'memories' AND column_name = 'valid_from') THEN
                        ALTER TABLE memories ADD COLUMN valid_from TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'memories' AND column_name = 'valid_to') THEN
                        ALTER TABLE memories ADD COLUMN valid_to TIMESTAMPTZ DEFAULT NULL;
                    END IF;
                    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'memories' AND column_name = 'importance') THEN
                        ALTER TABLE memories ADD COLUMN importance INTEGER DEFAULT 3;
                    END IF;
                END $$;
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_memories_valid_to ON memories(user_id, valid_to);
            """))
            print("  ✓ Versioning columns (superseded_by, valid_from, valid_to, importance) verified on memories")

            # 2. Create conversation_summaries table
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    summary TEXT NOT NULL,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
            """))
            print("  ✓ conversation_summaries table verified")
        else:
            # SQLite fallback
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    user_id INTEGER PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            print("  ✓ SQLite mode: conversation_summaries table verified.")

    print("Phase 2 Migration completed successfully!")

if __name__ == "__main__":
    run_migration()
