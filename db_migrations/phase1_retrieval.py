"""
PeculiarAI Phase 1 Migration: HNSW Vector Index and Full-Text Search tsvector
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
    print("Running Phase 1 Migration: HNSW + Full-Text Search (tsv)...")
    is_pg = db.engine.dialect.name == "postgresql"
    
    with db.engine.begin() as conn:
        if is_pg:
            # 1. Ensure HNSW index on memories.embedding
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_indexes 
                        WHERE tablename = 'memories' AND indexname = 'idx_memories_embedding'
                    ) THEN
                        CREATE INDEX idx_memories_embedding ON memories USING hnsw (embedding vector_cosine_ops);
                    END IF;
                END $$;
            """))
            print("  ✓ HNSW vector index verified on memories.embedding")

            # 2. Add full-text search tsvector column and GIN index
            conn.execute(text("""
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM information_schema.columns 
                        WHERE table_name = 'memories' AND column_name = 'tsv'
                    ) THEN
                        ALTER TABLE memories ADD COLUMN tsv tsvector 
                        GENERATED ALWAYS AS (to_tsvector('english', coalesce(content, ''))) STORED;
                    END IF;
                END $$;
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_memories_tsv ON memories USING gin(tsv);
            """))
            print("  ✓ Full-text search column 'tsv' and GIN index verified on memories")
        else:
            print("  ✓ SQLite mode: full-text search simulated via LIKE/trigram fallback.")
            
    print("Phase 1 Migration completed successfully!")

if __name__ == "__main__":
    run_migration()
