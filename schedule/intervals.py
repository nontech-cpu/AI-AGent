"""
PeculiarAI Interval Schedule Engine (Phase 4)
Precise temporal arithmetic, duration heuristics, travel buffers,
and timezone-safe IST overlap detection.
"""
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, date, time as dtime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from zoneinfo import ZoneInfo

from timing import IST
import db
import google_services

logger = logging.getLogger("peculiar_schedule")

# Default activity duration heuristics (minutes)
ACTIVITY_DURATIONS = {
    "movie": 180,
    "film": 180,
    "cinema": 180,
    "theatre": 180,
    "dinner": 90,
    "lunch": 75,
    "brunch": 75,
    "party": 120,
    "gym": 60,
    "workout": 60,
    "yoga": 60,
    "interview": 60,
    "presentation": 60,
    "exam": 180,
    "doctor": 45,
    "meeting": 30,
    "call": 30,
    "sync": 30,
    "standup": 15,
}

@dataclass
class TimeInterval:
    start: datetime
    end: datetime
    title: str
    kind: str = "meeting"
    buffer_minutes: int = 0
    meet_url: Optional[str] = None
    location: Optional[str] = None

    def __post_init__(self):
        # Guarantee timezone-aware IST
        if self.start.tzinfo is None:
            self.start = self.start.replace(tzinfo=IST)
        else:
            self.start = self.start.astimezone(IST)

        if self.end.tzinfo is None:
            self.end = self.end.replace(tzinfo=IST)
        else:
            self.end = self.end.astimezone(IST)

        if self.end < self.start:
            self.end = self.start + timedelta(minutes=30)

    @property
    def buffered_start(self) -> datetime:
        return self.start - timedelta(minutes=self.buffer_minutes)

    @property
    def buffered_end(self) -> datetime:
        return self.end + timedelta(minutes=self.buffer_minutes)

    def overlaps(self, other: "TimeInterval", use_buffer: bool = True) -> bool:
        """
        Two intervals [A_s, A_e] and [B_s, B_e] overlap if:
        max(A_s, B_s) < min(A_e, B_e)
        """
        s1 = self.buffered_start if use_buffer else self.start
        e1 = self.buffered_end if use_buffer else self.end
        s2 = other.buffered_start if use_buffer else other.start
        e2 = other.buffered_end if use_buffer else other.end
        return max(s1, s2) < min(e1, e2)

    def format_ist(self) -> str:
        s_str = self.start.strftime("%I:%M %p")
        e_str = self.end.strftime("%I:%M %p")
        return f"{s_str} – {e_str} IST"


def normalize_to_ist(dt_input: Any) -> datetime:
    """
    Normalizes any datetime string, UTC string, or datetime object into timezone-aware Asia/Kolkata (IST).
    Examples:
      '2026-10-22T12:30:00Z' -> 2026-10-22 18:00:00+05:30 (6:00 PM IST)
      '2026-10-22T18:00:00+05:30' -> 2026-10-22 18:00:00+05:30
    """
    if isinstance(dt_input, datetime):
        if dt_input.tzinfo is None:
            return dt_input.replace(tzinfo=IST)
        return dt_input.astimezone(IST)

    clean_str = str(dt_input).strip()

    # If ISO string ending with 'Z'
    if clean_str.endswith("Z"):
        clean_str = clean_str[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=IST)
        return dt.astimezone(IST)
    except Exception:
        # Fallback to current time IST
        return datetime.now(IST)


def parse_activity_duration(activity_type: Optional[str] = None, text_content: Optional[str] = None) -> int:
    """
    Determines duration in minutes from activity keyword or explicit text.
    '2 ghante movie' -> 120 min
    '3 hours' -> 180 min
    'movie' -> 180 min
    """
    combined = f"{activity_type or ''} {text_content or ''}".lower()

    # 1. Explicit hour mention
    hr_match = re.search(r'(\d+(?:\.\d+)?)\s*(?:hours?|hrs?|ghante?)', combined)
    if hr_match:
        try:
            return int(float(hr_match.group(1)) * 60)
        except Exception:
            pass

    # 2. Explicit minute mention
    min_match = re.search(r'(\d+)\s*(?:mins?|minutes?|minut)', combined)
    if min_match:
        try:
            return int(min_match.group(1))
        except Exception:
            pass

    # 3. Known activity type heuristics
    for act, dur in ACTIVITY_DURATIONS.items():
        if act in combined:
            return dur

    return 30


def parse_clock_time_ist(time_str: str, target_d: date) -> datetime:
    """Parses '5 PM', '17:00', '5:30 pm' into IST datetime for target_d."""
    clean = time_str.strip().lower()
    is_pm = "pm" in clean
    is_am = "am" in clean
    numbers = re.findall(r'\d+', clean)

    hour = int(numbers[0]) if numbers else 17
    minute = int(numbers[1]) if len(numbers) > 1 else 0

    if is_pm and hour < 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0

    return datetime(target_d.year, target_d.month, target_d.day, hour, minute, 0, tzinfo=IST)


async def check_interval_availability(
    chat_id: int,
    start_time_str: str,
    target_date_str: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    activity_type: Optional[str] = None,
    travel_buffer_minutes: int = 15
) -> Dict[str, Any]:
    """
    Checks if user is free for an activity at a given time and date,
    detecting interval clashes (e.g. 5 PM movie clashing with 6 PM meeting).
    """
    now_ist = datetime.now(IST)

    # 1. Resolve Target Date
    if target_date_str:
        try:
            target_d = date.fromisoformat(target_date_str.strip())
        except Exception:
            target_d = now_ist.date()
    else:
        target_d = now_ist.date()

    # 2. Resolve Duration
    duration = duration_minutes or parse_activity_duration(activity_type, start_time_str)

    # 3. Build Proposed Interval
    proposed_start = parse_clock_time_ist(start_time_str, target_d)
    proposed_end = proposed_start + timedelta(minutes=duration)
    proposed_interval = TimeInterval(
        start=proposed_start,
        end=proposed_end,
        title=activity_type.title() if activity_type else "Planned Activity",
        kind=activity_type or "activity",
        buffer_minutes=travel_buffer_minutes
    )

    # 4. Fetch Schedule for the Date
    from main import get_unified_schedule
    sched = await get_unified_schedule(chat_id, target_d)

    conflicts = []
    busy_intervals = []

    # Check Google Calendar events
    for evt in sched.get("calendar_events", []):
        start_raw = evt.get("start", {}).get("dateTime") or evt.get("start", {}).get("date")
        end_raw = evt.get("end", {}).get("dateTime") or evt.get("end", {}).get("date")
        if not start_raw:
            continue

        evt_start = normalize_to_ist(start_raw)
        evt_end = normalize_to_ist(end_raw) if end_raw else evt_start + timedelta(minutes=30)
        evt_title = evt.get("summary") or "Meeting"
        evt_interval = TimeInterval(
            start=evt_start,
            end=evt_end,
            title=evt_title,
            kind="calendar_meeting",
            buffer_minutes=10,
            meet_url=evt.get("meet_url") or evt.get("hangoutLink")
        )
        busy_intervals.append(evt_interval)

        # Overlap test with buffer
        if proposed_interval.overlaps(evt_interval, use_buffer=True):
            meet_tag = f" (Meet link: {evt_interval.meet_url})" if evt_interval.meet_url else ""
            conflicts.append(
                f"'{evt_title}' ({evt_interval.format_ist()}){meet_tag}"
            )

    # Check Reminders with explicit due time
    for rem in sched.get("reminders", []):
        if rem.get("due_time"):
            try:
                t_val = dtime.fromisoformat(str(rem["due_time"]))
                r_start = datetime.combine(target_d, t_val, tzinfo=IST)
                r_end = r_start + timedelta(minutes=30)
                r_interval = TimeInterval(start=r_start, end=r_end, title=rem["title"], kind="reminder")
                busy_intervals.append(r_interval)

                if proposed_interval.overlaps(r_interval, use_buffer=False):
                    conflicts.append(f"Reminder: '{rem['title']}' at {r_start.strftime('%I:%M %p')}")
            except Exception:
                pass

    # Check Day Commitments
    commitment_notes = [f"{c['name']}: {c['commitment']}" for c in sched.get("commitments", [])]

    is_free = len(conflicts) == 0
    free_slots = find_free_slots(busy_intervals, target_d) if not is_free else []

    return {
        "is_free": is_free,
        "target_date": str(target_d),
        "proposed_start": proposed_start.strftime("%I:%M %p"),
        "proposed_end": proposed_end.strftime("%I:%M %p"),
        "duration_minutes": duration,
        "activity_type": activity_type or "activity",
        "conflicts": conflicts,
        "commitments_on_day": commitment_notes,
        "suggested_free_slots": free_slots
    }


def find_free_slots(
    busy_intervals: List[TimeInterval],
    target_date: date,
    day_start_hour: int = 10,
    day_end_hour: int = 21,
    min_slot_minutes: int = 45
) -> List[str]:
    """
    Finds open time windows during waking hours (10:00 AM – 09:00 PM IST).
    """
    day_start = datetime(target_date.year, target_date.month, target_date.day, day_start_hour, 0, tzinfo=IST)
    day_end = datetime(target_date.year, target_date.month, target_date.day, day_end_hour, 0, tzinfo=IST)

    # Sort intervals by start time
    sorted_busy = sorted(busy_intervals, key=lambda x: x.start)

    free_slots = []
    current_cursor = day_start

    for b in sorted_busy:
        if b.start > current_cursor:
            gap_minutes = int((b.start - current_cursor).total_seconds() / 60)
            if gap_minutes >= min_slot_minutes:
                free_slots.append(f"{current_cursor.strftime('%I:%M %p')} – {b.start.strftime('%I:%M %p')}")
        current_cursor = max(current_cursor, b.end)

    if current_cursor < day_end:
        gap_minutes = int((day_end - current_cursor).total_seconds() / 60)
        if gap_minutes >= min_slot_minutes:
            free_slots.append(f"{current_cursor.strftime('%I:%M %p')} – {day_end.strftime('%I:%M %p')}")

    return free_slots[:3]


async def get_unified_daily_schedule(chat_id: int, target_date: Optional[date] = None) -> Dict[str, Any]:
    """
    Unified aggregator returning a clean IST timeline of Calendar events,
    Reminders, and Contact commitments.
    """
    from main import get_unified_schedule
    target_d = target_date or datetime.now(IST).date()
    sched = await get_unified_schedule(chat_id, target_d)

    timeline = []

    # Calendar events
    for evt in sched.get("calendar_events", []):
        start_raw = evt.get("start", {}).get("dateTime") or evt.get("start", {}).get("date")
        end_raw = evt.get("end", {}).get("dateTime") or evt.get("end", {}).get("date")
        if not start_raw:
            continue
        dt_start = normalize_to_ist(start_raw)
        dt_end = normalize_to_ist(end_raw) if end_raw else dt_start + timedelta(minutes=30)
        meet_url = evt.get("meet_url") or evt.get("hangoutLink")
        timeline.append({
            "type": "meeting",
            "title": evt.get("summary") or "Meeting",
            "time_ist": f"{dt_start.strftime('%I:%M %p')} – {dt_end.strftime('%I:%M %p')}",
            "meet_url": meet_url,
            "raw_start": dt_start.isoformat()
        })

    # Reminders
    for rem in sched.get("reminders", []):
        t_str = f" at {rem['due_time']}" if rem.get("due_time") else ""
        timeline.append({
            "type": "reminder",
            "title": rem["title"],
            "time_ist": t_str.strip() or "All Day",
            "raw_start": None
        })

    # Commitments
    for comm in sched.get("commitments", []):
        timeline.append({
            "type": "commitment",
            "title": f"Promise to {comm['name']}: {comm['commitment']}",
            "time_ist": "Deliverable",
            "raw_start": None
        })

    return {
        "date": str(target_d),
        "weekday": target_d.strftime("%A"),
        "total_items": len(timeline),
        "timeline": timeline,
        "is_busy": len(timeline) > 0
    }
