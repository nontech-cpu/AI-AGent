"""
core/timeutil.py - Centralized Temporal & Clock Authority for PeculiarAI.

Exposes ONE clock function (core.timeutil.now) used across the entire application.
Supports FrozenClock injection for deterministic testing and past/cutoff date rejection.
"""

from __future__ import annotations
import re
from datetime import datetime, date, time, timezone, timedelta
from typing import Optional, Union, Tuple
from zoneinfo import ZoneInfo

# Standard Default Timezone for PeculiarAI (India Standard Time)
DEFAULT_TIMEZONE = ZoneInfo("Asia/Kolkata")
IST = DEFAULT_TIMEZONE

# Global Clock Anchor for FrozenClock test injection
_FROZEN_NOW: Optional[datetime] = None


def set_frozen_clock(dt: Optional[datetime]) -> None:
    """Set or clear the global frozen clock for testing."""
    global _FROZEN_NOW
    _FROZEN_NOW = dt


def get_frozen_clock() -> Optional[datetime]:
    """Retrieve the current frozen clock value if set."""
    return _FROZEN_NOW


def resolve_timezone(tz: Optional[Union[str, ZoneInfo, timezone]] = None) -> ZoneInfo:
    """Resolve a timezone string, ZoneInfo, or timezone to a valid ZoneInfo."""
    if tz is None:
        return DEFAULT_TIMEZONE
    if isinstance(tz, ZoneInfo):
        return tz
    if isinstance(tz, timezone):
        return ZoneInfo("UTC") if tz == timezone.utc else DEFAULT_TIMEZONE
    if isinstance(tz, str):
        try:
            return ZoneInfo(tz.strip())
        except Exception:
            return DEFAULT_TIMEZONE
    return DEFAULT_TIMEZONE


def now(tz: Optional[Union[str, ZoneInfo, timezone]] = None) -> datetime:
    """
    Return the current timezone-aware datetime.
    Respects FrozenClock when active.
    """
    target_tz = resolve_timezone(tz)
    if _FROZEN_NOW is not None:
        if _FROZEN_NOW.tzinfo is None:
            aware = _FROZEN_NOW.replace(tzinfo=target_tz)
        else:
            aware = _FROZEN_NOW.astimezone(target_tz)
        return aware
    return datetime.now(target_tz)


def today(tz: Optional[Union[str, ZoneInfo, timezone]] = None) -> date:
    """
    Return current date in the specified timezone.
    Respects FrozenClock when active.
    """
    return now(tz).date()


def utcnow() -> datetime:
    """Return current UTC datetime (timezone-aware)."""
    return now(timezone.utc)


def date_metadata(target_date: date, current_date: date) -> dict:
    """
    Format standard date metadata for READ tools so the model cannot mistake past/future dates.
    """
    diff = (target_date - current_date).days
    if diff == 0:
        rel = "today"
    elif diff == 1:
        rel = "tomorrow"
    elif diff == -1:
        rel = "yesterday"
    elif diff < -1:
        rel = f"{-diff} days ago"
    else:
        rel = f"in {diff} days"
    return {
        "date": target_date.isoformat(),
        "today": current_date.isoformat(),
        "is_today": (target_date == current_date),
        "relative": rel,
        "day_label": "today" if target_date == current_date else target_date.strftime("%A, %d %B %Y")
    }


def resolve_date(
    date_str: Optional[str],
    tz: Optional[Union[str, ZoneInfo, timezone]] = None,
    allow_past: bool = False
) -> Tuple[bool, Optional[date], Optional[str]]:
    """
    Strict date resolution:
    - If date_str is missing or empty -> defaults to today in user's timezone.
    - If date_str is provided: parses ISO YYYY-MM-DD (or DD-MM-YYYY).
    - If allow_past is False and date is before today -> rejects with structured error.
    - If allow_past is True (for READ tools) -> accepts any valid historical calendar date.
    
    Returns: (is_valid, resolved_date, error_message)
    """
    user_tz = resolve_timezone(tz)
    current_date = today(user_tz)

    if not date_str or not str(date_str).strip():
        return True, current_date, None

    clean_str = str(date_str).strip()

    # Match YYYY-MM-DD
    match = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", clean_str)
    if not match:
        # Fallback check for DD-MM-YYYY or common formats
        match_alt = re.match(r"^(\d{1,2})[/-](\d{1,2})[/-](\d{4})", clean_str)
        if match_alt:
            d_p, m_p, y_p = int(match_alt.group(1)), int(match_alt.group(2)), int(match_alt.group(3))
            try:
                parsed = date(y_p, m_p, d_p)
            except ValueError:
                return False, None, f"Invalid date '{clean_str}'"
        else:
            return False, None, f"Invalid date format '{clean_str}'. Expected YYYY-MM-DD."
    else:
        y_p, m_p, d_p = int(match.group(1)), int(match.group(2)), int(match.group(3))
        try:
            parsed = date(y_p, m_p, d_p)
        except ValueError:
            return False, None, f"Invalid calendar date '{clean_str}'."

    # Check if date is in the past for WRITE operations
    if not allow_past and parsed < current_date:
        return (
            False,
            None,
            f"Date '{clean_str}' is in the past. Today is {current_date.isoformat()} ({user_tz.key}). "
            f"Please provide an upcoming date or confirm if you mean today {current_date.isoformat()}."
        )

    return True, parsed, None


def resolve_time(
    time_str: Optional[str],
    default_time: Optional[time] = None
) -> Tuple[bool, Optional[time], Optional[str]]:
    """
    Resolve time string in HH:MM or HH:MM:SS (24-hour or 12-hour with AM/PM).
    Returns: (is_valid, resolved_time, error_message)
    """
    if not time_str or not str(time_str).strip():
        if default_time is not None:
            return True, default_time, None
        return False, None, "Time parameter is missing or empty."

    clean_str = str(time_str).strip().upper()

    # 12-hour format: e.g. 5:30 PM, 05:30PM, 5 PM
    m12 = re.match(r"^(\d{1,2})(?::(\d{2}))?(?::(\d{2}))?\s*(AM|PM)$", clean_str)
    if m12:
        hr = int(m12.group(1))
        mn = int(m12.group(2) or 0)
        sec = int(m12.group(3) or 0)
        meridiem = m12.group(4)
        if hr == 12:
            hr = 0 if meridiem == "AM" else 12
        elif meridiem == "PM":
            hr += 12
        if 0 <= hr <= 23 and 0 <= mn <= 59 and 0 <= sec <= 59:
            return True, time(hr, mn, sec), None
        return False, None, f"Invalid 12-hour time '{clean_str}'"

    # 24-hour format: e.g. 17:00, 17:00:00, 09:15
    m24 = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", clean_str)
    if m24:
        hr = int(m24.group(1))
        mn = int(m24.group(2))
        sec = int(m24.group(3) or 0)
        if 0 <= hr <= 23 and 0 <= mn <= 59 and 0 <= sec <= 59:
            return True, time(hr, mn, sec), None
        return False, None, f"Invalid 24-hour time '{clean_str}'"

    return False, None, f"Unrecognized time format '{clean_str}'. Expected HH:MM or HH:MM AM/PM."


def resolve_datetime(
    date_str: Optional[str],
    time_str: Optional[str],
    tz: Optional[Union[str, ZoneInfo, timezone]] = None,
    allow_past: bool = False,
    default_time: Optional[time] = None
) -> Tuple[bool, Optional[datetime], Optional[str]]:
    """
    Resolve both date and time into a timezone-aware datetime.
    Returns: (is_valid, resolved_datetime, error_message)
    """
    user_tz = resolve_timezone(tz)
    ok_d, resolved_d, err_d = resolve_date(date_str, tz=user_tz, allow_past=allow_past)
    if not ok_d:
        return False, None, err_d

    ok_t, resolved_t, err_t = resolve_time(time_str, default_time=default_time)
    if not ok_t:
        return False, None, err_t

    assert resolved_d is not None and resolved_t is not None
    combined = datetime.combine(resolved_d, resolved_t, tzinfo=user_tz)

    # Check if combined datetime is in past
    if not allow_past:
        cur_now = now(user_tz)
        if combined < cur_now - timedelta(minutes=2):  # 2-minute grace for turn processing
            return (
                False,
                None,
                f"Datetime '{combined.isoformat()}' is in the past. Current time is {cur_now.strftime('%Y-%m-%d %H:%M')} ({user_tz.key}). "
                f"Please provide an upcoming time."
            )

    return True, combined, None

