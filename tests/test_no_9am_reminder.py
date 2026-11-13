from datetime import date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import text

import core.timeutil as timeutil
import db
import main
from timing import IST, build_plan


def _reset_time():
    timeutil.set_frozen_clock(None)


def test_build_plan_generates_only_proximity_alerts_and_zero_9am():
    """Timed meetings generate only 15min, 5min, and at_time alerts; zero 9am reminders."""
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    event_date = date(2026, 11, 14)
    event_time = time(17, 0)  # 5:00 PM tomorrow

    plan = build_plan(event_date, event_time, now=now)
    assert plan["status"] == "scheduled"
    reminders = plan["reminders"]

    labels = [r[1] for r in reminders]
    # Exactly proximity alerts
    assert labels == ["15min", "5min", "at_time"]

    # Zero 9:00 AM alerts
    for rem_at, label in reminders:
        assert label not in ("day_before", "due_day")
        assert rem_at.astimezone(IST).time() != time(9, 0)


def test_build_plan_all_day_generates_zero_9am_reminders():
    """All-day events generate zero 9 AM alerts (covered proactively by 8 AM morning briefing)."""
    now = datetime(2026, 11, 13, 10, 0, tzinfo=IST)
    event_date = date(2026, 11, 14)

    plan = build_plan(event_date, None, now=now)
    assert plan["reminders"] == []


@pytest.mark.asyncio
async def test_run_reminders_supersedes_legacy_9am_reminders():
    """Legacy 9 AM reminder rows in DB are cleanly superseded and never sent to Telegram."""
    _reset_time()
    chat_id = 998877805
    db.ensure_user(chat_id)
    now = datetime(2026, 11, 13, 9, 0, tzinfo=IST)
    timeutil.set_frozen_clock(now)

    try:
        with db.engine.begin() as conn:
            conn.execute(text("DELETE FROM reminders"))
            uid = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).scalar()
            item_id = conn.execute(
                text(
                    "INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status) "
                    "VALUES (:u, 'appointments', 'Legacy Sugar Check', :d, '11:00', 'daily', 'active')"
                ),
                {"u": uid, "d": date(2026, 11, 13)},
            ).lastrowid

        # Insert legacy due_day and day_before reminders
        db.add_reminders(item_id, [
            (datetime(2026, 11, 13, 9, 0, tzinfo=IST), "due_day"),
            (datetime(2026, 11, 13, 9, 0, tzinfo=IST), "day_before"),
        ])

        sent_messages = []
        async def mock_send(cid, msg):
            sent_messages.append((cid, msg))

        with patch("main.send_telegram_text", side_effect=mock_send):
            count = await main.run_reminders(worker_id="test_legacy_9am")

        # Zero messages sent to Telegram
        assert count == 0
        assert len(sent_messages) == 0

        # Rows in DB must be superseded
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("SELECT label, status, sent FROM reminders WHERE item_id = :i"),
                {"i": item_id},
            ).fetchall()
            assert len(rows) == 2
            assert all(r[1] == "superseded" for r in rows)
    finally:
        _reset_time()

