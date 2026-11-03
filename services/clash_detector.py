"""
Saptic AI — Universal Proactive Collision & Clash Shield
Detects scheduling conflicts across Google Calendar events, reminders (memory_items),
and 4D continuous timeline transit spans.
Pure arithmetic and timestamp comparison: 0 LLM tokens, 0 MB extra RAM.
"""
import logging
from datetime import datetime, date, time as dtime, timedelta
from typing import Optional, Dict, Any, List

import db
from timing import IST

logger = logging.getLogger("saptic_clash_shield")


def parse_time_to_minutes(t_val: Any) -> Optional[int]:
    """Converts a time, datetime, or time string to minutes from midnight."""
    if not t_val:
        return None
    if isinstance(t_val, dtime):
        return t_val.hour * 60 + t_val.minute
    if isinstance(t_val, datetime):
        return t_val.hour * 60 + t_val.minute
    s = str(t_val).strip().lower()
    try:
        if ":" in s:
            parts = s.split(":")
            h = int(parts[0])
            m = int(parts[1][:2])
            if "pm" in s and h < 12:
                h += 12
            elif "am" in s and h == 12:
                h = 0
            return h * 60 + m
    except Exception:
        pass
    return None


async def detect_schedule_clash(
    chat_id: int,
    target_date: date,
    start_time: Any,
    duration_minutes: int = 30,
    exclude_title: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Checks if a proposed slot [start, start + duration] collides with:
    1. Google Calendar events
    2. Saved reminders & appointments in memory_items
    3. Active 4D timeline transit spans
    Returns clash details dict if collision found, else None.
    """
    start_mins = parse_time_to_minutes(start_time)
    if start_mins is None:
        return None

    end_mins = start_mins + max(15, duration_minutes)
    cid_can = db.canonical_chat_id(chat_id)

    # 1. Check existing reminders / appointments for this date
    try:
        existing_items = db.items_for_date(cid_can, target_date) or []
        for it in existing_items:
            it_title = it.get("title") if isinstance(it, dict) else (it[1] if len(it) > 1 else "")
            if exclude_title and exclude_title.lower() in it_title.lower():
                continue
            it_time = it.get("due_time") if isinstance(it, dict) else (it[3] if len(it) > 3 else None)
            it_mins = parse_time_to_minutes(it_time)
            if it_mins is not None:
                # Default reminder window is 30 mins
                it_end_mins = it_mins + 30
                if (start_mins < it_end_mins) and (end_mins > it_mins):
                    h_s = it_mins // 60
                    m_s = it_mins % 60
                    return {
                        "has_clash": True,
                        "kind": "reminder",
                        "title": it_title,
                        "time_str": f"{h_s:02d}:{m_s:02d}",
                        "message": f"Existing task '{it_title}' at {h_s:02d}:{m_s:02d}",
                    }
    except Exception as e:
        logger.warning(f"Error checking reminder clashes: {e}")

    # 2. Check Google Calendar events
    try:
        import google_services
        user_tz_str = await google_services.get_user_calendar_timezone(cid_can)
        try:
            from zoneinfo import ZoneInfo
            u_tz = ZoneInfo(user_tz_str)
        except Exception:
            u_tz = IST
        t_start = datetime(target_date.year, target_date.month, target_date.day, 0, 0, 0, tzinfo=u_tz)
        t_end = t_start + timedelta(days=1)
        ev_items = await google_services.list_calendar_events(
            cid_can,
            time_min_iso=t_start.isoformat(),
            time_max_iso=t_end.isoformat(),
            max_results=20
        )
        for ev in ev_items or []:
            st_iso = ev.get("start", {}).get("dateTime")
            en_iso = ev.get("end", {}).get("dateTime")
            if st_iso:
                st_dt = datetime.fromisoformat(st_iso.replace("Z", "+00:00")).astimezone(u_tz)
                en_dt = datetime.fromisoformat(en_iso.replace("Z", "+00:00")).astimezone(u_tz) if en_iso else (st_dt + timedelta(minutes=30))
                ev_start_mins = st_dt.hour * 60 + st_dt.minute
                ev_end_mins = en_dt.hour * 60 + en_dt.minute
                ev_title = ev.get("summary") or "Calendar Event"
                if exclude_title and exclude_title.lower() in ev_title.lower():
                    continue
                if (start_mins < ev_end_mins) and (end_mins > ev_start_mins):
                    return {
                        "has_clash": True,
                        "kind": "calendar_event",
                        "title": ev_title,
                        "time_str": st_dt.strftime("%I:%M %p"),
                        "message": f"Calendar event '{ev_title}' ({st_dt.strftime('%I:%M %p')} - {en_dt.strftime('%I:%M %p')})",
                    }
    except Exception as cal_err:
        logger.debug(f"Calendar clash check skipped: {cal_err}")

    # 3. Check 4D continuous timeline transit spans
    try:
        from state.timeline import get_user_timeline_spans
        c_dt = datetime(target_date.year, target_date.month, target_date.day, 12, 0, tzinfo=IST)
        spans = get_user_timeline_spans(cid_can, center_dt=c_dt, window_days_back=1, window_days_forward=1)
        for sp in spans:
            st_span = sp.get("start_dt")
            en_span = sp.get("end_dt")
            if st_span and en_span:
                if st_span.date() <= target_date <= en_span.date():
                    sp_title = sp.get("title") or "Transit / Trip"
                    return {
                        "has_clash": True,
                        "kind": "transit_span",
                        "title": sp_title,
                        "time_str": "All-day / Transit span",
                        "message": f"Transit span '{sp_title}' active on this date",
                    }
    except Exception as span_err:
        logger.debug(f"Span clash check skipped: {span_err}")

    return None
