"""
Phase 3 Migration: Standalone Commitments Table with State Machine and Proactive Nudge Support.
Idempotent and reversible. Supports both Neon PostgreSQL and SQLite.
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


def run_migration():
    print(f"Connecting to database: {engine.url.render_as_string(hide_password=True)}")
    is_postgres = "postgresql" in engine.url.drivername

    with engine.begin() as conn:
        print("Checking/creating table 'commitments'...")
        if is_postgres:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS commitments (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    subject_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    target_date TIMESTAMPTZ,
                    condition_type VARCHAR(32) NOT NULL DEFAULT 'date',
                    condition_details JSONB,
                    status VARCHAR(32) NOT NULL DEFAULT 'pending',
                    nudge_count INTEGER NOT NULL DEFAULT 0,
                    max_nudges INTEGER NOT NULL DEFAULT 3,
                    last_nudged_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_commitments_user_status 
                ON commitments(user_id, status);
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_commitments_target_status 
                ON commitments(target_date, status);
            """))
        else:
            # SQLite syntax
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS commitments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    subject_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
                    title TEXT NOT NULL,
                    description TEXT,
                    target_date TEXT,
                    condition_type TEXT NOT NULL DEFAULT 'date',
                    condition_details TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    nudge_count INTEGER NOT NULL DEFAULT 0,
                    max_nudges INTEGER NOT NULL DEFAULT 3,
                    last_nudged_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_commitments_user_status 
                ON commitments(user_id, status);
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_commitments_target_status 
                ON commitments(target_date, status);
            """))

    print("Phase 3 commitments migration complete.")


if __name__ == "__main__":
    run_migration()
