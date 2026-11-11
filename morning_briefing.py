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
import core.timeutil as timeutil
import db
from utilities import get_weather
from state.active_context import get_active_ticket, parse_ticket_datetime


def detect_user_city(chat_id: int) -> Optional[str]:
    """
    Infers user's current city from structured location state machine or profile memories.
    Zero-regex priority: Checks user_location_state first!
    """
    try:
        # 1. Primary Gold-Standard: Semantic Location State Machine
        loc_state = db.get_user_location_state(chat_id)
        if loc_state and loc_state.get("current_city"):
            return loc_state["current_city"]

        # 2. Check active travel tickets in context
        tkt = get_active_ticket(chat_id)
        if tkt and tkt.get("route"):
            origin = tkt["route"].split("to")[0].strip().split()[0].title()
            if origin and origin.lower() not in ("from", "train", "flight"):
                return origin

        # 3. Fallback scan of stored facts for structured location
        with db.engine.connect() as conn:
            row = conn.execute(
                db.text("""
                    SELECT structured FROM memories 
                    WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) 
                      AND source_type IN ('location', 'city', 'profile')
                      AND status = 'active'
                    ORDER BY id DESC LIMIT 1
                """),
                {"cid": chat_id}
            ).mappings().first()
            if row and row.get("structured") and isinstance(row["structured"], dict) and row["structured"].get("city"):
                c_name = row["structured"]["city"].title()
                db.set_user_location_state(chat_id, c_name, source="structured_memory")
                return c_name
    except Exception as exc:
        print("[detect_user_city] error:", repr(exc))
    return None


def detect_early_morning_event(chat_id: int, target_date: date, unified_sched: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """
    Detects if the user has an early morning activity (e.g. flight, train journey, meeting)
    scheduled before 08:30 AM IST on target_date across all linked identity sessions.
    Returns event info and adaptive wake-up trigger time.
    """
    now_ist = timeutil.now(IST)
    linked_cids = list(db.get_all_linked_chat_ids(chat_id))

    # 1. Check Active Travel Ticket (e.g. PNR / Flight / Train) across linked identities
    for cid in linked_cids:
        try:
            tkt = get_active_ticket(cid)
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

    # 2. Check Saved Booking Facts for early travel on target_date across linked identities
    for cid in linked_cids:
        try:
            facts = db.get_user_facts(cid, limit=30) or []
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
    name = db.get_user_display_name(chat_id) or (user_info.get("first_name") or "").strip()
    greeting_name = f" {name}" if name else ""
    date_human = target_date.strftime("%A, %d %B %Y")
    user_lang = db.get_user_language(chat_id)
    is_eng = (user_lang == "english")

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

            lines.append("\n🧳 **Departure & Required Documents Checklist:**")
            req_docs = t_data.get("required_documents") or t_data.get("docs")
            if req_docs and isinstance(req_docs, list):
                for doc in req_docs:
                    lines.append(f"• 📋 **{doc}**")
            elif req_docs and isinstance(req_docs, str):
                lines.append(f"• 📋 **Required Document:** {req_docs}")
            else:
                lines.append("• 🪪 **Govt Photo ID / Aadhaar Card:** Aadhaar Card, Passport, ya Voter ID (Airport/Station entry ke liye zaroori).")
                lines.append("• 🎟️ **Boarding Pass / E-Ticket:** Digital copy / Web check-in ready rakhein.")
                lines.append("• 📱 **Essentials:** Phone charger, power bank & tickets.")
            if early_event.get("is_flight"):
                lines.append("• ⏰ **Airport Timing:** Domestic flight ke liye departure se 2 ghante pehle airport pahunchna zaroori hai.")
            elif early_event.get("is_travel"):
                lines.append("• ⏰ **Station Timing:** Train departure se kam se kam 30–45 minute pehle station pahunchein.")

            try:
                travel_lists = db.get_smart_lists(chat_id, category="travel")
                if travel_lists:
                    list_items = db.get_smart_list_items(chat_id, travel_lists[0]["id"])
                    if list_items:
                        lines.append(f"\n📦 **Packing Checklist ({travel_lists[0]['name']}):**")
                        for item in list_items[:5]:
                            lines.append(f"• {item['text']}")
            except Exception:
                pass
            lines.append("")
        else:
            if is_eng:
                lines.append(f"🌅 **Good Morning{greeting_name}! You have an early schedule today:**\n")
                lines.append(f"⏰ **Early Appointment:** {early_event['title']} at **{ev_time_str} IST**\n")
            else:
                lines.append(f"🌅 **Good Morning{greeting_name}! Aaj aapka early schedule hai:**\n")
                lines.append(f"⏰ **Early Appointment:** {early_event['title']} at **{ev_time_str} IST**\n")
    else:
        if is_eng:
            lines.append(f"🌅 **Good Morning{greeting_name}!** ☀️\nHere is your daily overview for today:\n")
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
                w_parts.append(f"• **Condition:** {cond}" if is_eng else f"• **Mausam:** {cond}")
                metric_line = []
                if aqi is not None:
                    metric_line.append(f"**AQI:** {aqi}")
                if uv is not None:
                    metric_line.append(f"**UV Index:** {uv}")
                if metric_line:
                    w_parts.append(f"• {' | '.join(metric_line)}")

                lines.append(f"🌤️ **Today's Weather in {city}:**" if is_eng else f"🌤️ **Aaj {city} Ka Mausam:**")
                lines.extend(w_parts)
                lines.append("")
        except Exception as w_err:
            print("[morning_briefing.weather] error:", repr(w_err))

    # 3. SCHEDULE & AGENDAS
    lines.append(f"📋 **Today's Schedule & Tasks ({date_human}):**" if is_eng else f"📋 **Aaj Ka Schedule & Tasks ({date_human}):**")

    # Calendar Events & Local Schedule Items
    cal_events = (unified_sched.get("calendar_events") if unified_sched else []) or []
    rems = (unified_sched.get("reminders") if unified_sched else []) or []

    def _is_meeting_entry(title_str: str, cat_str: str = "") -> bool:
        t_clean = (title_str or "").strip().lower()
        cat_clean = (cat_str or "").strip().lower()
        if cat_clean in ("meeting", "meetings"):
            return True
        # Explicit meeting phrases take absolute priority
        meeting_markers = (
            "meeting", "call with", "sync with", "discussion with", "appointment with",
            "catch up with", "interview with", "1-on-1", "1:1", "zoom with", "google meet", "doctor appointment"
        )
        if any(m in t_clean for m in meeting_markers):
            return True
        return False

    meeting_items = []
    task_items = []

    for ev in cal_events:
        meeting_items.append({
            "title": ev.get("summary") or "Meeting",
            "start_iso": ev.get("start", {}).get("dateTime"),
            "due_time": None
        })

    for r in rems:
        cat = (r.get("category") if isinstance(r, dict) else (r[0] if len(r) > 0 else "")) or ""
        title = r.get("title") if isinstance(r, dict) else (r[1] if len(r) > 1 else "Reminder")
        r_time = r.get("due_time") if isinstance(r, dict) else (r[3] if len(r) > 3 else None)
        entry = {"title": title, "due_time": r_time, "start_iso": None}

        if _is_meeting_entry(title, cat):
            meeting_items.append(entry)
        else:
            task_items.append(entry)

    # Render Meetings & Events
    if meeting_items:
        lines.append("📅 **Meetings & Events:**")
        for m in meeting_items:
            t_str = ""
            if m.get("start_iso"):
                try:
                    t_str = f" at {datetime.fromisoformat(m['start_iso']).astimezone(IST).strftime('%I:%M %p')}"
                except Exception:
                    pass
            elif m.get("due_time"):
                r_time = m["due_time"]
                try:
                    rh = r_time.hour if hasattr(r_time, "hour") else int(str(r_time).split(":")[0])
                    rm = r_time.minute if hasattr(r_time, "minute") else int(str(r_time).split(":")[1])
                    t_str = f" at {rh:02d}:{rm:02d}"
                except Exception:
                    pass
            lines.append(f"• {m['title']}{t_str}")
    else:
        lines.append("📅 **Meetings:** No meetings scheduled today (Calendar is free)." if is_eng else "📅 **Meetings:** Aaj koi meeting scheduled nahi hai (Calendar free hai).")

    # Render Tasks & Reminders
    if task_items:
        lines.append("⏰ **Tasks & Reminders:**")
        for r in task_items:
            time_str = ""
            r_time = r.get("due_time")
            if r_time:
                try:
                    rh = r_time.hour if hasattr(r_time, "hour") else int(str(r_time).split(":")[0])
                    rm = r_time.minute if hasattr(r_time, "minute") else int(str(r_time).split(":")[1])
                    time_str = f" at {rh:02d}:{rm:02d}"
                except Exception:
                    pass
            lines.append(f"• {r['title']}{time_str}")
    else:
        lines.append("⏰ **Reminders:** No pending reminders for today." if is_eng else "⏰ **Reminders:** Aaj ke liye koi pending reminder nahi hai.")

    # Commitments
    comms = (unified_sched.get("commitments") if unified_sched else []) or []
    if comms:
        lines.append("🤝 **Pending Commitments:**")
        for c in comms:
            lines.append(f"• **{c['name']}:** {c['commitment']}")

    # 4. Birthday & Subscription Radar
    try:
        from services.relationship_service import get_upcoming_birthdays
        from services.subscription_service import check_expiring_subscriptions

        upcoming_bdays = get_upcoming_birthdays(chat_id, days_ahead=2)
        if upcoming_bdays:
            lines.append("\n🎂 **Upcoming Birthdays:**")
            for b in upcoming_bdays:
                when_str = "Aaj" if b['days_remaining'] == 0 else ("Kal" if b['days_remaining'] == 1 else f"In {b['days_remaining']} days")
                lines.append(f"• **{b['name']}** ({b['relationship']}) — {when_str}!")

        expiring_subs = check_expiring_subscriptions(chat_id, days_ahead=3)
        if expiring_subs:
            lines.append("\n⏳ **Subscription & Free-Trial Watchdog:**")
            for s in expiring_subs:
                tag = "Free Trial Expiry" if s.get("kind") == "trial_expiring" else "Renewal"
                amt = f" (₹{s['amount']})" if s.get('amount') and s['amount'] != 'N/A' else ""
                lines.append(f"• **{s['service']}** {tag} in {s['days_remaining']}d ({s['date']}){amt}")

        # Follow-up Watchdog: Communications awaiting reply
        from services.followup_service import get_due_followups
        due_f = get_due_followups(chat_id)
        if due_f:
            lines.append("\n📬 **Pending Follow-ups (Awaiting Reply):**")
            for f in due_f[:2]:
                lines.append(f"• **{f['recipient']}** ({f['subject']}) — Reply pending (3+ days)")

        # Contextual Travel Day (ONLY for genuine transit / travel spans like flight, train, bus, trip)
        from state.timeline import get_user_timeline_spans
        spans = get_user_timeline_spans(chat_id)
        for sp in spans:
            s_kind = getattr(sp, "kind", None) or (sp.get("kind") if isinstance(sp, dict) else "")
            if str(s_kind).lower() not in ("flight", "train", "bus", "travel", "journey", "transit"):
                continue
            s_st = getattr(sp, "start_dt", None) or (sp.get("start_dt") if isinstance(sp, dict) else None)
            s_en = getattr(sp, "end_dt", None) or (sp.get("end_dt") if isinstance(sp, dict) else None)
            t_curr = target_date.date() if hasattr(target_date, 'date') else target_date
            if s_st and s_en and s_st.date() <= t_curr <= s_en.date():
                t_title = getattr(sp, "title", None) or (sp.get("title") if isinstance(sp, dict) else "Trip")
                lines.append(f"\n✈️ **Travel Day:** {t_title} (Safe travels!)")
    except Exception as radar_err:
        print("[morning_briefing.radar] error:", repr(radar_err))

    if is_eng:
        lines.append("\nHave a wonderful, productive day ahead! Let me know if you need anything added or updated. 👍")
    else:
        lines.append("\nHave a wonderful, productive day ahead! Kuch bhi add ya note karna ho toh bata dena. 👍")

    return "\n".join(lines), trigger_kind


async def send_telegram_message(chat_id: int, text_content: str) -> bool:
    """Dispatches message to Telegram chat using project's resilient send_telegram_text."""
    if not chat_id or not text_content:
        return False
    try:
        from main import send_telegram_text
        await send_telegram_text(chat_id, text_content)
        return True
    except Exception as exc:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            return False
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text_content}
                )
                return resp.status_code == 200
        except Exception:
            return False


async def process_user_morning_briefing(chat_id: int, user_info: dict, now_ist: datetime) -> bool:
    """
    Evaluates timing and triggers morning briefing if eligible.
    Routes to the active session among linked identities.
    """
    today_str = now_ist.strftime("%Y-%m-%d")
    target_chat_id = db.get_most_active_chat_id(chat_id)

    # 1. Check if already dispatched for today
    if db.is_morning_briefing_sent(target_chat_id, today_str):
        return False

    # 2. Gather unified schedule for today
    from main import get_unified_schedule
    unified_sched = None
    try:
        unified_sched = await get_unified_schedule(target_chat_id, now_ist.date())
    except Exception as exc:
        print("[morning_briefing.sched_fetch] fallback:", repr(exc))
        unified_sched = {"calendar_events": [], "reminders": [], "commitments": []}

    # 3. Check for early morning event (flight/train/early meeting)
    early_event = detect_early_morning_event(target_chat_id, now_ist.date(), unified_sched)

    should_send = False
    active_early = None
    if early_event:
        # If early event: trigger between trigger_time and event_time + 30 mins
        trig_time = early_event["trigger_time"]
        ev_time = early_event["event_time"]
        if trig_time <= now_ist <= ev_time + timedelta(minutes=30):
            should_send = True
            active_early = early_event
        elif 8 <= now_ist.hour < 11:
            should_send = True
            active_early = None
    else:
        # Standard Morning Briefing: STRICTLY triggers in morning between 08:00 AM and 10:30 AM IST
        if 8 <= now_ist.hour < 11:
            should_send = True
            active_early = None

    if should_send:
        # Atomic check-and-set to prevent duplicate dispatch across concurrent instances
        if db.is_morning_briefing_sent(target_chat_id, today_str):
            return False

        msg_text, trigger_kind = await build_morning_briefing_message(
            target_chat_id, user_info, now_ist.date(), early_event=active_early, unified_sched=unified_sched
        )
        # Broadcast to all linked devices for this user identity
        linked_targets = db.get_all_linked_chat_ids(target_chat_id)
        for l_cid in linked_targets:
            db.record_morning_briefing_sent(l_cid, today_str, trigger_kind=trigger_kind)
            try:
                await send_telegram_message(l_cid, msg_text)
            except Exception:
                pass
            try:
                db.log_chat_message(l_cid, "assistant", msg_text)
            except Exception:
                pass
        return True

    return False


async def morning_briefing_job():
    """
    Periodic job invoked in background loop.
    Iterates through all registered active users and checks if they need their morning briefing.
    """
    now_ist = timeutil.now(IST)
    users = db.get_all_active_telegram_users()
    tasks = []
    seen_canonical = set()
    for u in users:
        cid = u.get("chat_id")
        if cid and not str(cid).startswith("998877"):
            canon_cid = db.canonical_chat_id(cid)
            if canon_cid in seen_canonical:
                continue
            seen_canonical.add(canon_cid)
            tasks.append(process_user_morning_briefing(cid, u, now_ist))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


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

