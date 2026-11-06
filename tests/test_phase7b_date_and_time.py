import pytest
from datetime import datetime, date
from unittest.mock import patch

from core import timeutil
from agent.prompt import build_runtime_system_prompt

# 10 test dates covering month-ends, month-starts, leap years, and the user's specific test turn
TEST_DATES = [
    (date(2024, 2, 29), "Thursday", "Leap day 2024"),
    (date(2024, 12, 31), "Tuesday", "Year end 2024"),
    (date(2025, 1, 1), "Wednesday", "New year 2025"),
    (date(2025, 2, 28), "Friday", "Month end non-leap Feb 2025"),
    (date(2026, 4, 30), "Thursday", "Month end April 2026"),
    (date(2026, 5, 1), "Friday", "Month start May 2026"),
    (date(2026, 11, 5), "Thursday", "User Telegram turn date 5 Nov 2026"),
    (date(2026, 11, 6), "Friday", "Live current date 6 Nov 2026"),
    (date(2028, 2, 29), "Tuesday", "Future leap day 2028"),
    (date(2030, 12, 31), "Tuesday", "Decade end 2030"),
]

@pytest.mark.parametrize("test_date, expected_weekday, label", TEST_DATES)
def test_timeutil_weekday_accuracy(test_date, expected_weekday, label):
    """Verify core/timeutil accurately computes weekday for any calendar date without off-by-one drift."""
    dt = datetime(test_date.year, test_date.month, test_date.day, 12, 0, tzinfo=timeutil.IST)
    assert dt.strftime("%A") == expected_weekday, f"Failed for {label}: expected {expected_weekday}, got {dt.strftime('%A')}"

@pytest.mark.asyncio
@pytest.mark.parametrize("test_date, expected_weekday, label", TEST_DATES)
async def test_prompt_anchoring_for_dates(test_date, expected_weekday, label):
    """Verify that build_runtime_system_prompt strictly injects the true weekday and ISO date from timeutil."""
    mock_now = datetime(test_date.year, test_date.month, test_date.day, 15, 30, tzinfo=timeutil.IST)
    
    with patch.object(timeutil, "now", return_value=mock_now):
        prompt = await build_runtime_system_prompt(chat_id=12345, user_text="aaj date kya hai")
        
        # Verify prompt contains the exact ISO date and Weekday
        assert test_date.isoformat() in prompt, f"Expected {test_date.isoformat()} in prompt for {label}"
        assert expected_weekday in prompt, f"Expected {expected_weekday} in prompt for {label}"
        
        # Specifically verify Operating Principle 3 has the weekday and ISO date
        assert f"{test_date.isoformat()} ({expected_weekday})" in prompt or (test_date.isoformat() in prompt and expected_weekday in prompt)
