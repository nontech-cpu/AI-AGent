"""
PeculiarAI Test Harness: FrozenClock
Allows deterministically freezing and advancing system time for time-sensitive tests.
Patches core.timeutil, the central clock authority for the entire application.
"""
from datetime import datetime as real_datetime, date as real_date, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import Optional, Union, List, Any
import core.timeutil as timeutil

IST = ZoneInfo("Asia/Kolkata")


def get_now(tz: Optional[ZoneInfo] = None) -> real_datetime:
    """Returns time from centralized clock authority core.timeutil."""
    return timeutil.now(tz or IST)


def get_today(tz: Optional[ZoneInfo] = None) -> real_date:
    """Returns today's date from centralized clock authority core.timeutil."""
    return timeutil.today(tz or IST)


class FrozenClock:
    """
    Context manager that freezes the current time at a specific timestamp.
    Defaults to 2026-11-05 13:30:00 IST.
    Only patches core.timeutil, the single clock function authority for the repo.
    """
    def __init__(self, target_time: Optional[Union[real_datetime, str]] = None, tz: ZoneInfo = IST):
        if target_time is None:
            self.frozen_dt = real_datetime(2026, 11, 5, 13, 30, 0, tzinfo=tz)
        elif isinstance(target_time, str):
            dt = real_datetime.fromisoformat(target_time)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            self.frozen_dt = dt
        elif isinstance(target_time, real_datetime):
            if target_time.tzinfo is None:
                self.frozen_dt = target_time.replace(tzinfo=tz)
            else:
                self.frozen_dt = target_time
        else:
            raise ValueError(f"Unsupported target_time type: {type(target_time)}")
        
        self.tz = tz

    def now(self, tz=None):
        return timeutil.now(tz or self.tz)

    def today(self):
        return timeutil.today(self.tz)

    def advance(self, **kwargs):
        """Advances frozen time by timedelta kwargs (e.g. days=1, hours=2, minutes=30)."""
        self.frozen_dt += timedelta(**kwargs)
        timeutil.set_frozen_clock(self.frozen_dt)

    def __enter__(self):
        timeutil.set_frozen_clock(self.frozen_dt)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        timeutil.set_frozen_clock(None)
