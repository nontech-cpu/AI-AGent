"""
Idempotent Migration: Phase 0 Structured Measurements & Failure Tracking
Creates 'measurements' and 'failed_extractions' tables with proper indexing.
Reversible and non-destructive.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sqlalchemy import text
import db

def migrate():
    is_pg = db.engine.dialect.name == "postgresql"
    with db.engine.begin() as conn:
        if is_pg:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS measurements (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    subject_entity_id INTEGER REFERENCES entities(id) ON DELETE SET NULL,
                    metric VARCHAR(64) NOT NULL,
                    value NUMERIC NOT NULL,
                    unit VARCHAR(32),
                    measured_at TIMESTAMPTZ NOT NULL,
                    context VARCHAR(64),
                    source_memory_id INTEGER REFERENCES memories(id) ON DELETE SET NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE INDEX IF NOT EXISTS idx_measurements_user_metric_time 
                ON measurements (user_id, metric, measured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_measurements_subject 
                ON measurements (subject_entity_id);

                CREATE TABLE IF NOT EXISTS failed_extractions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    raw_text TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    attempts INTEGER DEFAULT 1,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
            """))
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    subject_entity_id INTEGER,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT,
                    measured_at TIMESTAMP NOT NULL,
                    context TEXT,
                    source_memory_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_measurements_user_metric_time 
                ON measurements (user_id, metric, measured_at DESC);
                CREATE INDEX IF NOT EXISTS idx_measurements_subject 
                ON measurements (subject_entity_id);

                CREATE TABLE IF NOT EXISTS failed_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    raw_text TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    attempts INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
    print("Phase 0 Migration applied successfully!")

if __name__ == "__main__":
    migrate()
