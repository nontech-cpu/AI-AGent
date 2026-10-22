import os
import re
import json
import secrets
import sqlite3
import hashlib
import string
import asyncio
import httpx
from collections import deque
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

import difflib
from db import (give_consent, has_consent, set_persona, get_persona,
                set_categories, get_categories, save_memory, list_memories,
                items_between, items_for_date, get_board_cards, delete_user, export_data, add_reminders,
                get_pending_reminders, mark_sent, create_email_link,
                get_link_by_email, get_linked_email, verify_email_link,
                remove_email_link, ensure_user, get_state, set_state,
                save_google_token, get_google_token, delete_google_token,
                find_meetings_before, find_meetings_at_times, find_recent_meetings, delete_memory_items, engine, text,
                save_contact_memory, get_contact_memories, update_contact_commitment,
                log_chat_message, get_recent_chat_history, get_chat_turns_mentioning_keyword, save_user_fact, get_user_facts,
                save_user_integration, get_user_integration, delete_user_integration, get_user_integrations_summary)
from integrations import (validate_notion_token, create_notion_page,
                          validate_github_token, list_github_issues, list_github_repos)
from file_generator import build_contacts_excel, build_styled_pdf
from categories import CATEGORIES, DEFAULTS
from llm import (extract, understand, guess_persona, is_sensitive, parse_followup_time,
                 is_explicit_calendar_action, classify_yes_no_any_language, _WORD_NUMBERS, _chat_json,
                 answer_conversation_memory)
from nlu import yes_no, match_persona, PERSONA_LABELS
from timing import IST, build_plan, fmt_when, event_over
from inbox import fetch_unseen
from lang import (reply_lang, detect_text_language, language_from_whisper,
                  get_language, set_language, localize)
from voice import transcribe_telegram_voice
from multimodal import (
    process_telegram_photo,
    process_telegram_document,
    summarize_document_content,
    answer_document_query,
)
from google_services import (
    send_email as google_send_email,
    upload_text_file as google_upload_text_file,
    create_meeting as google_create_meeting,
    update_meeting as google_update_meeting,
    delete_calendar_event as google_delete_calendar_event,
    find_calendar_event as google_find_calendar_event,
    list_calendar_events as google_list_calendar_events,
    list_drive_folders,
)
from google_auth import (
    build_auth_url,
    callback_url,
    verify_state,
    verify_state_payload,
    exchange_code,
    get_google_email,
    encrypt_token,
    decrypt_token,
    refresh_access_token,
)

load_dotenv()
# Reverse-proxy deployment note: run this FastAPI app on port 8001; proxy.py owns port 8000.
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SECRET = os.getenv("WEBHOOK_SECRET")
BOT_EMAIL = os.getenv("BOT_EMAIL")
API = f"https://api.telegram.org/bot{TOKEN}"

MAX_VOICE_SECONDS = 60

EMAIL_FIND = re.compile(r"[^\s@<>()]+@[^\s@<>()]+\.[^\s@<>()]+")
EMAIL_OK = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Natural-language Google connection requests (no slash-command required).
GOOGLE_CONNECT_RE = re.compile(
    r"\b(?:connect|link|integrate|authorize|give)\b.{0,35}\bgoogle\b"
    r"|\bgoogle\b.{0,35}\b(?:connect|link|integrate|authorize|access)\b",
    re.IGNORECASE,
)

# Telegram jab retry kare to wahi update dobara process na ho
SEEN_UPDATES = deque(maxlen=1000)
# Background tasks ko zinda rakhne ke liye
BG_TASKS = set()
# Pending Google actions are held until explicit yes/no confirmation.
PENDING_GOOGLE_ACTIONS = {}

# Website verification database and initialization
WEB_VERIFY_DB = os.getenv("PECULIARAI_WEB_VERIFY_DB", "peculiarai_web_verify.sqlite3")


def _init_web_verify_db():
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS web_verifications (
            token TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            otp_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            telegram_chat_id TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0
        )""")
        conn.commit()


# ---------- Texts ----------

WELCOME_CONSENT = (
    "Hi! I'm your personal assistant. I remember important things like bills, "
    "bookings and meetings, and remind you at the right time.\n\n"
    "A few promises:\n"
    "• I keep only short details (title, amount, date), never your full messages.\n"
    "• I never ask for OTPs, passwords or card numbers.\n"
    "• Your messages and voice notes are processed by an AI provider (Groq) "
    "to understand them. Voice notes are not saved on my side.\n"
    "• You can delete everything anytime.\n\n"
    "Shall we continue? Reply yes or no."
)

PERSONA_TEXT = (
    "What do you do? This helps me track the right things for you.\n\n"
    "1. Job\n2. Housework\n3. Business\n4. Student\n5. Other\n\n"
    "Reply with a number or just type it."
)

HELP_TEXT = (
    "I can remember important things, manage your schedule, and help you get things done. Try:\n"
    "• Electricity bill 1200 due on 25 Oct\n"
    "• Meeting tomorrow at 5 PM\n"
    "• Send a photo of a bill, receipt, or invite 🖼️\n"
    "• Send a PDF or document to read & summarize 📄\n"
    "• What's on my plate today? (or tomorrow, this week)\n"
    "• Show everything you remember\n"
    "• Connect Google (Gmail, Calendar, Drive)\n"
    "• Link my email\n"
    "• Or send a voice note in your own language 🎙️"
)


# ---------- Memory & Context Store ----------

RECENT_DOCS = {}  # chat_id -> {"filename": str, "content": str, "type": str, "summary": str, "timestamp": datetime}
RECENT_CHAT_TURNS = {}  # chat_id -> deque([{"role": str, "text": str}], maxlen=10)
LAST_SCANNED_CONTACT = {}  # chat_id -> dict of last scanned contact card details


def record_chat_turn(chat_id: int, role: str, text: str):
    if not text:
        return
    clean_msg = text.strip()
    if chat_id not in RECENT_CHAT_TURNS:
        RECENT_CHAT_TURNS[chat_id] = deque(maxlen=20)
    RECENT_CHAT_TURNS[chat_id].append({"role": role, "text": clean_msg})
    # Persist in Neon PostgreSQL chat_logs
    try:
        log_chat_message(chat_id, role, clean_msg)
    except Exception as exc:
        print("record_chat_turn persist error:", repr(exc))


# ---------- Telegram helpers (WhatsApp ke time sirf ye 3 badlenge) ----------

async def tg(method, payload):
    async with httpx.AsyncClient() as client:
        await client.post(f"{API}/{method}", json=payload)


async def say_raw(chat_id, text):
    """Bina translate kiye bhejta hai (jaise voice ka transcript)."""
    record_chat_turn(chat_id, "assistant", text)
    await tg("sendMessage", {"chat_id": chat_id, "text": text})


async def say(chat_id, text):
    """User ki language me (agar English nahi hai) bhejta hai."""
    text = await localize(text, reply_lang.get())
    record_chat_turn(chat_id, "assistant", text)
    await tg("sendMessage", {"chat_id": chat_id, "text": text})


async def send_file(chat_id, filename, content_bytes, caption, mime_type="application/octet-stream"):
    async with httpx.AsyncClient(timeout=60.0) as client:
        await client.post(
            f"{API}/sendDocument",
            data={"chat_id": chat_id, "caption": caption},
            files={"document": (filename, content_bytes, mime_type)},
        )


# ---------- Saving items ----------

async def save_item_flow(chat_id, item):
    # Rule: sirf wahi category jo user ne on rakhi hai
    if item["category"] not in get_categories(chat_id):
        await say(chat_id, f"This looks like a {item['category']} item, but I'm not tracking "
                           "that right now. Say 'track " + item["category"] + "' if you want me to.")
        return

    item_id = save_memory(chat_id, item["category"], item["title"], item["amount"],
                          item["due_date"], item["due_time"])
    plan = build_plan(item["due_date"], item["due_time"])
    if plan["reminders"] and item_id:
        add_reminders(item_id, plan["reminders"])

    parts = [f"Saved ✅ {CATEGORIES[item['category']]}: {item['title']}"]
    if item["amount"] is not None:
        parts.append(f"Amount: {item['amount']}")
    if item["due_date"]:
        parts.append(f"Date: {item['due_date']}")
    if item["due_time"]:
        parts.append(f"Time: {item['due_time'].strftime('%H:%M')}")

    if plan["status"] == "scheduled":
        now = datetime.now(IST)
        when = ", ".join(fmt_when(at, now) for at, _ in plan["reminders"])
        parts.append(f"I'll remind you: {when}")
    elif plan["status"] == "past":
        parts.append("That time has already passed, so I won't set a reminder.")
    elif plan["status"] == "none" and item["due_date"]:
        parts.append("No reminder was set (the usual reminder times have already passed).")
    await say(chat_id, "\n".join(parts))

    # Bahut kareeb ka event: turant alert
    if plan["status"] == "urgent":
        await say(chat_id, f"⚡ Heads up! {item['title']} starts in about "
                           f"{plan['minutes']} min.")


async def handle_memory_text(chat_id, text):
    # Emails ke liye: sirf item nikalna, koi command nahi
    if is_sensitive(text):
        await say(chat_id, "That looks sensitive (OTP, password or card number). "
                           "I never store those, so I ignored it.")
        return
    try:
        item = await extract(text)
    except Exception as e:
        print("extract error:", e)
        await say(chat_id, "Sorry, something went wrong. Please try again.")
        return
    if item is None:
        await say(chat_id, "I only remember important things like bills, payments, "
                           "bookings, orders, meetings and appointments. "
                           "This one wasn't saved.")
        return
    await save_item_flow(chat_id, item)


# ---------- Showing things ----------

def _as_date(value):
    """Normalize dates from the LLM/JSON state or SQL driver to date objects."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        value = value.strip()
        # Accept ISO dates and ISO datetimes; reject ambiguous guesses.
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            pass
        for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
    raise ValueError(f"Unsupported date value: {value!r}")


def _as_time(value):
    """Normalize SQL/LLM time values without assuming a particular driver type."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.time()
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        raw = value.strip()
        for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M", "%I:%M %p", "%I:%M%p"):
            try:
                return datetime.strptime(raw, fmt).time()
            except ValueError:
                continue
    raise ValueError(f"Unsupported time value: {value!r}")


def day_label(d, today):
    d = _as_date(d)
    today = _as_date(today)
    if d == today:
        return "Today"
    if d == today + timedelta(days=1):
        return "Tomorrow"
    if d == today - timedelta(days=1):
        return "Yesterday"
    return d.strftime("%a %d %b")


WEEKDAY_ALIASES = {
    "monday": ["monday", "somwar", "somvaar", "mon"],
    "tuesday": ["tuesday", "mangalwar", "mangalvaar", "mangal", "tue", "tues"],
    "wednesday": ["wednesday", "budhwar", "budhvaar", "budh", "wed"],
    "thursday": ["thursday", "guruwar", "guruvaar", "veervaar", "thu", "thurs"],
    "friday": ["friday", "shukrawar", "shukravaar", "shukra", "fri"],
    "saturday": ["saturday", "shaniwar", "shanivaar", "shani", "sat"],
    "sunday": ["sunday", "ravivar", "itwar", "itvaar", "sun"]
}


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


def find_matching_contact(text: str, contacts: list[dict]) -> dict | None:
    """Intelligently match contact from text using substring, word stems, and fuzzy similarity."""
    if not contacts or not text:
        return None
    text_low = text.lower()
    words = re.findall(r"[a-zA-Z]{3,}", text_low)
    for c in contacts:
        c_name = c["name"].lower()
        first_name = c_name.split()[0]
        # Exact substring on full name or first name
        if first_name in text_low or c_name in text_low:
            return c
        # Fuzzy match for common phonetic/typo variations (e.g. annaya, ananay, ananaya vs ananya)
        for w in words:
            if difflib.SequenceMatcher(None, first_name, w).ratio() >= 0.75:
                return c
    return None


async def get_unified_schedule(chat_id: int, target_date: date):
    """
    Unified Temporal Schedule Aggregator:
    Combines:
    1. Google Calendar events
    2. Saved reminders & appointments (memory_items)
    3. Contact Commitments (commitments tied to weekday/date)
    4. Task Board cards (due on target_date)
    """
    weekday_str = target_date.strftime("%A").lower()
    day_aliases = WEEKDAY_ALIASES.get(weekday_str, [weekday_str])
    date_d_b = target_date.strftime("%d %b").lower()
    date_iso = target_date.isoformat()

    # 1. Reminders
    reminders = items_for_date(chat_id, target_date)

    # 2. Google Calendar
    calendar_events = []
    try:
        is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
        if is_conn:
            t_min = f"{date_iso}T00:00:00+05:30"
            t_max = f"{date_iso}T23:59:59+05:30"
            calendar_events = await google_list_calendar_events(chat_id, time_min_iso=t_min, time_max_iso=t_max)
    except Exception as exc:
        print("Calendar fetch in unified schedule skipped/failed:", repr(exc))

    # 3. Contact Commitments
    matched_commitments = []
    try:
        contacts = get_contact_memories(chat_id)
        for c in contacts:
            comm = (c.get("commitments") or "").lower()
            notes = (c.get("context_notes") or "").lower()
            combined = f"{comm} {notes}"
            matches = any(alias in combined for alias in day_aliases) or (date_d_b in combined) or (date_iso in combined)
            if matches and c.get("commitments"):
                matched_commitments.append({
                    "name": c["name"],
                    "company": c.get("company"),
                    "role_title": c.get("role_title"),
                    "commitment": c["commitments"],
                    "email": c.get("email"),
                    "phone": c.get("phone")
                })
    except Exception as exc:
        print("Contact commitments in unified schedule error:", repr(exc))

    # 4. Board Tasks
    matched_tasks = []
    try:
        board = get_board_cards(chat_id)
        for st, cards in board.items():
            for card in cards:
                if card.get("due_date") == date_iso:
                    matched_tasks.append(card)
    except Exception as exc:
        print("Board tasks in unified schedule error:", repr(exc))

    return {
        "date": target_date,
        "weekday": weekday_str,
        "calendar_events": calendar_events,
        "reminders": reminders,
        "commitments": matched_commitments,
        "tasks": matched_tasks
    }


async def detect_meeting_conflicts(chat_id: int, start_iso: str, duration_minutes: int = 30):
    """
    Detect exact time conflicts or day-level commitments before booking a meeting.
    Returns: (list of time_conflicts, list of day_commitments)
    """
    try:
        start_dt = datetime.fromisoformat(start_iso)
        end_dt = start_dt + timedelta(minutes=int(duration_minutes))
    except Exception:
        return [], []

    target_date = start_dt.date()
    sched = await get_unified_schedule(chat_id, target_date)

    time_conflicts = []
    # Check Google Calendar events overlap
    for ev in sched.get("calendar_events", []):
        ev_start_str = ev.get("start", {}).get("dateTime")
        ev_end_str = ev.get("end", {}).get("dateTime")
        if ev_start_str and ev_end_str:
            try:
                ev_start = datetime.fromisoformat(ev_start_str)
                ev_end = datetime.fromisoformat(ev_end_str)
                if start_dt < ev_end and end_dt > ev_start:
                    time_conflicts.append(f"Google Calendar: '{ev.get('summary')}' ({ev_start.strftime('%H:%M')} - {ev_end.strftime('%H:%M')})")
            except Exception:
                pass

    # Check Reminders within 30 mins
    for cat, title, amt, d_time in sched.get("reminders", []):
        if d_time:
            rem_dt = datetime.combine(target_date, d_time, tzinfo=IST)
            if abs((rem_dt - start_dt).total_seconds()) < 1800:
                time_conflicts.append(f"Reminder: '{title}' ({d_time.strftime('%H:%M')})")

    # Day commitments
    day_commitments = [
        f"{c['name']} ({c.get('company') or 'Contact'}): {c['commitment']}" for c in sched.get("commitments", [])
    ]

    return time_conflicts, day_commitments


async def show_range(chat_id, start, end, meeting_only=False, query_text=""):
    now = datetime.now(IST)
    today = now.date()
    try:
        start = _as_date(start)
        end = _as_date(end)
    except (TypeError, ValueError) as exc:
        print("show_range invalid date:", repr(exc), "start=", repr(start), "end=", repr(end))
        await say(chat_id, "Date samajhne mein problem hui. Please 'aaj ke reminders' ya 'kal ke reminders' dobara bolo.")
        return

    single = start == end
    label = day_label(start, today) if single else f"{start:%d %b} to {end:%d %b}"
    is_free_query = bool(re.search(r"\b(free|khali|available|busy|vyast|schedule kya|kya plan|koi kaam)\b", query_text, re.I))

    # If single day, fetch unified schedule across all tiers
    sched = None
    if single:
        sched = await get_unified_schedule(chat_id, start)
        total_items = (len(sched.get("calendar_events", [])) +
                       len(sched.get("reminders", [])) +
                       len(sched.get("commitments", [])) +
                       len(sched.get("tasks", [])))

        if is_free_query:
            if total_items > 0:
                response = f"Bhai, {label} ({start:%d %b}) ko aap poori tarah free nahi ho — aapka schedule yeh hai:\n\n"
                if sched.get("calendar_events"):
                    for ev in sched["calendar_events"]:
                        start_t = format_event_clock_ist(ev.get("start", {}).get("dateTime"))
                        response += f"📅 [Google Calendar] {start_t} - {ev.get('summary')}\n"
                if sched.get("reminders"):
                    for cat, title, amt, d_time in sched["reminders"]:
                        t_str = d_time.strftime("%I:%M %p").lstrip("0") if d_time else "All day"
                        response += f"⏰ [Reminder] {t_str} - {CATEGORIES.get(cat, cat)}: {title}\n"
                if sched.get("commitments"):
                    for comm in sched["commitments"]:
                        comp = f" ({comm['company']})" if comm.get("company") else ""
                        response += f"🤝 [Commitment] **{comm['name']}{comp}**: *{comm['commitment']}*\n"
                if sched.get("tasks"):
                    for t in sched["tasks"]:
                        response += f"📋 [Task] {t['title']} ({t['status']})\n"

                # Check if user mentioned a specific time in their query (e.g. 5 bje / 5 pm for movie)
                time_warning = ""
                queried_time = None
                parsed_q = parse_followup_time(query_text, datetime.now(IST))
                if parsed_q and parsed_q[0] is not None:
                    queried_time = parsed_q[0]
                else:
                    m_t = re.search(r"\b(\d{1,2})\s*(?:bje|baje|pm|am)?\b", query_text, re.I)
                    if m_t:
                        try:
                            h = int(m_t.group(1))
                            if 1 <= h <= 12:
                                if "pm" in query_text.lower() or any(w in query_text.lower() for w in ("shaam", "sham", "evening", "raat")):
                                    if h < 12: h += 12
                                queried_time = time(h, 0)
                        except Exception:
                            pass

                if queried_time and sched.get("calendar_events"):
                    for ev in sched["calendar_events"]:
                        raw_dt = ev.get("start", {}).get("dateTime")
                        if raw_dt:
                            try:
                                ev_dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00")).astimezone(IST)
                                diff_mins = (ev_dt.hour * 60 + ev_dt.minute) - (queried_time.hour * 60 + queried_time.minute)
                                if -60 <= diff_mins <= 120:
                                    ev_clock = ev_dt.strftime("%I:%M %p").lstrip("0")
                                    time_warning = f"\n⚠️ **Schedule Warning:** Aapne {queried_time.strftime('%I:%M %p').lstrip('0')} ka plan poocha hai, lekin {ev_clock} par aapki '{ev.get('summary', 'Meeting')}' scheduled hai, isliye schedule tight/overlap ho sakta hai!\n"
                                    break
                            except Exception:
                                pass

                response += f"{time_warning}\nBaaki ka samay aapka free hai! Kya koi specific time par meeting ya task schedule karna hai?"
                await say(chat_id, response)
                return
            else:
                await say(chat_id, f"Haan bhai, {label} ({start:%d %b}) ko aapka koi bhi meeting, reminder ya commitment scheduled nahi hai. Aap bilkul free ho! 🎉")
                return

    # Regular agenda view
    rows = items_between(chat_id, start, end)
    normalized_rows = []
    for row in rows:
        try:
            due_date = _as_date(row[0])
            due_time = _as_time(row[1])
            normalized_rows.append((due_date, due_time, row[2], row[3], row[4]))
        except (TypeError, ValueError) as exc:
            print("Skipping malformed reminder row:", repr(row), repr(exc))
    rows = normalized_rows
    if meeting_only:
        rows = [row for row in rows if row[2] == "appointments" and re.search(r"meeting|metting|meetting|meetng|appointment|call", str(row[3]), re.I)]

    # If single day and we have unified items, include calendar & commitments in agenda view
    has_sched_items = bool(sched and (sched.get("calendar_events") or sched.get("commitments") or sched.get("tasks")))

    if not rows and not has_sched_items:
        if meeting_only:
            await say(chat_id, "I couldn't find a matching saved meeting in that date range. I won't guess a time.")
        else:
            await say(chat_id, f"Nothing saved for {label.lower() if single else label}.")
        return

    lines = [f"📅 {label}:" if not single else f"📅 {label} ({start:%d %b}):"]
    
    # Append Google Calendar events if single day
    if sched and sched.get("calendar_events"):
        for ev in sched["calendar_events"]:
            start_t = format_event_clock_ist(ev.get("start", {}).get("dateTime"))
            lines.append(f"📅 {start_t} - Google Meet: {ev.get('summary')}")
            
    # Append Reminders
    current = None
    for due_date, due_time, category, title, amount in rows:
        if not single and due_date != current:
            current = due_date
            lines.append(f"\n{day_label(due_date, today)} ({due_date:%d %b}):")
        if due_time:
            over = datetime.combine(due_date, due_time, tzinfo=IST) <= now
            icon = "✅" if over else "⏳"
            when = due_time.strftime("%H:%M")
        else:
            icon = "📌"
            when = "All day"
        line = f"{icon} {when} - {CATEGORIES.get(category, category)}: {title}"
        if amount is not None:
            line += f" | {amount}"
        lines.append(line)

    # Append Contact Commitments if single day
    if sched and sched.get("commitments"):
        lines.append("\n🤝 **Contact Commitments:**")
        for comm in sched["commitments"]:
            comp = f" ({comm['company']})" if comm.get("company") else ""
            lines.append(f"• **{comm['name']}{comp}**: {comm['commitment']}")

    # Append Tasks if single day
    if sched and sched.get("tasks"):
        lines.append("\n📋 **Tasks Due:**")
        for t in sched["tasks"]:
            lines.append(f"• [{t['priority'].upper()}] {t['title']} ({t['status']})")

    await say(chat_id, "\n".join(lines))


async def show_memories(chat_id):
    rows = list_memories(chat_id)
    if not rows:
        await say(chat_id, "I haven't saved anything yet. Try telling me "
                           "'Electricity bill 1200 due on 25 Oct'.")
        return
    lines = ["Here's what I remember:"]
    for category, title, amount, due, due_time in rows:
        line = f"• {CATEGORIES.get(category, category)}: {title}"
        if amount is not None:
            line += f" | {amount}"
        if due:
            line += f" | {due}"
        if due_time:
            line += f" {due_time.strftime('%H:%M')}"
        lines.append(line)
    await say(chat_id, "\n".join(lines))


async def send_contacts_excel(chat_id):
    """Generates an Excel spreadsheet of all contacts & tasks and sends it to Telegram."""
    contacts = get_contact_memories(chat_id)
    tasks = list_memories(chat_id)
    if not contacts and not tasks:
        await say(chat_id, "Abhi aapke paas koi saved contacts ya tasks nahi hain Excel file banane ke liye.")
        return

    excel_bytes = build_contacts_excel(contacts, tasks)
    caption = (
        f"📊 Ye rahi aapki Excel file!\n\n"
        f"Isme aapke {len(contacts)} saved contacts aur {len(tasks)} tasks/reminders successfully export kar diye hain."
    )
    await send_file(
        chat_id=chat_id,
        filename="My_Contacts_and_Tasks.xlsx",
        content_bytes=excel_bytes,
        caption=caption,
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


async def send_smart_pdf(chat_id, user_request):
    """
    Generates a structured executive PDF document based on user prompt, contacts, and memories,
    and sends it directly to Telegram chat.
    """
    contacts = get_contact_memories(chat_id)
    tasks = list_memories(chat_id)
    facts = get_user_facts(chat_id)

    prompt = (
        "You are an expert executive document writer. The user asked for a PDF document.\n"
        f"User request: \"{user_request}\"\n\n"
        f"Available Context:\n"
        f"- Saved Contacts: {len(contacts)} contacts\n"
        f"- Tasks/Reminders: {len(tasks)} items\n"
        f"- Active Life Facts: {[f['fact'] for f in facts[:10]]}\n\n"
        "Generate a structured document JSON strictly with:\n"
        "{\n"
        "  \"title\": \"Clear Document Title\",\n"
        "  \"sections\": [\n"
        "    {\"heading\": \"Executive Summary\", \"paragraphs\": [\"...\"]},\n"
        "    {\"heading\": \"Key Details / Action Items\", \"paragraphs\": [\"...\"]},\n"
        "    {\"heading\": \"Overview Table\", \"table\": [[\"Category / Item\", \"Details\"], [\"Item 1\", \"Value 1\"]]}\n"
        "  ]\n"
        "}"
    )
    try:
        await say(chat_id, "📄 Generating your PDF document...")
        doc_data = await _chat_json(prompt, user_request)
        title = doc_data.get("title") or "PeculiarAI Document"
        sections = doc_data.get("sections") or [{"heading": "Summary", "paragraphs": ["Generated based on your request."]}]
        pdf_bytes = build_styled_pdf(title, sections)
        safe_fname = re.sub(r'[^a-zA-Z0-9_-]', '_', title.strip())[:30] + ".pdf"
        await send_file(
            chat_id=chat_id,
            filename=safe_fname,
            content_bytes=pdf_bytes,
            caption=f"📄 Ye rahi aapki generated PDF file: '{title}'",
            mime_type="application/pdf"
        )
    except Exception as exc:
        print("send_smart_pdf error:", repr(exc))
        await say(chat_id, "PDF generate karne mein thodi samasya aayi. Kripya dobara try karein.")


async def execute_notion_action(chat_id, user_prompt):
    """Executes a Notion note/page creation from user prompt or recent context."""
    notion_info = get_user_integration(chat_id, "notion")
    if not notion_info:
        msg = (
            "Bhai aapka **Notion Workspace** abhi connect nahi hai! 🔗\n\n"
            "Aap niche diye gaye link par jakar 1-click mein apna Notion connect kar sakte ho:\n"
            "👉 https://peculiar-ai.netlify.app/connect.html\n\n"
            "Connect hone ke baad aap mujhe yahan koi bhi notes, meeting summaries ya text forward karoge to main seedhe aapke Notion workspace mein save kar dunga!"
        )
        await say_raw(chat_id, msg)
        log_chat_message(chat_id, "user", user_prompt)
        log_chat_message(chat_id, "assistant", msg)
        return

    token = notion_info["api_key"]
    await say_raw(chat_id, "⏳ Notion mein add kar raha hu...")

    # Extract title and structured summary/notes from prompt and recent turns
    history_turns = get_recent_chat_history(chat_id, limit=5)
    context_text = "\n".join([f"{t['role']}: {t['text']}" for t in history_turns])

    prompt = (
        f"You are PeculiarAI. The user wants to save notes or a summary to their Notion workspace.\n"
        f"User Prompt: {user_prompt}\n"
        f"Recent Chat Context:\n{context_text}\n\n"
        f"Generate a clear, crisp page title and structured notes with bullet points.\n"
        f"Respond ONLY with valid JSON: {{\"title\": \"...\", \"notes\": \"...\"}}"
    )
    try:
        res = await _chat_json(prompt)
        title = res.get("title") or "PeculiarAI Note"
        notes = res.get("notes") or user_prompt
    except Exception:
        title = "PeculiarAI Note"
        notes = user_prompt

    success, msg_detail, page_url = await create_notion_page(token, title, notes)
    if success:
        reply = (
            f"✅ **Notion mein successfully add ho gaya!** 📝\n\n"
            f"📌 **Title:** {title}\n"
            f"🔗 **Link:** {page_url or 'Aapke Notion workspace mein page save ho gaya hai'}"
        )
    else:
        reply = (
            f"⚠️ **Notion update issue:**\n{msg_detail}\n\n"
            f"💡 *Tip:* Notion mein apne kisi page ke 'Share' menu mein jakar integration ko invite/access zaroor karein."
        )
    await say_raw(chat_id, reply)
    log_chat_message(chat_id, "user", user_prompt)
    log_chat_message(chat_id, "assistant", reply)


async def execute_github_action(chat_id: int, text: str):
    """Executes real GitHub queries (repositories, issues, PRs) with ZERO hallucination."""
    clean_low = text.lower().strip()
    gh_info = get_user_integration(chat_id, "github")
    token = gh_info["api_key"] if gh_info else os.getenv("GITHUB_TOKEN")

    # Extract username from url or text if present (e.g. https://github.com/nontech-cpu or nontech-cpu)
    extracted_username = None
    gh_url_match = re.search(r"github\.com/([a-zA-Z0-9_\-]+)", text)
    if gh_url_match:
        extracted_username = gh_url_match.group(1)
    elif re.fullmatch(r"@?[a-zA-Z0-9_\-]{2,39}", text.strip()):
        extracted_username = text.strip().lstrip("@")

    # Check if query is about issues / PRs
    is_issues_query = any(w in clean_low for w in ("issue", "issues", "pr", "prs", "pull request", "pulls"))

    if is_issues_query:
        if not token:
            msg = (
                "Bhai aapka **GitHub Account** abhi connect nahi hai! 🐙\n\n"
                "Aap niche diye gaye link par jakar 1-click mein apna GitHub connect kar sakte ho:\n"
                "👉 https://peculiar-ai.netlify.app/connect.html\n\n"
                "Connect hote hi aap yahan direct issues aur PRs track kar sakte ho!"
            )
            await say_raw(chat_id, msg)
            log_chat_message(chat_id, "user", text)
            log_chat_message(chat_id, "assistant", msg)
            return

        success, issues = await list_github_issues(token)
        if success and issues:
            lines = [f"• #{it.get('number')} **{it.get('title')}** ({it.get('state')}) - {it.get('html_url')}" for it in issues[:7]]
            reply = "🐙 **GitHub Open Issues & Tasks:**\n\n" + "\n".join(lines)
        elif success:
            reply = "🐙 **GitHub:** Aapke pass abhi koi open issue assigned nahi hai!"
        else:
            reply = "⚠️ GitHub issues fetch karne mein issue aaya. Token permissions verify karein."
        await say_raw(chat_id, reply)
        log_chat_message(chat_id, "user", text)
        log_chat_message(chat_id, "assistant", reply)
        return

    # Otherwise: Repositories Query (100% Real Live GitHub Data)
    await say_raw(chat_id, "⏳ GitHub se aapke real repositories fetch kar raha hu...")
    success, repos, resolved_user = await list_github_repos(token=token, username=extracted_username)

    if success and repos:
        user_label = resolved_user or "Aapke"
        lines = []
        for idx, r in enumerate(repos[:15], 1):
            r_name = r.get("name")
            r_url = r.get("html_url")
            r_desc = f" - {r.get('description')}" if r.get("description") else ""
            r_lang = f" `[{r.get('language')}]`" if r.get("language") else ""
            lines.append(f"{idx}. 📁 **[{r_name}]({r_url})**{r_lang}{r_desc}")

        reply = f"🐙 **GitHub Repositories ({user_label}):**\n\n" + "\n".join(lines)
        if len(repos) > 15:
            reply += f"\n\n*Total {len(repos)} repositories found on GitHub.*"
    elif success:
        reply = f"🐙 **GitHub ({resolved_user}):** Is account par abhi koi public repository nahi mili."
    else:
        if not token and not extracted_username:
            reply = (
                "Bhai aapka **GitHub Account** abhi connect nahi hai! 🐙\n\n"
                "Aap connect page par jakar GitHub link kar sakte ho:\n"
                "👉 https://peculiar-ai.netlify.app/connect.html\n\n"
                "Ya mujhe apna GitHub profile link (jaise: `https://github.com/username`) batao, main turant fetch kar dunga!"
            )
        else:
            reply = f"⚠️ GitHub user '{extracted_username or 'account'}' ki repositories fetch nahi ho paayi. Username check karein."

    await say_raw(chat_id, reply)
    log_chat_message(chat_id, "user", text)
    log_chat_message(chat_id, "assistant", reply)


async def extract_and_save_user_facts(chat_id: int, text: str):
    """
    Extracts life updates, schedules, plans, preferences, travel, and personal facts
    from user messages and stores them in user_facts table (ChatGPT Memory style).
    """
    if len(text.strip()) < 8:
        return
    if text.strip().lower() in ("yes", "no", "haan", "nahi", "cancel", "ok", "done", "/start", "/help"):
        return

    prompt = (
        "Extract any permanent personal facts, future plans, travel, habits, preferences, or life events "
        "mentioned by the user in this message.\n\n"
        f"User message: \"{text}\"\n\n"
        "Guidelines:\n"
        "- If user mentions going somewhere, an event, or plan (e.g. 'going to Jaipur today for model event', 'going movie today'): extract as a plan.\n"
        "- If user mentions preferences, work info, people they know, projects: extract as a fact.\n"
        "- If message is just a casual greeting or command with no personal facts/plans, return empty list.\n\n"
        "Return strictly JSON: {\"facts\": [{\"fact\": \"...\", \"category\": \"plan|preference|profile|work\"}]}"
    )
    try:
        data = await _chat_json(prompt, text)
        facts = data.get("facts") or []
        for item in facts:
            f_text = (item.get("fact") or "").strip()
            cat = item.get("category") or "general"
            if f_text and len(f_text) > 3:
                save_user_fact(chat_id, f_text, cat)
    except Exception as exc:
        print("extract_and_save_user_facts error:", repr(exc))


async def send_export(chat_id):
    data = export_data(chat_id)
    if data is None:
        await say(chat_id, "I have no data about you.")
        return
    linked = get_linked_email(chat_id)
    data["linked_email"] = linked[0] if linked else None
    content = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    await send_file(chat_id, "my_data.json", content.encode("utf-8"),
                    "Here is all the data I have about you.")


# ---------- Onboarding ----------

async def ask_persona(chat_id):
    await say(chat_id, PERSONA_TEXT)


async def apply_persona(chat_id, persona):
    set_persona(chat_id, persona)
    set_categories(chat_id, DEFAULTS[persona])
    set_state(chat_id, None)
    names = ", ".join(CATEGORIES[c] for c in DEFAULTS[persona])
    await say(chat_id,
              f"Great, you're set up as {PERSONA_LABELS[persona]}. I'll keep track of: {names}.\n\n"
              "Just tell me things to remember, like 'Electricity bill 1200 due on 25 Oct' "
              "or 'Meeting tomorrow at 5 PM'. Ask 'what's on today?' anytime. "
              "To change what I track, say something like 'stop tracking work'. "
              "You can also send voice notes in your own language.")


async def handle_persona_answer(chat_id, text):
    persona, how = match_persona(text)
    if persona and how == "exact":
        await apply_persona(chat_id, persona)
        return
    if persona and how == "typo":
        set_state(chat_id, "persona_confirm", persona)
        await say(chat_id, "I think there's a spelling mistake. "
                           f"Did you mean {PERSONA_LABELS[persona]}? (yes/no)")
        return
    try:
        guessed = await guess_persona(text)
    except Exception as e:
        print("persona error:", e)
        guessed = None
    if guessed:
        await apply_persona(chat_id, guessed)
        return
    await say(chat_id, "Sorry, I didn't catch that.\n\n" + PERSONA_TEXT)


# ---------- Settings via natural language ----------

async def change_categories(chat_id, on, off):
    if not on and not off:
        await say(chat_id, "Tell me what to change, like 'stop tracking work' or "
                           "'track travel too'. I can track: "
                           + ", ".join(CATEGORIES.values()) + ".")
        return
    enabled = get_categories(chat_id)
    for c in on:
        if c not in enabled:
            enabled.append(c)
    enabled = [c for c in enabled if c not in off]
    set_categories(chat_id, enabled)
    names = ", ".join(CATEGORIES[c] for c in enabled) or "nothing"
    await say(chat_id, f"Done. I'm now tracking: {names}.")


# ---------- Email ----------

async def start_email_link(chat_id, addr):
    if not BOT_EMAIL:
        await say(chat_id, "The email feature is not set up yet.")
        return
    linked = get_linked_email(chat_id)
    if linked and linked[1]:
        verified_addr = linked[0]
        bot_email = BOT_EMAIL or "bot771347@gmail.com"
        await say(chat_id, f"✅ Your email is already linked: {verified_addr}. You can now forward or CC your bills, bookings and tickets to {bot_email}.")
        return
    if not EMAIL_OK.match(addr) or addr == BOT_EMAIL.lower():
        await say(chat_id, "That doesn't look like a valid email address. "
                           "Say 'link my email' to try again.")
        return
    existing = get_link_by_email(addr)
    if existing and existing[0] != chat_id:
        await say(chat_id, "This email is already linked to another account.")
        return
    code = secrets.token_hex(3).upper()
    create_email_link(chat_id, addr, code)
    await say(chat_id,
              f"To verify, send an email FROM {addr} TO {BOT_EMAIL} "
              f"with this code in the subject or body:\n\n{code}\n\n"
              "I'll confirm here once it arrives. After that, CC or forward "
              f"your bills and bookings to {BOT_EMAIL}.")


async def handle_mail(m):
    link = get_link_by_email(m["sender"])
    if link is None:
        return  # unknown sender: ignore
    chat_id, code, verified = link
    reply_lang.set(get_language(chat_id))
    if not m["authenticated"]:
        print("mail ignored (not authenticated):", m["sender"])
        return

    mail_text = (m.get("subject", "") + " " + m.get("body", "")).lower()
    is_website_verification_mail = (
        "verify my email for peculiarai" in m.get("subject", "").lower()
        or "i requested this email-link verification" in mail_text
        or "verification code:" in mail_text and "peculiarai" in mail_text
    )

    if not verified:
        if code.lower() in mail_text:
            verify_email_link(m["sender"])
            # Website-started email linking must update the website UI only.
            # Preserve the existing Telegram notification for Telegram-started linking.
            website_started = False
            try:
                _init_web_verify_db()
                with sqlite3.connect(WEB_VERIFY_DB) as conn:
                    cols = {r[1] for r in conn.execute("PRAGMA table_info(web_email_link_sources)").fetchall()}
                    if cols:
                        row = conn.execute(
                            "SELECT 1 FROM web_email_link_sources WHERE email = ? AND telegram_chat_id = ? AND expires_at > ? LIMIT 1",
                            (m["sender"].strip().lower(), int(chat_id), __import__("time").time()),
                        ).fetchone()
                        website_started = bool(row)
                        if website_started:
                            conn.execute("DELETE FROM web_email_link_sources WHERE email = ? AND telegram_chat_id = ?", (m["sender"].strip().lower(), int(chat_id)))
                            conn.commit()
            except Exception as exc:
                print("website email-link source check failed:", repr(exc))
            # Website verification emails must never notify the Telegram chat,
            # even if the website-source marker is missing or expired.
            if not website_started and not is_website_verification_mail:
                await say(chat_id, f"✅ Email {m['sender']} linked! Now CC or forward "
                                   f"bills, bookings and tickets to {BOT_EMAIL}.")
        return

    # A verification email is only a handshake. Never feed it to the assistant
    # as a memory item or announce it as an ordinary received email.
    if is_website_verification_mail:
        return

    if not has_consent(chat_id):
        return
    text = (m["subject"] + "\n" + m["body"]).strip()
    if not text:
        return
    await say(chat_id, f"📧 Email received: {m['subject'][:60] or '(no subject)'}")
    await handle_memory_text(chat_id, text)


async def mailbox_loop():
    while True:
        try:
            mails = await asyncio.to_thread(fetch_unseen)
            for m in mails:
                await handle_mail(m)
        except Exception as e:
            print("mailbox error:", e)
        # Check frequently so website email-link status normally updates within 5-10 seconds.
        await asyncio.sleep(5)


# ---------- Google connection ----------


async def get_valid_google_connection_by_chat_id(chat_id: int) -> tuple[bool, str | None]:
    """Check if the user has a valid, unrevoked Google connection. Returns (is_connected, email)."""
    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).first()
        if not row:
            return False, None
        db_user_id = int(row[0])
        token = get_google_token(db_user_id)
        if not token or not token.get("refresh_token_enc"):
            return False, None
        raw_refresh = decrypt_token(token["refresh_token_enc"])
        if not raw_refresh:
            return False, None
        # Attempt to refresh token with Google to ensure it's not revoked or expired
        refreshed = await refresh_access_token(raw_refresh)
        if refreshed and refreshed.get("access_token"):
            return True, token.get("email")
        return False, None
    except Exception as exc:
        print("Google connection validation check failed:", repr(exc))
        return False, None


async def start_google_connection(chat_id):
    """Start Google OAuth from an ordinary chat message, e.g. 'connect Google'."""
    is_connected, google_email = await get_valid_google_connection_by_chat_id(chat_id)
    if is_connected:
        await say(
            chat_id,
            f"✅ Your Google account is already connected: {google_email}. You can use your connected Google services here."
        )
        return

    if not has_consent(chat_id):
        await say(chat_id, "Please finish setup and consent first, then ask me to connect Google.")
        return

    try:
        url = build_auth_url(int(chat_id), source="tg")
    except Exception as exc:
        print("Google auth URL error:", exc)
        await say(chat_id, "Google connection is not configured correctly yet. Please try again later.")
        return

    await say(
        chat_id,
        "Sure — connect your Google account using this secure link:\n\n"
        f"{url}\n\n"
        "Google will ask you to approve Drive file creation, Calendar events, and sending email. "
        "Nothing will be uploaded, scheduled, or emailed just by connecting."
    )


def _safe_web_redirect(return_url: str | None, query_string: str) -> str:
    """Ensure redirect URL stays within permitted local or production origins."""
    fallback = os.getenv("FRONTEND_URL", "http://localhost:3000").rstrip("/") + "/connect.html"
    if not return_url:
        target = fallback
    else:
        from urllib.parse import urlparse
        try:
            parsed = urlparse(return_url)
            allowed_hosts = {
                "localhost", "127.0.0.1", "peculiarai.shapebytes.com",
                "tightly-barbell-creamed.ngrok-free.dev"
            }
            extra_frontend = os.getenv("FRONTEND_URL", "").strip()
            if extra_frontend:
                extra_host = urlparse(extra_frontend).hostname
                if extra_host:
                    allowed_hosts.add(extra_host)

            is_netlify = bool(parsed.hostname and parsed.hostname.endswith(".netlify.app"))
            if parsed.hostname in allowed_hosts or is_netlify or (not parsed.netloc and parsed.path):
                target = return_url.split("?")[0].split("#")[0]
            else:
                target = fallback
        except Exception:
            target = fallback
    sep = "&" if "?" in target else "?"
    return f"{target}{sep}{query_string}"


def _google_callback_page(title, message, success=False, redirect_url=None):
    import html as _html
    colour = "#16803c" if success else "#b42318"
    safe_title = _html.escape(title)
    safe_message = _html.escape(message)
    redirect_script = ""
    redirect_link = "<p>You can close this page and return to your chat.</p>"
    if redirect_url:
        safe_url = _html.escape(redirect_url)
        redirect_script = f"""
        <meta http-equiv="refresh" content="2;url={safe_url}">
        <script>setTimeout(function() {{ window.location.href = "{safe_url}"; }}, 1500);</script>
        """
        redirect_link = f"""
        <p style="margin-top:20px;">
          <a href="{safe_url}" style="display:inline-block;padding:10px 20px;background:#4f46e5;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">
            Return to PeculiarAI Website &rarr;
          </a>
        </p>
        <p style="font-size:13px;color:#64748b;">Redirecting automatically in 2 seconds...</p>
        """
    return HTMLResponse(
        f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
        <title>{safe_title}</title>{redirect_script}</head>
        <body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;max-width:620px;margin:12vh auto;padding:24px;line-height:1.6;background:#f8fafc;color:#1e293b;">
        <div style="background:#ffffff;border:1px solid #e2e8f0;border-radius:16px;padding:32px;box-shadow:0 4px 12px rgba(0,0,0,0.05);">
        <h2 style="color:{colour};margin-top:0;">{safe_title}</h2>
        <p style="font-size:15px;color:#334155;">{safe_message}</p>
        {redirect_link}
        </div>
        </body></html>"""
    )


# ---------- Google actions: explicit confirmation required ----------
def format_event_time_ist(dt_str: str | None) -> str:
    """Format Google Calendar / ISO dateTime string into user-friendly IST string."""
    if not dt_str:
        return ""
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt_ist = dt.replace(tzinfo=IST)
        else:
            dt_ist = dt.astimezone(IST)
        return dt_ist.strftime("%d %b, %I:%M %p").replace(" 0", " ")
    except Exception:
        return dt_str[:16]


CONFIRM_PREFIX_RE = re.compile(
    r'^\s*(haan\s*ji|haan\s*bhai|haan|han|haa|ha|yes\s*please|yes|yep|yeah|yup|sure|ok\s*kardo|ok\s*kar\s*do|ok|okay|theek\s*hai|thik\s*hai|sahi\s*hai|kar\s*do|kardo|kr\s*do|krdo|karo|kro|delete\s*kar\s*do|delete\s*kr\s*do|delete\s*kardo|delete\s*krdo|delete\s*karo|delete\s*kr|hata\s*do|hata\s*de|हाँ\s*जी|हाँ|हां|हाँजी|जी\s*हाँ|जी|बिल्कुल|सही|ठीक|कर\s*दो|हटा\s*दो)\b\s*(?:aur|and|lekin|or|,|-)?\s*',
    re.I
)

REJECT_PREFIX_RE = re.compile(
    r'^\s*(nahi\s*bhai|nahi|nahin|na|no|nope|cancel|mat\s*karo|mat\s*kr|rehne\s*do|rahne\s*do|chhoro|नहीं|ना|मत\s*करो|रहने\s*दो|रद्द)\b\s*(?:aur|and|lekin|or|,|-)?\s*',
    re.I
)

FILLER_WORDS = {
    'kar do', 'kardo', 'kr do', 'krdo', 'karo', 'kro', 'rehne do', 'rahne do',
    'bhai', 'please', 'it', 'thanks', 'thank you', 'shukriya', 'dost', 'yaar',
    'cancel it', 'mat karo', 'nahi'
}

def parse_confirmation_and_remainder(text: str) -> tuple[bool | None, str]:
    """Parse whether text starts with affirmative/negative confirmation and extract remaining instruction."""
    m_pos = CONFIRM_PREFIX_RE.match(text)
    if m_pos:
        remainder = text[m_pos.end():].strip()
        return True, remainder
    m_neg = REJECT_PREFIX_RE.match(text)
    if m_neg:
        remainder = text[m_neg.end():].strip()
        # If user explicitly specifies a target time or meeting to cancel/schedule (e.g. 'cancel 5pm meeting'),
        # it is NOT a rejection of the pending action, but a new command!
        if re.search(r'\b(\d{1,2}(:\d{2})?\s*(baje|bje|pm|am)|\d{1,2}:\d{2}|meeting|reminder)\b', remainder, re.I):
            return None, text
        return False, remainder
    return None, text

def clean_remainder_instruction(rem: str) -> str:
    """Filter out polite conversational fillers from remainder instructions."""
    rem_clean = rem.strip(' .,!?')
    if not rem_clean or rem_clean.lower() in FILLER_WORDS:
        return ''
    rem_clean = re.sub(r'^(kar\s*do|kardo|kr\s*do|krdo|bhai|please)\s*(?:aur|and|,|-)?\s*', '', rem_clean, flags=re.I).strip()
    if not rem_clean or rem_clean.lower() in FILLER_WORDS:
        return ''
    if re.search(r'\b(\d{1,2}(:\d{2})?\s*(baje|bje|pm|am)|\d{1,2}:\d{2}|meeting|reminder|call|event|schedule|rkh|rakh|bana|set|dikha|bata|email|drive)\b', rem_clean, re.I):
        return rem_clean
    if len(rem_clean.split()) >= 3:
        return rem_clean
    return ''


async def handle_google_confirmation(chat_id, clean, raw_text=""):
    pending = PENDING_GOOGLE_ACTIONS.get(chat_id)
    if not pending:
        return False
    full_text = raw_text or clean
    yn_parsed, rem_parsed = parse_confirmation_and_remainder(full_text)
    if yn_parsed is not None:
        yn = yn_parsed
    else:
        yn = yes_no(clean)
    if yn is None:
        yn = await classify_yes_no_any_language(clean, "Please confirm if you want to proceed with this Google Calendar / Meet / Email / Drive action.")
    if yn is None:
        await say(chat_id, "Please reply yes to confirm this Google action, or no to cancel it.")
        return True
    PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
    if yn is False:
        await say(chat_id, "Cancelled. Nothing was sent, uploaded, or scheduled.")
        rem_clean = clean_remainder_instruction(rem_parsed)
        if rem_clean:
            await handle_active(chat_id, rem_clean)
        return True
    try:
        kind = pending['kind']
        if kind == 'send_email':
            result = await google_send_email(chat_id, pending['to'], pending['subject'], pending['body'])
            await say(chat_id, f"Email sent successfully to {pending['to']}. (Message ID: {result})")
        elif kind == 'upload_file':
            result = await google_upload_text_file(chat_id, pending['filename'], pending['content'], pending.get('folder_id'))
            await say(chat_id, "File uploaded to Google Drive: " + (result.get('webViewLink') or result.get('name', pending['filename'])))
        elif kind == 'create_meeting':
            result = await google_create_meeting(chat_id, pending['title'], pending['start_iso'], pending['duration_minutes'], pending.get('attendees', []))
            raw_start = result.get('start') if isinstance(result, dict) else None
            if isinstance(raw_start, dict):
                start_val = raw_start.get('dateTime') or raw_start.get('date') or pending['start_iso']
            elif isinstance(raw_start, str):
                start_val = raw_start
            else:
                start_val = pending['start_iso']
            start_fmt = format_event_time_ist(start_val)
            msg = f"Meeting created: {result.get('summary') or pending['title']}\nTime: {start_fmt}"
            if result.get('meet_url'):
                msg += "\nGoogle Meet: " + result['meet_url']
            if result.get('htmlLink'):
                msg += "\nCalendar Link: " + result['htmlLink']
            await say(chat_id, msg)
        elif kind == 'update_meeting':
            result = await google_update_meeting(
                chat_id,
                pending['event_id'],
                new_start_iso=pending.get('new_start_iso'),
                duration_minutes=pending.get('duration_minutes'),
                new_title=pending.get('new_title'),
                attendees=pending.get('attendees')
            )
            start_fmt = format_event_time_ist(result.get('start', {}).get('dateTime') or pending.get('new_start_iso'))
            msg = f"Meeting updated successfully ✅: {result.get('summary')}\nStart: {start_fmt}"
            if result.get('meet_url'):
                msg += f"\nGoogle Meet: {result['meet_url']}"
            if result.get('htmlLink'):
                msg += f"\nCalendar Link: {result['htmlLink']}"
            await say(chat_id, msg)
        elif kind == 'delete_meeting':
            await google_delete_calendar_event(chat_id, pending['event_id'])
            await say(chat_id, f"Meeting '{pending.get('title')}' deleted successfully from your Google Calendar ✅")

        # Check if user had a follow-up instruction in the same message (e.g. "haan aur 6bje rkh new meeting")
        rem_clean = clean_remainder_instruction(rem_parsed)
        if rem_clean:
            await handle_active(chat_id, rem_clean)

        return True
    except Exception as exc:
        print('Google action error:', repr(exc))
        await say(chat_id, "Google action failed. Nothing was confirmed as successful. Please check the connection and try again.")
        return True


async def prepare_google_action(chat_id, r):
    intent = r['intent']
    if intent == 'send_email':
        to, subject, body = r.get('to'), r.get('subject'), r.get('body')
        if not to or not EMAIL_OK.match(to) or not subject or not body:
            await say(chat_id, "Email bhejne ke liye recipient email, subject aur message chahiye. Example: 'Send an email to name@example.com, subject Test, message Hello'.")
            return
        pending = {'kind':'send_email','to':to,'subject':subject,'body':body, 'created_at': datetime.now(IST)}
        PENDING_GOOGLE_ACTIONS[chat_id] = pending
        await say(chat_id, f"Please confirm — I will send this email from your connected Gmail account:\nTo: {to}\nSubject: {subject}\nMessage: {body}\n\nReply yes to send or no to cancel.")
    elif intent == 'upload_file':
        filename, content = r.get('filename'), r.get('content')
        if not filename or not content:
            await say(chat_id, "Drive par file banane ke liye file name aur content batao. Example: 'Create a Drive text file named notes.txt with content: My notes'.")
            return
        folders = await list_drive_folders(chat_id)
        folder = next((f for f in folders if f.get('name','').lower() == 'ai assistant'), None)
        folder_id = folder.get('id') if folder else None
        PENDING_GOOGLE_ACTIONS[chat_id] = {'kind':'upload_file','filename':filename,'content':content,'folder_id':folder_id, 'created_at': datetime.now(IST)}
        folder_name = folder.get('name') if folder else 'My Drive root'
        await say(chat_id, f"Please confirm — upload/create `{filename}` in Google Drive ({folder_name}) with this content:\n{content}\n\nReply yes to upload or no to cancel.")
    elif intent == 'create_meeting':
        title, start_iso = r.get('title'), r.get('start_iso')
        is_connected, _ = await get_valid_google_connection_by_chat_id(chat_id)
        if not is_connected:
            msg = (
                "Google Calendar/Meet meeting schedule karne ke liye aapka Google account connect hona zaroori hai.\n\n"
                "👉 Google connect karne ke liye type karein: 'connect google' ya website par connect karein."
            )
            item = None
            if title and start_iso:
                from datetime import datetime as _dt
                try:
                    dt = _dt.fromisoformat(start_iso)
                    item = {"category": "appointments", "title": title[:100], "due_date": dt.date().isoformat(), "due_time": dt.time().strftime("%H:%M")}
                except Exception:
                    pass
            if item:
                set_state(chat_id, "meeting_offer", json.dumps({"title": title, "item": item}))
                msg += f"\n\nKya tab tak ke liye main '{title}' ka ek Reminder set kar doon? (Reply yes / no)"
            else:
                set_state(chat_id, "meeting_offer", json.dumps({"title": title or "Meeting", "item": None}))
                msg += "\n\nKya tab tak ke liye main iska ek Reminder set kar doon? (Reply yes / no)"
            await say(chat_id, msg)
            return

        if r.get('time_ambiguous'):
            set_state(chat_id, "create_meeting_details", json.dumps({
                "title": title or "Meeting", "duration_minutes": r.get("duration_minutes", 30),
                "attendees": r.get("attendees", [])
            }))
            await say(chat_id, "Kaunse time par meeting schedule karun? (jaise shaam 5 baje ya 6 baje). Exact time bataiye; abhi meeting create nahi hui.")
            return

        title = title or "Meeting"
        if not start_iso:
            set_state(chat_id, "create_meeting_details", json.dumps({
                "title": title, "duration_minutes": r.get("duration_minutes", 30),
                "attendees": r.get("attendees", [])
            }))
            await say(chat_id, "Bilkul. Meeting kis din aur kitne baje rakhun? Duration bhi bata sakte ho, jaise aaj shaam 5 baje, 30 minutes. Main pehle details confirm karunga; abhi event create nahi hua.")
            return
        attendees = r.get('attendees', [])
        time_conflicts, day_commitments = await detect_meeting_conflicts(chat_id, start_iso, r.get('duration_minutes', 30))
        conflict_msg = ""
        if time_conflicts:
            conflict_msg = "⚠️ **Time Conflict Warning:** Is samay par already aapka schedule booked hai:\n" + "\n".join([f"• {c}" for c in time_conflicts]) + "\n\n"
        if day_commitments:
            conflict_msg += "📌 **Existing Commitments:** Us din aapka yeh commitment bhi hai: " + ", ".join(day_commitments) + "\n\n"

        PENDING_GOOGLE_ACTIONS[chat_id] = {'kind':'create_meeting','title':title,'start_iso':start_iso,'duration_minutes':r.get('duration_minutes',30),'attendees':attendees, 'created_at': datetime.now(IST)}
        confirm_text = f"{conflict_msg}Please confirm — create this Google Calendar meeting with a Google Meet link?\nTitle: {title}\nStart: {start_iso} (India time)\nDuration: {r.get('duration_minutes',30)} minutes\nAttendees: {', '.join(attendees) if attendees else 'None'}\n\nReply yes to create or no to cancel."
        await say(chat_id, confirm_text)
    elif intent == 'update_meeting':
        target_title = r.get('target_title')
        target_time = r.get('target_time')
        new_title = r.get('title')
        start_iso = r.get('start_iso')
        is_connected, _ = await get_valid_google_connection_by_chat_id(chat_id)
        if not is_connected:
            await say(chat_id, "Google Calendar meeting update karne ke liye aapka Google account connect hona zaroori hai. Type karein: 'connect google'")
            return
        matches, is_ambig = await google_find_calendar_event(
            chat_id,
            title_query=target_title or (None if target_time else new_title),
            target_time=target_time
        )
        if is_ambig:
            candidates = [f"• '{ev.get('summary')}' on {ev.get('start', {}).get('dateTime', '')[:16]}" for ev in matches[:4]]
            await say(chat_id, "Aapki multiple meetings match ho rahi hain. Kripya exact meeting ka naam ya time batayein:\n" + "\n".join(candidates))
            return
        if not matches:
            await say(chat_id, f"Google Calendar mein matching meeting nahi mili. Please title ya time check karein; koi meeting update ya delete nahi hui.")
            return
        event = matches[0]
        event_id = event['id']
        old_title = event.get('summary') or target_title or 'Meeting'
        final_title = new_title or old_title
        if not start_iso:
            set_state(chat_id, "update_meeting_time", json.dumps({
                "event_id": event_id, "title": final_title, "duration_minutes": r.get("duration_minutes", 30)
            }))
            await say(chat_id, f"Found meeting: '{old_title}'. Is meeting ko kis din aur kitne baje move karna hai? (jaise 'aaj 5 PM' ya 'kal 11:00 AM').")
            return
        pending = {
            'kind': 'update_meeting',
            'event_id': event_id,
            'title': old_title,
            'new_title': final_title,
            'new_start_iso': start_iso,
            'duration_minutes': r.get('duration_minutes', 30),
            'created_at': datetime.now(IST),
        }
        PENDING_GOOGLE_ACTIONS[chat_id] = pending
        title_change_text = f" with new title '{final_title}'" if new_title and new_title.lower() != old_title.lower() else ""
        await say(chat_id, f"Please confirm — reschedule '{old_title}'{title_change_text} to {start_iso} (India time)?\nDuration: {r.get('duration_minutes', 30)} minutes\n(Existing Meet link will be preserved).\n\nReply yes to confirm or no to cancel.")
    elif intent == 'cancel_meeting':
        target_title = r.get('target_title') or r.get('title')
        target_time = r.get('target_time')
        if chat_id in PENDING_GOOGLE_ACTIONS:
            pending = PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
            await say(chat_id, f"Theek hai, '{pending.get('title', 'meeting')}' cancel kar di. Kuch bhi schedule hua.")
            return
        is_connected, _ = await get_valid_google_connection_by_chat_id(chat_id)
        if is_connected:
            matches, is_ambig = await google_find_calendar_event(
                chat_id,
                title_query=target_title,
                target_time=target_time
            )
            if is_ambig:
                candidates = [f"• '{ev.get('summary')}' on {ev.get('start', {}).get('dateTime', '')[:16]}" for ev in matches[:4]]
                await say(chat_id, "Aapki multiple meetings match ho rahi hain. Kripya exact meeting ka naam ya time batayein:\n" + "\n".join(candidates))
                return
            if matches:
                event = matches[0]
                pending = {
                    'kind': 'delete_meeting',
                    'event_id': event['id'],
                    'title': event.get('summary') or target_title or 'Meeting',
                    'created_at': datetime.now(IST),
                }
                PENDING_GOOGLE_ACTIONS[chat_id] = pending
                await say(chat_id, f"Please confirm — delete this meeting from your Google Calendar?\nTitle: {event.get('summary')}\nStart: {event.get('start', {}).get('dateTime', '')}\n\nReply yes to delete or no to cancel.")
                return
        # If Google not connected or event not in Google Calendar, check local reminders
        if await _begin_meeting_deletion(chat_id, r.get('title') or 'meeting'):
            return
        await say(chat_id, f"'{target_title or 'meeting'}' naam ka koi scheduled calendar event ya reminder nahi mila. Kuch delete nahi hua.")



def _state_payload(data):
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        try:
            value = json.loads(data)
            return value if isinstance(value, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def _time_to_text(value):
    return value.strftime("%H:%M") if value else "unspecified time"


def _extract_requested_times(message):
    """Extract exact clock times, including 14:00, 5 PM, '2 baje', and '5 बजे वाली'."""
    low = message.casefold()
    for word, digit in _WORD_NUMBERS.items():
        low = re.sub(rf"(^|\s){re.escape(word)}\s*(baje|bje|bajey|bajay|बजे|o\'?clock|am|pm|pr|par|pe|ko)?(\s|$)", rf"\g<1>{digit} \g<2>\g<3>", low, flags=re.I)

    found = []
    # Prefer HH:MM so the 00 part is not parsed again as a separate time.
    for m in re.finditer(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", low):
        found.append((int(m.group(1)), int(m.group(2))))
    masked = re.sub(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", " ", low)
    for m in re.finditer(r"(?<!\d)(1[0-2]|0?[1-9])\s*(am|pm|baje|bje|bajay|bajey|बजे|बजेवाली|वाली|वाले|वाला|o'clock|oclock)(?:\b|\s|$)", masked):
        hour, meridian = int(m.group(1)), m.group(2)
        if meridian == "pm" and hour < 12: hour += 12
        elif meridian == "am" and hour == 12: hour = 0
        elif re.search(r"\b(pm|shaam|sham|evening|raat|dopahar)\b|शाम|रात|दोपहर", low):
            if hour < 12: hour += 12
        elif re.search(r"\b(am|subah|subha|morning)\b|सुबह", low):
            if hour == 12: hour = 0
        elif 1 <= hour <= 6:
            hour += 12
        found.append((hour, 0))
    # 24-hour shorthand e.g. "14 wale" or "17 wale" is accepted only when a deletion request exists.
    for m in re.finditer(r"(?<!\d)(1[3-9]|2[0-3])\s*(?:wale|waale|vala|wala|वाली|वाले|वाला|baje|bje|बजे)?(?:\b|\s|$)", masked):
        found.append((int(m.group(1)), 0))
    # Bare 1-12 followed by 'wale' commonly means the local clock hour.
    for m in re.finditer(r"(?<!\d)(1[0-2]|0?[1-9])\s*(?:wale|waale|vala|wala|वाली|वाले|वाला)(?:\b|\s|$)", masked):
        hour = int(m.group(1))
        if re.search(r"\b(pm|shaam|sham|evening|raat|dopahar)\b|शाम|रात|दोपहर", low):
            if hour < 12: hour += 12
        elif re.search(r"\b(am|subah|morning)\b|सुबह", low):
            if hour == 12: hour = 0
        elif 1 <= hour <= 6:
            hour += 12
        found.append((hour, 0))
    return sorted(set(found))


def _deletion_time(text):
    low = text.casefold()
    if not re.search(r"(before|pehle|pahle|se pehle|ke pehle|ke phele)", low):
        return None
    times = _extract_requested_times(text)
    if not times:
        return None
    from datetime import time as _time
    return _time(times[0][0], times[0][1])


async def _begin_meeting_deletion(chat_id, clean):
    low = clean.casefold()
    deletion_verb = bool(re.search(
        r"(delete|remove|clear|cancel|hata|htaa|hatao|hata do|hata de|nikal|nikaal|mitao|mita do|रद्द|हटा|हटाओ|हटाएं|हटाए|मिटा|डिलीट|डिलिट|elimina|borrar|supprimer|löschen|删除|削除)",
        low
    ))
    meeting_scope = bool(re.search(
        r"(meeting|meetings|metting|meetting|meetng|appointment|appointments|reminder|reminders|yaad|मीटिंग|मिटिंग|बैठक|isse|ise|isko|use|usko|wo|yeh|ye|ऊपर|upar)",
        low
    ))
    if not (deletion_verb and meeting_scope):
        return False

    # If the user is asking to reschedule, move, change time/title, or create/schedule a replacement,
    # DO NOT intercept as a pure deletion! Let the intent router handle it as update_meeting or compound action.
    reschedule_or_replacement = bool(re.search(
        r"(rakh|rkh|shift|move|reschedule|badal|change|nayi\b|nayee\b|new\b|create\b|jagah\b|badle\b|bana|banao|cancel\s+(krkr|karke)|delete\s+(krkr|karke)|baje\s+(kar|rakh|bana)|pm\s+(kar|rakh|bana)|am\s+(kar|rakh|bana)|and\s+name|title|naam)",
        low
    ))
    if reschedule_or_replacement:
        return False

    # 1. Check if there is an active pending Google action awaiting confirmation
    if chat_id in PENDING_GOOGLE_ACTIONS:
        pending = PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
        await say(chat_id, f"Theek hai, '{pending.get('title', 'meeting')}' cancel kar di. Kuch bhi schedule ya save nahi hua.")
        return True

    # 2. Check if user has a connected Google account with matching calendar events
    is_connected, _ = await get_valid_google_connection_by_chat_id(chat_id)
    if is_connected:
        exact_times = _extract_requested_times(clean)
        target_t = f"{exact_times[0][0]:02d}:{exact_times[0][1]:02d}" if exact_times else None
        title_search = re.sub(
            r"\b(\d{1,2}(:\d{2})?\s*(baje|bje|pm|am)?|\d{1,2}\s*baje|\d{1,2}\s*bje|delete|remove|clear|cancel|hata|htaa|hatao|hata do|hata de|nikal|nikaal|mitao|mita do|रद्द|हटा|हटाओ|हटाएं|हटाए|मिटा|डिलीट|डिलिट|meeting|meetings|wali|vali|ko|par|se|karo|kar do|bhai|mereliye|mere liye|jo|h|hai|usko|iska)\b",
            " ",
            clean,
            flags=re.I
        ).strip()
        matches, is_ambig = await google_find_calendar_event(
            chat_id,
            title_query=title_search if title_search else None,
            target_time=target_t
        )
        if matches and not is_ambig:
            ev = matches[0]
            pending = {
                'kind': 'delete_meeting',
                'event_id': ev['id'],
                'title': ev.get('summary') or 'Meeting',
                'created_at': datetime.now(IST),
            }
            PENDING_GOOGLE_ACTIONS[chat_id] = pending
            formatted_time = format_event_time_ist(ev.get('start', {}).get('dateTime'))
            await say(chat_id, f"Google Calendar meeting mili:\n• {ev.get('summary')} ({formatted_time})\n\nKya is event ko Google Calendar se delete kar doon? Reply yes to delete or no to cancel.")
            return True

    # 3. Check local database reminders
    exact_times = _extract_requested_times(clean)
    if exact_times:
        rows = find_meetings_at_times(chat_id, datetime.now(IST).date(), exact_times, clean)
    else:
        before_time = _deletion_time(clean)
        if before_time:
            rows = find_meetings_before(chat_id, datetime.now(IST).date(), before_time)
        else:
            recent = find_recent_meetings(chat_id, limit=2)
            if recent:
                rows = [recent[0]]
            else:
                set_state(chat_id, "delete_meetings_target", json.dumps({"date": datetime.now(IST).date().isoformat(), "source": clean}))
                await say(chat_id, "Kaunse meeting reminders delete karne hain? Exact time batao (jaise '5 baje' ya 17:00). Abhi kuch delete nahi hua.")
                return True

    if not rows:
        await say(chat_id, "Koi matching meeting event ya saved reminder nahi mila. Kuch delete nahi hua.")
        return True
    preview = [f"• {row[2]:%d %b} {row[3].strftime('%H:%M')}: {row[1]}" for row in rows]
    payload = {"ids": [int(row[0]) for row in rows], "count": len(rows)}
    set_state(chat_id, "delete_meetings_confirm", json.dumps(payload))
    await say(chat_id, "Ye exact saved meeting reminder mil raha hai:\n" + "\n".join(preview) + "\n\nInhe delete kar doon? Abhi kuch delete nahi hua. Haan/yes = delete, nahi/no = cancel.")
    return True



async def _handle_meeting_offer(chat_id, clean, data):
    yn = yes_no(clean)
    payload = _state_payload(data)
    if yn is False:
        set_state(chat_id, None)
        await say(chat_id, "Theek hai — meeting reminder nahi banaya. Kuch save nahi hua.")
        return True
    if yn is not True:
        await say(chat_id, "Bas haan ya nahi batao: kya main meeting ke liye reminder set karun?")
        return True

    item = payload.get("item")
    if isinstance(item, dict) and item.get("due_date") and item.get("due_time"):
        # JSON state may turn dates/times into strings; normalize safely.
        from datetime import date as _date, time as _time
        try:
            item["due_date"] = _date.fromisoformat(str(item["due_date"]))
            item["due_time"] = _time.fromisoformat(str(item["due_time"])[:5])
        except (ValueError, TypeError):
            item = None
        if item:
            set_state(chat_id, None)
            await save_item_flow(chat_id, item)
            return True

    set_state(chat_id, "meeting_time", json.dumps({"title": payload.get("title") or "Meeting"}))
    await say(chat_id, "What date and time is the meeting? For example: today at 2:30 PM or tomorrow at 11:00. Nothing has been saved yet.")
    return True


async def _handle_meeting_time_followup(chat_id, clean, data):
    payload = _state_payload(data)
    parsed = parse_followup_time(clean, datetime.now(IST))
    if not parsed:
        if re.search(r"\b(cancel|cancel karo|rehne do|nahi|no)\b", clean.lower()):
            set_state(chat_id, None)
            await say(chat_id, "Theek hai, reminder cancel kar diya. Kuch save nahi hua.")
        else:
            await say(chat_id, "Time samajh nahi aaya. Aise bhejo: 2:30 PM, 14:30, ya kal 11 baje.")
        return True
    due_time, due_date = parsed
    item = {"category": "appointments", "title": str(payload.get("title") or "Meeting")[:100],
            "amount": None, "due_date": due_date, "due_time": due_time}
    set_state(chat_id, None)
    await save_item_flow(chat_id, item)
    return True


async def _handle_delete_meetings_confirmation(chat_id, clean, data, raw_text=""):
    full_text = raw_text or clean
    yn_parsed, rem_parsed = parse_confirmation_and_remainder(full_text)
    if yn_parsed is not None:
        yn = yn_parsed
    else:
        yn = yes_no(clean)
    if yn is None:
        await say(chat_id, "Delete karne ke liye haan/yes, cancel karne ke liye nahi/no bolo. Abhi kuch delete nahi hua.")
        return True
    payload = _state_payload(data)
    set_state(chat_id, None)
    if yn is False:
        await say(chat_id, "Cancelled. Koi meeting reminder delete nahi hua.")
        rem_clean = clean_remainder_instruction(rem_parsed)
        if rem_clean:
            await handle_active(chat_id, rem_clean)
        return True
    deleted = delete_memory_items(chat_id, payload.get("ids", []))
    if deleted:
        rem_clean = clean_remainder_instruction(rem_parsed)
        if rem_clean:
            await say(chat_id, f"Done — {deleted} meeting reminder(s) delete kar diye ✅.")
            await handle_active(chat_id, rem_clean)
            return True
        set_state(chat_id, "updated_reminders_offer", json.dumps({"date": datetime.now(IST).date().isoformat()}))
        await say(chat_id, f"Done — {deleted} meeting reminder(s) delete kar diye. Kya aaj ki updated reminder list dikhaun?")
    else:
        await say(chat_id, "Koi item delete nahi hua. Database mein matching items dobara verify karna hoga.")
    return True


# ---------- Main conversation brain ----------


async def handle_conversational_chat(chat_id, user_text):
    clean = user_text.strip().lower()
    if clean in {"help", "/help", "kya kar sakte ho", "commands", "menu"}:
        await say(chat_id, HELP_TEXT)
        return

    # Check if asking for an Excel / CSV / document file
    if re.search(r"\b(excel|csv|sheet|spreadsheet)\b", clean) or ("file" in clean and any(w in clean for w in ("bhej", "bhejo", "bana", "banao", "de", "dede", "export", "send", "download", "tayyar", "taiyar", "do"))):
        await send_contacts_excel(chat_id)
        return

    if is_recent_document_query(chat_id, user_text):
        doc = RECENT_DOCS[chat_id]
        history = list(RECENT_CHAT_TURNS.get(chat_id, []))
        reply = await answer_document_query(doc["filename"], doc["content"], user_text, history, get_language(chat_id))
        record_chat_turn(chat_id, "user", user_text)
        record_chat_turn(chat_id, "assistant", reply)
        await say_raw(chat_id, reply)
        return

    # Check if asking for PDF document
    if re.search(r"\b(pdf)\b", clean) and any(w in clean for w in ("bhej", "bhejo", "bana", "banao", "de", "dede", "export", "send", "download", "tayyar", "taiyar", "do", "karo", "kr")):
        await send_smart_pdf(chat_id, user_text)
        return

    # Check if asking to schedule, host, or set up a meeting:
    if any(w in clean for w in ("meeting", "schedule", "meet link", "google meet", "calendar")) and any(w in clean for w in ("set", "rakh", "rkh", "bana", "kar", "kr", "host", "karo", "do", "bhejo", "arrange")):
        await handle_active(chat_id, user_text)
        return

    # Load persistent chat history from PostgreSQL
    history_turns = get_recent_chat_history(chat_id, limit=8)
    history_context = ""
    if history_turns:
        history_context = "RECENT CONVERSATION HISTORY (From PostgreSQL Database):\n" + "\n".join([
            f"- {t['role']}: {t['text'][:120]}" for t in history_turns[-5:]
        ]) + "\n\n"

    # Load user life context facts & plans (ChatGPT style memory)
    user_facts = get_user_facts(chat_id, limit=20)
    facts_context = ""
    if user_facts:
        facts_context = "USER'S PERMANENT LIFE CONTEXT, PLANS & HABITS (ChatGPT-style Memory):\n" + "\n".join([
            f"- [{f['category'].upper()}] {f['fact']}" for f in user_facts
        ]) + "\n\n"

    doc_context = ""
    if chat_id in RECENT_DOCS:
        doc = RECENT_DOCS[chat_id]
        doc_context = f"User recently uploaded document: '{doc['filename']}'.\n\n"

    # Load permanent contacts and items from PostgreSQL
    contacts = get_contact_memories(chat_id)
    contacts_context = ""
    if contacts:
        contacts_context = "USER'S PERMANENT SAVED CONTACTS & NETWORKING MEMORY (NEVER FORGET THESE):\n" + "\n".join([
            f"- {c['name']} (Company: {c['company']}, Role: {c['role_title']}): Email: {c['email']}, Phone: {c['phone']}, Met At: {c['met_at']}, Commitments/Action Items: '{c['commitments']}', Notes: '{c['context_notes']}'"
            for c in contacts
        ]) + "\n\n"

    # Episodic past conversation search if user mentions any contact
    matched_c = find_matching_contact(user_text, contacts) if contacts else None
    episodic_context = ""
    if matched_c:
        first_n = matched_c["name"].split()[0]
        past_turns = get_chat_turns_mentioning_keyword(chat_id, first_n, limit=8)
        if past_turns:
            episodic_context = f"RELEVANT PAST CONVERSATIONS ABOUT {matched_c['name'].upper()}:\n" + "\n".join([
                f"- {pt['role']}: {pt['text']}" for pt in past_turns
            ]) + "\n\n"

    saved_items = list_memories(chat_id)
    items_context = ""
    if saved_items:
        items_context = "USER'S SAVED ITEMS & REMINDERS:\n" + "\n".join([
            f"- {CATEGORIES.get(it[0], it[0])}: {it[1]}" + (f" (Due: {it[3]})" if it[3] else "")
            for it in saved_items[:8]
        ]) + "\n\n"

    system = (
        "You are PeculiarAI, an empathetic, hyper-competent personal AI assistant on Telegram with persistent memory.\n\n"
        + facts_context
        + contacts_context
        + episodic_context
        + items_context
        + doc_context
        + history_context
        + "COGNITIVE MEMORY & CONTINUITY GUIDELINES (Like ChatGPT Memory):\n"
        "1. Proactively connect context between different messages and conversations! For example, if the user previously mentioned going to a movie today or meeting someone, and now says they are traveling to Jaipur or doing something else, intelligently observe and ask: 'Aap Jaipur ja rahe ho? To kya movie ka plan cancel ho gaya?'\n"
        "2. If user mentions a contact (Rahul, Ananya, etc.), remember all commitments, phone, company, and networking history.\n"
        "3. NEVER hallucinate that you cannot perform an action or that you will 'send in 1-2 minutes'. If user asks for an action or file, execute it.\n"
        "4. Always reply in natural, friendly Hinglish (Roman English script, e.g. 'Haan bhai, bilkul note kar liya!'). Never use Devanagari script unless user explicitly types in Devanagari.\n"
        "5. Keep responses concise (1-3 sentences) unless an in-depth breakdown was requested.\n"
        "6. STRICT ZERO-HALLUCINATION RULE FOR INTEGRATIONS: NEVER fabricate, invent, or guess GitHub repositories (e.g. 'awesome-python', 'crypto-tracker', 'web-scraper'), Notion pages, or files. If the user asks for repositories or tools data, fetch them via official integrations or ask for their username. NEVER make up fake project names.\n"
        "7. TEMPORAL & SCHEDULE REASONING: When the user asks about their schedule, plans, or whether they are free on a specific day (e.g. 'kya main Tuesday ko free hu?'), ALWAYS inspect the user's saved contacts commitments, items, and facts! If there is a commitment (e.g. 'Ananya ko model/module bhejna hai Tuesday ko'), tell the user clearly: 'Aap Tuesday ko poori tarah free nahi ho, aapka Ananya Roy (Elevate Ventures) ko module bhejna hai. Baaki time aapka free hai.' NEVER say 'mere ko nahi pata' when the information is present in the context!\n"
        "8. STRICT ZERO-HALLUCINATION FOR MEETINGS & CALENDAR: NEVER claim that a meeting has been scheduled, created, or emailed ('meeting set kar diya hai', 'invite email pe bhej diya hai') in casual chat! Meetings and Google Meet links can ONLY be created when the Google Calendar tool runs and confirms. If the user asks to schedule, change time, or create a link, you must NOT pretend it was completed. Instead, ask for missing details or tell them to confirm the schedule.\n"
        "9. CONTACT COMMITMENTS & DELIVERABLES: If the user asks what they need to send, give, or do for a contact (e.g. 'Ananya ko kya bhejna tha?'), NEVER hallucinate or draft an apology message! Answer directly from the contact's commitments and past notes in the context (e.g. 'Aapko Ananya Roy (Elevate Ventures) ko module bhejna tha!')."
    )
    try:
        data = await _chat_json(system + ' Return JSON only: {"reply": "<concise natural reply>"}', user_text)
        reply = (data.get("reply") or "").strip()
        if reply:
            record_chat_turn(chat_id, "assistant", reply)
            await say(chat_id, reply)
            return
    except Exception as exc:
        print("conversational chat error:", repr(exc))
    fallback_reply = "Main aapki reminders, meetings, documents, aur contacts track karne me madad karta hu. Aap bataiye kya karna hai?"
    record_chat_turn(chat_id, "assistant", fallback_reply)
    await say(chat_id, fallback_reply)


async def handle_active(chat_id, text):
    # Fast local response for greetings: avoid a needless Groq round-trip.
    if re.fullmatch(r"\s*(hi+|hello+|heello+|hey+|hlo+|hii+|namaste|namaskar|salam|salaam|adaab|good morning|good evening|good afternoon)[!. ]*\s*", text, re.I):
        greet_reply = "Namaste! Main aapka personal assistant hu. Aap reminder, meeting schedule, ya bill tracking ke baare me bataiye!"
        record_chat_turn(chat_id, "assistant", greet_reply)
        await say(chat_id, greet_reply)
        return

    clean_low = text.lower()
    trimmed = text.strip()

    # Feature Flag: AGENT_CORE=v2 (Native Tool-Calling Agent Loop)
    if os.getenv("AGENT_CORE", "v1").lower() == "v2":
        try:
            from agent.loop import run_agent_turn
            res = await run_agent_turn(chat_id, text)
            await say(chat_id, res["reply"])
            return
        except Exception as exc:
            print("AGENT_CORE v2 fallback to v1 on error:", repr(exc))

    # 0. Check if user sent a raw Notion or GitHub token to link
    if (trimmed.startswith("secret_") or trimmed.startswith("ntn_")) and len(trimmed) > 20:
        is_valid, name = await validate_notion_token(trimmed)
        if is_valid:
            save_user_integration(chat_id, "notion", trimmed)
            msg = (
                f"✅ **Notion Workspace successfully connect ho gaya!** 📝\n"
                f"Workspace: **{name}**\n\n"
                f"Ab aap mujhe kabhi bhi bol sakte ho:\n"
                f"• *'ye summary mere Notion mein add kar de'*\n"
                f"• *'meeting notes Notion par bhej de'*"
            )
            await say_raw(chat_id, msg)
            log_chat_message(chat_id, "assistant", msg)
            return
        else:
            await say_raw(chat_id, f"⚠️ Notion token validation failed: {name}. Kripya check karein ki token sahi hai.")
            return

    if (trimmed.startswith("ghp_") or trimmed.startswith("github_pat_")) and len(trimmed) > 20:
        is_valid, login = await validate_github_token(trimmed)
        if is_valid:
            save_user_integration(chat_id, "github", trimmed)
            msg = (
                f"✅ **GitHub successfully connect ho gaya!** 🐙\n"
                f"User: **@{login}**\n\n"
                f"Ab aap mujhe kabhi bhi bol sakte ho:\n"
                f"• *'mere GitHub issues check kar'*\n"
                f"• *'open PRs dikha'*"
            )
            await say_raw(chat_id, msg)
            log_chat_message(chat_id, "assistant", msg)
            return
        else:
            await say_raw(chat_id, f"⚠️ GitHub token validation failed: {login}. Kripya check karein ki token sahi hai.")
            return

    # Notion Actions Trigger
    if "notion" in clean_low and any(w in clean_low for w in ("add", "daal", "dal", "save", "note", "notes", "page", "write", "bana", "bna", "bnaa", "banao", "create", "sync", "send", "kr", "karo", "kar", "me", "mein")):
        await execute_notion_action(chat_id, text)
        return

    # GitHub Actions Trigger (Catches github, girhub, repo, repositories, github.com links)
    is_github_query = (
        any(w in clean_low for w in ("github", "girhub", "githun", "guthub", "git hub", "github.com"))
        or any(w in clean_low for w in ("repository", "repositories", "repo", "repos"))
        or "github.com/" in clean_low
    )
    if is_github_query:
        await execute_github_action(chat_id, text)
        return

    # Meeting Link Request Trigger (e.g. "meeting ka link ya de", "yhaa bhjdo link", "google meet link bhejo")
    is_link_query = bool(
        re.search(r"\b(link|meet\s*link|google\s*meet)\b", clean_low)
        and any(w in clean_low for w in ("bhej", "bhj", "bhjdo", "bhejo", "de", "dede", "dedo", "send", "give", "share", "yha", "yhaa", "kaha", "kahan", "do", "bhai", "bhy"))
    )
    if is_link_query:
        is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
        if is_conn:
            now_iso = datetime.now(IST).isoformat()
            try:
                events = await google_list_calendar_events(chat_id, time_min_iso=now_iso, max_results=5)
                meet_events = [ev for ev in events if ev.get('meet_url')]
                if meet_events:
                    ev = meet_events[0]
                    start_fmt = format_event_time_ist(ev.get('start', {}).get('dateTime') or '')
                    msg = f"Aapki upcoming meeting '{ev.get('summary', 'Meeting')}' ({start_fmt}) ka Google Meet link yeh raha:\n🔗 {ev['meet_url']}"
                    await say(chat_id, msg)
                    return
                else:
                    await say(chat_id, "Aapke Google Calendar mein abhi koi upcoming meeting with Google Meet link nahi mili. Agar aap chahein toh mujhe boliye: 'Monday shaam 5 baje meeting schedule kar do', aur main instant Google Meet link generate kar dunga!")
                    return
            except Exception as exc:
                print("Error fetching meet link from calendar:", repr(exc))
        else:
            await say(chat_id, "Google Meet link fetch karne ke liye aapka Google account connect hona zaroori hai. Type karein 'connect google' ya connect.html portal par login karein.")
            return

    # Check if this message is a meeting follow-up like "monday 5 bje" or "tuesday 5pm" when recent context was about a meeting
    parsed_time_active = parse_followup_time(text, datetime.now(IST))
    if parsed_time_active and parsed_time_active[0] is not None:
        history = list(RECENT_CHAT_TURNS.get(chat_id, []))
        last_bot_turn = next((t["content"] for t in reversed(history) if t["role"] == "assistant"), "").lower()
        if any(w in last_bot_turn for w in ("meeting", "google meet", "calendar", "schedule", "details confirm", "kis din")):
            due_time, due_date = parsed_time_active
            start_dt = datetime.combine(due_date, due_time, tzinfo=IST)
            await prepare_google_action(chat_id, {
                "intent": "create_meeting",
                "title": "Meeting",
                "start_iso": start_dt.isoformat(),
                "duration_minutes": 30,
                "attendees": []
            })
            return

    # 1. PDF Generation Trigger
    pdf_request = bool(
        re.search(r"\b(pdf)\b", clean_low) and any(w in clean_low for w in ("bhej", "bhejo", "bana", "bna", "bnaa", "banao", "de", "dede", "export", "send", "download", "give", "karo", "kr"))
    )
    if pdf_request:
        await send_smart_pdf(chat_id, text)
        return

    # 2. Excel / CSV Export Trigger
    excel_request = bool(
        re.search(r"\b(excel|csv|sheet|spreadsheet)\b", clean_low) or
        re.search(r"\b(file|document)\b.*\b(bhej|bhejo|bana|bna|bnaa|banao|de|dede|export|send|download|give)\b", clean_low) or
        re.search(r"\b(bhej|bhejo|bana|bna|bnaa|banao|de|dede|export|send|download|give)\b.*\b(file|document|excel|csv)\b", clean_low)
    )
    if excel_request:
        # Check if user also wanted to save a reminder in the same message
        if any(k in clean_low for k in ("remind", "reminder", "yaad", "save", "bhejna hai", "bhejna h", "module", "send kar")):
            try:
                r_item = await understand(text)
                if r_item.get("intent") == "save_item" and r_item.get("item"):
                    await save_item_flow(chat_id, r_item["item"])
                else:
                    now_ist = datetime.now(IST)
                    ex_prompt = (
                        f"Today is {now_ist.strftime('%A, %Y-%m-%d')}. "
                        "The user message may combine a file request with a reminder/task/commitment. "
                        "Extract any reminder or task to remember into JSON: "
                        '{"title": "short task title", "due_date": "YYYY-MM-DD", "due_time": "HH:MM or null", "category": "appointments"}. '
                        'If no reminder or task is mentioned, return {"title": null}.'
                    )
                    ex_data = await _chat_json(ex_prompt, text)
                    if ex_data and ex_data.get("title") and ex_data.get("due_date"):
                        await save_item_flow(chat_id, {
                            "category": ex_data.get("category") or "appointments",
                            "title": ex_data["title"],
                            "amount": None,
                            "due_date": ex_data.get("due_date"),
                            "due_time": ex_data.get("due_time")
                        })
            except Exception as e:
                print("dual intent parse error:", repr(e))

        # Check if user mentions commitment for an existing contact (e.g. Rahul, Ananya)
        try:
            contacts_list = get_contact_memories(chat_id)
            for c in contacts_list:
                first_name = c["name"].split()[0].lower()
                if len(first_name) >= 3 and first_name in clean_low:
                    clean_comm = text
                    try:
                        c_json = await _chat_json(
                            f"Extract the specific commitment or action item for {c['name']} from this message into a short clean sentence (e.g. '{c['name']} ko module bhejna hai Tuesday ko'). "
                            'Return JSON: {"commitment": "..."}. If none, return {"commitment": null}.',
                            text
                        )
                        if c_json and c_json.get("commitment"):
                            clean_comm = c_json["commitment"]
                    except Exception:
                        pass
                    update_contact_commitment(chat_id, first_name, commitments=clean_comm, context_notes="From Telegram")
        except Exception as exc:
            print("contact commitment update error:", repr(exc))

        await send_contacts_excel(chat_id)
        return

    # 2. Check if text is pasted business card info
    if any(k in clean_low for k in ("company name:", "person's name:", "contact person:", "contact information:")):
        try:
            card_json = await _chat_json(
                'Extract business card contact details strictly into JSON: '
                '{"name": "...", "company": "...", "role_title": "...", "email": "...", "phone": "...", "location": "..."}. '
                'If no person name is found, return {"name": null}.',
                text
            )
            if card_json and card_json.get("name"):
                c_name = card_json.get("name").strip()
                save_contact_memory(
                    chat_id=chat_id,
                    name=c_name,
                    company=(card_json.get("company") or "").strip(),
                    role_title=(card_json.get("role_title") or "").strip(),
                    email=(card_json.get("email") or "").strip(),
                    phone=(card_json.get("phone") or "").strip(),
                    met_at=(card_json.get("location") or "").strip(),
                    context_notes="Pasted contact details",
                    commitments=""
                )
                await say(chat_id, f"✅ Contact permanently saved in memory: {c_name} ({card_json.get('company') or 'Business Contact'})")
        except Exception as exc:
            print("text card parse error:", repr(exc))

    # 3. Contact Commitment / Deliverable Query Handler (e.g. "bhy annaya ko mujhe ky bhjna th", "ananya jo h contact m usko ky bhjna th", "kya bhejna tha")
    is_commitment_q = bool(
        re.search(r"\b(kya|ky|kisko|kisse|what|kaunsa|konsa|yaad|yad|bata|btao|batao)\b", clean_low)
        and any(w in clean_low for w in ("bhejna", "bhjna", "bheje", "bhje", "dena", "dna", "karna", "krna", "commitment", "task", "deliverable", "share", "send"))
    )
    if is_commitment_q:
        contacts_list = get_contact_memories(chat_id)
        matched_c = find_matching_contact(clean_low, contacts_list) if contacts_list else None
        if matched_c:
            comm = matched_c.get("commitments")
            first_n = matched_c["name"].split()[0]
            if not comm:
                # Episodic search in chat_logs
                past_turns = get_chat_turns_mentioning_keyword(chat_id, first_n, limit=10)
                for pt in reversed(past_turns):
                    if pt["role"] == "user":
                        match_item = re.search(r"\b(module|deck|pitch deck|pitch|presentation|report|file|document|design|code|draft|summary|notes|proposal|bill|invoice)\b", pt["text"], re.I)
                        if match_item or any(w in pt["text"].lower() for w in ("bhejna", "bhjna", "dena", "send", "give", "share")):
                            item_name = match_item.group(1) if match_item else "deliverable"
                            comm = f"{matched_c['name']} ko {item_name} bhejna hai"
                            update_contact_commitment(chat_id, first_n, commitments=comm)
                            break
            if comm:
                reply = f"Bhai, **{matched_c['name']}** ({matched_c.get('company', 'Contact')}) ko aapko: **{comm}**!"
                await say(chat_id, reply)
                record_chat_turn(chat_id, "user", text)
                record_chat_turn(chat_id, "assistant", reply)
                log_chat_message(chat_id, "assistant", reply)
                return
            else:
                reply = f"Bhai, {matched_c['name']} ({matched_c.get('company', 'Contact')}) ke sath koi specific commitment ya deliverable record nahi hai. Kya unhe kuch bhejna tha jo main yaad rakhun?"
                await say(chat_id, reply)
                record_chat_turn(chat_id, "user", text)
                record_chat_turn(chat_id, "assistant", reply)
                log_chat_message(chat_id, "assistant", reply)
                return
        elif any(w in clean_low for w in ("contact", "contacts", "kisko", "kisse", "all", "sab")):
            active_comms = [c for c in contacts_list if c.get("commitments")]
            if active_comms:
                lines = [f"• **{c['name']}** ({c.get('company', '')}): {c['commitments']}" for c in active_comms]
                reply = "Aapke contacts ke sath yeh commitments saved hain:\n\n" + "\n".join(lines)
                await say(chat_id, reply)
                record_chat_turn(chat_id, "user", text)
                record_chat_turn(chat_id, "assistant", reply)
                log_chat_message(chat_id, "assistant", reply)
                return

    # 4. Check if user states/establishes a commitment for a contact (e.g. "Ananya ko module bhejna hai Tuesday ko")
    is_q = bool(re.search(r"\b(kya|ky|what|kisko|kisse|kaunsa|konsa|\?)\b", clean_low))
    if not is_q:
        try:
            contacts_list = get_contact_memories(chat_id)
            matched_c = find_matching_contact(clean_low, contacts_list) if contacts_list else None
            has_action = any(w in clean_low for w in ("bhejna", "bhjna", "dena", "dna", "karna", "krna", "module", "deck", "pitch", "report", "call", "send", "give", "share"))
            if matched_c and has_action:
                first_name = matched_c["name"].split()[0].lower()
                clean_comm = text
                try:
                    c_json = await _chat_json(
                        f"Extract the specific commitment, deliverable, or action item for {matched_c['name']} from this message into a short clean sentence (e.g. '{matched_c['name']} ko module bhejna hai Tuesday ko'). "
                        'Return JSON: {"commitment": "..."}. If none, return {"commitment": null}.',
                        text
                    )
                    if c_json and c_json.get("commitment"):
                        clean_comm = c_json["commitment"]
                except Exception:
                    pass
                update_contact_commitment(chat_id, first_name, commitments=clean_comm, context_notes="From Telegram")
        except Exception as exc:
            print("contact commitment update error:", repr(exc))

    # Check if user is asking about recently sent document
    if is_recent_document_query(chat_id, text):
        doc = RECENT_DOCS[chat_id]
        history = list(RECENT_CHAT_TURNS.get(chat_id, []))
        reply = await answer_document_query(doc["filename"], doc["content"], text, history, get_language(chat_id))
        record_chat_turn(chat_id, "user", text)
        record_chat_turn(chat_id, "assistant", reply)
        await say_raw(chat_id, reply)
        return

    # Natural-language trigger: no slash command is required, but must not intercept task requests
    if GOOGLE_CONNECT_RE.search(text) and not is_explicit_calendar_action(text) and not any(w in text.lower() for w in ("meeting", "schedule", "calendar", "email", "send", "draft", "file", "upload")):
        await start_google_connection(chat_id)
        return

    if is_sensitive(text):
        await say(chat_id, "That looks sensitive (OTP, password or card number). "
                           "I never store those, so I ignored it.")
        return
    if await _begin_meeting_deletion(chat_id, text):
        return
    try:
        r = await understand(text)
    except Exception as e:
        print("understand error:", e)
        await say(chat_id, "Sorry, something went wrong. Please try again.")
        return

    intent = r["intent"]
    if intent == "link_google":
        await start_google_connection(chat_id)
    elif intent == "unlink_google":
        from sqlalchemy import text as sql_text
        with engine.connect() as conn:
            row = conn.execute(sql_text("SELECT id FROM users WHERE telegram_chat_id=:c"), {"c": chat_id}).first()
        if row:
            delete_google_token(int(row[0]))
        await say(chat_id, "Google account disconnected from this assistant. You can connect it again anytime.")
    elif intent in {"send_email", "upload_file", "create_meeting", "update_meeting", "cancel_meeting"}:
        try:
            await prepare_google_action(chat_id, r)
        except Exception as exc:
            print("Google prepare error:", repr(exc))
            await say(chat_id, "I couldn't prepare that Google action. Please make sure Google is connected and try again.")
    elif intent == "info_query":
        history = list(RECENT_CHAT_TURNS.get(chat_id, []))
        reply = await answer_conversation_memory(text, history, get_language(chat_id))
        record_chat_turn(chat_id, "assistant", reply)
        await say_raw(chat_id, reply)
    elif intent == "offer_meeting_reminder":

        item = r.get("item")
        payload = {"title": (item or {}).get("title") or "Meeting", "item": item}
        set_state(chat_id, "meeting_offer", json.dumps(payload, default=str))
        await say(chat_id, "Would you like me to set a reminder for this meeting? Reply yes or no. Nothing has been saved yet.")
    elif intent == "ask_meeting_time":
        payload = {"title": r.get("title") or "Meeting"}
        set_state(chat_id, "meeting_time", json.dumps(payload))
        await say(chat_id, "What date and time is the meeting? For example: today at 2:30 PM or tomorrow at 11:00. Nothing has been saved yet.")
    elif intent == "save_item":
        await save_item_flow(chat_id, r["item"])
    elif intent == "not_important":
        await say(chat_id, "I only remember important things like bills, payments, "
                           "bookings, orders, meetings and appointments. "
                           "This one wasn't saved.")
    elif intent == "show_agenda":
        await show_range(chat_id, r["start"], r["end"], meeting_only=bool(r.get("meeting_only")), query_text=text)
    elif intent == "show_saved":
        await show_memories(chat_id)
    elif intent == "change_categories":
        await change_categories(chat_id, r["on"], r["off"])
    elif intent == "change_persona":
        await apply_persona(chat_id, r["persona"])
    elif intent == "link_email":
        linked = get_linked_email(chat_id)
        if linked and linked[1]:
            bot_email = BOT_EMAIL or "bot771347@gmail.com"
            await say(chat_id, f"✅ Your email is already linked: {linked[0]}. You can now forward or CC your bills, bookings and tickets to {bot_email}.")
        else:
            m = EMAIL_FIND.search(text)
            if m:
                await start_email_link(chat_id, m.group(0).strip(".,;").lower())
            else:
                set_state(chat_id, "email")
                await say(chat_id, "Sure. Which email address do you want to link?")
    elif intent == "unlink_email":
        remove_email_link(chat_id)
        await say(chat_id, "Done, your email has been unlinked.")
    elif intent == "export_data":
        await send_export(chat_id)
    elif intent == "delete_data":
        set_state(chat_id, "forget_confirm")
        await say(chat_id, "This will permanently delete everything I know about you "
                           "(settings, linked email and all saved items). "
                           "Type DELETE to confirm, or anything else to cancel.")
    else:
        await handle_conversational_chat(chat_id, text)


async def handle_user_text(chat_id, text, lang_hint=None):
    clean = text.strip()
    if not clean:
        return
    record_chat_turn(chat_id, "user", clean)
    # Background: extract and learn user life facts, habits, and plans
    try:
        task_f = asyncio.create_task(extract_and_save_user_facts(chat_id, clean))
        BG_TASKS.add(task_f)
        task_f.add_done_callback(BG_TASKS.discard)
    except Exception as exc:
        print("fact extraction trigger error:", repr(exc))

    st = get_state(chat_id)
    if st is None:  # pehli baar aaya
        ensure_user(chat_id)
        set_state(chat_id, "consent")
        await say(chat_id, WELCOME_CONSENT)
        return
    state, data = st

    # Top-level command interruptions should ALWAYS be respected and immediately reset state and pending actions!
    clean_lower = clean.lower()
    cmd_token = clean_lower.split()[0].split("@")[0]
    if cmd_token in {"/start", "start"}:
        set_state(chat_id, None)
        PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
        await say(chat_id, "Welcome back!\n\n" + HELP_TEXT)
        return
    if cmd_token in {"/cancel", "cancel"} or clean_lower in {"/cancel", "cancel", "rehne do", "chhoro", "radd karo", "cancel karo", "stop", "रद्द", "मत करो"}:
        set_state(chat_id, None)
        PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
        await say(chat_id, "Cancelled. Kuch save ya schedule nahi hua.")
        return
    if cmd_token in {"/help", "help", "/menu", "menu"}:
        set_state(chat_id, None)
        await say(chat_id, HELP_TEXT)
        return

    # Multi-turn meeting flow. A pending question must never hijack unrelated
    # messages: only a recognizable yes/no answers the reminder offer. Any other
    # message cancels the stale offer and is routed normally (in any language).
    if state == "meeting_offer":
        if is_explicit_calendar_action(clean):
            set_state(chat_id, None)
            state = None
            data = None
        else:
            yn = yes_no(clean)
            if yn is None:
                yn = await classify_yes_no_any_language(
                    clean, "The assistant asked whether to save a reminder for a mentioned meeting."
                )
            if yn is not None:
                reply_lang.set(get_language(chat_id))
                # Normalize a multilingual yes/no into a value understood by the handler.
                await _handle_meeting_offer(chat_id, "yes" if yn else "no", data)
                return
            set_state(chat_id, None)
            state = None
            data = None

    if state == "meeting_time":
        if is_explicit_calendar_action(clean):
            set_state(chat_id, None)
            state = None
            data = None
        else:
            # Only consume this message as a time follow-up if a time was actually
            # parsed, or as a clear cancellation. Otherwise let the normal router
            # interpret it as a fresh request (e.g. "hi" or create a Meet link).
            parsed_time = parse_followup_time(clean, datetime.now(IST))
            yn = yes_no(clean)
            if yn is None and not parsed_time:
                yn = await classify_yes_no_any_language(
                    clean, "The assistant is collecting date/time for a meeting reminder; user may be cancelling."
                )
            if yn is False and not parsed_time:
                set_state(chat_id, None)
                reply_lang.set(get_language(chat_id))
                await say(chat_id, "Okay, the meeting reminder was cancelled. Nothing was saved.")
                return
            if parsed_time:
                reply_lang.set(get_language(chat_id))
                await _handle_meeting_time_followup(chat_id, clean, data)
                return
            # If the user did not provide a time, don't trap them in meeting_time!
            set_state(chat_id, None)
            await handle_active(chat_id, clean)
            return

    if state == "delete_meetings_target":
        if _extract_requested_times(clean):
            set_state(chat_id, None)
            await _begin_meeting_deletion(chat_id, clean + " meeting reminders delete")
            return
        # If the reply is a fresh request rather than target details, don't trap it.
        set_state(chat_id, None)
        await handle_active(chat_id, clean)
        return

    if state == "create_meeting_details":
        if is_recent_document_query(chat_id, clean):
            set_state(chat_id, None)
            doc = RECENT_DOCS[chat_id]
            history = list(RECENT_CHAT_TURNS.get(chat_id, []))
            reply = await answer_document_query(doc["filename"], doc["content"], clean, history, get_language(chat_id))
            record_chat_turn(chat_id, "user", clean)
            record_chat_turn(chat_id, "assistant", reply)
            await say_raw(chat_id, reply)
            return

        if len(clean) < 60 and re.search(r"\b(no|cancel|cancel karo|rehne do|nahi|nahin|chhoro|mat karo)\b|रद्द|मत करो", clean, re.I):
            set_state(chat_id, None)
            await say(chat_id, "Theek hai, meeting create nahi ki. Kuch bhi schedule nahi hua.")
            return

        # Check if user is asking an informational question about previous context/conversation
        clean_low = clean.lower()
        is_context_q = "?" in clean or any(phrase in clean_low for phrase in ("kya naam", "kya bataya", "kya tha", "kya bola", "kya kaha", "what did i", "what was", "which project", "konsa project", "meeting ka title", "title kya", "kya rakhne", "kya rakhne ko")) or ("kya" in clean_low and "bataya" in clean_low)
        if is_context_q and not any(w in clean_low for w in ("baje", "pm", "am", "today", "tomorrow", "kal", "aaj", "parso")):
            history = list(RECENT_CHAT_TURNS.get(chat_id, []))
            reply = await answer_conversation_memory(clean, history, get_language(chat_id))
            record_chat_turn(chat_id, "assistant", reply)
            await say_raw(chat_id, reply)
            return

        # Check if user is actually providing date/time or meeting details
        parsed = parse_followup_time(clean, datetime.now(IST))
        has_time_kw = any(w in clean_low for w in ("baje", "pm", "am", "clock", ":00", ":30", ":15", ":45", "today", "tomorrow", "kal", "aaj", "parso", "shaam", "sham", "evening", "morning", "dopahar", "afternoon", "raat", "night"))
        has_meeting_kw = any(w in clean_low for w in ("meeting", "schedule", "title", "naam", "minute", "minutes", "min", "host", "link"))

        if parsed or has_time_kw or has_meeting_kw:
            context = _state_payload(data)
            # Check for ambiguous time period without exact hour (e.g. "kal shaam")
            if parsed and parsed[0] is None:
                await say(chat_id, "Shaam ko kis time par meeting schedule karun? (jaise shaam 5 baje ya 6 baje). Exact time bataiye; abhi meeting create nahi hui.")
                return
            if not parsed and any(w in clean_low for w in ("shaam", "sham", "evening")):
                await say(chat_id, "Shaam ko kis time par meeting schedule karun? (jaise shaam 5 baje ya 6 baje). Exact time bataiye; abhi meeting create nahi hui.")
                return

            if parsed and parsed[0] is not None:
                from datetime import datetime as _dt
                due_time, due_date = parsed
                start_dt = _dt.combine(due_date, due_time, tzinfo=IST)
                details = {
                    "intent": "create_meeting",
                    "title": context.get("title") or "Meeting",
                    "start_iso": start_dt.isoformat(),
                    "duration_minutes": context.get("duration_minutes") or 30,
                    "attendees": context.get("attendees") or [],
                }
                set_state(chat_id, None)
                await prepare_google_action(chat_id, details)
                return

            try:
                details = await understand(
                    f"Existing meeting context: title='{context.get('title') or 'Meeting'}', duration={context.get('duration_minutes', 30)}. "
                    f"Extract updated meeting details from this message: {clean}. "
                    f"Return start_iso in ISO 8601 with +05:30 timezone."
                )
            except Exception as exc:
                print("meeting details parse error:", repr(exc))
                details = {}

            if details.get("time_ambiguous"):
                await say(chat_id, "Shaam ko kis time par meeting schedule karun? (jaise shaam 5 baje ya 6 baje). Exact time bataiye; abhi meeting create nahi hui.")
                return

            if details.get("start_iso"):
                details["intent"] = "create_meeting"
                details["title"] = details.get("title") or context.get("title") or "Meeting"
                details["duration_minutes"] = details.get("duration_minutes") or context.get("duration_minutes") or 30
                details["attendees"] = details.get("attendees") or context.get("attendees") or []
                set_state(chat_id, None)
                await prepare_google_action(chat_id, details)
                return

        # The message is NOT meeting details (e.g. user said "hi", "aur bhy kesa h", "remidner mere aajke", etc.).
        # Clear the pending state so the user is never trapped, and route to handle_active!
        set_state(chat_id, None)
        await handle_active(chat_id, clean)
        return

    if state == "update_meeting_time":
        if len(clean) < 60 and re.search(r"\b(no|cancel|cancel karo|rehne do|nahi|nahin|chhoro|mat karo)\b|रद्द|मत करो", clean, re.I):
            set_state(chat_id, None)
            await say(chat_id, "Theek hai, meeting update cancel kar diya. Kuch update nahi hua.")
            return
        context = _state_payload(data)
        parsed = parse_followup_time(clean, datetime.now(IST))
        has_time_kw = any(w in clean.lower() for w in ("baje", "pm", "am", "today", "tomorrow", "kal", "aaj", "parso"))
        if not parsed or parsed[0] is None:
            if not has_time_kw:
                # User changed topic or asked something else
                set_state(chat_id, None)
                await handle_active(chat_id, clean)
                return
            await say(chat_id, "Time samajh nahi aaya. Exact time batayein, jaise 'aaj 5 PM' ya 'kal 11:00 AM'.")
            return
        due_time, due_date = parsed
        from datetime import datetime as _dt
        start_dt = _dt.combine(due_date, due_time, tzinfo=IST)
        set_state(chat_id, None)
        pending = {
            'kind': 'update_meeting',
            'event_id': context.get('event_id'),
            'title': context.get('title', 'Meeting'),
            'new_start_iso': start_dt.isoformat(),
            'duration_minutes': context.get('duration_minutes', 30),
            'created_at': datetime.now(IST),
        }
        PENDING_GOOGLE_ACTIONS[chat_id] = pending
        await say(chat_id, f"Please confirm — update '{context.get('title')}' to {start_dt.isoformat()}?\n(Existing Meet link will be preserved).\n\nReply yes to confirm or no to cancel.")
        return


    if state == "updated_reminders_offer":
        yn = yes_no(clean)
        if yn is None:
            yn = await classify_yes_no_any_language(clean, "The assistant asked whether to show the updated reminder list.")
        set_state(chat_id, None)
        if yn is True:
            today = datetime.now(IST).date()
            await show_range(chat_id, today, today)
        elif yn is False:
            await say(chat_id, "Theek hai, nahi dikhata. Thank you!")
        else:
            await handle_active(chat_id, clean)
        return

    if state == "delete_meetings_confirm":
        # Check if user confirmed or issued a new command/redirection
        yn_del, rem_del = parse_confirmation_and_remainder(clean)
        if yn_del is None:
            # If not starting with yes/no, check if user gave a new command e.g. "17:00 bje jo meeting h usko cancel kr"
            if _extract_requested_times(clean) or len(clean.split()) >= 4:
                set_state(chat_id, None)
                await handle_active(chat_id, clean)
                return
            yn_del = await classify_yes_no_any_language(clean, "The assistant asked whether to delete the previewed meeting reminders.")
        if yn_del is None:
            set_state(chat_id, None)
            await handle_active(chat_id, clean)
            return
        reply_lang.set(get_language(chat_id))
        await _handle_delete_meetings_confirmation(chat_id, "yes" if yn_del else "no", data, raw_text=clean)
        return

    # Setup ke baad wale states: user ki language me reply
    if state in ("forget_confirm", "email"):
        reply_lang.set(get_language(chat_id))

    if state == "consent":
        yn = yes_no(clean)
        if yn is None:
            yn = await classify_yes_no_any_language(clean, "The assistant is asking for initial user consent to continue setup.")
        if yn is True:
            give_consent(chat_id)
            set_state(chat_id, "persona")
            await say(chat_id, "Thank you!")
            await ask_persona(chat_id)
        elif yn is False:
            delete_user(chat_id)
            await say(chat_id, "Okay, nothing has been saved. "
                               "Message me anytime if you change your mind.")
        else:
            await say(chat_id, "Please reply yes to continue, or no to cancel.")
        return

    if state == "persona":
        await handle_persona_answer(chat_id, clean)
        return

    if state == "persona_confirm":
        yn = yes_no(clean)
        if yn is None:
            yn = await classify_yes_no_any_language(clean, "The assistant is asking confirmation if the guessed persona is correct.")
        if yn is True and data in DEFAULTS:
            await apply_persona(chat_id, data)
        elif yn is False:
            set_state(chat_id, "persona")
            await say(chat_id, "No problem.\n\n" + PERSONA_TEXT)
        else:
            await handle_persona_answer(chat_id, clean)
        return

    if state == "forget_confirm":
        clean_norm = clean.lower().strip(" .!")
        if clean_norm in {"delete", "डिलीट"}:
            set_state(chat_id, None)
            delete_user(chat_id)
            await say(chat_id, "Done. All your data has been deleted. "
                               "Message me anytime to start fresh.")
            return

        yn = yes_no(clean)
        if yn is None:
            yn = await classify_yes_no_any_language(
                clean, "The assistant asked confirmation to delete user data; user may say no or cancel."
            )
        if yn is False or clean_norm in {"no", "cancel", "nahi", "nahin", "rehne do", "chhoro", "don't delete", "dont delete", "mat karo", "रद्द", "नहीं", "रहने दो"}:
            set_state(chat_id, None)
            await say(chat_id, "Okay, nothing was deleted.")
            return

        # If user changed the topic or sent a new command (e.g. meeting, bill, question):
        set_state(chat_id, None)
        await say(chat_id, "Okay, deletion cancel ho gaya hai. Kuch delete nahi hua.")
        await handle_active(chat_id, clean)
        return

    if state == "email":
        set_state(chat_id, None)
        linked = get_linked_email(chat_id)
        if linked and linked[1]:
            bot_email = BOT_EMAIL or "bot771347@gmail.com"
            await say(chat_id, f"✅ Your email is already linked: {linked[0]}. You can now forward or CC your bills, bookings and tickets to {bot_email}.")
            return
        m = EMAIL_FIND.search(clean)
        if m:
            await start_email_link(chat_id, m.group(0).strip(".,;").lower())
        else:
            await say(chat_id, "That doesn't look like an email address. "
                               "Say 'link my email' to try again.")
        return

    # Normal (active) user
    if not has_consent(chat_id):
        set_state(chat_id, "consent")
        await say(chat_id, WELCOME_CONSENT)
        return
    if not get_persona(chat_id):
        set_state(chat_id, "persona")
        await ask_persona(chat_id)
        return

    # Language: pakka pata chale to badlo, warna purani language chalne do
    detected = lang_hint or detect_text_language(clean)
    stored = get_language(chat_id)
    if detected and detected != stored:
        set_language(chat_id, detected)
        stored = detected
    reply_lang.set(stored)

    if chat_id in PENDING_GOOGLE_ACTIONS:
        pending = PENDING_GOOGLE_ACTIONS[chat_id]
        created_at = pending.get("created_at")
        age = (datetime.now(IST) - created_at).total_seconds() if created_at else 0
        if age > 900:  # Expired after 15 minutes
            PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
        else:
            yn_pending, rem_pending = parse_confirmation_and_remainder(clean)
            if yn_pending is True:
                await handle_google_confirmation(chat_id, "yes", raw_text=clean)
                return
            elif yn_pending is False:
                await handle_google_confirmation(chat_id, "no", raw_text=clean)
                return
            else:
                # If user replied with a new date/time for the pending meeting (e.g. "monday 5 bje" or "tuesday 5pm"):
                if pending.get('kind') == 'create_meeting':
                    parsed_new = parse_followup_time(clean, datetime.now(IST))
                    if parsed_new and parsed_new[0] is not None:
                        new_due_time, new_due_date = parsed_new
                        new_dt = datetime.combine(new_due_date, new_due_time, tzinfo=IST)
                        pending['start_iso'] = new_dt.isoformat()
                        pending['created_at'] = datetime.now(IST)

                        time_conflicts, day_commitments = await detect_meeting_conflicts(chat_id, pending['start_iso'], pending.get('duration_minutes', 30))
                        conflict_msg = ""
                        if time_conflicts:
                            conflict_msg = "⚠️ **Time Conflict Warning:** Is samay par already aapka schedule booked hai:\n" + "\n".join([f"• {c}" for c in time_conflicts]) + "\n\n"
                        if day_commitments:
                            conflict_msg += "📌 **Existing Commitments:** Us din aapka yeh commitment bhi hai: " + ", ".join(day_commitments) + "\n\n"

                        display_time_str = f"{new_dt.strftime('%A, %d %b')} at {new_due_time.strftime('%I:%M %p')}"
                        confirm_text = (
                            f"{conflict_msg}Meeting time update kar diya hai: **{display_time_str}**.\n\n"
                            f"Please confirm — create this Google Calendar meeting with a Google Meet link?\n"
                            f"Title: {pending.get('title', 'Meeting')}\n"
                            f"Start: {pending['start_iso']} (India time)\n"
                            f"Duration: {pending.get('duration_minutes', 30)} minutes\n"
                            f"Attendees: {', '.join(pending.get('attendees', [])) if pending.get('attendees') else 'None'}\n\n"
                            f"Reply yes to create or no to cancel."
                        )
                        await say(chat_id, confirm_text)
                        return

                # User gave an entirely different command; pop pending action and route
                PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
                await handle_active(chat_id, clean)
                return

    if clean.lower() == "/start":
        await say(chat_id, "Welcome back!\n\n" + HELP_TEXT)
        return
    await handle_active(chat_id, clean)


# ---------- Voice ----------

async def process_voice(chat_id, voice):
    try:
        st = get_state(chat_id)
        # Naya user, ya setup adhura: normal flow initialize karega
        if st is None:
            ensure_user(chat_id)
            set_state(chat_id, "consent")
            await say(chat_id, WELCOME_CONSENT)
            return

        reply_lang.set(get_language(chat_id))

        if voice.get("duration", 0) > MAX_VOICE_SECONDS:
            await say(chat_id, f"That voice note is too long. "
                               f"Please keep it under {MAX_VOICE_SECONDS} seconds.")
            return

        transcript, spoken = await transcribe_telegram_voice(voice["file_id"])
        if len(transcript) < 2:
            await say(chat_id, "I couldn't hear that clearly. Please try again or type it.")
            return

        # Sensitive ho to transcript dikhana bhi nahi
        if is_sensitive(transcript):
            await say(chat_id, "That looks sensitive (OTP, password or card number). "
                               "I never store those, so I ignored it.")
            return

        # User ko dikhao ki bot ne kya suna (galat amount/date pakadne ke liye)
        await say_raw(chat_id, f"🎙️ “{transcript}”")
        await handle_user_text(chat_id, transcript, lang_hint=language_from_whisper(spoken))
    except Exception as e:
        print("voice error:", e)
        try:
            await say(chat_id, "Sorry, I couldn't process that voice note. "
                               "Please try again or type it.")
        except Exception as e2:
            print("voice error (reply failed):", e2)


# ---------- Multimodal (Photos & Documents) ----------

def is_recent_document_query(chat_id: int, text: str) -> bool:
    doc = RECENT_DOCS.get(chat_id)
    if not doc:
        return False
    age = (datetime.now(IST) - doc["timestamp"]).total_seconds()
    if age > 7200:  # 2 hours validity
        return False

    low = text.casefold()
    # Fast exclusions
    if re.search(r"\b(plate|agenda|today\'s reminders|aaj ke reminders)\b", low):
        return False
    if re.search(r"\b(bill|bijli|electricity|water|wifi)\b.*\d+", low):
        return False
    if re.search(r"\b(excel|csv|sheet|spreadsheet)\b", low):
        return False
    if re.search(r"\b(bna|bnaa|banao|bana do|bhej|bhejo|create|make|export|download|give)\b", low) and re.search(r"\b(file|document|csv|excel|sheet)\b", low):
        return False
    if doc.get("filename") == "Photo" and not any(w in low for w in ("photo", "tasveer", "image", "pic", "picture")):
        return False

    doc_kw = re.search(r"\b(pdf|report|doc|document|file|attachment|page|points|point|takeaways|summary|summarize|explain)\b|रिपोर्ट|पीडीएफ|डॉक्यूमेंट|फ़ाइल|पॉइंट्स|महत्वपूर्ण|समरी|निचोड़", low)
    ref_kw = re.search(r"(is\s*me\s*se|isme\s*se|ismein\s*se|us\s*me\s*se|usme\s*se|usmein\s*se|upar\s*wali|upar\s*jo|upar\s*bheja|pehle\s*bheja|iski|iske|iska|iss\s*ka|iss\s*ke|iss\s*ki|इसमें\s*se|उसमें\s*se|इसमें\s*से|उसमें\s*से|ऊपर\s*वाली|ऊपर\s*जो)", low)
    clean_fname = re.sub(r"\.[a-zA-Z0-9]+$", "", doc["filename"]).strip().lower()
    name_kw = (len(clean_fname) >= 4 and clean_fname in low)

    return bool(doc_kw or ref_kw or name_kw)


async def process_photo(chat_id, photo_list, caption=""):
    try:
        st = get_state(chat_id)
        if st is None:
            ensure_user(chat_id)
            set_state(chat_id, "consent")
            await say(chat_id, WELCOME_CONSENT)
            return

        reply_lang.set(get_language(chat_id))
        set_state(chat_id, None)

        await say(chat_id, "🔍 Analyzing image...")
        best_photo = photo_list[-1]
        file_id = best_photo["file_id"]

        analysis = await process_telegram_photo(file_id, caption)
        if not analysis:
            await say(chat_id, "I couldn't extract details from that image. Please try another image or type.")
            return

        RECENT_DOCS[chat_id] = {
            "filename": "Photo",
            "content": analysis,
            "type": "image",
            "summary": analysis,
            "timestamp": datetime.now(IST),
        }
        record_chat_turn(chat_id, "user", f"[Sent Photo] {caption or ''}")
        record_chat_turn(chat_id, "assistant", analysis)

        await say_raw(chat_id, f"🖼️ {analysis}")

        # Automatic Business / Visiting Card Detection & Database Persistence
        is_contact_card = any(k in analysis.lower() for k in ("company", "person's name", "job title", "contact information", "phone", "email", "visiting card", "business card"))
        if is_contact_card:
            try:
                card_json = await _chat_json(
                    'Extract business card contact details strictly into JSON: '
                    '{"name": "...", "company": "...", "role_title": "...", "email": "...", "phone": "...", "location": "..."}. '
                    'If no person name is found, return {"name": null}.',
                    analysis
                )
                if card_json and card_json.get("name"):
                    c_name = card_json.get("name").strip()
                    cid = save_contact_memory(
                        chat_id=chat_id,
                        name=c_name,
                        company=(card_json.get("company") or "").strip(),
                        role_title=(card_json.get("role_title") or "").strip(),
                        email=(card_json.get("email") or "").strip(),
                        phone=(card_json.get("phone") or "").strip(),
                        met_at=(card_json.get("location") or "").strip(),
                        context_notes=caption or "",
                        commitments=""
                    )
                    LAST_SCANNED_CONTACT[chat_id] = {
                        "id": cid,
                        "name": c_name,
                        "company": card_json.get("company"),
                        "role_title": card_json.get("role_title"),
                        "email": card_json.get("email"),
                        "phone": card_json.get("phone")
                    }
                    await say(chat_id, f"✅ Contact permanently saved in memory: {c_name} ({card_json.get('company') or 'Business Contact'})")
            except Exception as card_err:
                print("auto save contact from photo error:", repr(card_err))

        try:
            r = await understand(f"User sent image. Details: {analysis}. Caption: {caption or ''}")
            if r.get("intent") == "save_item" and r.get("item"):
                await save_item_flow(chat_id, r["item"])
            elif r.get("intent") == "create_meeting":
                await prepare_google_action(chat_id, r)
        except Exception as exc:
            print("photo intent error:", repr(exc))
    except Exception as e:
        print("photo processing error:", repr(e))
        await say(chat_id, "Sorry, I had trouble processing that image. Please try again or type.")


async def process_document(chat_id, doc, caption=""):
    try:
        st = get_state(chat_id)
        if st is None:
            ensure_user(chat_id)
            set_state(chat_id, "consent")
            await say(chat_id, WELCOME_CONSENT)
            return

        reply_lang.set(get_language(chat_id))
        set_state(chat_id, None)

        file_name = doc.get("file_name", "document")
        file_id = doc["file_id"]
        mime_type = doc.get("mime_type", "")

        await say(chat_id, f"📄 Reading {file_name}...")
        res = await process_telegram_document(file_id, file_name, mime_type, caption)
        if not res or res.get("is_empty"):
            err = (res or {}).get("error") or f"I couldn't extract content from {file_name}."
            await say(chat_id, err)
            return

        text_content = res.get("text", "")
        RECENT_DOCS[chat_id] = {
            "filename": file_name,
            "content": text_content,
            "type": "document",
            "summary": "",
            "timestamp": datetime.now(IST),
        }
        record_chat_turn(chat_id, "user", f"[Sent Document: {file_name}] {caption or ''}")

        summary = await summarize_document_content(file_name, text_content, caption, get_language(chat_id))
        RECENT_DOCS[chat_id]["summary"] = summary
        record_chat_turn(chat_id, "assistant", summary)
        await say_raw(chat_id, summary)

        try:
            low_text = text_content[:2000].lower()
            if any(w in low_text for w in ("due date", "bill amount", "total due", "amount payable", "invoice amount", "बिजली", "बिल")):
                r = await understand(f"This document might be a bill or invoice: {text_content[:800]}")
                if r.get("intent") == "save_item" and r.get("item"):
                    await save_item_flow(chat_id, r["item"])
        except Exception as exc:
            print("doc reminder check error:", repr(exc))

    except Exception as e:
        print("document processing error:", repr(e))
        await say(chat_id, "Sorry, I couldn't process that document. Please try again.")


# ---------- Reminders ----------

HEADS = {
    "day_before": "🔔 Due tomorrow",
    "due_day": "⏰ Due today",
    "15min": "⏳ In 15 minutes",
    "5min": "⚡ In 5 minutes",
}


def reminder_text(label, category, title, amount, due_time):
    line = f"{HEADS.get(label, '🔔 Reminder')}: {CATEGORIES.get(category, category)}: {title}"
    if due_time:
        line += f" at {due_time.strftime('%H:%M')}"
    if amount is not None:
        line += f" | {amount}"
    return line


async def run_reminders():
    now = datetime.now(IST)
    for rid, chat_id, category, title, amount, due_date, due_time, label in get_pending_reminders(now):
        # Event nikal chuka (jaise laptop band tha) to purana reminder mat bhejo
        if event_over(due_date, due_time, now):
            mark_sent(rid)
            continue
        reply_lang.set(get_language(chat_id))
        await say(chat_id, reminder_text(label, category, title, amount, due_time))
        mark_sent(rid)


async def reminder_loop():
    while True:
        try:
            await run_reminders()
        except Exception as e:
            print("reminder error:", e)
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app):
    tasks = [asyncio.create_task(reminder_loop()),
             asyncio.create_task(mailbox_loop())]
    yield
    for t in tasks:
        t.cancel()


# ---------- Temporary website verification via Telegram (not SMS) ----------
WEB_VERIFY_DB = os.getenv("PECULIARAI_WEB_VERIFY_DB", "peculiarai_web_verify.sqlite3")
WEB_BASE_URL = os.getenv("STREAMLIT_BASE_URL", "http://localhost:8501").rstrip("/")
BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
OTP_BOT_TOKEN = os.getenv("TELEGRAM_OTP_BOT_TOKEN", "").strip()
OTP_BOT_USERNAME = os.getenv("TELEGRAM_OTP_BOT_USERNAME", "PeculiarAI_Verify_Bot").strip().lstrip("@")


def _init_web_verify_db():
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS web_verifications (
            token TEXT PRIMARY KEY,
            phone TEXT NOT NULL,
            otp_hash TEXT NOT NULL,
            created_at REAL NOT NULL,
            telegram_chat_id TEXT,
            attempts INTEGER NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0
        )""")
        conn.commit()


def _otp_digest(token, otp):
    return hashlib.sha256(f"{token}:{otp}".encode("utf-8")).hexdigest()


def _create_web_verification(phone):
    import time as _time
    token = secrets.token_urlsafe(20).replace("-", "").replace("_", "")[:28]
    otp = f"{secrets.randbelow(1_000_000):06d}"
    _init_web_verify_db()
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        conn.execute("DELETE FROM web_verifications WHERE (verified = 0 AND created_at < ?) OR (created_at < ?)", (_time.time() - 1800, _time.time() - 86400))
        conn.execute(
            "INSERT INTO web_verifications(token, phone, otp_hash, created_at) VALUES (?, ?, ?, ?)",
            (token, phone, _otp_digest(token, otp), _time.time()),
        )
        conn.commit()
    return token, otp


def _resolve_web_session(verification_id: str, max_age: float = 86400.0) -> int:
    """Validate verification_id and return the associated telegram_chat_id as int."""
    if not verification_id or not isinstance(verification_id, str):
        raise HTTPException(status_code=400, detail="Verification session ID is required")
    token = verification_id.strip()
    _init_web_verify_db()
    import time as _time
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        row = conn.execute(
            "SELECT telegram_chat_id, created_at, verified FROM web_verifications WHERE token = ?",
            (token,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Verification session not found. Please verify again.")
    chat_id_str, created_at, verified = row
    if _time.time() - created_at > max_age:
        raise HTTPException(status_code=400, detail="Verification session expired. Please verify again.")
    if not verified or not chat_id_str:
        raise HTTPException(status_code=403, detail="Complete Telegram verification first")
    return int(chat_id_str)


async def _send_web_otp_to_telegram(chat_id, token):
    """Deliver the temporary code through the separate OTP bot only."""
    import time as _time
    if not OTP_BOT_TOKEN:
        print("TELEGRAM_OTP_BOT_TOKEN is not configured")
        return
    _init_web_verify_db()
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        row = conn.execute(
            "SELECT created_at, verified FROM web_verifications WHERE token = ?",
            (token,),
        ).fetchone()
        if not row or row[1] or _time.time() - row[0] > 600:
            await _send_otp_bot_message(chat_id, "This website verification link has expired. Return to the website and request a new code.")
            return
        cols = {r[1] for r in conn.execute("PRAGMA table_info(web_verifications)").fetchall()}
        if "otp_delivery" not in cols:
            conn.execute("ALTER TABLE web_verifications ADD COLUMN otp_delivery TEXT")
            conn.commit()
        delivery = conn.execute("SELECT otp_delivery FROM web_verifications WHERE token = ?", (token,)).fetchone()
        otp = delivery[0] if delivery else None
        if not otp:
            await _send_otp_bot_message(chat_id, "This verification request is no longer valid. Please request a new code from the website.")
            return
        conn.execute(
            "UPDATE web_verifications SET telegram_chat_id = ?, otp_delivery = NULL WHERE token = ?",
            (str(chat_id), token),
        )
        conn.commit()
    await _send_otp_bot_message(
        chat_id,
        f"Your temporary PeculiarAI website verification code is: {otp}\n\nEnter it on the website within 10 minutes. This is a Telegram-delivered test code, not an SMS."
    )


async def _send_otp_bot_message(chat_id, message):
    if not OTP_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Set TELEGRAM_OTP_BOT_TOKEN in .env")
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{OTP_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": message},
        )
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise RuntimeError("Telegram OTP bot sendMessage failed")


app = FastAPI(lifespan=lifespan)

# CORS: allow the local frontend and the configured production/testing origins.
_allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://peculiarai.shapebytes.com",
    "https://tightly-barbell-creamed.ngrok-free.dev",
]
_extra_origin = os.getenv("FRONTEND_URL", "").strip()
if _extra_origin and _extra_origin not in _allowed_origins:
    _allowed_origins.append(_extra_origin)
_allowed_origins_env = os.getenv("ALLOWED_ORIGINS", "").strip()
if _allowed_origins_env:
    for _o in _allowed_origins_env.split(","):
        _o = _o.strip()
        if _o and _o not in _allowed_origins:
            _allowed_origins.append(_o)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_origin_regex=r"^https://[a-zA-Z0-9-]+\.netlify\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from api_v1 import router as api_v1_router
app.include_router(api_v1_router)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


@app.post("/web/verification/start")
async def web_verification_start(request: Request):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    phone = str(body.get("phone", "")).strip() if isinstance(body, dict) else ""
    if not re.fullmatch(r"\+?[0-9 ()-]{7,20}", phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number")
    if not OTP_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Set TELEGRAM_OTP_BOT_TOKEN in .env")
    if not OTP_BOT_USERNAME:
        raise HTTPException(status_code=503, detail="Set TELEGRAM_OTP_BOT_USERNAME in .env")
    token, otp = _create_web_verification(phone)
    _init_web_verify_db()
    import time as _time
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        # Store the delivery code only until the Telegram deep link is opened; it is cleared on delivery.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(web_verifications)").fetchall()}
        if "otp_delivery" not in cols:
            conn.execute("ALTER TABLE web_verifications ADD COLUMN otp_delivery TEXT")
        conn.execute("UPDATE web_verifications SET otp_delivery = ? WHERE token = ?", (otp, token))
        conn.commit()
    return {"verification_id": token, "telegram_url": f"https://t.me/{OTP_BOT_USERNAME}?start=verify_{token}", "expires_in_seconds": 600}


@app.post("/web/verification/verify")
async def web_verification_verify(request: Request):
    import time as _time
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    token = str(body.get("verification_id", "")).strip() if isinstance(body, dict) else ""
    otp = str(body.get("otp", "")).strip() if isinstance(body, dict) else ""
    if not token or not re.fullmatch(r"\d{6}", otp):
        raise HTTPException(status_code=400, detail="Enter the 6-digit code")
    _init_web_verify_db()
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        row = conn.execute("SELECT otp_hash, created_at, telegram_chat_id, attempts, verified FROM web_verifications WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Verification request not found")
        otp_hash, created_at, chat_id, attempts, verified = row
        if verified:
            return {"verified": True}
        if _time.time() - created_at > 600:
            raise HTTPException(status_code=400, detail="Code expired. Request a new one.")
        if not chat_id:
            raise HTTPException(status_code=400, detail="Open the Telegram bot link and press Start to receive your code")
        if attempts >= 5:
            raise HTTPException(status_code=429, detail="Too many attempts. Request a new code.")
        if not secrets.compare_digest(otp_hash, _otp_digest(token, otp)):
            conn.execute("UPDATE web_verifications SET attempts = attempts + 1 WHERE token = ?", (token,))
            conn.commit()
            raise HTTPException(status_code=400, detail="Incorrect code")
        conn.execute("UPDATE web_verifications SET verified = 1 WHERE token = ?", (token,))
        conn.commit()
    return {"verified": True, "method": "telegram_test_code", "note": "This confirms access to the Telegram account, not ownership of the entered phone number."}


@app.post("/web/email-link/start")
async def web_email_link_start(request: Request):
    """Prepare an email-link verification draft for a Telegram-verified web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    addr = str(body.get("email", "")).strip().lower()
    verification_id = str(body.get("verification_id", "")).strip()

    if not EMAIL_OK.fullmatch(addr):
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    if not BOT_EMAIL:
        raise HTTPException(status_code=503, detail="Email linking is not configured: BOT_EMAIL is missing")
    if addr == BOT_EMAIL.lower():
        raise HTTPException(status_code=400, detail="Use your own email address, not the PeculiarAI inbox")

    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)

    # Check if this account already has a verified email
    linked = get_linked_email(chat_id)
    if linked and linked[1]:
        return {
            "prepared": False,
            "already_linked": True,
            "email": linked[0],
            "message": f"Your email is already linked: {linked[0]}.",
        }

    existing = get_link_by_email(addr)
    if existing and str(existing[0]) != str(chat_id):
        raise HTTPException(status_code=409, detail="This email is already linked to another account")

    code = secrets.token_hex(3).upper()
    create_email_link(chat_id, addr, code)

    # Mark this pending link as website-originated so verification is reported
    # on the website rather than sent as an unsolicited Telegram message.
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS web_email_link_sources (
            email TEXT NOT NULL,
            telegram_chat_id INTEGER NOT NULL,
            verification_id TEXT NOT NULL,
            expires_at REAL NOT NULL,
            PRIMARY KEY (email, telegram_chat_id)
        )""")
        import time as _time
        conn.execute(
            "INSERT OR REPLACE INTO web_email_link_sources (email, telegram_chat_id, verification_id, expires_at) VALUES (?, ?, ?, ?)",
            (addr, chat_id, verification_id, _time.time() + 3600),
        )
        conn.commit()

    from urllib.parse import quote
    subject = "Verify my email for PeculiarAI"
    body_text = (
        "Please verify this email address for my PeculiarAI account.\n\n"
        f"Verification code: {code}\n\n"
        "I requested this email-link verification."
    )
    mailto_url = (
        f"mailto:{quote(BOT_EMAIL, safe='')}?subject={quote(subject)}"
        f"&body={quote(body_text)}"
    )
    return {
        "prepared": True,
        "email": addr,
        "recipient": BOT_EMAIL,
        "subject": subject,
        "body": body_text,
        "mailto_url": mailto_url,
        "expires_in_seconds": 600,
        "message": "Open the prepared email in your mail app and send it from the email address you want to link. The address is linked only after PeculiarAI receives and authenticates the email."
    }


@app.post("/web/email-link/status")
async def web_email_link_status(request: Request):
    """Check email-link status for a Telegram-verified web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    verification_id = str(body.get("verification_id", "")).strip()
    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)

    addr = str(body.get("email", "")).strip().lower()

    # 1. If an explicit email was provided, check only that specific email for this account
    if addr and EMAIL_OK.fullmatch(addr):
        link = get_link_by_email(addr)
        if link and str(link[0]) == str(chat_id):
            if bool(link[2]):
                return {
                    "linked": True,
                    "verified": True,
                    "email": addr,
                    "status": "linked",
                    "message": "Email linked successfully",
                }
            else:
                return {
                    "linked": False,
                    "verified": False,
                    "email": addr,
                    "status": "pending",
                    "message": "Waiting for verification email to arrive",
                }
        return {
            "linked": False,
            "verified": False,
            "email": None,
            "status": "not_linked",
            "message": "This email is not linked to your account",
        }

    # 2. When no email is specified (e.g. on page load), check the account's existing linked email
    account_link = get_linked_email(chat_id)
    if account_link and account_link[1]:
        return {
            "linked": True,
            "verified": True,
            "email": account_link[0],
            "status": "linked",
            "message": "Email linked successfully",
        }

    return {
        "linked": False,
        "verified": False,
        "email": None,
        "status": "not_linked",
        "message": "No email linked",
    }


@app.post("/web/email-link/unlink")
async def web_email_link_unlink(request: Request):
    """Unlink email for a Telegram-verified web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    verification_id = str(body.get("verification_id", "")).strip()
    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)

    remove_email_link(chat_id)
    return {"unlinked": True, "message": "Email unlinked successfully"}


@app.post("/web/google/start")
async def web_google_start(request: Request):
    """Initiate Google OAuth for a Telegram-verified web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    verification_id = str(body.get("verification_id", "")).strip()
    return_url = str(body.get("return_url", "")).strip() or None
    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)
    give_consent(chat_id)

    is_connected, google_email = await get_valid_google_connection_by_chat_id(chat_id)
    if is_connected:
        return {
            "already_connected": True,
            "connected": True,
            "email": google_email,
            "message": f"Your Google account is already connected: {google_email}."
        }

    try:
        auth_url = build_auth_url(chat_id, source="web", return_url=return_url)
        return {"auth_url": auth_url, "already_connected": False}
    except Exception as exc:
        print("web_google_start error:", repr(exc))
        raise HTTPException(status_code=503, detail="Google OAuth is not configured correctly")


@app.post("/web/google/status")
async def web_google_status(request: Request):
    """Retrieve Google connection status for a Telegram-verified web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    verification_id = str(body.get("verification_id", "")).strip()
    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)

    is_connected, google_email = await get_valid_google_connection_by_chat_id(chat_id)
    return {
        "connected": is_connected,
        "email": google_email if is_connected else None,
        "message": "Google connected" if is_connected else "Google not connected",
    }


@app.post("/web/google/disconnect")
async def web_google_disconnect(request: Request):
    """Disconnect Google for a Telegram-verified web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    verification_id = str(body.get("verification_id", "")).strip()
    chat_id = _resolve_web_session(verification_id)

    with engine.connect() as conn:
        user_row = conn.execute(
            text("SELECT id FROM users WHERE telegram_chat_id = :chat_id"),
            {"chat_id": chat_id},
        ).first()
    if user_row:
        delete_google_token(int(user_row[0]))

    return {"disconnected": True, "message": "Google account disconnected successfully"}


@app.post("/web/integrations/status")
async def web_integrations_status(request: Request):
    """Retrieve Notion and GitHub connection statuses for a Telegram-verified web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    verification_id = str(body.get("verification_id", "")).strip()
    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)

    status_data = get_user_integrations_summary(chat_id)
    return {"status": "ok", "integrations": status_data}


@app.post("/web/integrations/save")
async def web_integrations_save(request: Request):
    """Validate, encrypt, and save a third-party token (Notion, GitHub) for a web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    verification_id = str(body.get("verification_id", "")).strip()
    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)

    service = str(body.get("service", "")).strip().lower()
    api_key = str(body.get("api_key", "")).strip()
    config = body.get("config") or {}

    if not api_key:
        raise HTTPException(status_code=400, detail="Token/API Key cannot be empty.")

    if service == "notion":
        is_valid, info = await validate_notion_token(api_key)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid Notion Token: {info}")
        saved = save_user_integration(chat_id, "notion", api_key, json.dumps(config))
        if not saved:
            raise HTTPException(status_code=500, detail="Failed to save Notion token.")

        # Notify user in Telegram / messaging channel
        try:
            confirm_msg = (
                f"🎉 **Notion Workspace successfully connected!** 📝\n\n"
                f"Workspace: **{info}**\n\n"
                f"Ab aap mujhe koi bhi notes, meeting summaries ya tasks forward karoge to main seedhe aapke Notion workspace mein save kar dunga!"
            )
            await say_raw(chat_id, confirm_msg)
            log_chat_message(chat_id, "assistant", confirm_msg)
        except Exception as exc:
            print("Telegram notification dispatch error:", repr(exc))

        return {
            "connected": True,
            "service": "notion",
            "name": info,
            "message": f"Successfully connected to Notion ({info})!"
        }

    elif service == "github":
        is_valid, info = await validate_github_token(api_key)
        if not is_valid:
            raise HTTPException(status_code=400, detail=f"Invalid GitHub Token: {info}")
        saved = save_user_integration(chat_id, "github", api_key, json.dumps(config))
        if not saved:
            raise HTTPException(status_code=500, detail="Failed to save GitHub token.")

        # Notify user in Telegram / messaging channel
        try:
            confirm_msg = (
                f"🎉 **GitHub successfully connected!** 🐙\n\n"
                f"Account: **@{info}**\n\n"
                f"Ab aap mujhe GitHub issues track karne ya open PRs check karne ko bol sakte ho!"
            )
            await say_raw(chat_id, confirm_msg)
            log_chat_message(chat_id, "assistant", confirm_msg)
        except Exception as exc:
            print("Telegram notification dispatch error:", repr(exc))

        return {
            "connected": True,
            "service": "github",
            "name": info,
            "message": f"Successfully connected to GitHub as @{info}!"
        }

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported service '{service}'. Supported: 'notion', 'github'")


@app.post("/web/integrations/disconnect")
async def web_integrations_disconnect(request: Request):
    """Disconnect and delete an integration token for a web session."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    verification_id = str(body.get("verification_id", "")).strip()
    chat_id = _resolve_web_session(verification_id)

    service = str(body.get("service", "")).strip().lower()
    delete_user_integration(chat_id, service)
    return {"disconnected": True, "service": service, "message": f"{service.capitalize()} disconnected successfully"}



@app.get("/web/google/diagnostic")
def web_google_diagnostic():
    """Safe diagnostic endpoint showing the active Google OAuth redirect URI without exposing secrets."""
    try:
        cb_uri = callback_url()
        configured = True
        err = None
    except Exception as exc:
        cb_uri = None
        configured = False
        err = str(exc)

    return {
        "status": "ok",
        "redirect_uri": cb_uri,
        "is_configured": configured,
        "error": err,
    }


@app.get("/google/callback", response_class=HTMLResponse)
async def google_callback(request: Request):
    """Google OAuth redirect endpoint. Saves only an encrypted refresh token."""
    params = request.query_params
    code = params.get("code")
    state = params.get("state")

    # Decode state early to check if initiated from web
    state_data = {}
    is_web = False
    return_url = None
    if state:
        try:
            state_data = verify_state_payload(state)
            is_web = (state_data.get("source") == "web")
            return_url = state_data.get("return_url")
        except Exception:
            pass

    if params.get("error"):
        error_code = params.get("error")
        if is_web:
            target_url = _safe_web_redirect(return_url, f"google_status=error&google_message=Google connection was cancelled or denied ({error_code})")
            return _google_callback_page(
                "Google connection cancelled",
                "You did not approve Google access. Nothing was connected.",
                success=False,
                redirect_url=target_url,
            )
        return _google_callback_page(
            "Google connection cancelled",
            "You did not approve Google access. Nothing was connected.",
            success=False,
        )

    try:
        chat_id = verify_state(state)
        ensure_user(chat_id)
        if is_web and not has_consent(chat_id):
            give_consent(chat_id)

        if not has_consent(chat_id):
            return _google_callback_page(
                "Connection not completed",
                "This chat has not granted consent. Return to the assistant and complete setup first.",
                success=False,
            )

        tokens = await exchange_code(code)
        email = await get_google_email(tokens["access_token"])

        # google_tokens.user_id references users.id, not chat ID.
        with engine.connect() as conn:
            user_row = conn.execute(
                text("SELECT id FROM users WHERE telegram_chat_id = :chat_id"),
                {"chat_id": chat_id},
            ).first()
        if not user_row:
            return _google_callback_page(
                "Connection not completed",
                "I could not find your assistant account. Return to your chat and try again.",
                success=False,
            )
        db_user_id = int(user_row[0])

        refresh_token = tokens.get("refresh_token")
        if not refresh_token:
            previous = get_google_token(db_user_id)
            if previous and previous.get("refresh_token_enc"):
                refresh_token = decrypt_token(previous["refresh_token_enc"])
            else:
                if is_web:
                    target_url = _safe_web_redirect(return_url, "google_status=error&google_message=Google did not return a refresh token. Please reconnect.")
                    return _google_callback_page(
                        "Connection not completed",
                        "Google did not return a refresh token. Please return to the website and try connecting again.",
                        success=False,
                        redirect_url=target_url,
                    )
                return _google_callback_page(
                    "Connection not completed",
                    "Google did not return a refresh token. Please return to your chat and ask me to connect Google again.",
                    success=False,
                )

        encrypted = encrypt_token(refresh_token)
        scopes = " ".join(tokens.get("scope", "").split()) or " ".join([
            "openid",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/calendar.events",
            "https://www.googleapis.com/auth/gmail.send",
        ])
        save_google_token(db_user_id, email, encrypted, scopes)

        if is_web:
            target_url = _safe_web_redirect(return_url, "google_status=connected")
            return _google_callback_page(
                "Google connected successfully",
                f"Connected account: {email}. Your assistant can use the approved Google services when you ask.",
                success=True,
                redirect_url=target_url,
            )

        return _google_callback_page(
            "Google connected successfully",
            f"Connected account: {email}. Your assistant can use the approved Google services when you ask. No file, calendar event, or email has been created yet.",
            success=True,
        )
    except Exception as exc:
        print("Google OAuth callback error:", repr(exc))
        if is_web:
            from urllib.parse import quote
            target_url = _safe_web_redirect(return_url, f"google_status=error&google_message={quote(str(exc)[:100])}")
            return _google_callback_page(
                "Google connection failed",
                "Something went wrong while connecting your account. Return to the website and try again.",
                success=False,
                redirect_url=target_url,
            )
        return _google_callback_page(
            "Google connection failed",
            "Something went wrong while connecting your account. Return to your chat and try again.",
            success=False,
        )


@app.get("/")
def root():
    return {"status": "ok", "app": "PeculiarAI Backend API", "health": "/health"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/web/chat")
async def web_chat(request: Request):
    """Web-only text chat. It never executes account actions or writes user data."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    message = str(body.get("message", "")).strip() if isinstance(body, dict) else ""
    if not message:
        raise HTTPException(status_code=400, detail="Message is required")
    if len(message) > 4000:
        raise HTTPException(status_code=413, detail="Message is too long (maximum 4000 characters)")
    if is_sensitive(message):
        return {"reply": "Please don't send passwords, OTPs, card numbers, or other secrets here. I ignored that message."}

    # Classify first. Until a verified web identity is linked to the existing user
    # record, do not run reminder, email, calendar, memory, or deletion workflows.
    try:
        intent_data = await understand(message)
        intent = intent_data.get("intent", "chat")
    except Exception as exc:
        print("web chat intent error:", repr(exc))
        raise HTTPException(status_code=503, detail="Assistant model is temporarily unavailable")

    if intent != "chat":
        return {"reply": "I understood this as a request to use a PeculiarAI feature, but web actions aren't enabled until this browser account is securely linked to your assistant account. No reminder, email, calendar event, or data change was made. For now, use your existing Telegram bot for that action."}

    history = body.get("history", []) if isinstance(body, dict) else []
    messages = [{
        "role": "system",
        "content": (
            "You are PeculiarAI, a helpful personal assistant. Reply naturally and concisely, "
            "and support English, Hindi, and Hinglish. You may answer questions and have casual "
            "conversation, but do not claim to have created reminders, sent emails, changed data, "
            "or accessed calendar/Drive. If asked to perform an action, explain that web actions "
            "are not enabled yet and no action was taken. Never ask for passwords, OTPs, or card details."
        ),
    }]
    if isinstance(history, list):
        for item in history[-8:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if role in ("user", "assistant") and isinstance(content, str):
                messages.append({"role": role, "content": content[:4000]})
    messages.append({"role": "user", "content": message})

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"},
                json={"model": os.getenv("GROQ_MODEL"), "temperature": 0.4, "messages": messages},
            )
            r.raise_for_status()
            reply = r.json()["choices"][0]["message"]["content"].strip()
            return {"reply": reply[:6000]}
    except Exception as exc:
        print("web chat completion error:", repr(exc))
        raise HTTPException(status_code=503, detail="Assistant model is temporarily unavailable")


@app.post("/telegram/otp-webhook")
async def telegram_otp_webhook(request: Request):
    """Webhook dedicated to the OTP bot; never invokes the main assistant handler."""
    if not OTP_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="OTP bot is not configured")
    update = await request.json()
    message = update.get("message") or {}
    text_value = message.get("text", "").strip()
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id or not text_value:
        return {"ok": True}
    match = re.fullmatch(r"/start(?:@\w+)?\s+verify_([A-Za-z0-9]{10,40})", text_value)
    if match:
        try:
            await _send_web_otp_to_telegram(chat_id, match.group(1))
        except Exception as exc:
            print("OTP bot delivery error:", repr(exc))
            # Do not send a normal assistant welcome from this bot.
    elif text_value.split()[0].split("@")[0] == "/start":
        await _send_otp_bot_message(
            chat_id,
            "PeculiarAI Verification bot. To receive a code, start verification from the PeculiarAI website and open the link it provides."
        )
    return {"ok": True}


@app.post("/telegram/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(None),
):
    if not SECRET or not x_telegram_bot_api_secret_token or not secrets.compare_digest(x_telegram_bot_api_secret_token, SECRET):
        raise HTTPException(status_code=403, detail="Forbidden: Invalid secret token")

    update = await request.json()

    # Telegram retry kare to dobara process nahi karna
    uid = update.get("update_id")
    if uid is not None:
        if uid in SEEN_UPDATES:
            return {"ok": True}
        SEEN_UPDATES.append(uid)

    # Purane messages ke buttons: ab buttons nahi hain
    if "callback_query" in update:
        cq = update["callback_query"]
        await tg("answerCallbackQuery", {"callback_query_id": cq["id"]})
        await say(cq["message"]["chat"]["id"],
                  "I don't use buttons anymore. Just type to me 🙂")
        return {"ok": True}

    message = update.get("message")
    if message:
        chat_id = message["chat"]["id"]
        if "text" in message:
            incoming_text = message["text"].strip()
            await handle_user_text(chat_id, incoming_text)
        elif "voice" in message:
            # Voice me time lagta hai: background me chalao, Telegram ko turant 200 do
            task = asyncio.create_task(process_voice(chat_id, message["voice"]))
            BG_TASKS.add(task)
            task.add_done_callback(BG_TASKS.discard)
        elif "audio" in message:
            task = asyncio.create_task(process_voice(chat_id, message["audio"]))
            BG_TASKS.add(task)
            task.add_done_callback(BG_TASKS.discard)
        elif "photo" in message:
            caption = (message.get("caption") or "").strip()
            task = asyncio.create_task(process_photo(chat_id, message["photo"], caption))
            BG_TASKS.add(task)
            task.add_done_callback(BG_TASKS.discard)
        elif "document" in message:
            caption = (message.get("caption") or "").strip()
            task = asyncio.create_task(process_document(chat_id, message["document"], caption))
            BG_TASKS.add(task)
            task.add_done_callback(BG_TASKS.discard)
        else:
            await say(chat_id, "I can understand text, voice notes, photos, bills, and PDF documents 🙂")

    return {"ok": True}