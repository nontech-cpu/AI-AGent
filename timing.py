import re
from datetime import datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo
import core.timeutil as timeutil

IST = ZoneInfo("Asia/Kolkata")
MORNING = dtime(9, 0)   # din wale reminders ka time
URGENT_MINUTES = 10     # itne ya kam minute me ho to turant alert

_WORD_NUMBERS = {
    # Hindi Devanagari
    "एक": "1", "दो": "2", "तीन": "3", "चार": "4", "पांच": "5", "पाँच": "5",
    "छह": "6", "सात": "7", "आठ": "8", "नौ": "9", "दस": "10", "ग्यारह": "11", "बारह": "12",
    # Hindi/Hinglish Transliterated
    "ek": "1", "do": "2", "teen": "3", "chaar": "4", "char": "4", "paanch": "5", "panch": "5",
    "chhe": "6", "che": "6", "saat": "7", "sat": "7", "aath": "8", "nau": "9", "das": "10", "gyarah": "11", "barah": "12",
    # English words
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6",
    "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
}

WEEKDAY_PATTERNS = {
    0: r"\b(monday|somwar|somvaar|mnday|mon)\b|सोमवार",
    1: r"\b(tuesday|tue|tues|tueday|tuedday|mangalwar|mangalvaar|mangal)\b|मंगलवार",
    2: r"\b(wednesday|wed|wednessday|budhwar|budhvaar|budh)\b|बुधवार",
    3: r"\b(thursday|thu|thurs|thurday|guruwar|guruvaar|veervaar|brihaspatiwar)\b|गुरुवार|वीरवार",
    4: r"\b(friday|fri|shukrawar|shukravaar|shukra)\b|शुक्रवार",
    5: r"\b(saturday|sat|shaniwar|shanivaar|shani)\b|शनिवार",
    6: r"\b(sunday|sun|ravivar|itwar|itvaar)\b|रविवार|इतवार",
}


def _at(d, t):
    if isinstance(d, str):
        from datetime import date
        d = date.fromisoformat(d)
    if isinstance(t, str):
        t = dtime.fromisoformat(t)
    return datetime.combine(d, t, tzinfo=IST)


def parse_followup_time(message: str, now: datetime | None = None):
    """Parse a time follow-up using current conversation time, supporting digits and number words."""
    if now is None:
        now = timeutil.now(IST)
    low = message.lower().strip()
    # Replace spoken/written number words (Hindi and English) strictly before time units
    for word, digit in _WORD_NUMBERS.items():
        low = re.sub(rf"(^|\s){re.escape(word)}\s+(baje|bje|bajey|bajay|बजे|o\'?clock|am|pm|pr|par|pe|ko)(\s|$)", rf"\g<1>{digit} \g<2>\g<3>", low, flags=re.I)

    # Strip explicit day-of-month phrases so e.g. "aaj 16 ko 2 baje" doesn't parse 16 as a time.
    time_text = re.sub(r"\b(?:aaj|today)\s+\d{1,2}(?:st|nd|rd|th)?\s+ko\b", " ", low)

    # Check for shift / transition expressions: "from X to Y", "X ki jagah Y", "X ke badle Y"
    # In rescheduling, the destination (second) time is the desired target!
    trans_match = re.search(
        r"(?:from\s+)?\b(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|baje)?)\s*(?:to|ki\s*jagah|ke\s*badle|badal\s*ke|instead\s*of)\s*(\d{1,2})(?::([0-5]\d))?\s*(am|pm)?\s*(?:baje|bje|bajey|bajay|बजे|pr|par|pe|ko)?\b",
        time_text,
        re.I
    )
    if trans_match:
        match = trans_match
    else:
        match = re.search(
            r"(?<!\d)(2[0-3]|1[0-9]|0?[1-9])(?::([0-5]\d))?\s*(am|pm)?\s*(?:baje|bje|bajey|bajay|बजे|pr|par|pe|ko)?\b",
            time_text,
        )

    if not match:
        # Check if a date or broad period was provided without an exact hour (e.g. "kal shaam", "today evening")
        explicit_tomorrow = bool(re.search(r"\b(tomorrow|tmrw|kal|कल)\b", low))
        explicit_parso = bool(re.search(r"\b(parso|day after tomorrow|परसों)\b", low))
        explicit_today = bool(re.search(r"\b(today|aaj|आज)\b", low))
        has_period = bool(re.search(r"\b(shaam|sham|evening|dopahar|afternoon|subah|subha|morning|raat|night)\b|शाम|दोपहर|सुबह|रात", low))
        if (explicit_tomorrow or explicit_parso or explicit_today) and has_period:
            t_date = now.date() + timedelta(days=1) if explicit_tomorrow else (now.date() + timedelta(days=2) if explicit_parso else now.date())
            return None, t_date

        explicit_weekday = None
        for wd_num, pat in WEEKDAY_PATTERNS.items():
            if re.search(pat, low, re.I):
                explicit_weekday = wd_num
                break
        if explicit_weekday is not None:
            days_ahead = (explicit_weekday - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            t_date = now.date() + timedelta(days=days_ahead)
            return None, t_date

        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    meridian = match.group(3)
    is_pm_indicator = bool(re.search(r"\b(pm|shaam|sham|dopahar|evening|afternoon|raat|night)\b|शाम|दोपहर|रात", low))
    is_am_indicator = bool(re.search(r"\b(am|subah|subha|morning)\b|सुबह", low))

    if (meridian == "pm" or is_pm_indicator) and hour < 12:
        hour += 12
    elif (meridian == "am" or is_am_indicator) and hour == 12:
        hour = 0
    elif meridian is None and not is_am_indicator and 1 <= hour <= 6:
        # Common convention: 1 to 6 for meetings without morning indicator implies PM (afternoon/evening)
        hour += 12

    explicit_tomorrow = bool(re.search(r"\b(tomorrow|tmrw|kal|कल)\b", low))
    explicit_parso = bool(re.search(r"\b(parso|day after tomorrow|परसों)\b", low))
    explicit_today = bool(re.search(r"\b(today|aaj|आज)\b", low))
    explicit_weekday = None
    for wd_num, pat in WEEKDAY_PATTERNS.items():
        if re.search(pat, low, re.I):
            explicit_weekday = wd_num
            break

    if meridian is None and not is_pm_indicator and not is_am_indicator and 1 <= hour <= 12 and not (explicit_tomorrow or explicit_parso or explicit_weekday is not None):
        # Choose the nearest upcoming occurrence today; tolerate a just-passed minute as "now".
        candidates = []
        for h in {hour % 12, (hour % 12) + 12}:
            candidate = dtime(h, minute)
            dt = datetime.combine(now.date(), candidate, tzinfo=IST)
            if dt >= now - timedelta(minutes=2):
                candidates.append((dt, candidate))
        if candidates:
            future = min(candidates, key=lambda x: x[0])
            return future[1], future[0].date()
        chosen_hour = hour + 12 if now.hour >= 12 else hour
        return dtime(chosen_hour, minute), now.date() + timedelta(days=1)

    due_time = dtime(hour, minute)
    target_date = now.date()
    if explicit_weekday is not None:
        days_ahead = (explicit_weekday - now.weekday()) % 7
        if days_ahead == 0:
            if datetime.combine(now.date(), due_time, tzinfo=IST) < now - timedelta(minutes=2):
                days_ahead = 7
        target_date = now.date() + timedelta(days=days_ahead)
    elif explicit_tomorrow:
        target_date += timedelta(days=1)
    elif explicit_parso:
        target_date += timedelta(days=2)
    elif explicit_today:
        target_date = now.date()
    elif datetime.combine(target_date, due_time, tzinfo=IST) < now - timedelta(minutes=2):
        target_date += timedelta(days=1)
    return due_time, target_date


def build_plan(due_date, due_time, now=None):
    """Returns {"status": none|past|urgent|scheduled, "minutes": int|None,
    "reminders": [(remind_at, label), ...]}"""
    now = now or timeutil.now(IST)
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
    now = now or timeutil.now(IST)
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
