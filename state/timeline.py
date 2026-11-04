"""
PeculiarAI — 4D Continuous Temporal Reality & World Model Engine
Provides continuous interval-based temporal reasoning (spans with start and end times),
midnight rollover continuity, pre-action common-sense physical feasibility checks,
and anti-sycophantic grounded timeline blocks for frontier-grade agentic intelligence.
"""
import re
import logging
from datetime import datetime, date, timedelta, time as dtime
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from timing import IST
import db

logger = logging.getLogger("peculiar_timeline")


@dataclass
class TimelineSpan:
    title: str
    start_dt: datetime
    end_dt: datetime
    kind: str  # "bus", "flight", "train", "movie", "meeting", "reminder", "commitment"
    is_blocking: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_hours(self) -> float:
        return (self.end_dt - self.start_dt).total_seconds() / 3600.0


def parse_datetime_flexible(dt_val: Any) -> Optional[datetime]:
    """Robustly parses datetime strings, date objects, or timestamps into timezone-aware IST datetime."""
    if not dt_val:
        return None
    if isinstance(dt_val, datetime):
        return dt_val.astimezone(IST) if dt_val.tzinfo else dt_val.replace(tzinfo=IST)
    if isinstance(dt_val, date):
        return datetime.combine(dt_val, dtime(9, 0), tzinfo=IST)

    clean = str(dt_val).strip()
    # Normalize common ISO formats
    clean = clean.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
        return dt.astimezone(IST) if dt.tzinfo else dt.replace(tzinfo=IST)
    except Exception:
        pass

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%b-%Y %H:%M",
        "%d-%b-%Y",
        "%d %b %Y, %I:%M %p",
        "%d %b %Y %H:%M",
        "%d %b %Y",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
    ]
    for fmt in formats:
        try:
            d = datetime.strptime(clean, fmt)
            return d.replace(tzinfo=IST)
        except Exception:
            try:
                d = datetime.strptime(clean[:19], fmt)
                return d.replace(tzinfo=IST)
            except Exception:
                pass
    return None


def estimate_event_duration(title: str, kind: str = "general", route_or_summary: str = "") -> float:
    """
    Estimates physical duration in hours based on domain heuristics.
    Used to construct continuous spans for World Model conflict detection.
    """
    t_low = (title + " " + route_or_summary).lower()

    # Explicit duration stated in title or summary
    m_hour = re.search(r'\b(\d+(?:\.\d+)?)\s*(?:hour|hr|ghante|ghanta)\b', t_low)
    if m_hour:
        return float(m_hour.group(1))
    if re.search(r'\b(?:one|1)\s*(?:hour|hr|ghanta)\b', t_low):
        return 1.0
    if re.search(r'\b(?:two|2)\s*(?:hours|hrs|ghante)\b', t_low):
        return 2.0
    if re.search(r'\b(?:half\s*an?\s*hour|30\s*(?:min|mins|minute|minutes))\b', t_low):
        return 0.5
    m_min = re.search(r'\b(\d+)\s*(?:min|mins|minute|minutes)\b', t_low)
    if m_min:
        return float(m_min.group(1)) / 60.0

    # 1. Travel: Flights
    if kind == "flight" or any(k in t_low for k in ("flight", "airline", "indigo", "air india", "vistara", "spicejet", "akasa", "airport", "boarding pass")):
        # 2 hrs flight + 2 hrs airport buffer + 0.5 hrs baggage = 4.5 hours
        return 4.5

    # 2. Travel: Bus
    if kind == "bus" or any(k in t_low for k in ("bus", "travels", "sleeper", "volvo", "roadstar", "redbus", "intercity")):
        # Common routes check
        if ("udaipur" in t_low and "ahmedabad" in t_low) or ("ahmedabad" in t_low and "udaipur" in t_low):
            return 8.5
        if ("delhi" in t_low and "jaipur" in t_low) or ("jaipur" in t_low and "delhi" in t_low):
            return 6.0
        if ("mumbai" in t_low and "pune" in t_low) or ("pune" in t_low and "mumbai" in t_low):
            return 4.0
        return 8.0

    # 3. Travel: Trains
    if kind == "train" or any(k in t_low for k in ("train", "irctc", "express", "railway", "rajdhani", "shatabdi", "vande bharat")):
        return 6.5

    # 4. Cinema / Movies
    if any(k in t_low for k in ("movie", "theatre", "theater", "cinema", "film", "pvr", "inox", "cinepolis")):
        return 2.5

    # 5. Meetings / Calendar
    if kind == "meeting" or any(k in t_low for k in ("meeting", "sync", "standup", "discussion", "interview", "call")):
        return 1.0

    # Default general appointment/task
    return 1.0


def get_user_timeline_spans(
    chat_id: int,
    center_dt: Optional[datetime] = None,
    window_days_back: int = 1,
    window_days_forward: int = 4
) -> List[TimelineSpan]:
    """
    Constructs a continuous 4D timeline of all scheduled commitments, meetings,
    and active travel transit blocks around center_dt.
    """
    canon_cid = db.canonical_chat_id(chat_id)
    center_dt = center_dt or datetime.now(IST)
    start_window = center_dt - timedelta(days=window_days_back)
    end_window = center_dt + timedelta(days=window_days_forward)

    spans: List[TimelineSpan] = []
    seen_keys = set()

    # 1. Active Travel Ticket in Context
    try:
        from state.active_context import get_active_ticket
        tkt = get_active_ticket(canon_cid)
        if tkt:
            t_dt = parse_datetime_flexible(tkt.get("datetime_str"))
            if t_dt and (start_window <= t_dt <= end_window):
                c_name = tkt.get("carrier") or "Travel Provider"
                route = tkt.get("route") or ""
                pnr = tkt.get("pnr") or ""
                is_bus = "bus" in (c_name + route + tkt.get("summary", "")).lower() or "travels" in c_name.lower()
                is_flight = any(k in (c_name + route + tkt.get("summary", "")).lower() for k in ("flight", "indigo", "air india", "vistara", "airline"))
                t_kind = "flight" if is_flight else ("bus" if is_bus else "train")
                dur = estimate_event_duration(c_name, t_kind, route + " " + (tkt.get("summary") or ""))
                span_end = t_dt + timedelta(hours=dur)
                title = f"{c_name} ({route})" if route else f"{c_name} (PNR {pnr})"
                key = f"{t_dt.isoformat()}_{title}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    spans.append(TimelineSpan(
                        title=title,
                        start_dt=t_dt,
                        end_dt=span_end,
                        kind=t_kind,
                        is_blocking=True,
                        metadata={"pnr": pnr, "route": route, "carrier": c_name, "source": "active_ticket"}
                    ))
    except Exception as exc:
        logger.warning(f"Error extracting active ticket span: {exc}")

    # 2. Past Facts mentioning Bookings / Tickets
    try:
        facts = db.get_user_facts(canon_cid, limit=20) or []
        for f in facts:
            f_text = f.get("fact", "")
            if not any(k in f_text.lower() for k in ("booking/ticket:", "ticket:", "pnr:", "route:")):
                continue
            date_m = re.search(r"(?:date|travel date)[:\s]+([^|]+)", f_text, re.I)
            if not date_m:
                continue
            raw_dt_str = date_m.group(1).strip()
            parsed_dt = parse_datetime_flexible(raw_dt_str)
            if not parsed_dt or not (start_window <= parsed_dt <= end_window):
                continue
            carrier_m = re.search(r"(?:booking/ticket|provider|service)[:\s]+([^|]+)", f_text, re.I)
            route_m = re.search(r"route[:\s]+([^|]+)", f_text, re.I)
            c_title = carrier_m.group(1).strip() if carrier_m else "Travel Journey"
            route_str = route_m.group(1).strip() if route_m else ""
            t_kind = "bus" if "bus" in f_text.lower() or "travels" in c_title.lower() else ("flight" if "flight" in f_text.lower() else "train")
            dur = estimate_event_duration(c_title, t_kind, route_str + " " + f_text)
            span_end = parsed_dt + timedelta(hours=dur)
            span_title = f"{c_title} ({route_str})" if route_str else c_title
            key = f"{parsed_dt.isoformat()}_{span_title}"
            if key not in seen_keys:
                seen_keys.add(key)
                spans.append(TimelineSpan(
                    title=span_title,
                    start_dt=parsed_dt,
                    end_dt=span_end,
                    kind=t_kind,
                    is_blocking=True,
                    metadata={"fact": f_text, "source": "saved_facts"}
                ))
    except Exception as exc:
        logger.warning(f"Error parsing booking facts for timeline: {exc}")

    # 3. Database Memory Items (Reminders & Appointments)
    try:
        range_items = db.items_for_date_range(canon_cid, start_window.date(), end_window.date()) or []
        for it in range_items:
            if isinstance(it, dict):
                i_title = it.get("title", "")
                i_cat = it.get("category", "")
                i_time = it.get("due_time")
                i_date = it.get("due_date")
            else:
                i_cat = it[0] if len(it) > 0 else ""
                i_title = it[1] if len(it) > 1 else ""
                i_time = it[3] if len(it) > 3 else None
                i_date = it[4] if len(it) > 4 else None

            if not i_time or not i_date:
                continue
            try:
                if isinstance(i_date, str):
                    i_date = date.fromisoformat(i_date)
                i_h = i_time.hour if hasattr(i_time, "hour") else int(str(i_time).split(":")[0])
                i_m = i_time.minute if hasattr(i_time, "minute") else int(str(i_time).split(":")[1])
                s_dt = datetime.combine(i_date, dtime(i_h, i_m), tzinfo=IST)
                dur = estimate_event_duration(i_title, i_cat)
                e_dt = s_dt + timedelta(hours=dur)
                key = f"{s_dt.isoformat()}_{i_title}"
                if key not in seen_keys:
                    seen_keys.add(key)
                    spans.append(TimelineSpan(
                        title=i_title,
                        start_dt=s_dt,
                        end_dt=e_dt,
                        kind="movie" if "movie" in i_title.lower() else (i_cat or "reminder"),
                        is_blocking=True,
                        metadata={"category": i_cat, "source": "memory_items"}
                    ))
            except Exception:
                pass
    except Exception as exc:
        logger.warning(f"Error parsing memory items for timeline: {exc}")

    # Sort spans chronologically by start_dt
    spans.sort(key=lambda s: s.start_dt)
    return spans


def check_timeline_collision(
    chat_id: int,
    candidate_title: str,
    candidate_start_dt: datetime,
    candidate_duration_hours: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """
    Executes a pure mathematical 4D interval overlap check against the user's continuous timeline.
    Detects physical impossibilities (e.g. attempting to attend a 1:00 AM movie while on an overnight bus).
    Returns None if clear, or a rich diagnostic dict if a collision exists.
    """
    if not candidate_start_dt:
        return None

    candidate_start_dt = candidate_start_dt.astimezone(IST) if candidate_start_dt.tzinfo else candidate_start_dt.replace(tzinfo=IST)
    if candidate_duration_hours is None:
        candidate_duration_hours = estimate_event_duration(candidate_title)

    candidate_end_dt = candidate_start_dt + timedelta(hours=candidate_duration_hours)

    # Gather timeline spans around the candidate event (1 day before to 2 days after)
    spans = get_user_timeline_spans(
        chat_id,
        center_dt=candidate_start_dt,
        window_days_back=1,
        window_days_forward=2
    )

    clean_cand_title = candidate_title.lower().strip()

    for span in spans:
        # Don't conflict with itself (e.g. rescheduling identical item)
        clean_span_title = span.title.lower().strip()
        if clean_cand_title in clean_span_title or clean_span_title in clean_cand_title:
            continue

        # Mathematical Interval Overlap condition:
        # max(A_start, B_start) < min(A_end, B_end)
        overlap_start = max(span.start_dt, candidate_start_dt)
        overlap_end = min(span.end_dt, candidate_end_dt)

        if overlap_start < overlap_end:
            overlap_duration_mins = (overlap_end - overlap_start).total_seconds() / 60.0
            hours_into_journey = (candidate_start_dt - span.start_dt).total_seconds() / 3600.0

            time_pos_desc = ""
            if hours_into_journey >= 0:
                time_pos_desc = f" (departure ke {round(hours_into_journey, 1)} ghante baad)"
            else:
                mins_before = abs(round(hours_into_journey * 60))
                time_pos_desc = f" (departure se {mins_before} mins pehle)"

            explanation = (
                f"Physical Conflict Detected: {candidate_start_dt.strftime('%d %b ko %I:%M %p')} par aap "
                f"'{span.title}' mein honge (jo {span.start_dt.strftime('%d %b %I:%M %p')} se lekar "
                f"{span.end_dt.strftime('%d %b %I:%M %p')} tak chalegi). "
                f"{candidate_start_dt.strftime('%I:%M %p')}{time_pos_desc} par aap transit/highway par honge, "
                f"isliye is waqt '{candidate_title}' physically attend karna possible nahi hoga."
            )

            return {
                "has_conflict": True,
                "conflict_type": span.kind,
                "blocking_event": span.title,
                "span_start_dt": span.start_dt,
                "span_end_dt": span.end_dt,
                "candidate_start_dt": candidate_start_dt,
                "overlap_duration_mins": round(overlap_duration_mins),
                "hours_into_journey": round(hours_into_journey, 1),
                "explanation": explanation,
                "suggested_free_time": f"{span.end_dt.strftime('%d %b ko %I:%M %p')} ke baad"
            }

        # Transit Buffer / Tight Transition Check:
        # If both are physical events on the same day, and gap between one ending and the other starting is <= 30 mins
        gap_sec = 0
        if candidate_start_dt >= span.end_dt:
            gap_sec = (candidate_start_dt - span.end_dt).total_seconds()
        elif span.start_dt >= candidate_end_dt:
            gap_sec = (span.start_dt - candidate_end_dt).total_seconds()

        is_transit_sensitive = any(k in (span.title + " " + candidate_title).lower() for k in ("movie", "theatre", "theater", "cinema", "flight", "bus", "train", "travel", "meeting", "doctor", "hospital", "date"))
        if is_transit_sensitive and 0 <= gap_sec <= 1800:
            gap_mins = round(gap_sec / 60.0)
            explanation = (
                f"Schedule Conflict / Tight Buffer Detected: Aapki '{span.title}' "
                f"{span.start_dt.strftime('%I:%M %p')} se {span.end_dt.strftime('%I:%M %p')} tak chalegi, "
                f"aur '{candidate_title}' {candidate_start_dt.strftime('%I:%M %p')} par scheduled ho rahi hai. "
                f"Dono ke beech sirf {gap_mins} minutes ka transit/travel window hai, jo physically bohot tight hai."
            )
            return {
                "has_conflict": True,
                "conflict_type": "tight_buffer",
                "blocking_event": span.title,
                "span_start_dt": span.start_dt,
                "span_end_dt": span.end_dt,
                "candidate_start_dt": candidate_start_dt,
                "overlap_duration_mins": 0,
                "gap_mins": gap_mins,
                "explanation": explanation,
                "suggested_free_time": f"{(span.end_dt + timedelta(minutes=45)).strftime('%d %b ko %I:%M %p')} ke baad"
            }

    return None


def format_continuous_timeline_prompt_block(chat_id: int, now_ist: Optional[datetime] = None) -> str:
    """
    Formats a chronological, continuous moving timeline block for runtime prompt injection.
    Eliminates midnight rollover confusion and prevents sycophantic hallucinations.
    """
    now_ist = now_ist or datetime.now(IST)
    spans = get_user_timeline_spans(chat_id, center_dt=now_ist, window_days_back=1, window_days_forward=3)

    if not spans:
        return "No active transit spans or blocking commitments in the 48-hour window (Free schedule)."

    lines = []
    lines.append("Chronological Continuity (Start -> End):")
    for s in spans:
        st_day = s.start_dt.strftime("%d %b (%a)")
        st_clk = s.start_dt.strftime("%I:%M %p")
        end_day = s.end_dt.strftime("%d %b (%a)")
        end_clk = s.end_dt.strftime("%I:%M %p")

        # Explicitly highlight crossing midnight to prevent model blindspots!
        if s.start_dt.date() != s.end_dt.date():
            time_str = f"{st_day} {st_clk} -> {end_day} {end_clk} (Overnight / Crosses Midnight 🌙)"
        else:
            time_str = f"{st_day} {st_clk} - {end_clk}"

        status_tag = "[IN-TRANSIT / OCCUPIED]" if s.kind in ("bus", "flight", "train") else "[SCHEDULED]"
        icon = "🚌" if s.kind == "bus" else ("✈️" if s.kind == "flight" else ("🚆" if s.kind == "train" else "🎬" if s.kind == "movie" else "📌"))
        lines.append(f"• {icon} {time_str}: {s.title} {status_tag}")

    return "\n".join(lines)

