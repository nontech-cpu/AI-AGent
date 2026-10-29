from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MORNING = dtime(9, 0)   # din wale reminders ka time
URGENT_MINUTES = 10     # itne ya kam minute me ho to turant alert


def _at(d, t):
    return datetime.combine(d, t, tzinfo=IST)


def parse_followup_time(message, now=None):
    from llm import parse_followup_time as _pft
    return _pft(message, now)


def build_plan(due_date, due_time, now=None):
    """Returns {"status": none|past|urgent|scheduled, "minutes": int|None,
    "reminders": [(remind_at, label), ...]}"""
    now = now or datetime.now(IST)
    if due_date is None:
        return {"status": "none", "minutes": None, "reminders": []}

    reminders = []

    if due_time is not None:
        due_dt = _at(due_date, due_time)
        mins = (due_dt - now).total_seconds() / 60
        if mins <= 0:
            return {"status": "past", "minutes": None, "reminders": []}

        # Subah 9 wale reminders: sirf tab jab event me kam se kam 1 ghanta ho
        for d, label in ((due_date - timedelta(days=1), "day_before"),
                         (due_date, "due_day")):
            at = _at(d, MORNING)
            if at > now and at <= due_dt - timedelta(hours=1):
                reminders.append((at, label))
        # Time se pehle wale reminders (jo abhi bhi future me hain)
        for mins_before, label in ((15, "15min"), (5, "5min")):
            at = due_dt - timedelta(minutes=mins_before)
            if at > now:
                reminders.append((at, label))
        # Exact due time reminder
        if due_dt > now:
            reminders.append((due_dt, "at_time"))
    else:
        if due_date < now.date():
            return {"status": "past", "minutes": None, "reminders": []}
        for d, label in ((due_date - timedelta(days=1), "day_before"),
                         (due_date, "due_day")):
            at = _at(d, MORNING)
            if at > now:
                reminders.append((at, label))

    status = "urgent" if (due_time is not None and mins <= URGENT_MINUTES) else ("scheduled" if reminders else "none")
    return {"status": status, "minutes": max(1, round(mins)) if (due_time is not None and mins <= URGENT_MINUTES) else None, "reminders": reminders}


def event_over(due_date, due_time, now):
    if due_date is None:
        return False
    if due_time is not None:
        # Event is only considered over if ended more than 45 minutes ago (prevents premature drop on exact match)
        return (_at(due_date, due_time) + timedelta(minutes=45)) <= now
    return due_date < now.date()


def fmt_when(at, now=None):
    now = now or datetime.now(IST)
    at = at.astimezone(IST)
    if at.date() == now.date():
        day = "today"
    elif at.date() == (now + timedelta(days=1)).date():
        day = "tomorrow"
    else:
        day = at.strftime("%d %b")
    return f"{day} {at.strftime('%H:%M')}"


def format_event_clock_ist(dt_str: str | None) -> str:
    """Format Google Calendar / ISO dateTime string into an IST clock string (e.g. '6:00 PM')."""
    if not dt_str:
        return "All day"
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt_ist = dt.replace(tzinfo=IST)
        else:
            dt_ist = dt.astimezone(IST)
        return dt_ist.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return dt_str[11:16] if len(dt_str) >= 16 else (dt_str or "All day")