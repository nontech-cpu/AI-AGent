import pytest
from datetime import datetime, date, time as dtime, timedelta
from unittest.mock import patch, AsyncMock
from timing import IST
import db
import google_services
from agent.tools import execute_tool

TEST_CHAT_ID = 88776655
LINKED_CHAT_ID = 88776656


@pytest.fixture(autouse=True)
def setup_recurring_fixtures():
    from core.idempotency import clear_idempotency_cache
    clear_idempotency_cache()
    db.ensure_user(TEST_CHAT_ID)
    db.ensure_user(LINKED_CHAT_ID)
    db._init_memory_items_recurrence_column()
    db.link_user_identities(TEST_CHAT_ID, LINKED_CHAT_ID, "same_user_iphone")
    try:
        with db.engine.begin() as conn:
            conn.execute(
                db.text("DELETE FROM memory_items WHERE user_id IN (SELECT id FROM users WHERE telegram_chat_id IN (:c1, :c2))"),
                {"c1": TEST_CHAT_ID, "c2": LINKED_CHAT_ID}
            )
    except Exception:
        pass
    yield
    clear_idempotency_cache()
    try:
        with db.engine.begin() as conn:
            conn.execute(
                db.text("DELETE FROM memory_items WHERE user_id IN (SELECT id FROM users WHERE telegram_chat_id IN (:c1, :c2))"),
                {"c1": TEST_CHAT_ID, "c2": LINKED_CHAT_ID}
            )
    except Exception:
        pass


@pytest.mark.asyncio
async def test_daily_reminder_created_at_past_time_rolls_to_tomorrow():
    """
    CRITICAL USER SCENARIO:
    User asks at 12:32 PM for 'daily reminder of jogging at 10am'.
    10:00 AM has already passed today.
    The engine MUST:
    1. Detect that today's 10:00 AM has passed.
    2. Auto-roll the first occurrence to tomorrow (11 Nov 2026).
    3. Save recurrence='daily' in the database.
    4. Provide clear, helpful confirmation explaining it starts tomorrow.
    """
    now_1232pm = datetime(2026, 11, 10, 12, 32, tzinfo=IST)
    with patch("core.timeutil.now", return_value=now_1232pm), \
         patch("main.get_valid_google_connection_by_chat_id", return_value=(False, None)):

        res = await execute_tool(
            TEST_CHAT_ID,
            "create_reminder",
            {
                "title": "Jogging",
                "due_time": "10:00",
                "recurrence": "daily"
            },
            user_context={"user_text": "mere daily reminder set krdo joggina ka 10am ka"}
        )

        assert res["status"] == "success"
        assert res["due_date"] == "2026-11-11"  # Automatically advanced to tomorrow!
        assert res["due_time"] == "10:00"
        assert res["recurrence"] == "daily"
        assert res["rolled_to_tomorrow"] is True
        # Confirmation message clarifies that 10:00 AM today passed and starts tomorrow
        assert "Daily" in res["message"]
        assert "kal" in res["message"].lower() or "11 Nov" in res["message"]


def test_virtual_recurrence_projection_returns_jogging_for_tomorrow_and_future():
    """
    Verifies that querying tomorrow's date or any future date returns the daily reminder
    without needing 365 bloated database rows.
    """
    # Save a daily reminder starting tomorrow (11 Nov 2026) at 10:00 AM
    start_d = date(2026, 11, 11)
    db.save_memory(
        TEST_CHAT_ID,
        category="appointments",
        title="Jogging",
        amount=None,
        due_date=start_d,
        due_time=dtime(10, 0),
        duration_minutes=30,
        recurrence="daily"
    )

    # 1. Tomorrow (11 Nov 2026) -> MUST appear
    items_tomorrow = db.items_for_date(TEST_CHAT_ID, date(2026, 11, 11))
    titles_tomorrow = [r[1] for r in items_tomorrow]
    assert "Jogging" in titles_tomorrow

    # 2. 5 days later (16 Nov 2026) -> MUST appear
    items_future = db.items_for_date(TEST_CHAT_ID, date(2026, 11, 16))
    titles_future = [r[1] for r in items_future]
    assert "Jogging" in titles_future

    # 3. Yesterday (10 Nov 2026) -> MUST NOT appear (started on 11 Nov)
    items_yesterday = db.items_for_date(TEST_CHAT_ID, date(2026, 11, 10))
    titles_yesterday = [r[1] for r in items_yesterday]
    assert "Jogging" not in titles_yesterday


@pytest.mark.asyncio
async def test_google_calendar_sync_receives_rrule_freq_daily():
    """Verifies that recurring reminders send standard RFC-5545 RRULE:FREQ=DAILY to Google Calendar."""
    now_1232pm = datetime(2026, 11, 10, 12, 32, tzinfo=IST)
    with patch("core.timeutil.now", return_value=now_1232pm), \
         patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock, return_value=(True, "test@test.com")), \
         patch("google_services.create_calendar_event", new_callable=AsyncMock) as mock_cal:

        mock_cal.return_value = {"id": "cal_123", "summary": "🔔 Jogging"}

        res = await execute_tool(
            TEST_CHAT_ID,
            "create_reminder",
            {
                "title": "Jogging",
                "due_time": "10:00",
                "recurrence": "daily"
            }
        )

        assert res["status"] == "success"
        assert mock_cal.called is True
        call_kwargs = mock_cal.call_args[1]
        assert call_kwargs.get("recurrence") == ["RRULE:FREQ=DAILY"]


def test_non_recurring_reminder_does_not_leak_to_tomorrow():
    """Verifies that standard one-time reminders never leak into future days."""
    db.save_memory(
        TEST_CHAT_ID,
        category="appointments",
        title="Doctor Appointment",
        amount=None,
        due_date=date(2026, 11, 10),
        due_time=dtime(17, 0),
        duration_minutes=30,
        recurrence="none"
    )

    items_today = db.items_for_date(TEST_CHAT_ID, date(2026, 11, 10))
    assert "Doctor Appointment" in [r[1] for r in items_today]

    items_tomorrow = db.items_for_date(TEST_CHAT_ID, date(2026, 11, 11))
    assert "Doctor Appointment" not in [r[1] for r in items_tomorrow]


def test_weekdays_recurrence_skips_weekends():
    """Verifies that weekdays recurrence correctly applies Monday-Friday and skips Saturday-Sunday."""
    # Start on Friday (13 Nov 2026)
    fri_d = date(2026, 11, 13)
    db.save_memory(
        TEST_CHAT_ID,
        category="appointments",
        title="Team Standup",
        amount=None,
        due_date=fri_d,
        due_time=dtime(9, 30),
        duration_minutes=15,
        recurrence="weekdays"
    )

    # Saturday (14 Nov 2026) -> MUST NOT appear
    sat_items = db.items_for_date(TEST_CHAT_ID, date(2026, 11, 14))
    assert "Team Standup" not in [r[1] for r in sat_items]

    # Sunday (15 Nov 2026) -> MUST NOT appear
    sun_items = db.items_for_date(TEST_CHAT_ID, date(2026, 11, 15))
    assert "Team Standup" not in [r[1] for r in sun_items]

    # Monday (16 Nov 2026) -> MUST appear
    mon_items = db.items_for_date(TEST_CHAT_ID, date(2026, 11, 16))
    assert "Team Standup" in [r[1] for r in mon_items]


@pytest.mark.asyncio
async def test_list_reminders_tool_returns_tomorrow_daily_reminder():
    """
    CRITICAL USER SCENARIO:
    When user says 'kl mere remidner btana', the list_reminders tool for tomorrow's date
    MUST return the daily reminder.
    """
    # Setup daily reminder starting 11 Nov 2026
    db.save_memory(
        TEST_CHAT_ID,
        category="appointments",
        title="Jogging",
        amount=None,
        due_date=date(2026, 11, 11),
        due_time=dtime(10, 0),
        duration_minutes=30,
        recurrence="daily"
    )

    now_10th = datetime(2026, 11, 10, 12, 32, tzinfo=IST)
    with patch("core.timeutil.now", return_value=now_10th):
        res = await execute_tool(
            TEST_CHAT_ID,
            "list_reminders",
            {"date_str": "2026-11-11"}
        )
        assert res["status"] == "success"
        titles = [r["title"] for r in res.get("reminders", [])]
        assert "Jogging" in titles
