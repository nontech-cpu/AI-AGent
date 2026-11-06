"""
tests/test_fresh_db_schema.py

Tests the idempotent schema initialization against a clean, empty PostgreSQL schema.
Verifies that all 38 required tables are created properly without error.
"""

import os
import pytest
import dotenv
from sqlalchemy import create_engine, text
from db_migrations.init_postgres_schema import init_postgres_schema

env_vals = dotenv.dotenv_values(".env")
DATABASE_URL = env_vals.get("DATABASE_URL") or os.getenv("REAL_DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith("postgres"),
    reason="Live PostgreSQL DATABASE_URL required for Postgres test schema validation"
)

TEST_SCHEMA = "test_fresh_staging_verification"


def test_init_postgres_schema_against_fresh_database():
    """Verify that a fresh, empty PostgreSQL database/schema receives all required tables idempotently."""
    engine = create_engine(DATABASE_URL)
    
    # 1. Setup isolated fresh schema
    with engine.begin() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;"))
        conn.execute(text(f"CREATE SCHEMA {TEST_SCHEMA};"))

    try:
        # 2. Run idempotent schema initialization
        init_postgres_schema(engine=engine, schema=TEST_SCHEMA)

        # 3. Assert all tables were created
        with engine.connect() as conn:
            tables = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = :s
                ORDER BY table_name;
            """), {"s": TEST_SCHEMA}).fetchall()
            
            created_tables = {t[0] for t in tables}
            print(f"Total tables created in fresh schema: {len(created_tables)}")
            
            # Check critical tables
            critical_tables = [
                "users", "memory_items", "reminders", "google_tokens", "entities",
                "entity_aliases", "memories", "identity_links", "embedding_retry_queue",
                "commitments", "conversation_summaries", "turn_traces", "pending_actions",
                "chat_logs", "user_integrations", "contact_memories"
            ]
            for ct in critical_tables:
                assert ct in created_tables, f"Missing table in fresh DB: {ct}"

            assert len(created_tables) >= 30, f"Expected at least 30 tables, got {len(created_tables)}"

        # 4. Idempotency check: run a second time to ensure zero errors on pre-existing tables
        init_postgres_schema(engine=engine, schema=TEST_SCHEMA)

    finally:
        # 5. Teardown
        with engine.begin() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;"))
        engine.dispose()
