"""
PeculiarAI — Autonomous Proactive Morning Briefing Engine
Dispatches intelligent daily morning briefings to active Telegram users:
1. Adaptive Wake-Up Timing:
   - Default: Standard 8:00 AM IST briefing.
   - Adaptive Early Wake-Up: If user has an early morning flight, train, or meeting before 8:30 AM (e.g. 6:00 AM flight),
     the briefing dynamically triggers 1.5 - 2 hours early (e.g. 4:30 AM / 5:00 AM) with essential boarding/travel checklist
     (Aadhaar/Govt ID, Ticket/Boarding Pass, PNR, baggage) so the user never misses their trip!
2. Implicit City-Aware Weather:
   - Never explicitly interrogates or badgers the user for their city.
   - If user has previously shared or memory contains their city (e.g. 'lives in Delhi', 'staying in Mumbai'),
     fetches live temperature, weather condition, Air Quality (AQI), and UV Index.
   - If city is unknown, smoothly presents the morning schedule without weather or questions.
3. Daily Agenda Overview:
   - Summarizes Google Calendar meetings, tasks, reminders, and commitments for today.
   - Note: Normal individual reminders STILL fire at their exact scheduled times independently!
4. Deduplication:
   - Strictly one briefing per user per calendar day (persisted in DB).
"""
import os
import re
import asyncio
from datetime import datetime, date, time as dtime, timedelta
from typing import Dict, Any, Optional, Tuple, List
import httpx

from timing import IST
import db
from utilities import get_weather
from state.active_context import get_active_ticket, parse_ticket_datetime


def detect_user_city(chat_id: int) -> Optional[str]:
    """
    Infers user's current city from saved facts or profile memories without explicitly asking.
    """
    try:
        facts = db.get_user_facts(chat_id, limit=50) or []
        for f in facts:
            txt = f.get("fact", "")
            # 1. Structured labels e.g. "City: Mumbai", "Hometown: Jaipur", "Location: Bangalore"
            m1 = re.search(r"\b(?:city|location|residence|hometown|shehar)[:\s]+([a-zA-Z\s]{3,25})\b", txt, re.I)
            if m1:
                cand = m1.group(1).strip()
                if cand.lower() not in ("unknown", "null", "none", "n/a", "india"):
                    return cand.title()

            # 2. Natural expressions e.g. "lives in Delhi", "staying in Pune", "based in Bangalore"
            m2 = re.search(r"\b(?:lives\s+in|living\s+in|stays\s+in|staying\s+in|based\s+in|shifted\s+to|rehta\s+hai|rehti\s+hai|rehta\s+hu|rehti\s+hu)\s+([a-zA-Z]{3,20})\b", txt, re.I)
            if m2:
                cand = m2.group(1).strip()
                stop_words = {"home", "house", "apartment", "hostel", "flat", "pg", "hotel", "room", "office", "india", "abroad", "village"}
                if cand.lower() not in stop_words:
                    return cand.title()

            # 3. Route or Ticket origin e.g. "Route: JODHPUR JN (JU) to ABU ROAD (ABR)"
            m3 = re.search(r"Route:\s*([A-Za-z]+)\s*(?:JN|CANTT|CITY)?\s*(?:\([A-Z]+\))?\s*to", txt, re.I)
            if m3:
                cand = m3.group(1).strip()
                if cand.lower() not in ("from", "train", "flight"):
                    return cand.title()

        # Check active ticket in context
        tkt = get_active_ticket(chat_id)
        if tkt and tkt.get("route"):
            m_route = re.search(r"^([A-Za-z]+)\s*(?:JN|CANTT|CITY)?\s*(?:\([A-Z]+\))?\s*to", tkt["route"], re.I)
            if m_route:
                return m_route.group(1).strip().title()
    except Exception as exc:
        print("[morning_briefing.detect_user_city] error:", repr(exc))
    return None


def detect_early_morning_event(chat_id: int, target_date: date, unified_sched: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Detects if the user has an early morning activity (e.g. flight, train journey, meeting)
    scheduled before 08:30 AM IST on target_date.
    Returns event info and adaptive wake-up trigger time.
    """
    now_ist = datetime.now(IST)

    # 1. Check Active Travel Ticket (e.g. PNR / Flight / Train)
    try:
        tkt = get_active_ticket(chat_id)
        if tkt:
            tkt_dt = parse_ticket_datetime(tkt.get("datetime_str"))
            if tkt_dt and tkt_dt.date() == target_date:
                # Early departure check (before 08:30 AM)
                if tkt_dt.time() < dtime(8, 30):
                    # For flights: wake up 2 hours before; for trains: 1.5 hours before
                    is_flight = any(k in (tkt.get("carrier", "") + tkt.get("summary", "")).lower() for k in ("flight", "airline", "air india", "indigo", "vistara", "spicejet", "akasa"))
                    lead_hours = 2.0 if is_flight else 1.5
                    trigger_dt = tkt_dt - timedelta(hours=lead_hours)
                    return {
                        "is_travel": True,
                        "is_flight": is_flight,
                        "kind": "flight" if is_flight else "train",
                        "title": f"{tkt.get('carrier', 'Travel Provider')} ({tkt.get('route', 'Journey')})",
                        "event_time": tkt_dt,
                        "trigger_time": trigger_dt,
                        "ticket_data": tkt
                    }
    except Exception as exc:
        print("[morning_briefing.ticket_check] error:", repr(exc))

    # 2. Check Saved Booking Facts for early travel on target_date
    try:
        facts = db.get_user_facts(chat_id, limit=30) or []
        for f in facts:
            f_text = f.get("fact", "")
            if any(k in f_text.lower() for k in ("booking/ticket", "flight", "train", "pnr")):
                date_m = re.search(r"date\s*[:\-#]?\s*([^|]+)", f_text, re.I)
                if date_m:
                    dt_parsed = parse_ticket_datetime(date_m.group(1).strip())
                    if dt_parsed and dt_parsed.date() == target_date:
                        if dt_parsed.time() < dtime(8, 30):
                            is_flight = "flight" in f_text.lower() or "air" in f_text.lower()
                            lead_hours = 2.0 if is_flight else 1.5
                            trigger_dt = dt_parsed - timedelta(hours=lead_hours)
                            carrier_m = re.search(r"(?:booking/ticket|ticket|provider)\s*[:\-#]?\s*([^|]+)", f_text, re.I)
                            route_m = re.search(r"route\s*[:\-#]?\s*([^|]+)", f_text, re.I)
                            pnr_m = re.search(r"pnr\s*[:\-#]?\s*([a-zA-Z0-9]+)", f_text, re.I)
                            return {
                                "is_travel": True,
                                "is_flight": is_flight,
                                "kind": "flight" if is_flight else "train",
                                "title": carrier_m.group(1).strip() if carrier_m else "Travel Booking",
                                "event_time": dt_parsed,
                                "trigger_time": trigger_dt,
                                "ticket_data": {
                                    "carrier": carrier_m.group(1).strip() if carrier_m else "Travel Provider",
                                    "route": route_m.group(1).strip() if route_m else "Journey Route",
                                    "pnr": pnr_m.group(1).strip() if pnr_m else "",
                                    "datetime_str": dt_parsed.strftime("%d %b %Y, %I:%M %p"),
                                    "summary": f_text
                                }
                            }
    except Exception as exc:
        print("[morning_briefing.fact_ticket_check] error:", repr(exc))

    # 3. Check Google Calendar Events (Unified Schedule)
    if unified_sched:
        cal_events = unified_sched.get("calendar_events", [])
        for ev in cal_events:
            st_iso = ev.get("start", {}).get("dateTime")
            if st_iso:
                try:
                    st_dt = datetime.fromisoformat(st_iso).astimezone(IST)
                    if st_dt.date() == target_date and st_dt.time() < dtime(8, 30):
                        trigger_dt = st_dt - timedelta(hours=1.0)
                        return {
                            "is_travel": False,
                            "is_flight": False,
                            "kind": "meeting",
                            "title": ev.get("summary") or "Important Meeting",
                            "event_time": st_dt,
                            "trigger_time": trigger_dt,
                            "ticket_data": None
                        }
                except Exception:
                    pass

        # 4. Check Reminders for early morning travel departures
        rems = unified_sched.get("reminders", [])
        for r in rems:
            r_title = (r.get("title") if isinstance(r, dict) else (r[1] if len(r) > 1 else "")) or ""
            # Only treat as early travel wakeup if it explicitly mentions travel/flight/train/airport
            if not any(k in r_title.lower() for k in ("flight", "train", "airport", "journey", "boarding", "departure")):
                continue
            r_time = r.get("due_time") if isinstance(r, dict) else (r[3] if len(r) > 3 else None)
            if r_time:
                try:
                    r_h = r_time.hour if hasattr(r_time, "hour") else int(str(r_time).split(":")[0])
                    r_m = r_time.minute if hasattr(r_time, "minute") else int(str(r_time).split(":")[1])
                    if r_h < 8 or (r_h == 8 and r_m < 30):
                        dt_r = datetime.combine(target_date, dtime(r_h, r_m), tzinfo=IST)
                        is_flight = "flight" in r_title.lower() or "air" in r_title.lower()
                        trigger_dt = dt_r - timedelta(hours=2.0 if is_flight else 1.5)
                        return {
                            "is_travel": True,
                            "is_flight": is_flight,
                            "kind": "flight" if is_flight else "train",
                            "title": r_title,
                            "event_time": dt_r,
                            "trigger_time": trigger_dt,
                            "ticket_data": None
                        }
                except Exception:
                    pass

    return None


async def build_morning_briefing_message(
    chat_id: int,
    user_info: dict,
    target_date: date,
    early_event: Optional[Dict[str, Any]] = None,
    unified_sched: Optional[Dict[str, Any]] = None
) -> Tuple[str, str]:
    """
    Constructs the formatted morning briefing text.
    Returns: (briefing_text, trigger_kind)
    """
    name = (user_info.get("first_name") or "").strip()
    greeting_name = f" {name}" if name else ""
    date_human = target_date.strftime("%A, %d %B %Y")

    lines = []
    trigger_kind = "standard_8am"

    # 1. HEADER & GREETING
    if early_event:
        trigger_kind = f"early_{early_event['kind']}"
        ev_time_str = early_event["event_time"].strftime("%I:%M %p")
        if early_event.get("is_travel"):
            ev_icon = "✈️" if early_event.get("is_flight") else "🚆"
            lines.append(f"🌅 **Good Morning{greeting_name}! Uth jaiye — aaj aapki early journey hai!** {ev_icon}\n")
            lines.append(f"🚨 **Early Travel Alert:**")
            t_data = early_event.get("ticket_data") or {}
            carrier = t_data.get("carrier") or early_event.get("title")
            route = t_data.get("route") or ""
            pnr = t_data.get("pnr") or ""
            seats = t_data.get("seats") or ""
            lines.append(f"• **Provider / Service:** {carrier}")
            lines.append(f"• **Departure Time:** {ev_time_str} IST")
            if route:
                lines.append(f"• **Route:** {route}")
            if pnr:
                lines.append(f"• **PNR / Booking ID:** `{pnr}`")
            if seats:
                lines.append(f"• **Seat / Berth:** {seats}")

            lines.append("\n🧳 **Departure & Boarding Checklist:**")
            lines.append("• 🪪 **Govt Photo ID / Aadhaar Card:** Entry ke liye zaroori hai.")
            lines.append("• 🎟️ **Boarding Pass / E-Ticket:** Ready rakhein (web check-in done).")
            lines.append("• 📱 **Essentials:** Phone charger, power bank & travel documents.")
            if early_event.get("is_flight"):
                lines.append("• ⏰ **Airport Timing:** Domestic flight ke liye 2 ghante pehle airport pahunchna recommended hota hai.")
            lines.append("")
        else:
            lines.append(f"🌅 **Good Morning{greeting_name}! Aaj aapka early schedule hai:**\n")
            lines.append(f"⏰ **Early Appointment:** {early_event['title']} at **{ev_time_str} IST**\n")
    else:
        lines.append(f"🌅 **Good Morning{greeting_name}!** ☀️\nAaj ka din shuru ho chuka hai — yahan hai aapka daily overview:\n")

    # 2. WEATHER (ONLY if user's city is known in memory/facts, without asking)
    city = detect_user_city(chat_id)
    if city:
        try:
            w_res = await get_weather(city)
            if w_res and w_res.get("found"):
                temp = w_res.get("temperature")
                cond = w_res.get("condition", "Pleasant")
                min_t = w_res.get("min_temp")
                max_t = w_res.get("max_temp")
                aqi = w_res.get("aqi")
                uv = w_res.get("uv_index")

                w_parts = [f"• **Temperature:** {temp}°C (Min: {min_t}°C | Max: {max_t}°C)"]
                w_parts.append(f"• **Mausam:** {cond}")
                metric_line = []
                if aqi is not None:
                    metric_line.append(f"**AQI:** {aqi}")
                if uv is not None:
                    metric_line.append(f"**UV Index:** {uv}")
                if metric_line:
                    w_parts.append(f"• {' | '.join(metric_line)}")

                lines.append(f"🌤️ **Aaj {city} Ka Mausam:**")
                lines.extend(w_parts)
                lines.append("")
        except Exception as w_err:
            print("[morning_briefing.weather] error:", repr(w_err))

    # 3. SCHEDULE & AGENDAS
    lines.append(f"📋 **Aaj Ka Schedule & Tasks ({date_human}):**")

    # Calendar Events
    cal_events = (unified_sched.get("calendar_events") if unified_sched else []) or []
    if cal_events:
        lines.append("📅 **Meetings & Events:**")
        for ev in cal_events:
            summ = ev.get("summary") or "Meeting"
            st_iso = ev.get("start", {}).get("dateTime")
            t_str = ""
            if st_iso:
                try:
                    t_str = f" at {datetime.fromisoformat(st_iso).astimezone(IST).strftime('%I:%M %p')}"
                except Exception:
                    pass
            lines.append(f"• {summ}{t_str}")
    else:
        lines.append("📅 **Meetings:** Aaj koi meeting scheduled nahi hai (Calendar free hai).")

    # Reminders
    rems = (unified_sched.get("reminders") if unified_sched else []) or []
    if rems:
        lines.append("⏰ **Tasks & Reminders:**")
        for r in rems:
            title = r.get("title") if isinstance(r, dict) else (r[1] if len(r) > 1 else "Reminder")
            r_time = r.get("due_time") if isinstance(r, dict) else (r[3] if len(r) > 3 else None)
            time_str = ""
            if r_time:
                try:
                    rh = r_time.hour if hasattr(r_time, "hour") else int(str(r_time).split(":")[0])
                    rm = r_time.minute if hasattr(r_time, "minute") else int(str(r_time).split(":")[1])
                    time_str = f" at {rh:02d}:{rm:02d}"
                except Exception:
                    pass
            lines.append(f"• {title}{time_str}")
    else:
        lines.append("⏰ **Reminders:** Aaj ke liye koi pending reminder nahi hai.")

    # Commitments
    comms = (unified_sched.get("commitments") if unified_sched else []) or []
    if comms:
        lines.append("🤝 **Pending Commitments:**")
        for c in comms:
            lines.append(f"• **{c['name']}:** {c['commitment']}")

    lines.append("\nHave a wonderful, productive day ahead! Kuch bhi add ya note karna ho toh bata dena. 👍")

    return "\n".join(lines), trigger_kind


async def send_telegram_message(chat_id: int, text_content: str) -> bool:
    """Dispatches message to Telegram chat using official bot token."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not chat_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text_content,
                    "parse_mode": "Markdown"
                }
            )
            return resp.status_code == 200
    except Exception as exc:
        print(f"[morning_briefing.send_telegram_message] error for {chat_id}:", repr(exc))
        return False


async def process_user_morning_briefing(chat_id: int, user_info: dict, now_ist: datetime) -> bool:
    """
    Evaluates timing and triggers morning briefing if eligible.
    """
    today_str = now_ist.strftime("%Y-%m-%d")

    # 1. Check if already dispatched for today
    if db.is_morning_briefing_sent(chat_id, today_str):
        return False

    # 2. Gather unified schedule for today
    from main import get_unified_schedule
    unified_sched = None
    try:
        unified_sched = await get_unified_schedule(chat_id, now_ist.date())
    except Exception as exc:
        print("[morning_briefing.sched_fetch] fallback:", repr(exc))
        unified_sched = {"calendar_events": [], "reminders": [], "commitments": []}

    # 3. Check for early morning event (flight/train/early meeting)
    early_event = detect_early_morning_event(chat_id, now_ist.date(), unified_sched)

    should_send = False
    active_early = None
    if early_event:
        # If early event: trigger between trigger_time and event_time + 30 mins
        trig_time = early_event["trigger_time"]
        ev_time = early_event["event_time"]
        if now_ist >= trig_time and now_ist <= ev_time + timedelta(minutes=30):
            should_send = True
            active_early = early_event
        elif now_ist.hour >= 8 and now_ist.hour < 22:
            # Fallback: If early wake-up window passed but briefing was never sent today,
            # send standard daily briefing so user isn't left without their daily overview!
            should_send = True
            active_early = None
    else:
        # Standard Morning Briefing: Triggers from 08:00 AM IST onwards (until 22:00 PM if not sent)
        if now_ist.hour >= 8 and now_ist.hour < 22:
            should_send = True
            active_early = None

    if should_send:
        msg_text, trigger_kind = await build_morning_briefing_message(
            chat_id, user_info, now_ist.date(), early_event=active_early, unified_sched=unified_sched
        )
        sent_ok = await send_telegram_message(chat_id, msg_text)
        if sent_ok:
            db.record_morning_briefing_sent(chat_id, today_str, trigger_kind)
            # Log turn in chat history
            try:
                db.log_chat_message(chat_id, "assistant", msg_text)
            except Exception:
                pass
            return True

    return False


async def morning_briefing_job():
    """
    Periodic job invoked in background loop.
    Iterates through all registered active users and checks if they need their morning briefing.
    """
    now_ist = datetime.now(IST)
    users = db.get_all_active_telegram_users()
    for u in users:
        try:
            cid = u["chat_id"]
            await process_user_morning_briefing(cid, u, now_ist)
        except Exception as user_err:
            print(f"[morning_briefing_job] error for user {u}:", repr(user_err))


async def morning_briefing_loop():
    """Background monitoring loop running inside FastAPI lifespan."""
    # Delay initial execution so server startup and web requests are blazing fast
    await asyncio.sleep(60)
    while True:
        try:
            await morning_briefing_job()
        except Exception as exc:
            print("[morning_briefing_loop] cycle error:", repr(exc))
        await asyncio.sleep(60)  # Check every 60 seconds

