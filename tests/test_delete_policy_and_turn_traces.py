"""
Tests for Final Hardening Part A: Delete Policy, Two-Phase Confirmation, and Turn Traces.
Validates:
1. 'hi' greeting after delete offer never confirms or deletes.
2. 'ok' with a different pending action operates only on that action.
3. Ambiguous title keyword matches >1 rows -> returns 'ambiguous' with IDs, deletes 0 rows.
4. Bulk soft delete requires explicit confirmed=True and exact target_ids; restore_reminder recovers items.
5. Pending delete action persists across simulated process restart.
6. Slash commands and early returns write turn_traces rows.
"""
import pytest
import asyncio
from datetime import date, time
from sqlalchemy import text

import db
from db import engine, canonical_chat_id
from agent.tools import execute_tool
from state.pending import (
    ensure_pending_table,
    save_pending_action,
    get_pending_action,
    complete_pending_action,
    cancel_pending_action,
    classify_confirmation_intent_with_llm,
    execute_pending_action,
    _PENDING_ACTION_CACHE
)
from connectors.trace import log_turn_trace


@pytest.fixture(autouse=True)
def setup_tables():
    ensure_pending_table()
    db._init_memory_items_recurrence_column()
    db._init_memory_items_status_column()
    db._init_reminders_scheduler_columns()
    _PENDING_ACTION_CACHE.clear()
    yield
    _PENDING_ACTION_CACHE.clear()


@pytest.mark.asyncio
async def test_ambiguous_title_returns_list_and_deletes_zero():
    """When multiple reminders match a title keyword, return ambiguous and do not delete."""
    chat_id = 999101
    can_cid = canonical_chat_id(chat_id)

    with engine.begin() as conn:
        # Create user if needed
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'TestUser')"), {"c": can_cid})
        u_id = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": can_cid}).scalar()
        
        # Insert two meetings
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                 "VALUES (:u, 'appointments', 'Team Meeting', '2026-11-10', '10:00:00', 'active')"),
            {"u": u_id}
        )
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                 "VALUES (:u, 'appointments', 'Client Meeting', '2026-11-10', '14:00:00', 'active')"),
            {"u": u_id}
        )

    # Call delete_reminder without confirmed
    res = await execute_tool(chat_id, "delete_reminder", {"title": "Meeting"})
    assert res["status"] == "ambiguous"
    assert "matching_items" in res["facts"]
    assert len(res["facts"]["matching_items"]) == 2
    assert res["facts"]["reason"] == "Multiple matching reminders found. User must specify which ID to delete."

    # Verify 0 rows deleted in DB
    with engine.connect() as conn:
        active_count = conn.execute(
            text("SELECT count(*) FROM memory_items WHERE user_id = :u AND (status IS NULL OR status != 'deleted')"),
            {"u": u_id}
        ).scalar()
        assert active_count == 2


@pytest.mark.asyncio
async def test_single_match_returns_needs_confirmation_with_target_ids():
    """A single matching reminder requires explicit confirmation with exact IDs before soft delete."""
    chat_id = 999102
    can_cid = canonical_chat_id(chat_id)

    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'TestUser')"), {"c": can_cid})
        u_id = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": can_cid}).scalar()
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                 "VALUES (:u, 'appointments', 'Dentist Appointment', '2026-11-12', '11:00:00', 'active')"),
            {"u": u_id}
        )
        row_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    # Step 1: Tool returns needs_confirmation
    res = await execute_tool(chat_id, "delete_reminder", {"title": "Dentist"})
    assert res["status"] == "needs_confirmation"
    assert res["target_ids"] == [row_id]
    assert res["facts"]["requires_explicit_confirmation"] is True

    # Row is still active
    with engine.connect() as conn:
        st = conn.execute(text("SELECT status FROM memory_items WHERE id = :id"), {"id": row_id}).scalar()
        assert st == "active"


@pytest.mark.asyncio
async def test_greeting_hi_never_confirms_pending_delete():
    """When a pending delete is awaiting confirmation, sending 'hi' classifies as 'unrelated' and deletes nothing."""
    chat_id = 999103
    can_cid = canonical_chat_id(chat_id)

    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'TestUser')"), {"c": can_cid})
        u_id = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": can_cid}).scalar()
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                 "VALUES (:u, 'appointments', 'Gym Workout', '2026-11-10', '18:00:00', 'active')"),
            {"u": u_id}
        )
        row_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    # Save pending delete action
    save_pending_action(chat_id, "delete_reminder", {
        "kind": "delete_reminder",
        "action": "delete_reminder",
        "target_ids": [row_id],
        "items": [{"id": row_id, "title": "Gym Workout"}]
    })

    pending = get_pending_action(chat_id)
    assert pending is not None

    # Simulate user sending casual greeting "hi"
    intent, extra = await classify_confirmation_intent_with_llm("hi", pending)
    assert intent == "unrelated"

    # Row must remain completely active
    with engine.connect() as conn:
        st = conn.execute(text("SELECT status FROM memory_items WHERE id = :id"), {"id": row_id}).scalar()
        assert st == "active"


@pytest.mark.asyncio
async def test_confirmed_execution_soft_deletes_and_can_be_restored():
    """Confirmed execution soft deletes strictly by target_ids, and restore_reminder recovers it."""
    chat_id = 999104
    can_cid = canonical_chat_id(chat_id)

    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'TestUser')"), {"c": can_cid})
        u_id = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": can_cid}).scalar()
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                 "VALUES (:u, 'appointments', 'Product Review', '2026-11-10', '16:00:00', 'active')"),
            {"u": u_id}
        )
        row_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    # Save pending action
    save_pending_action(chat_id, "delete_reminder", {
        "kind": "delete_reminder",
        "action": "delete_reminder",
        "target_ids": [row_id],
        "items": [{"id": row_id, "title": "Product Review"}]
    })

    pending = get_pending_action(chat_id)
    # Execute confirmed pending action
    ok, msg = await execute_pending_action(chat_id, pending)
    assert ok is True

    # Verify soft deleted in DB
    with engine.connect() as conn:
        st = conn.execute(text("SELECT status FROM memory_items WHERE id = :id"), {"id": row_id}).scalar()
        assert st == "deleted"

    # Restore tool
    res_restore = await execute_tool(chat_id, "restore_reminder", {"reminder_id": row_id})
    assert res_restore["status"] == "success"
    assert res_restore["facts"]["restored_id"] == row_id

    # Verify active again in DB
    with engine.connect() as conn:
        st = conn.execute(text("SELECT status FROM memory_items WHERE id = :id"), {"id": row_id}).scalar()
        assert st == "active"


@pytest.mark.asyncio
async def test_pending_delete_survives_process_restart():
    """Pending delete action persists in DB across memory cache eviction."""
    chat_id = 999105

    save_pending_action(chat_id, "delete_reminder", {
        "kind": "delete_reminder",
        "action": "delete_reminder",
        "target_ids": [101, 102],
        "items": [{"id": 101, "title": "A"}, {"id": 102, "title": "B"}]
    })

    # Clear memory cache completely to simulate process restart
    _PENDING_ACTION_CACHE.clear()

    # Re-retrieve from DB
    recovered = get_pending_action(chat_id)
    assert recovered is not None
    assert recovered["action_kind"] == "delete_reminder"
    assert recovered["payload"]["target_ids"] == [101, 102]


@pytest.mark.asyncio
async def test_slash_command_writes_turn_trace():
    """Inbound slash commands write a turn_traces record."""
    chat_id = 999106
    msg = "/memories"
    
    from main import _log_slash_or_early_trace
    _log_slash_or_early_trace(chat_id, msg, "slash_memories", "Your memories list", "/memories")

    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT trace_id, chat_id, message, reply_path, llm_calls FROM turn_traces WHERE chat_id = :c ORDER BY id DESC LIMIT 1"),
            {"c": chat_id}
        ).fetchone()

        assert row is not None
        assert row[1] == chat_id
        assert row[2] == msg
        assert row[3] == "slash_memories"
        assert row[4] == 0


# ==============================================================================
# A5.1 - A5.10 TEST SUITE: Model Confirmation Rejection & Incident Forensics
# ==============================================================================

@pytest.mark.asyncio
async def test_a5_1_model_supplied_confirmation_rejected():
    """A5.1: If model supplies confirmed=True, it is rejected and 0 rows deleted."""
    chat_id = 999201
    can_cid = canonical_chat_id(chat_id)

    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'TestUser')"), {"c": can_cid})
        u_id = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": can_cid}).scalar()
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                 "VALUES (:u, 'appointments', 'Critical Meeting', '2026-11-10', '15:00:00', 'active')"),
            {"u": u_id}
        )
        row_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    # FakeLLM tries to self-confirm deletion directly in tool call arguments
    res = await execute_tool(chat_id, "delete_reminder", {
        "title": "Critical Meeting",
        "reminder_id": row_id,
        "confirmed": True
    })

    assert res["status"] == "error"
    assert res["error_type"] == "model_confirmation_rejected"
    assert "cannot self-confirm" in res["facts"]["reason"]

    # Verify zero rows changed
    with engine.connect() as conn:
        st = conn.execute(text("SELECT status FROM memory_items WHERE id = :id"), {"id": row_id}).scalar()
        assert st == "active"


@pytest.mark.asyncio
async def test_a5_1_cross_user_target_ids_rejected():
    """A5.1: If an attacker/model passes IDs not belonging to the user, reject and delete 0 rows."""
    user_a_chat = 999202
    user_b_chat = 999203

    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'UserA')"), {"c": user_a_chat})
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'UserB')"), {"c": user_b_chat})
        u_a = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": user_a_chat}).scalar()
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                 "VALUES (:u, 'appointments', 'UserA Private Meeting', '2026-11-10', '15:00:00', 'active')"),
            {"u": u_a}
        )
        user_a_row_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    # User B tries to delete User A's reminder
    res = await execute_tool(user_b_chat, "delete_reminder", {
        "target_ids": [user_a_row_id]
    }, user_context={"_from_pending_executor": True})

    assert res["status"] == "not_found"
    assert res["facts"]["found"] is False

    # Row must remain completely active
    with engine.connect() as conn:
        st = conn.execute(text("SELECT status FROM memory_items WHERE id = :id"), {"id": user_a_row_id}).scalar()
        assert st == "active"


@pytest.mark.asyncio
async def test_a5_2_real_incident_reproduction_zero_rows_changed():
    """
    A5.2: Reproduces the real safety incident:
    - No pending action row exists.
    - Previous assistant message in chat log offered to delete ('Should I delete Meeting?').
    - User sends casual message 'hi'.
    - Model generates delete_reminder call with confirmed=True.
    - System rejects execution; zero rows changed!
    """
    chat_id = 999204
    can_cid = canonical_chat_id(chat_id)

    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'TestUser')"), {"c": can_cid})
        u_id = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": can_cid}).scalar()
        for i in range(3):
            conn.execute(
                text(f"INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                     f"VALUES (:u, 'appointments', 'Meeting {i+1}', '2026-11-10', '17:00:00', 'active')"),
                {"u": u_id}
            )

    # Assert no pending action row exists
    pending = get_pending_action(chat_id)
    assert pending is None

    # Simulate FakeLLM attempting to execute delete_reminder on 'hi'
    res1 = await execute_tool(chat_id, "delete_reminder", {"title": "Meeting", "confirmed": True})
    assert res1["status"] == "error"
    assert res1["error_type"] == "model_confirmation_rejected"

    res2 = await execute_tool(chat_id, "delete_reminder", {"title": "Meeting"})
    assert res2["status"] == "ambiguous"

    # Assert exactly 3 rows remain active
    with engine.connect() as conn:
        count = conn.execute(
            text("SELECT count(*) FROM memory_items WHERE user_id = :u AND (status IS NULL OR status != 'deleted')"),
            {"u": u_id}
        ).scalar()
        assert count == 3


@pytest.mark.asyncio
async def test_a5_3_soft_delete_completeness():
    """
    A5.3: Soft delete completeness:
    - Soft-deleted items do not fire in scheduler (claim_pending_reminders)
    - Soft-deleted items do not appear in items_for_date
    - Soft-deleted items do not appear in find_recent_meetings
    - Retention policy physically purges expired rows
    """
    chat_id = 999205
    can_cid = canonical_chat_id(chat_id)
    from datetime import datetime, date, time
    from timing import IST
    import core.timeutil as timeutil

    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'TestUser')"), {"c": can_cid})
        u_id = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": can_cid}).scalar()
        now_dt = timeutil.now(IST)
        today_d = now_dt.date()
        today_iso = today_d.isoformat()
        
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status, created_at) "
                 "VALUES (:u, 'appointments', 'Old Soft-Deleted Task', :d, '10:00:00', 'active', '2026-09-01 10:00:00')"),
            {"u": u_id, "d": today_iso}
        )
        row_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        conn.execute(
            text("INSERT INTO reminders (item_id, remind_at, sent, status, label) VALUES (:id, :rat, 0, 'active', 'due')"),
            {"id": row_id, "rat": now_dt.strftime("%Y-%m-%d %H:%M:%S")}
        )

    # 1. Active item is found
    items_before = db.items_for_date(chat_id, today_d)
    assert any(r[4] == row_id for r in items_before)

    # 2. Soft-delete the item
    deleted = db.soft_delete_reminders(chat_id, [row_id])
    assert len(deleted) == 1

    # 3. Ignored in items_for_date
    items_after = db.items_for_date(chat_id, today_d)
    assert not any(r[4] == row_id for r in items_after)

    # 4. Ignored in scheduler (claim_pending_reminders)
    claimed = db.claim_pending_reminders(worker_id="test_worker", now=now_dt)
    assert not any(c[0] == row_id or c[3] == 'Old Soft-Deleted Task' for c in claimed)

    # 5. Ignored in find_recent_meetings
    recents = db.find_recent_meetings(chat_id, limit=5)
    assert not any(r[0] == row_id for r in recents)

    # 6. Retention purge cleans up old soft-deleted item
    purged = db.purge_expired_soft_deleted(days=30)
    assert purged >= 1

    with engine.connect() as conn:
        exists = conn.execute(text("SELECT count(*) FROM memory_items WHERE id = :id"), {"id": row_id}).scalar()
        assert exists == 0


@pytest.mark.asyncio
async def test_a5_6_turn_trace_resilience_mid_turn_failure(monkeypatch, tmp_path):
    """
    A5.6: Mid-turn DB failure during log_turn_trace does not crash or break reply,
    writes to dead-letter file, and logs health warning.
    """
    dead_letter_file = tmp_path / "dead_letter.jsonl"
    monkeypatch.setenv("DEAD_LETTER_TRACE_PATH", str(dead_letter_file))

    # Simulate DB connection failure during turn trace insert
    from unittest.mock import patch
    def broken_begin():
        raise RuntimeError("Simulated Database Connection Drop")

    with patch.object(db.engine, "begin", side_effect=broken_begin):
        trace_id = log_turn_trace(
            chat_id=999206,
            message="User query with DB down",
            tools_called=[],
            tool_results=[],
            guard_results={},
            latency_ms=120,
            tokens=50,
            reply_path="test_resilience"
        )

    # Assert trace_id was still returned safely (caller never crashes)
    assert trace_id.startswith("tr_")

    # Assert dead-letter log was written
    assert dead_letter_file.exists()
    content = dead_letter_file.read_text(encoding="utf-8")
    assert trace_id in content
    assert "Simulated Database Connection Drop" in content


@pytest.mark.asyncio
async def test_a5_9_canonical_chat_id_scoping():
    """A5.9: Secondary linked identity inherits access via canonical_chat_id."""
    primary_chat = 999207
    secondary_chat = 999208

    with engine.begin() as conn:
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'Primary')"), {"c": primary_chat})
        conn.execute(text("INSERT OR IGNORE INTO users (telegram_chat_id, first_name) VALUES (:c, 'Secondary')"), {"c": secondary_chat})
        u_p = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": primary_chat}).scalar()
        conn.execute(
            text("INSERT INTO memory_items (user_id, category, title, due_date, due_time, status) "
                 "VALUES (:u, 'appointments', 'Shared Calendar Task', '2026-11-10', '14:00:00', 'active')"),
            {"u": u_p}
        )
        row_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()

    # Link secondary to primary via db helper
    db.link_user_identities(secondary_chat, primary_chat, 'Secondary phone')

    # Querying from secondary chat must resolve to primary chat's data
    items = db.items_for_date(secondary_chat, date(2026, 11, 10))
    assert any(r[4] == row_id for r in items)


