"""
Tests for Part C:
C3: Calendar confirmation shows date with weekday, time, duration, recurrence, and calendar name.
C4: Recurrence delivery across multiple days with FrozenClock, sleep catch-up, exactly one send per occurrence,
    and soft delete with scope 'this' vs 'all'.
"""
import pytest
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import text
import core.timeutil as timeutil
import db
from core.i18n import get_i18n_string

IST = ZoneInfo("Asia/Kolkata")
TEST_CHAT_ID = 887766554


@pytest.fixture(autouse=True)
def setup_user():
    db._init_reminders_scheduler_columns()
    db._init_memory_items_status_column()
    db._init_memory_items_recurrence_column()
    db.ensure_user(TEST_CHAT_ID)
    db.set_state(TEST_CHAT_ID, "active")
    yield
    timeutil.set_frozen_clock(None)


def test_c3_calendar_confirmation_fields():
    """C3: Assert confirmation string contains date with weekday, time, duration, recurrence, calendar name."""
    dt_val = datetime(2026, 11, 11, 10, 0, tzinfo=IST)
    weekday_str = dt_val.strftime("%A, %d %B %Y")  # Wednesday, 11 November 2026
    time_str = "10:00 AM IST"
    duration = 30
    recurrence = "daily"
    cal_name = "Primary Google Calendar"

    conf_text = get_i18n_string(
        "confirm_calendar_event",
        lang="english",
        title="Jogging",
        date_weekday=weekday_str,
        time=time_str,
        duration=duration,
        recurrence=recurrence,
        calendar_name=cal_name
    )

    assert "Wednesday, 11 November 2026" in conf_text
    assert "10:00 AM IST" in conf_text
    assert "30 minutes" in conf_text
    assert "daily" in conf_text
    assert "Primary Google Calendar" in conf_text
    assert "Jogging" in conf_text


def test_c4_recurrence_delivery_and_sleep_catchup():
    """
    C4: Prove scheduler fires each occurrence across 3 days, including after sleep catch-up.
    Exactly one send per occurrence.
    """
    db._init_reminders_scheduler_columns()
    db._init_memory_items_status_column()

    start_dt = datetime(2026, 11, 10, 8, 0, tzinfo=IST)
    timeutil.set_frozen_clock(start_dt)

    # 1. Create a daily recurring reminder starting Nov 10 at 09:00 AM
    due_d = date(2026, 11, 10)
    due_t_str = "09:00"
    remind_at = datetime(2026, 11, 10, 9, 0, tzinfo=IST)

    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM reminders"))
        conn.execute(text("DELETE FROM memory_items WHERE title = 'Morning Gym'"))
        uid = conn.execute(
            text("SELECT id FROM users WHERE telegram_chat_id = :cid"),
            {"cid": TEST_CHAT_ID}
        ).scalar()
        item_id = conn.execute(
            text("""
                INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status)
                VALUES (:uid, 'reminder', 'Morning Gym', :due_date, :due_time, 'daily', 'active')
            """),
            {"uid": uid, "due_date": due_d, "due_time": due_t_str}
        ).lastrowid
        rem_id = conn.execute(
            text("""
                INSERT INTO reminders (item_id, remind_at, due_at, label)
                VALUES (:item_id, :remind_at, :due_at, 'due')
            """),
            {"item_id": item_id, "remind_at": remind_at, "due_at": remind_at}
        ).lastrowid

    # DAY 1: Advance clock to 09:01 AM (Occurrence 1 fires)
    timeutil.set_frozen_clock(datetime(2026, 11, 10, 9, 1, tzinfo=IST))
    pending_day1 = db.claim_pending_reminders(worker_id="test_w", now=datetime(2026, 11, 10, 9, 1, tzinfo=IST))
    assert len(pending_day1) == 1
    assert pending_day1[0][3] == "Morning Gym"
    db.mark_sent(pending_day1[0][0], sent_at=datetime(2026, 11, 10, 9, 1, tzinfo=IST))

    # DAY 2 SLEEP CATCH-UP: Clock jumps to Day 3 10:00 AM (Server was asleep for Day 2)
    timeutil.set_frozen_clock(datetime(2026, 11, 12, 10, 0, tzinfo=IST))
    # Day 2 occurrence was self-scheduled when Day 1 was marked sent
    pending_catchup_1 = db.claim_pending_reminders(worker_id="test_w", now=datetime(2026, 11, 12, 10, 0, tzinfo=IST))
    assert len(pending_catchup_1) == 1
    # Sent Day 2 occurrence
    db.mark_sent(pending_catchup_1[0][0], sent_at=datetime(2026, 11, 12, 10, 0, tzinfo=IST))

    # Day 3 occurrence is immediately scheduled and claimed
    pending_catchup_2 = db.claim_pending_reminders(worker_id="test_w", now=datetime(2026, 11, 12, 10, 0, tzinfo=IST))
    assert len(pending_catchup_2) == 1
    db.mark_sent(pending_catchup_2[0][0], sent_at=datetime(2026, 11, 12, 10, 0, tzinfo=IST))

    # Future occurrence (Day 4) is forward-looking and NOT claimed today
    pending_future = db.claim_pending_reminders(worker_id="test_w", now=datetime(2026, 11, 12, 10, 0, tzinfo=IST))
    assert len(pending_future) == 0


def test_c4_scope_this_vs_all():
    """
    C4: Test scope='this' cancels only the current occurrence and leaves the recurring series alive.
    scope='all' deletes the entire series.
    """
    due_d = date(2026, 11, 10)
    due_t_str = "18:00"
    remind_at = datetime(2026, 11, 10, 18, 0, tzinfo=IST)

    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM reminders"))
        conn.execute(text("DELETE FROM memory_items WHERE title = 'Morning Gym'"))
        uid = conn.execute(
            text("SELECT id FROM users WHERE telegram_chat_id = :cid"),
            {"cid": TEST_CHAT_ID}
        ).scalar()
        item_id = conn.execute(
            text("""
                INSERT INTO memory_items (user_id, category, title, due_date, due_time, recurrence, status)
                VALUES (:uid, 'reminder', 'Daily Evening Walk', :due_date, :due_time, 'daily', 'active')
            """),
            {"uid": uid, "due_date": due_d, "due_time": due_t_str}
        ).lastrowid
        conn.execute(
            text("INSERT INTO reminders (item_id, remind_at, due_at, label) VALUES (:item_id, :remind_at, :due_at, 'due')"),
            {"item_id": item_id, "remind_at": remind_at, "due_at": remind_at}
        )

    # 1. Soft-delete with scope='this'
    del_res = db.soft_delete_reminders(TEST_CHAT_ID, [item_id], scope="this")
    assert len(del_res) == 1

    # Verify parent memory_item is STILL active (not deleted)
    with db.engine.connect() as conn:
        m_row = conn.execute(
            text("SELECT status, recurrence FROM memory_items WHERE id = :id"),
            {"id": item_id}
        ).first()
        assert m_row[0] == "active"
        assert m_row[1] == "daily"

        # Verify next occurrence row was scheduled
        next_rems = conn.execute(
            text("SELECT id, remind_at, status FROM reminders WHERE item_id = :id AND (status IS NULL OR status != 'deleted')"),
            {"id": item_id}
        ).fetchall()
        assert len(next_rems) == 1
        assert "2026-11-11" in str(next_rems[0][1])

    # 2. Soft-delete with scope='all'
    del_all = db.soft_delete_reminders(TEST_CHAT_ID, [item_id], scope="all")
    assert len(del_all) == 1

    with db.engine.connect() as conn:
        m_row = conn.execute(
            text("SELECT status FROM memory_items WHERE id = :id"),
            {"id": item_id}
        ).first()
        assert m_row[0] == "deleted"
