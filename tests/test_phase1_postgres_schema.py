"""
tests/test_phase1_postgres_schema.py - Real PostgreSQL Test Schema Execution.

Runs Phase 1 Date & Clash Tests against a real Neon PostgreSQL test schema (test_phase1).
Guarantees 100% isolation from production data in the 'public' schema.
Creates test_phase1 on setup, runs tests, and drops test_phase1 on teardown.
"""
import os
import pytest
from datetime import date, time as dtime
from zoneinfo import ZoneInfo
from unittest.mock import patch
import dotenv
from sqlalchemy import create_engine, text, event
import db
import main
import google_services
from agent.tools import execute_tool
from tests.harness.clock import FrozenClock

IST = ZoneInfo("Asia/Kolkata")
env_vals = dotenv.dotenv_values(".env")
DATABASE_URL = env_vals.get("DATABASE_URL") or os.getenv("REAL_DATABASE_URL")

from tests.conftest import PROD_DB_FINGERPRINT

# Skip if DATABASE_URL is not configured or if pointing to protected production DB
pytestmark = pytest.mark.skipif(
    not DATABASE_URL or not DATABASE_URL.startswith("postgres") or (PROD_DB_FINGERPRINT in (DATABASE_URL or "") and os.getenv("ALLOW_NEON_TEST_SCHEMA") != "1"),
    reason="Live PostgreSQL DATABASE_URL required (protected production Neon DB skipped in automated test harness)"
)

TEST_SCHEMA = "test_phase1"


@pytest.fixture(scope="module", autouse=True)
def postgres_test_schema_fixture():
    """
    Sets up isolated test_phase1 schema in Neon Postgres and tears it down after tests.
    """
    admin_engine = create_engine(DATABASE_URL)
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {TEST_SCHEMA};"))
        conn.commit()
        # Replicate tables using PostgreSQL LIKE ... INCLUDING ALL
        for tbl in ["users", "memory_items", "user_facts", "memories", "contact_memories", "chat_logs"]:
            conn.execute(text(f"CREATE TABLE IF NOT EXISTS {TEST_SCHEMA}.{tbl} (LIKE public.{tbl} INCLUDING ALL);"))
        conn.commit()

    # Create schema-isolated engine and hook search_path via connect and checkout events (compatible with Neon PgBouncer pooler)
    test_pg_engine = create_engine(DATABASE_URL)

    @event.listens_for(test_pg_engine, "connect")
    def set_search_path_on_connect(dbapi_connection, connection_record):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {TEST_SCHEMA}, public;")

    @event.listens_for(test_pg_engine, "checkout")
    def set_search_path_on_checkout(dbapi_connection, connection_record, connection_proxy):
        with dbapi_connection.cursor() as cursor:
            cursor.execute(f"SET search_path TO {TEST_SCHEMA}, public;")

    # Monkeypatch db.engine, main.engine, and google_services.engine
    with patch.object(db, "engine", test_pg_engine), \
         patch.object(main, "engine", test_pg_engine, create=True), \
         patch.object(google_services, "engine", test_pg_engine):
        yield test_pg_engine

    # Teardown: drop test schema cleanly
    with admin_engine.connect() as conn:
        conn.execute(text(f"DROP SCHEMA IF EXISTS {TEST_SCHEMA} CASCADE;"))
        conn.commit()
    admin_engine.dispose()
    test_pg_engine.dispose()


@pytest.mark.asyncio
async def test_postgres_poisoned_cutoff_date_rejected():
    """Verify poisoned dates are rejected when running against Neon Postgres."""
    chat_id = 998877501
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        res = await execute_tool(chat_id, "create_reminder", {
            "title": "Neon Postgres Test Reminder",
            "due_date": "2025-01-24",
            "due_time": "16:30"
        })
        assert res["status"] == "error"
        assert res["error"] == "invalid_date_or_in_past"
        assert "2025-01-24" in res["message"]
        assert "2026-11-05" in res["message"]


@pytest.mark.asyncio
async def test_postgres_clash_detection_meeting_vs_movie():
    """Verify clash detection detects 16:00 meeting vs 16:30 movie against Neon Postgres."""
    chat_id = 998877502
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM memory_items WHERE user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c)"), {"c": chat_id})

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        res1 = await execute_tool(chat_id, "create_reminder", {
            "title": "Sync on Neon Postgres",
            "due_date": "2026-11-05",
            "due_time": "16:00",
            "duration_minutes": 60
        })
        assert res1["status"] == "success"

        res2 = await execute_tool(chat_id, "create_reminder", {
            "title": "Movie in Theater",
            "due_date": "2026-11-05",
            "due_time": "16:30",
            "duration_minutes": 120
        })
        assert res2["has_conflict"] is True
        assert res2["status"] == "conflict_detected"
        assert any("Sync on Neon Postgres" in c for c in res2["conflicts"])


@pytest.mark.asyncio
async def test_postgres_list_reminders_missing_date_returns_today():
    """Verify list_reminders with no date returns today's items on Neon Postgres."""
    chat_id = 998877503
    db.ensure_user(chat_id)
    db.give_consent(chat_id)

    with FrozenClock("2026-11-05T13:30:00", tz=IST):
        db.save_memory(chat_id, "appointments", "Dentist Neon Checkup", amount=None, due_date=date(2026, 11, 5), due_time=dtime(18, 0))

        res = await execute_tool(chat_id, "list_reminders", {})
        assert res["date"] == "2026-11-05"
        assert res["count"] >= 1
        assert any(r["title"] == "Dentist Neon Checkup" for r in res["reminders"])
