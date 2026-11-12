from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import text

import core.timeutil as timeutil
import db
import main
from timing import IST


def _reset_time():
    timeutil.set_frozen_clock(None)


def test_unsent_reminder_sql_uses_boolean_predicate_on_postgresql():
    """PostgreSQL queries must never emit `sent = 0` against a boolean column."""
    mock_dialect = MagicMock()
    mock_dialect.name = "postgresql"
    with patch.object(db.engine, "dialect", mock_dialect):
        assert db._unsent_reminder_sql("r") == "(r.sent IS NOT TRUE)"
        assert db._unsent_reminder_sql() == "(sent IS NOT TRUE)"
        assert "= 0" not in db._unsent_reminder_sql("r")


def test_mark_sent_commits_ack_even_if_recurrence_scheduling_fails():
    """Recurrence advancement failure must never roll back the sent=true commit."""
    _reset_time()
    chat_id = 998877801
    db.ensure_user(chat_id)
    now = datetime(2026, 11, 12, 11, 0, tzinfo=IST)

    with db.engine.begin() as conn:
        uid = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).scalar()
        item_id = conn.execute(
            text(
                "INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status) "
                "VALUES (:u, 'appointments', 'Check sugar', :d, '11:00', 'daily', 'active')"
            ),
            {"u": uid, "d": date(2026, 11, 12)},
        ).lastrowid
        rem_id = conn.execute(
            text("INSERT INTO reminders (item_id, remind_at, due_at, label) VALUES (:i, :a, :a, 'at_time')"),
            {"i": item_id, "a": now},
        ).lastrowid

    with patch("db._schedule_next_recurring_slot", side_effect=RuntimeError("simulated recurrence DB error")):
        db.mark_sent(rem_id, sent_at=now)

    with db.engine.connect() as conn:
        row = conn.execute(
            text("SELECT sent, sent_at, claimed_by FROM reminders WHERE id = :id"),
            {"id": rem_id},
        ).first()
        assert bool(row[0]) is True
        assert row[1] is not None
        assert row[2] is None


def test_per_label_recurring_advancement_and_idempotency():
    """All labels (due_day, 15min, 5min, at_time) advance independently to next day without duplicates."""
    _reset_time()
    chat_id = 998877802
    db.ensure_user(chat_id)
    base_date = date(2026, 11, 12)
    event_dt = datetime(2026, 11, 12, 11, 0, tzinfo=IST)
    slots = [
        (datetime(2026, 11, 12, 9, 0, tzinfo=IST), "due_day"),
        (event_dt - timedelta(minutes=15), "15min"),
        (event_dt - timedelta(minutes=5), "5min"),
        (event_dt, "at_time"),
    ]

    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM reminders"))
        uid = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).scalar()
        item_id = conn.execute(
            text(
                "INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status) "
                "VALUES (:u, 'appointments', 'Daily Medication', :d, '11:00', 'daily', 'active')"
            ),
            {"u": uid, "d": base_date},
        ).lastrowid
    db.add_reminders(item_id, slots)

    with db.engine.connect() as conn:
        rem_rows = conn.execute(
            text("SELECT id, label FROM reminders WHERE item_id = :i ORDER BY id"),
            {"i": item_id},
        ).fetchall()

    for rid, _ in rem_rows:
        db.mark_sent(rid, sent_at=event_dt)
        # Re-calling mark_sent must be idempotent (no duplicate tomorrow rows)
        db.mark_sent(rid, sent_at=event_dt)

    with db.engine.connect() as conn:
        tomorrow_rows = conn.execute(
            text("SELECT label, remind_at FROM reminders WHERE item_id = :i AND (sent IS NOT TRUE OR sent = 0)"),
            {"i": item_id},
        ).fetchall()
        labels = sorted(r[0] for r in tomorrow_rows)
        assert labels == ["15min", "5min", "at_time", "due_day"]
        assert all("2026-11-13" in str(r[1]) for r in tomorrow_rows)


@pytest.mark.asyncio
async def test_run_reminders_coalesces_stale_prealerts_and_preserves_future_prealerts():
    """Late catch-up suppresses obsolete pre-alerts for past events while sending one at_time alert."""
    _reset_time()
    chat_id = 998877803
    db.ensure_user(chat_id)
    now = datetime(2026, 11, 12, 12, 57, tzinfo=IST)
    timeutil.set_frozen_clock(now)

    try:
        with db.engine.begin() as conn:
            conn.execute(text("DELETE FROM reminders"))
            uid = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).scalar()
            past_item = conn.execute(
                text(
                    "INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status) "
                    "VALUES (:u, 'appointments', 'Check sugar', :d, '11:00', 'daily', 'active')"
                ),
                {"u": uid, "d": date(2026, 11, 12)},
            ).lastrowid
            future_item = conn.execute(
                text(
                    "INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status) "
                    "VALUES (:u, 'appointments', 'Meet with Arihant', :d, '13:00', 'none', 'active')"
                ),
                {"u": uid, "d": date(2026, 11, 12)},
            ).lastrowid

        db.add_reminders(past_item, [
            (datetime(2026, 11, 12, 9, 0, tzinfo=IST), "due_day"),
            (datetime(2026, 11, 12, 10, 45, tzinfo=IST), "15min"),
            (datetime(2026, 11, 12, 10, 55, tzinfo=IST), "5min"),
            (datetime(2026, 11, 12, 11, 0, tzinfo=IST), "at_time"),
        ])
        db.add_reminders(future_item, [
            (datetime(2026, 11, 12, 12, 55, tzinfo=IST), "5min"),
        ])

        sent_messages = []
        async def mock_send(cid, msg):
            sent_messages.append((cid, msg))

        with patch("main.send_telegram_text", side_effect=mock_send):
            count = await main.run_reminders(worker_id="test_coalesce")

        # Only 1 late at_time for Check sugar + 1 active 5min for Meet with Arihant
        assert count == 2
        sugar_msgs = [m[1] for m in sent_messages if "Check sugar" in m[1]]
        arihant_msgs = [m[1] for m in sent_messages if "Meet with Arihant" in m[1]]
        assert len(sugar_msgs) == 1
        assert "Delivered late" in sugar_msgs[0]
        assert "It's time" in sugar_msgs[0]
        assert len(arihant_msgs) == 1
        assert "In 5 minutes" in arihant_msgs[0]

        # Tomorrow's 4 recurring Check sugar slots must still be scheduled cleanly
        with db.engine.connect() as conn:
            next_sugar = conn.execute(
                text("SELECT label FROM reminders WHERE item_id = :i AND (sent IS NOT TRUE OR sent = 0)"),
                {"i": past_item},
            ).fetchall()
            assert sorted(r[0] for r in next_sugar) == ["15min", "5min", "at_time", "due_day"]
    finally:
        _reset_time()


@pytest.mark.asyncio
async def test_failed_telegram_delivery_dead_letters_after_bounded_attempts():
    """Repeated Telegram delivery failures transition to dead_letter instead of spamming indefinitely."""
    _reset_time()
    chat_id = 998877804
    db.ensure_user(chat_id)
    t0 = datetime(2026, 11, 12, 11, 0, tzinfo=IST)

    try:
        with db.engine.begin() as conn:
            conn.execute(text("DELETE FROM reminders"))
            uid = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).scalar()
            item_id = conn.execute(
                text(
                    "INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status) "
                    "VALUES (:u, 'appointments', 'Failing Task', :d, '11:00', 'none', 'active')"
                ),
                {"u": uid, "d": date(2026, 11, 12)},
            ).lastrowid
        db.add_reminders(item_id, [(t0, "at_time")])

        async def failing_send(cid, msg):
            raise RuntimeError("Telegram HTTP 500")

        with patch("main.send_telegram_text", side_effect=failing_send):
            for step in range(4):
                timeutil.set_frozen_clock(t0 + timedelta(minutes=6 * step))
                await main.run_reminders(worker_id="test_dlq")

        with db.engine.connect() as conn:
            row = conn.execute(
                text("SELECT status, attempt_count, last_error FROM reminders WHERE item_id = :i"),
                {"i": item_id},
            ).first()
            assert row[0] == "dead_letter"
            assert row[1] == 3
            assert "Telegram HTTP 500" in (row[2] or "")

        # Future claim calls return nothing for dead-lettered row
        timeutil.set_frozen_clock(t0 + timedelta(minutes=30))
        assert db.claim_pending_reminders(worker_id="test_dlq", now=timeutil.now(IST)) == []
    finally:
        _reset_time()
