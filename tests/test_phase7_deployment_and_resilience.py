"""
tests/test_phase7_deployment_and_resilience.py - Verification Suite for Phase 7.

Validates:
1. Production environment fail-fast: Refuses silent in-memory SQLite fallback if DATABASE_URL is missing.
2. Comprehensive /health endpoint: Live DB ping, latency measurement, LLM/embeddings config check, HTTP 200/503.
3. DB-driven scheduler with multi-instance concurrency control (SKIP LOCKED / atomic claims).
4. Startup catch-up pass: Overdue reminders accrued during server sleep are delivered with explicit late notice.
5. Active context persistence: Survives server restart/memory wipe via database backing.
6. Conversation history cache rebuilding: Safely repopulates from chat_logs on restart.
7. Structured turn observability: Complete telemetry with secret redaction.
"""

import pytest
import os
import json
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient

import db
import main
from main import app, get_recent_chat_turns, RECENT_CHAT_TURNS, run_reminders, validate_startup_environment
from state.active_context import update_active_context, get_active_context, clear_active_context, _ACTIVE_CONTEXT_STORE
from connectors.trace import log_turn_trace, get_last_trace
from timing import IST
import core.timeutil as timeutil


@pytest.fixture
def client():
    return TestClient(app)


def test_silent_database_url_fallback_raises_in_production():
    """Verify that in production (RENDER=true or ENV=production), missing DATABASE_URL raises RuntimeError."""
    with patch.dict(os.environ, {"RENDER": "true", "DATABASE_URL": ""}):
        with pytest.raises(RuntimeError) as exc_info:
            # Re-evaluating production guard
            validate_startup_environment()
        assert "DATABASE_URL is not set in production" in str(exc_info.value)

    with patch.dict(os.environ, {"ENV": "production", "DATABASE_URL": "sqlite:///:memory:"}):
        with pytest.raises(RuntimeError) as exc_info:
            validate_startup_environment()
        assert "SQLite database is forbidden in production" in str(exc_info.value)


def test_health_endpoint_healthy(client):
    """Verify /health endpoint returns structured status with live database latency."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] in ("healthy", "degraded")
    assert "database" in data
    assert data["database"]["status"] == "connected"
    assert isinstance(data["database"]["latency_ms"], (int, float))
    assert "llm" in data
    assert "embeddings" in data
    assert "timestamp" in data
    assert "version" in data


def test_health_endpoint_503_when_database_fails(client):
    """Verify /health endpoint returns HTTP 503 if database connection fails."""
    with patch.object(db.engine, "connect", side_effect=Exception("Database connection timeout")):
        res = client.get("/health")
        assert res.status_code == 503
        data = res.json()
        assert data["status"] == "degraded"
        assert data["database"]["status"] == "disconnected"
        assert "Database connection timeout" in data["database"]["error"]


def test_reminders_db_scheduler_claims_and_multi_instance_locking():
    """Verify DB scheduler atomically claims due reminders, preventing duplicate delivery across instances."""
    chat_id = 998877701
    db.ensure_user(chat_id)
    now = timeutil.now(IST)

    # Insert test memory item & reminder
    item_id = db.save_memory(chat_id, "reminder", "Urgent Team Standup", None, due_date=now.strftime("%Y-%m-%d"), due_time=now.strftime("%H:%M:%S"))
    db.add_reminders(item_id, [(now - timedelta(seconds=10), "at_time")])

    worker_a = "render_instance_a"
    worker_b = "render_instance_b"

    # Worker A claims pending reminders
    claimed_a = db.claim_pending_reminders(worker_id=worker_a, now=now)
    assert any(r[0] == item_id or r[3] == "Urgent Team Standup" for r in claimed_a)

    # Worker B simultaneously attempts to claim; must get 0 rows because Worker A has locked them
    claimed_b = db.claim_pending_reminders(worker_id=worker_b, now=now)
    assert not any(r[3] == "Urgent Team Standup" for r in claimed_b)

    # Clean up
    for r in claimed_a:
        db.mark_sent(r[0])


@pytest.mark.asyncio
async def test_startup_catchup_pass_delivers_missed_reminders_with_late_wording():
    """Verify startup catch-up pass delivers reminders that accrued during downtime with explicit late notice."""
    chat_id = 998877702
    db.ensure_user(chat_id)
    now = timeutil.now(IST)

    # Simulate reminder scheduled 15 minutes ago while server was asleep
    missed_time = now - timedelta(minutes=15)
    item_id = db.save_memory(chat_id, "reminder", "Client Proposal Submission", None, due_date=missed_time.strftime("%Y-%m-%d"), due_time=missed_time.strftime("%H:%M:%S"))
    db.add_reminders(item_id, [(missed_time, "at_time")])

    sent_messages = []
    async def mock_send(cid, text):
        sent_messages.append((cid, text))

    with patch("main.send_telegram_text", side_effect=mock_send):
        sent_count = await run_reminders(worker_id="startup_catchup_test", is_catchup=True)
        assert sent_count >= 1
        assert any("Client Proposal Submission" in msg[1] for msg in sent_messages)
        # MUST contain explicit late wording
        assert any("Delivered late" in msg[1] for msg in sent_messages)


def test_active_context_persistence_across_restart():
    """Verify active conversational focus is persisted to DB and rebuilds after in-memory cache wipe."""
    chat_id = 998877703
    db.ensure_user(chat_id)

    # Update active context
    update_active_context(
        chat_id,
        connector="Notion",
        topic="Q4 Engineering Roadmap",
        objects=["Roadmap Page", "Database Tasks"],
        url="https://notion.so/roadmap123"
    )

    # Verify present in memory
    ctx_mem = get_active_context(chat_id)
    assert ctx_mem["connector"] == "Notion"
    assert ctx_mem["topic"] == "Q4 Engineering Roadmap"

    # Simulate server reboot / in-memory cache wipe
    _ACTIVE_CONTEXT_STORE.clear()
    assert chat_id not in _ACTIVE_CONTEXT_STORE

    # Re-fetch: should seamlessly restore from database
    ctx_restored = get_active_context(chat_id)
    assert ctx_restored["connector"] == "Notion"
    assert ctx_restored["topic"] == "Q4 Engineering Roadmap"
    assert ctx_restored["last_url"] == "https://notion.so/roadmap123"
    assert "Roadmap Page" in ctx_restored["objects"]

    # Clean up
    clear_active_context(chat_id)


def test_recent_chat_turns_rebuild_from_database_after_cache_wipe():
    """Verify conversational turn history rebuilds from database chat_logs when memory cache is empty."""
    chat_id = 998877704
    db.ensure_user(chat_id)

    # Log turns to DB
    db.log_chat_message(chat_id, "user", "Project deadline kab hai?")
    db.log_chat_message(chat_id, "assistant", "Aapka project deadline kal shaam 5 baje hai.")

    # Wipe in-memory deque
    RECENT_CHAT_TURNS.clear()
    assert chat_id not in RECENT_CHAT_TURNS

    # get_recent_chat_turns should rebuild from DB chat_logs
    turns = get_recent_chat_turns(chat_id, limit=10)
    assert len(turns) >= 2
    assert any(t["text"] == "Project deadline kab hai?" for t in turns)
    assert any(t["text"] == "Aapka project deadline kal shaam 5 baje hai." for t in turns)


def test_structured_turn_logging_masks_secrets():
    """Verify turn trace logging masks sensitive credentials, tokens, and keys in DB and log output."""
    chat_id = 998877705
    db.ensure_user(chat_id)

    secret_msg = "Please connect using token ghp_1234567890abcdef1234567890 and secret_abcdef9876543210"

    trace_id = log_turn_trace(
        chat_id=chat_id,
        message=secret_msg,
        tools_called=["test_tool"],
        tool_results=[{"auth": "Bearer ghp_1234567890abcdef1234567890"}],
        guard_results={"ok": True},
        latency_ms=120,
        tokens=85,
        reply_path="test",
        provider="Gemini",
        model="gemini-3.1-flash-lite-preview"
    )

    tr = get_last_trace(chat_id)
    assert tr is not None
    # Secret tokens must NEVER appear raw in trace message or results
    assert "ghp_1234567890abcdef1234567890" not in tr["message"]
    assert "secret_abcdef9876543210" not in tr["message"]
    assert "[REDACTED" in tr["message"] or "***" in tr["message"]
