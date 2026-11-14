import logging
import os
import re
import json
import secrets
import sqlite3
import hashlib
import string
import asyncio
import httpx
from typing import Any, Dict
from collections import deque
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Request, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import smtplib
from email.message import EmailMessage

logger = logging.getLogger("peculiar_main")

import difflib
import db
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
                save_user_integration, get_user_integration, delete_user_integration, get_user_integrations_summary,
                wipe_all_user_memories, get_all_connected_google_users,
                is_user_email_processed, record_processed_user_email, has_user_email_baseline)
from integrations import (validate_notion_token, create_notion_page,
                          validate_github_token, list_github_issues, list_github_repos)
from file_generator import build_contacts_excel, build_styled_pdf
from categories import CATEGORIES, DEFAULTS
from llm import (extract, understand, guess_persona, is_sensitive, parse_followup_time,
                 is_explicit_calendar_action, classify_yes_no_any_language, _WORD_NUMBERS, _chat_json,
                 answer_conversation_memory)
from nlu import yes_no, match_persona, PERSONA_LABELS
from timing import IST, build_plan, fmt_when, event_over
import core.timeutil as timeutil
from inbox import fetch_unseen
from email_intelligence import analyze_incoming_email
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
    fetch_user_inbox_messages,
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
# Pending Google actions are held until explicit yes/no confirmation (Phase 5: Persistent Multi-Turn State)
from state.pending import get_pending_actions_proxy
PENDING_GOOGLE_ACTIONS = get_pending_actions_proxy()

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


# ---------- Texts & Master Wipe State ----------

PENDING_WIPE_CODES: dict[int, dict[str, Any]] = {}

def _sync_send_system_email(to_email: str, subject: str, body: str) -> bool:
    """Sends an outbound transactional/security email via SMTP using BOT_EMAIL credentials."""
    bot_email = os.getenv("BOT_EMAIL")
    app_pw = os.getenv("BOT_EMAIL_APP_PASSWORD")
    if not bot_email or not app_pw:
        logging.warning("send_system_email: Missing BOT_EMAIL or BOT_EMAIL_APP_PASSWORD")
        return False
    try:
        msg = EmailMessage()
        msg["From"] = f"PeculiarAI Security <{bot_email}>"
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=12) as server:
            server.login(bot_email, app_pw)
            server.send_message(msg)
        logging.info(f"send_system_email: Successfully sent email to {to_email}")
        return True
    except Exception as e:
        logging.error(f"send_system_email: Failed sending email to {to_email}: {e}")
        return False

async def send_system_email(to_email: str, subject: str, body: str) -> bool:
    return await asyncio.to_thread(_sync_send_system_email, to_email, subject, body)






WELCOME_CONSENT_BASE = (
    "Hi! I'm your personal assistant. I remember important things like bills, "
    "bookings and meetings, and remind you at the right time.\n\n"
    "A few promises:\n"
    "• I keep only short details (title, amount, date), never your full messages.\n"
    "• I never ask for OTPs, passwords or card numbers.\n"
    "• Your messages and voice notes are processed by an AI provider (Groq) "
    "to understand them. Voice notes are not saved on my side.\n"
    "• You can delete everything anytime.\n\n"
)

WELCOME_CONSENT = WELCOME_CONSENT_BASE + "[Recommended: Please connect and link your Google account to ensure a smooth flow]\n\nShall we continue? Reply yes or no."


async def get_welcome_consent_text(chat_id: int) -> str:
    """
    Builds dynamic onboarding consent greeting:
    - If user has NOT connected Google on web, displays '[Recommended: Please connect and link your Google account to ensure a smooth flow]'
    - If user has ALREADY connected Google, the recommendation bracket is omitted.
    """
    is_connected = False
    try:
        is_connected, _ = await get_valid_google_connection_by_chat_id(chat_id)
    except Exception:
        pass

    if not is_connected:
        return WELCOME_CONSENT_BASE + "[Recommended: Please connect and link your Google account to ensure a smooth flow]\n\nShall we continue? Reply yes or no."
    return WELCOME_CONSENT_BASE + "Shall we continue? Reply yes or no."


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
    "• /radar — Check your Memory Clarity & Cognitive Health score\n"
    "• /relationships — View mapped family, friends & manager graph\n"
    "• /subscriptions — View tracked subscriptions & expiring free-trials\n"
    "• /followups — View critical emails & proposals awaiting reply\n"
    "• /share_bubble <slug> & /join_bubble <code> — Collaborate on memory bubbles\n"
    "• Connect Google (Gmail, Calendar, Drive)\n"
    "• Link my email\n"
    "• Or send a voice note in your own language 🎙️"
)


# ---------- Memory & Context Store ----------

RECENT_DOCS = {}  # chat_id -> {"filename": str, "content": str, "type": str, "summary": str, "timestamp": datetime}
RECENT_CHAT_TURNS = {}  # chat_id -> deque([{"role": str, "text": str}], maxlen=10)
LAST_SCANNED_CONTACT = {}  # chat_id -> dict of last scanned contact card details


def get_recent_chat_turns(chat_id: int, limit: int = 20) -> list:
    """Retrieves recent chat turns from in-memory cache, safely rebuilding from DB if empty/restarted."""
    if chat_id in RECENT_CHAT_TURNS and RECENT_CHAT_TURNS[chat_id]:
        return list(RECENT_CHAT_TURNS[chat_id])
    # Rebuild safely from database
    try:
        from db import get_recent_chat_history
        turns = get_recent_chat_history(chat_id, limit=limit)
        if turns:
            RECENT_CHAT_TURNS[chat_id] = deque(turns, maxlen=limit)
            return list(RECENT_CHAT_TURNS[chat_id])
    except Exception as exc:
        logging.getLogger("uvicorn").debug(f"Failed to rebuild recent chat turns for {chat_id}: {exc}")
    return []


def record_chat_turn(chat_id: int, role: str, text: str):
    if not text:
        return
    clean_msg = text.strip()
    if chat_id not in RECENT_CHAT_TURNS:
        get_recent_chat_turns(chat_id, limit=20)
        if chat_id not in RECENT_CHAT_TURNS:
            RECENT_CHAT_TURNS[chat_id] = deque(maxlen=20)
    # Deduplicate consecutive identical messages
    if RECENT_CHAT_TURNS[chat_id] and RECENT_CHAT_TURNS[chat_id][-1].get("role") == role and RECENT_CHAT_TURNS[chat_id][-1].get("text") == clean_msg:
        return
    RECENT_CHAT_TURNS[chat_id].append({"role": role, "text": clean_msg})
    # Persist in Neon PostgreSQL chat_logs
    try:
        log_chat_message(chat_id, role, clean_msg)
    except Exception as exc:
        print("record_chat_turn persist error:", repr(exc))


# ---------- Telegram helpers (WhatsApp ke time sirf ye 3 badlenge) ----------

def clean_html_to_text(s: str) -> str:
    """Strips any raw HTML tags (e.g. <b>, <code>, <i>) so text renders cleanly for humans."""
    if not isinstance(s, str):
        return s
    s = re.sub(r"</?(?:b|strong)>", "", s, flags=re.I)
    s = re.sub(r"</?(?:i|em)>", "", s, flags=re.I)
    s = re.sub(r"</?code>", "", s, flags=re.I)
    s = re.sub(r"</?pre>", "", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return s


def format_for_telegram_html(text: str) -> str:
    if not text:
        return ""
    import html
    s = html.escape(text, quote=False)
    s = re.sub(r"```(?:[a-zA-Z0-9_\-\+]+)?\n?(.*?)```", r"<pre>\1</pre>", s, flags=re.DOTALL)
    s = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s, flags=re.DOTALL)
    s = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"<i>\1</i>", s, flags=re.DOTALL)
    s = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<i>\1</i>", s, flags=re.DOTALL)
    return s


def strip_markdown_formatting(text: str) -> str:
    if not text:
        return ""
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    s = re.sub(r"(?<!\w)\*(.+?)\*(?!\w)", r"\1", text)
    s = re.sub(r"`([^`]+)`", r"\1", text)
    return s


async def tg(method, payload):
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(f"{API}/{method}", json=payload)
            return resp
    except Exception as exc:
        logging.getLogger("uvicorn").warning(f"Telegram tg({method}) error: {exc}")
        return None


async def send_telegram_text(chat_id: int, text: str):
    """Sends message with parse_mode=HTML, with automatic fallback to clean plain text."""
    from utilities import convert_tables_to_cards
    text = convert_tables_to_cards(text)
    html_text = format_for_telegram_html(text)
    resp = await tg("sendMessage", {"chat_id": chat_id, "text": html_text, "parse_mode": "HTML"})
    if resp is None or resp.status_code != 200:
        plain_text = strip_markdown_formatting(text)
        await tg("sendMessage", {"chat_id": chat_id, "text": plain_text})


async def say_raw(chat_id, text):
    """Bina translate kiye bhejta hai (jaise voice ka transcript)."""
    from utilities import convert_tables_to_cards
    text = convert_tables_to_cards(text)
    record_chat_turn(chat_id, "assistant", text)
    await send_telegram_text(chat_id, text)


async def say(chat_id, text):
    """Sends message directly in natural LLM language without middleman translation."""
    from utilities import convert_tables_to_cards
    text = convert_tables_to_cards(text)
    record_chat_turn(chat_id, "assistant", text)
    await send_telegram_text(chat_id, text)



async def send_file(chat_id, filename, content_bytes=None, caption="", mime_type="application/octet-stream", file_id=None):
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")

    # Priority 1: If Telegram file_id is provided, send directly via Telegram servers
    if file_id:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
                resp = await client.post(
                    f"{API}/sendDocument",
                    data={"chat_id": chat_id, "document": file_id, "caption": (caption or "")[:1024]}
                )
            payload = {}
            try:
                payload = resp.json()
            except Exception:
                pass
            if resp.status_code == 200 and isinstance(payload, dict) and payload.get("ok"):
                return payload
            print(f"[send_file] Direct file_id send failed ({resp.status_code}): {payload}, falling back to content bytes")
        except Exception as fid_err:
            print("[send_file] Direct file_id send error:", repr(fid_err))

    # Priority 2: If content_bytes is None and filename is an existing file path, read bytes
    display_name = filename
    if content_bytes is None:
        if isinstance(filename, str) and os.path.exists(filename):
            with open(filename, "rb") as f:
                content_bytes = f.read()
            display_name = os.path.basename(filename)
        elif file_id:
            try:
                from multimodal import download_telegram_file
                downloaded_bytes, _ = await download_telegram_file(file_id)
                if downloaded_bytes:
                    content_bytes = downloaded_bytes
            except Exception as dl_err:
                print(f"[send_file] Re-download file {file_id} error:", repr(dl_err))

    if content_bytes is None:
        raise ValueError(f"send_file requires content_bytes, an existing file path, or valid file_id, got {filename}")
    elif isinstance(filename, str):
        display_name = os.path.basename(filename)

    if not mime_type or mime_type == "application/octet-stream":
        import mimetypes
        guessed, _ = mimetypes.guess_type(display_name)
        if guessed:
            mime_type = guessed

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        resp = await client.post(
            f"{API}/sendDocument",
            data={"chat_id": chat_id, "caption": (caption or "")[:1024]},
            files={"document": (display_name, content_bytes, mime_type)},
        )
    payload = {}
    try:
        payload = resp.json()
    except Exception:
        pass
    if resp.status_code != 200 or (isinstance(payload, dict) and payload.get("ok") is False):
        desc = payload.get("description") if isinstance(payload, dict) else resp.text[:200]
        raise RuntimeError(f"Telegram sendDocument failed ({resp.status_code}): {desc}")
    return payload


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
        now = timeutil.now(IST)
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


_UNIFIED_SCHED_CACHE: dict = {}

def invalidate_unified_schedule_cache(chat_id: int = None):
    global _UNIFIED_SCHED_CACHE
    if chat_id is None:
        _UNIFIED_SCHED_CACHE.clear()
    else:
        _UNIFIED_SCHED_CACHE = {k: v for k, v in _UNIFIED_SCHED_CACHE.items() if k[0] != chat_id}

async def get_unified_schedule(chat_id: int, target_date: date):
    import time as _pytime
    now_ts = _pytime.time()
    ck = (chat_id, str(target_date))
    cached = _UNIFIED_SCHED_CACHE.get(ck)
    if cached and (now_ts < cached["exp"]):
        return cached["data"]
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

    # Concurrent fetch of Schedule components to eliminate sequential DB latency
    async def _fetch_rems():
        try:
            return await asyncio.to_thread(items_for_date, chat_id, target_date)
        except Exception:
            return []

    async def _fetch_cal():
        try:
            is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
            if is_conn:
                t_min = f"{date_iso}T00:00:00+05:30"
                t_max = f"{date_iso}T23:59:59+05:30"
                return await google_list_calendar_events(chat_id, time_min_iso=t_min, time_max_iso=t_max)
        except Exception:
            pass
        return []

    async def _fetch_conts():
        try:
            return await asyncio.to_thread(get_contact_memories, chat_id)
        except Exception:
            return []

    async def _fetch_tasks():
        try:
            return await asyncio.to_thread(get_board_cards, chat_id)
        except Exception:
            return {}

    raw_rems_res, cal_res, conts_res, board_res = await asyncio.gather(
        _fetch_rems(), _fetch_cal(), _fetch_conts(), _fetch_tasks(),
        return_exceptions=True
    )

    raw_rems = raw_rems_res if isinstance(raw_rems_res, list) else []
    calendar_events = cal_res if isinstance(cal_res, list) else []
    contacts = conts_res if isinstance(conts_res, list) else []
    board = board_res if isinstance(board_res, dict) else {}

    reminders = []
    for r in raw_rems:
        if isinstance(r, dict):
            reminders.append(r)
        else:
            reminders.append({
                "category": r[0] if len(r) > 0 else "appointments",
                "title": r[1] if len(r) > 1 else "Reminder",
                "amount": r[2] if len(r) > 2 else None,
                "due_time": r[3] if len(r) > 3 else None,
            })

    matched_commitments = []
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

    matched_tasks = []
    for st, cards in board.items():
        if isinstance(cards, list):
            for card in cards:
                if isinstance(card, dict) and card.get("due_date") == date_iso:
                    matched_tasks.append(card)

    result = {
        "date": target_date,
        "weekday": weekday_str,
        "calendar_events": calendar_events,
        "reminders": reminders,
        "commitments": matched_commitments,
        "tasks": matched_tasks
    }
    _UNIFIED_SCHED_CACHE[ck] = {"exp": now_ts + 90.0, "data": result}
    return result


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

    # Check Reminders with realistic interval reasoning (travel takes 3-4h, movies ~3h, doctor ~2h)
    for rem in sched.get("reminders", []):
        if isinstance(rem, dict):
            title = rem.get("title", "Reminder")
            d_time = rem.get("due_time")
        elif isinstance(rem, (list, tuple)):
            title = rem[1] if len(rem) > 1 else "Reminder"
            d_time = rem[3] if len(rem) > 3 else None
        else:
            continue
        if d_time:
            rem_dt = datetime.combine(target_date, d_time, tzinfo=IST)
            low_t = title.lower()
            is_travel = any(w in low_t for w in ("travel", "train", "flight", "journey", "trip", "bus", "drive", "yatra", "safar"))
            is_movie = any(w in low_t for w in ("movie", "cinema", "film", "theatre"))
            is_doctor = any(w in low_t for w in ("doctor", "hospital", "clinic", "dentist", "appointment"))

            if is_travel:
                travel_end = rem_dt + timedelta(hours=4)
                if (rem_dt - timedelta(hours=1)) <= start_dt < travel_end:
                    time_conflicts.append(f"Train/Travel Reminder: '{title}' ({d_time.strftime('%I:%M %p')}) — safar ke dauran meeting attend karna mushkil hoga")
            elif is_movie:
                movie_end = rem_dt + timedelta(hours=3)
                if rem_dt <= start_dt < movie_end:
                    time_conflicts.append(f"Movie Reminder: '{title}' ({d_time.strftime('%I:%M %p')})")
            elif is_doctor:
                doc_end = rem_dt + timedelta(hours=2)
                if rem_dt <= start_dt < doc_end:
                    time_conflicts.append(f"Doctor Appointment: '{title}' ({d_time.strftime('%I:%M %p')})")
            else:
                if abs((rem_dt - start_dt).total_seconds()) < 3600:
                    time_conflicts.append(f"Reminder: '{title}' ({d_time.strftime('%I:%M %p')})")

    # Day commitments
    day_commitments = [
        f"{c['name']} ({c.get('company') or 'Contact'}): {c['commitment']}" for c in sched.get("commitments", [])
    ]

    return time_conflicts, day_commitments


async def show_range(chat_id, start, end, meeting_only=False, query_text=""):
    now = timeutil.now(IST)
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
                parsed_q = parse_followup_time(query_text, timeutil.now(IST))
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
        frontend_connect = os.getenv("FRONTEND_CONNECT_URL", "https://bucolic-dieffenbachia-c20bd0.netlify.app/connect.html")
        msg = (
            "Bhai aapka **Notion Workspace** abhi connect nahi hai! 🔗\n\n"
            f"Aap niche diye gaye link par jakar 1-click mein apna Notion connect kar sakte ho:\n"
            f"👉 {frontend_connect}\n\n"
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
            frontend_connect = os.getenv("FRONTEND_CONNECT_URL", "https://bucolic-dieffenbachia-c20bd0.netlify.app/connect.html")
            msg = (
                "Bhai aapka **GitHub Account** abhi connect nahi hai! 🐙\n\n"
                f"Aap niche diye gaye link par jakar 1-click mein apna GitHub connect kar sakte ho:\n"
                f"👉 {frontend_connect}\n\n"
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
            frontend_connect = os.getenv("FRONTEND_CONNECT_URL", "https://bucolic-dieffenbachia-c20bd0.netlify.app/connect.html")
            reply = (
                "Bhai aapka **GitHub Account** abhi connect nahi hai! 🐙\n\n"
                f"Aap connect page par jakar GitHub link kar sakte ho:\n"
                f"👉 {frontend_connect}\n\n"
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
        "Extract any permanent personal facts, future plans, travel, habits, preferences, life events, "
        "or people/relationships/birthdays mentioned by the user in this message.\n\n"
        f"User message: \"{text}\"\n\n"
        "Guidelines:\n"
        "- If user mentions going somewhere, an event, or plan (e.g. 'going to Jaipur today for model event', 'going movie today'): extract as a plan.\n"
        "- If user mentions preferences, work info, people they know, projects: extract as a fact.\n"
        "- If user mentions a relationship or family member (e.g. 'my brother Rahul', 'Ananya is my manager', 'papa ka naam Ramesh hai'), or birthday (e.g. 'Rahul birthday on 14 Nov'): extract under relationships.\n"
        "- If message is just a casual greeting or command with no personal facts/plans, return empty lists.\n\n"
        "Return strictly JSON:\n"
        "{\n"
        "  \"facts\": [{\"fact\": \"...\", \"category\": \"plan|preference|profile|work\"}],\n"
        "  \"relationships\": [{\"name\": \"...\", \"relationship\": \"...\", \"birthday\": \"YYYY-MM-DD or MM-DD or null\"}]\n"
        "}"
    )
    try:
        try:
            data = (await _chat_json(prompt, text)) or {}
        except Exception:
            data = {}
        facts = data.get("facts", []) if isinstance(data, dict) else []
        for item in facts:
            f_text = (item.get("fact") or "").strip()
            cat = item.get("category") or "general"
            if f_text and len(f_text) > 3:
                save_user_fact(chat_id, f_text, cat)

        # Structured People CRM / Relationships & Birthdays Extraction (Item 4a)
        try:
            from services.relationship_service import upsert_relationship
            saved_rel_names = set()
            for r_item in data.get("relationships", []):
                r_name = (r_item.get("name") or "").strip()
                r_rel = (r_item.get("relationship") or "friend").strip()
                r_bday = r_item.get("birthday")
                if r_name and len(r_name) > 1 and r_name.lower() not in ("user", "bot", "assistant"):
                    upsert_relationship(chat_id, name=r_name, relationship=r_rel, birthday=r_bday)
                    saved_rel_names.add(r_name.lower())

            # Fallback regex parsing if LLM did not return structured relationships
            if not saved_rel_names:
                rel_m = re.search(
                    r"(?:mera|mere|my)\s+(papa|father|dad|mom|mummy|mother|bhai|brother|behan|sister|wife|husband|patni|pati|boss|manager|friend|dost)\s+(?:ka\s+naam\s+|h\s+|hai\s+|is\s+|named\s+)?([A-Za-z\s]+?)(?:\s+hai|\s+h|\.|$)",
                    text,
                    re.I
                )
                if not rel_m:
                    rel_m2 = re.search(
                        r"([A-Za-z\s]+?)\s+(?:is\s+my|mera|mere)\s+(papa|father|dad|mom|mummy|mother|bhai|brother|behan|sister|wife|husband|boss|manager)",
                        text,
                        re.I
                    )
                    if rel_m2:
                        r_name = rel_m2.group(1).strip()
                        r_rel = rel_m2.group(2).strip()
                        upsert_relationship(chat_id, name=r_name, relationship=r_rel)
                else:
                    r_rel = rel_m.group(1).strip()
                    r_name = rel_m.group(2).strip()
                    if r_name and len(r_name) > 1 and r_name.lower() not in ("hai", "h", "ka", "ki", "naam"):
                        upsert_relationship(chat_id, name=r_name, relationship=r_rel)

                bday_m = re.search(
                    r"([A-Za-z\s]+?)(?:'s|\s+ka|\s+ki)\s+birthday\s+(?:is\s+on\s+|h\s+|hai\s+|on\s+)?([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+|[A-Za-z]+\s+[0-9]{1,2}|[0-9]{4}-[0-9]{2}-[0-9]{2})",
                    text,
                    re.I
                )
                if bday_m:
                    target_entity = bday_m.group(1).strip()
                    bday_val = bday_m.group(2).strip()
                    upsert_relationship(chat_id, name=target_entity, relationship="Family/Friend", birthday=bday_val)

            # Record interaction date for warmth radar
            from services.relationship_service import record_relationship_interaction
            record_relationship_interaction(chat_id, text)
        except Exception as rel_err:
            pass

        # Auto-extract Implicit Commitments & Promises ("Bolkar bhool jane ka ilaj")
        try:
            t_low = text.lower()
            commitment_signals = (
                "bhej dunga", "bhej dungi", "call karunga", "call karungi", "de dunga", "de dungi",
                "kar dunga", "kar dungi", "send karunga", "share karunga", "submit karunga",
                "i will send", "i will call", "i'll send", "i'll call", "i will do", "i will submit",
                "promise to", "by tomorrow", "kal tak", "shaam tak", "subah tak"
            )
            if any(w in t_low for w in commitment_signals):
                from memory.store import resolve_user_id, create_commitment
                uid = resolve_user_id(chat_id)
                target_dt = timeutil.today(IST)
                if any(w in t_low for w in ("kal", "tomorrow", "next day")):
                    target_dt = target_dt + timedelta(days=1)
                elif any(w in t_low for w in ("parso", "day after")):
                    target_dt = target_dt + timedelta(days=2)
                
                clean_title = text.strip()
                if len(clean_title) > 80:
                    clean_title = clean_title[:77] + "..."
                create_commitment(user_id=uid, title=clean_title, target_date=target_dt)
        except Exception as comm_err:
            pass
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
    await say(
        chat_id,
        f"Great, you're set up as {PERSONA_LABELS[persona]}. I'll keep track of: {names}.\n\n"
        "Just tell me things to remember, like 'Electricity bill 1200 due on 25 Oct' "
        "or 'Meeting tomorrow at 5 PM'. Ask 'what's on today?' anytime. "
        "To change what I track, say something like 'stop tracking work'. "
        "You can chat with me in any language you prefer!"
    )


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
    # Master Privacy Wipe Email Verification check (valid 5 mins)
    import time as _time
    sender_clean = (m.get("sender") or "").strip().lower()
    mail_text = ((m.get("subject") or "") + " " + (m.get("body") or "")).lower()

    pending_items = list(PENDING_WIPE_CODES.items())
    if not pending_items:
        try:
            with engine.connect() as conn:
                rows = conn.execute(
                    db.text("SELECT telegram_chat_id, state_data FROM users WHERE state IN ('awaiting_wipe_verification', 'awaiting_wipe_email_verification', 'awaiting_wipe_chat_otp')")
                ).fetchall()
                for r in rows:
                    c_id = r[0]
                    s_data = r[1]
                    if isinstance(s_data, str):
                        try:
                            s_data = json.loads(s_data)
                        except Exception:
                            s_data = {}
                    if isinstance(s_data, dict) and "code" in s_data:
                        pending_items.append((c_id, s_data))
        except Exception as e:
            print("DB check for pending wipe codes error:", repr(e))

    for p_chat_id, p_info in pending_items:
        p_code = str(p_info.get("code", "")).strip().lower()
        if p_code and p_code in mail_text:
            if _time.time() <= p_info.get("expires_at", 0) + 300:
                PENDING_WIPE_CODES.pop(p_chat_id, None)
                set_state(p_chat_id, None)
                RECENT_CHAT_TURNS.pop(p_chat_id, None)
                RECENT_DOCS.pop(p_chat_id, None)
                PENDING_GOOGLE_ACTIONS.pop(p_chat_id, None)
                wipe_all_user_memories(p_chat_id)
                await say(
                    p_chat_id,
                    "✅ **Master Privacy Wipe Confirmed**: Aapke email se OTP verify ho gaya hai. "
                    "Aapki saari memories, chat history, contacts, notes, smart lists aur reminders permanently delete kar diye gaye hain (Telegram, WhatsApp aur App sabhi jagah se)."
                )
                return
            else:
                PENDING_WIPE_CODES.pop(p_chat_id, None)
                set_state(p_chat_id, None)
                await say(
                    p_chat_id,
                    "⚠️ Verification code expire ho chuka tha. Memory delete nahi hui. Agar wipe karna chahte hain to dobara request karein."
                )
                return

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
    mail_content_str = (m["subject"] + "\n" + m["body"]).strip()
    if not mail_content_str:
        return

    # Check if incoming email is a travel ticket, flight booking, hotel reservation, or medium ticket
    is_ticket = bool(re.search(r'\b(flight|ticket|boarding\s*pass|pnr|reservation|hotel|airline|indigo|air\s*india|vistara|spicejet|train|irctc|medium\s*ticket)\b', mail_content_str, re.I))
    if is_ticket:
        # Save ticket in persistent user facts
        ticket_fact = f"Booking/Ticket: {m.get('subject', 'Ticket')} - {mail_content_str[:300]}"
        save_user_fact(chat_id, ticket_fact, category="booking")
        ticket_msg = (
            f"✈️ **Booking / Flight Ticket receive ho gayi hai!** 🎫\n"
            f"• **Title**: {m['subject'][:80] or 'Travel Ticket'}\n"
            f"• **Sender**: {m.get('sender', '')}\n\n"
            f"Maine ticket details aapki memory me save kar li hain.\n"
            f"Kya aap chahenge ki main iska **Google Calendar meeting/event** bana du ya **Goa trip packing checklist** me add kar du?\n\n"
            f"*(Note: Agar aapko ye ticket memory se nikalni ho, bas bolna: 'is ticket ko memory se hata do')*"
        )
        await say(chat_id, ticket_msg)
    else:
        await say(chat_id, f"📧 Email received: {m['subject'][:60] or '(no subject)'}")
        await handle_memory_text(chat_id, mail_content_str)


async def mailbox_loop():
    while True:
        try:
            mails = await asyncio.to_thread(fetch_unseen)
            for m in mails:
                await handle_mail(m)
        except Exception as e:
            print("mailbox error:", e)
        await asyncio.sleep(60)


async def connected_user_gmail_monitor_loop():
    """
    Autonomously monitors incoming unread emails in users' connected Google accounts.
    Intelligently identifies important emails (interviews/meetings with video links,
    ticket bookings with PNR, credit card / utility bills with due dates, and urgent deliveries),
    extracts key details, and instantly notifies the user on Telegram.
    """
    await asyncio.sleep(10)
    while True:
        try:
            connected_users = await asyncio.to_thread(get_all_connected_google_users)
            seen_mailboxes = set()
            for u in connected_users:
                chat_id_raw = u.get("telegram_chat_id")
                if not chat_id_raw:
                    continue
                try:
                    chat_id = int(chat_id_raw)
                except (ValueError, TypeError):
                    continue

                raw_email = (u.get("email") or "").strip().lower()
                if raw_email:
                    if raw_email in seen_mailboxes:
                        continue
                    seen_mailboxes.add(raw_email)

                # Route alerts to active chat session among linked identities
                delivery_chat_id = db.get_most_active_chat_id(chat_id)

                # Determine connection timestamp
                conn_dt = u.get("created_at") or u.get("updated_at")
                conn_ts = None
                if conn_dt:
                    try:
                        conn_ts = conn_dt.timestamp() if hasattr(conn_dt, "timestamp") else None
                    except Exception:
                        pass

                try:
                    messages = await fetch_user_inbox_messages(chat_id, max_results=10, query="in:inbox")
                except Exception as fetch_err:
                    continue

                if not messages:
                    continue

                for msg in messages:
                    email_id = msg.get("id")
                    if not email_id:
                        continue

                    # Check deduplication across all linked identities
                    already_processed = await asyncio.to_thread(is_user_email_processed, delivery_chat_id, email_id)
                    if already_processed:
                        continue

                    sender = msg.get("sender", "")
                    subject = msg.get("subject", "")
                    body = msg.get("body", "")

                    # Check email arrival time vs account connection time
                    msg_ts = None
                    if msg.get("internal_date_ms"):
                        msg_ts = float(msg["internal_date_ms"]) / 1000.0
                    elif msg.get("date"):
                        try:
                            from email.utils import parsedate_to_datetime
                            msg_ts = parsedate_to_datetime(msg["date"]).timestamp()
                        except Exception:
                            pass

                    # If this email arrived BEFORE the user connected their account, skip silently!
                    is_pre_connection = False
                    if conn_ts is not None:
                        if msg_ts is not None:
                            is_pre_connection = (msg_ts < (conn_ts - 30.0))
                        else:
                            has_baseline = await asyncio.to_thread(has_user_email_baseline, delivery_chat_id)
                            is_pre_connection = (not has_baseline)
                    elif msg_ts is not None:
                        import time as _pytime
                        is_pre_connection = (msg_ts < (_pytime.time() - 120.0))

                    if is_pre_connection:
                        await asyncio.to_thread(
                            record_processed_user_email,
                            delivery_chat_id,
                            email_id,
                            sender=sender,
                            subject=subject,
                            category="pre_connection_email",
                            is_important=False
                        )
                        continue

                    try:
                        analysis = await analyze_incoming_email(sender, subject, body)
                    except Exception as err:
                        print("analyze_incoming_email error:", repr(err))
                        analysis = None

                    if analysis and analysis.get("is_important") and analysis.get("alert_text"):
                        await asyncio.to_thread(
                            record_processed_user_email,
                            delivery_chat_id,
                            email_id,
                            sender=sender,
                            subject=subject,
                            category=analysis.get("category", ""),
                            is_important=True
                        )
                        alert_text = analysis["alert_text"]

                        # Save structured fact to user cognitive memory
                        cat = analysis.get("category", "email")
                        raw = analysis.get("raw_data") or {}
                        fact_text = ""
                        if cat == "ticket_booking":
                            pnr_str = raw.get("pnr") or ""
                            route_str = raw.get("route") or ""
                            dt_str = raw.get("datetime_str") or ""
                            carrier_str = raw.get("carrier") or "Travel"
                            seats_str = raw.get("seats") or ""
                            pax_str = raw.get("passengers") or ""
                            fact_text = f"Booking/Ticket: {carrier_str} | PNR: {pnr_str} | Route: {route_str} | Date: {dt_str} | Seats: {seats_str} | Passengers: {pax_str}"
                            try:
                                from state.active_context import set_active_ticket
                                set_active_ticket(delivery_chat_id, raw)
                            except Exception as sat_err:
                                print(f"Error setting active ticket in main: {sat_err}")

                            # Contextual Travel & Packing Radar
                            try:
                                dest = "Trip"
                                if "➔" in route_str:
                                    dest = route_str.split("➔")[-1].strip()
                                elif "->" in route_str:
                                    dest = route_str.split("->")[-1].strip()
                                elif "to" in route_str.lower():
                                    dest = route_str.lower().split("to")[-1].strip().title()
                                if dest and len(dest) > 1 and dest.lower() != "trip":
                                    list_name = f"🧳 Packing: {dest}"
                                    default_items = [
                                        f"Tickets / Boarding Pass (PNR: {pnr_str})",
                                        "Govt ID Proof (Aadhaar / Passport / Voter ID)",
                                        "Phone Charger & Power Bank",
                                        "Prescription Medicines & First Aid",
                                        "Wallet, Cash & Cards"
                                    ]
                                    db.create_smart_list_with_items(delivery_chat_id, list_name, category="travel", items=default_items)
                                    alert_text += f"\n\n🧳 **Auto-Packing List Ready:** Smart lists mein aapka '{list_name}' checklist add kar diya gaya hai!"
                            except Exception as pack_e:
                                print(f"Auto-packing list error: {pack_e}")

                        elif cat == "interview":
                            comp_str = raw.get("company") or ""
                            role_str = raw.get("role_or_title") or ""
                            dt_str = raw.get("datetime_str") or ""
                            meet_str = raw.get("meet_link") or ""
                            fact_text = f"Interview/Meeting: {comp_str} | Role: {role_str} | Date: {dt_str} | Link: {meet_str}"

                            # Universal Proactive Collision & Clash Shield
                            try:
                                from services.clash_detector import detect_schedule_clash
                                if dt_str and dt_str != "Check email details":
                                    today_ist = timeutil.today(IST)
                                    clash_res = await detect_schedule_clash(delivery_chat_id, today_ist, dt_str, duration_minutes=45, exclude_title=comp_str)
                                    if clash_res and clash_res.get("has_clash"):
                                        alert_text += f"\n\n⚠️ **Schedule Conflict Alert:** Is samay aapke schedule mein already '{clash_res['message']}' hai! Ek baar apna calendar zaroor review karein."
                            except Exception as cl_e:
                                print(f"Interview clash detection error: {cl_e}")
                        elif cat == "document_received":
                            title_str = raw.get("document_title") or raw.get("role_or_title") or subject
                            summary_str = raw.get("summary") or ""
                            fact_text = f"Received Document: {title_str} - {summary_str}"
                        elif cat == "bill_payment":
                            biller_str = raw.get("biller") or ""
                            amount_str = raw.get("amount") or ""
                            due_str = raw.get("due_date") or ""
                            fact_text = f"Bill Due: {biller_str} | Amount: {amount_str} | Due Date: {due_str}"
                        elif cat == "subscription_watchdog":
                            service_str = raw.get("service_name") or raw.get("biller") or "Subscription"
                            amount_val = raw.get("amount")
                            ren_date = raw.get("renewal_date") or raw.get("trial_expiry_date")
                            is_tr = bool(raw.get("is_trial"))
                            c_url = raw.get("cancel_url")
                            b_cycle = raw.get("billing_cycle") or "monthly"
                            fact_text = f"Subscription Tracked: {service_str} | Amount: {amount_val} | Next Date: {ren_date} | Trial: {is_tr}"
                            try:
                                from services.subscription_service import upsert_subscription
                                parsed_amt = None
                                if amount_val:
                                    clean_a = re.sub(r"[^\d.]", "", str(amount_val))
                                    if clean_a:
                                        try:
                                            parsed_amt = float(clean_a)
                                        except Exception:
                                            pass
                                await asyncio.to_thread(
                                    upsert_subscription,
                                    delivery_chat_id,
                                    service_name=service_str,
                                    amount=parsed_amt,
                                    billing_cycle=b_cycle,
                                    next_renewal_date=ren_date if not is_tr else None,
                                    is_trial=is_tr,
                                    trial_expiry_date=ren_date if is_tr else None,
                                    cancel_url=c_url,
                                )
                            except Exception as sub_e:
                                print(f"Error upserting subscription from email for {delivery_chat_id}: {sub_e}")

                        if fact_text:
                            try:
                                await asyncio.to_thread(save_user_fact, delivery_chat_id, fact_text, category=cat)
                            except Exception as f_err:
                                print(f"Error saving user fact from email for {delivery_chat_id}:", f_err)

                        try:
                            reply_lang.set(get_language(delivery_chat_id))
                            await say_raw(delivery_chat_id, alert_text)
                        except Exception as send_err:
                            print(f"Failed to send email alert to {delivery_chat_id}:", send_err)
                    else:
                        cat = (analysis or {}).get("category", "promotional")
                        await asyncio.to_thread(
                            record_processed_user_email,
                            delivery_chat_id,
                            email_id,
                            sender=sender,
                            subject=subject,
                            category=cat,
                            is_important=False
                        )
        except Exception as loop_err:
            print("connected_user_gmail_monitor_loop error:", repr(loop_err))

        await asyncio.sleep(120)


# ---------- Google connection ----------


_GOOGLE_CONN_CACHE: dict = {}

async def get_valid_google_connection_by_chat_id(chat_id: int) -> tuple[bool, str | None]:
    """Check if the user has a valid, unrevoked Google connection. Returns (is_connected, email)."""
    import time as _pytime
    from db import canonical_chat_id
    orig_chat_id = chat_id
    chat_id = canonical_chat_id(chat_id)
    now_ts = _pytime.time()
    cached = _GOOGLE_CONN_CACHE.get(chat_id)
    if cached and (now_ts - cached["ts"] < 180.0):
        return cached["res"]


    try:
        with engine.connect() as conn:
            row = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_id}).first()
        if not row:
            res = (False, None)
            _GOOGLE_CONN_CACHE[chat_id] = {"ts": now_ts, "res": res}
            return res
        db_user_id = int(row[0])
        token = get_google_token(db_user_id)
        if not token or not token.get("refresh_token_enc"):
            res = (False, None)
            _GOOGLE_CONN_CACHE[chat_id] = {"ts": now_ts, "res": res}
            return res
        raw_refresh = decrypt_token(token["refresh_token_enc"])
        if not raw_refresh:
            res = (False, None)
            _GOOGLE_CONN_CACHE[chat_id] = {"ts": now_ts, "res": res}
            return res
        # Fast optimistic check: if token has email and valid refresh token, trust it
        email_val = token.get("email")
        res = (True, email_val)
        _GOOGLE_CONN_CACHE[chat_id] = {"ts": now_ts, "res": res}
        return res
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
    fallback = os.getenv("FRONTEND_URL", "https://bucolic-dieffenbachia-c20bd0.netlify.app").rstrip("/") + "/connect.html"
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
def format_event_time_ist(dt_str: str | None, user_tz=None) -> str:
    """Format Google Calendar / ISO dateTime string into user-friendly string."""
    if not dt_str:
        return ""
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        target_tz = user_tz or IST
        if dt.tzinfo is None:
            dt_local = dt.replace(tzinfo=target_tz)
        else:
            dt_local = dt.astimezone(target_tz)
        return dt_local.strftime("%d %b, %I:%M %p").replace(" 0", " ")
    except Exception:
        return dt_str[:16]


CONFIRM_PREFIX_RE = re.compile(
    r'^\s*(haan(?:\s*ji|\s*bhai)?|han|haa|ha|yes(?:\s*please)?|yep|yeah|yup|sure|ok(?:\s*kardo|\s*kar\s*do)?|okay|theek\s*hai|thik\s*hai|sahi\s*hai|kar\s*do|kardo|kr\s*do|krdo|karo|kro|delete\s*(?:kar\s*do|kr\s*do|kardo|krdo|karo|kr)?|hata\s*(?:do|de)|हाँ(?:\s*जी)?|हां|हाँजी|जी(?:\s*हाँ)?|बिल्कुल|सही|ठीक|कर\s*दो|हटा\s*दो)\b\s*(?:aur|and|lekin|or|,|-)?\s*',
    re.I
)

REJECT_PREFIX_RE = re.compile(
    r'^\s*(nahi(?:\s*bhai)?|nahin|na|no|nope|cancel|mat\s*(?:karo|kr)|rehne\s*do|rahne\s*do|chhoro|नहीं|ना|मत\s*करो|रहने\s*दो|रद्द)\b\s*(?:aur|and|lekin|or|,|-)?\s*',
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
            from core.email_verifier import verify_email
            val_to = await verify_email(pending['to'])
            if not val_to.is_valid:
                err_text = val_to.error_message or f"Recipient email '{pending['to']}' is invalid."
                await say(chat_id, f"❌ Cannot send email: {err_text}")
                return True
            result = await google_send_email(chat_id, pending['to'], pending['subject'], pending['body'])
            try:
                from services.followup_service import record_followup
                record_followup(chat_id, recipient=pending['to'], subject=pending['subject'], days_ahead=3)
            except Exception:
                pass
            await say(chat_id, f"Email sent successfully to {pending['to']}. (Message ID: {result})\n\n💡 Follow-up Watchdog active: Agar 3 din mein reply nahi aata, toh main aapko follow-up remind kara dunga.")
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
        elif kind in ('create_calendar_event', 'calendar_event'):
            rec = pending.get("recurrence") or "none"
            g_rec = ["RRULE:FREQ=DAILY"] if rec == "daily" else (["RRULE:FREQ=WEEKLY"] if rec == "weekly" else None)
            from google_services import create_calendar_event as google_create_calendar_event
            result = await google_create_calendar_event(
                chat_id,
                summary=pending.get('title', 'Event'),
                start_time=pending.get('start_iso'),
                duration_minutes=pending.get('duration_minutes', 30),
                is_video_meeting=False,
                recurrence=g_rec
            )
            raw_start = result.get('start') if isinstance(result, dict) else None
            if isinstance(raw_start, dict):
                start_val = raw_start.get('dateTime') or raw_start.get('date') or pending['start_iso']
            elif isinstance(raw_start, str):
                start_val = raw_start
            else:
                start_val = pending['start_iso']
            start_fmt = format_event_time_ist(start_val)
            user_lang = db.get_user_language(chat_id)
            if user_lang == "english":
                msg = f"✅ Event added to Google Calendar: {result.get('summary') or pending.get('title')}\nTime: {start_fmt}"
            else:
                msg = f"✅ Calendar event schedule ho gaya: {result.get('summary') or pending.get('title')}\nTime: {start_fmt}"
            if isinstance(result, dict) and result.get('htmlLink'):
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
        elif kind == 'save_item':
            await save_item_flow(chat_id, pending['item'])

        # Check if user had a follow-up instruction in the same message (e.g. "haan aur 6bje rkh new meeting")
        rem_clean = clean_remainder_instruction(rem_parsed)
        if rem_clean:
            await handle_active(chat_id, rem_clean)

        return True
    except Exception as exc:
        print('Google action error:', repr(exc))
        err_str = str(exc)
        if "not connected" in err_str.lower() or "token" in err_str.lower():
            try:
                from google_auth import build_auth_url
                auth_url = build_auth_url(int(chat_id), source="tg")
                await say(
                    chat_id,
                    f"⚠️ Aapka Google account connected nahi hai. Google Calendar ya Meet use karne ke liye pehle is secure link par click karke connect karein:\n\n"
                    f"{auth_url}\n\n"
                    f"Connect hone ke baad aap easily meeting schedule kar sakte hain!"
                )
            except Exception:
                await say(chat_id, "Google account connected nahi hai. Google Calendar use karne ke liye pehle apna Google account connect karein.")
        else:
            await say(chat_id, f"Google action complete nahi ho paya: {str(exc)}. Please check your connection and try again.")
        return True


async def prepare_google_action(chat_id, r):
    intent = r['intent']
    if intent == 'send_email':
        to, subject, body = r.get('to'), r.get('subject'), r.get('body')
        if not to or not EMAIL_OK.match(to) or not subject or not body:
            await say(chat_id, "Email bhejne ke liye recipient email, subject aur message chahiye. Example: 'Send an email to name@example.com, subject Test, message Hello'.")
            return
        pending = {'kind':'send_email','to':to,'subject':subject,'body':body, 'created_at': timeutil.now(IST)}
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
        PENDING_GOOGLE_ACTIONS[chat_id] = {'kind':'upload_file','filename':filename,'content':content,'folder_id':folder_id, 'created_at': timeutil.now(IST)}
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

        PENDING_GOOGLE_ACTIONS[chat_id] = {'kind':'create_meeting','title':title,'start_iso':start_iso,'duration_minutes':r.get('duration_minutes',30),'attendees':attendees, 'created_at': timeutil.now(IST)}
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
            'created_at': timeutil.now(IST),
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
                    'created_at': timeutil.now(IST),
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
    # Standard intent detection without 25 foreign dictionary translations
    deletion_verb = any(w in low for w in ("delete", "remove", "clear", "cancel", "hata", "htaa", "mita", "रद्द", "हटा", "मिटा", "डिलीट"))
    meeting_scope = any(w in low for w in ("meeting", "appointment", "reminder", "event", "call", "मीटिंग", "बैठक", "ise", "isko", "usko", "ye", "yeh", "upar"))
    if not (deletion_verb and meeting_scope):
        return False

    # If the user is asking to reschedule, move, or replace, delegate to the agent core
    is_reschedule = any(w in low for w in ("shift", "move", "reschedule", "badal", "change", "nayi", "new", "create", "jagah", "badle", "bana", "rkhdo", "rakh do", "rakhdo", "rkh do", "rakho", "rkho"))
    if is_reschedule:
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
                'created_at': timeutil.now(IST),
            }
            PENDING_GOOGLE_ACTIONS[chat_id] = pending
            formatted_time = format_event_time_ist(ev.get('start', {}).get('dateTime'))
            await say(chat_id, f"Google Calendar meeting mili:\n• {ev.get('summary')} ({formatted_time})\n\nKya is event ko Google Calendar se delete kar doon? Reply yes to delete or no to cancel.")
            return True

    # 3. Check local database reminders
    exact_times = _extract_requested_times(clean)
    if exact_times:
        rows = find_meetings_at_times(chat_id, timeutil.today(IST), exact_times, clean)
    else:
        before_time = _deletion_time(clean)
        if before_time:
            rows = find_meetings_before(chat_id, timeutil.today(IST), before_time)
        else:
            recent = find_recent_meetings(chat_id, limit=2)
            if recent:
                rows = [recent[0]]
            else:
                set_state(chat_id, "delete_meetings_target", json.dumps({"date": timeutil.today(IST).isoformat(), "source": clean}))
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
    parsed = parse_followup_time(clean, timeutil.now(IST))
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
        set_state(chat_id, "updated_reminders_offer", json.dumps({"date": timeutil.today(IST).isoformat()}))
        await say(chat_id, f"Done — {deleted} meeting reminder(s) delete kar diye. Kya aaj ki updated reminder list dikhaun?")
    else:
        await say(chat_id, "Koi item delete nahi hua. Database mein matching items dobara verify karna hoga.")
    return True


# ---------- Main conversation brain ----------


async def handle_conversational_chat(chat_id, user_text):
    """Shim routing legacy conversational chat calls to handle_active."""
    return await handle_active(chat_id, user_text)


_CHAT_LOCKS = {}

def _get_chat_lock(cid: int) -> asyncio.Lock:
    if cid not in _CHAT_LOCKS:
        _CHAT_LOCKS[cid] = asyncio.Lock()
    return _CHAT_LOCKS[cid]


async def handle_active(chat_id, text):
    """Pure Native Tool-Calling Agent Loop with typing action & error fallback."""
    async with _get_chat_lock(chat_id):
        # Typing chat action if turn exceeds 2.0s; repeats every 4.5s for long turns (Phase 7c Item 9)
        async def _send_typing_after_delay():
            try:
                await asyncio.sleep(2.0)
                while True:
                    await tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})
                    await asyncio.sleep(4.5)
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        typing_task = asyncio.create_task(_send_typing_after_delay())
        _log_writer_tok = db.set_external_chat_log_writer(True)
        try:
            from agent.loop import run_agent_turn
            res = await run_agent_turn(chat_id, text, channel="telegram")
            await say(chat_id, res["reply"])
            return
        except Exception as exc:
            import traceback, uuid
            trace_id = f"trace_{uuid.uuid4().hex[:8]}"
            tb_str = traceback.format_exc()
            logging.getLogger("uvicorn").error(f"Agent turn error [{trace_id}] for {chat_id}: {repr(exc)}\n{tb_str}")
            from llm.provider import (
                ProviderTimeoutError,
                RateLimitedError,
                ToolExecutionError,
                DatabaseUnavailableError,
                ValidationError,
            )
            if isinstance(exc, ProviderTimeoutError):
                fallback_msg = f"AI model response timed out. Please try sending your message again in a moment. [Ref: {trace_id}]"
            elif isinstance(exc, RateLimitedError):
                fallback_msg = f"AI service is currently experiencing high demand. Please wait a few seconds and try again. [Ref: {trace_id}]"
            elif isinstance(exc, ToolExecutionError):
                fallback_msg = f"An issue occurred while executing the requested action. Please verify details and retry. [Ref: {trace_id}]"
            elif isinstance(exc, DatabaseUnavailableError):
                fallback_msg = f"Database connection is temporarily slow or unavailable. Your data is safe. Please retry shortly. [Ref: {trace_id}]"
            elif isinstance(exc, ValidationError):
                fallback_msg = f"Invalid parameters provided. Please check the details and retry. [Ref: {trace_id}]"
            else:
                fallback_msg = f"Something went wrong while processing your request. Please try again. [Ref: {trace_id}]"
            await say(chat_id, fallback_msg)
            return
        finally:
            db.reset_external_chat_log_writer(_log_writer_tok)
            typing_task.cancel()


def _log_slash_or_early_trace(chat_id: int, message: str, reply_path: str, reply_text: str, tool_name: str = None):
    try:
        from connectors.trace import log_turn_trace
        log_turn_trace(
            chat_id=chat_id,
            message=message,
            tools_called=[tool_name] if tool_name else [],
            tool_results=[{"reply": reply_text}],
            guard_results={},
            latency_ms=10,
            tokens=0,
            reply_path=reply_path,
            llm_calls=0
        )
    except Exception:
        pass


async def handle_user_text(chat_id, text, lang_hint=None):
    clean = text.strip()
    if not clean:
        return
    record_chat_turn(chat_id, "user", clean)
    # Background fact extraction disabled to conserve 100% of Groq rate limits for conversational turns
    pass

    st = get_state(chat_id)
    if st is None:
        ensure_user(chat_id)
        if not has_consent(chat_id):
            set_state(chat_id, "consent")
            w_text = await get_welcome_consent_text(chat_id)
            await say(chat_id, w_text)
            _log_slash_or_early_trace(chat_id, clean, "onboarding_prompt", w_text)
            return
        st = (None, None)
    state, data = st

    clean_lower = clean.lower()
    cmd_token = clean_lower.split()[0].split("@")[0]

    # Handle initial onboarding & consent for new users: never send "Welcome back!" to unconsented users
    if state in (None, "consent") and not has_consent(chat_id):
        if state == "consent":
            yn = yes_no(clean)
            if yn is None:
                yn = await classify_yes_no_any_language(clean, "The assistant is asking for initial user consent to continue setup.")
            if yn is True:
                give_consent(chat_id)
                set_state(chat_id, "persona")
                await say(chat_id, "Thank you!")
                await ask_persona(chat_id)
                _log_slash_or_early_trace(chat_id, clean, "onboarding_consent_given", "Thank you!")
                return
            elif yn is False:
                delete_user(chat_id)
                rep = "Okay, nothing has been saved. Message me anytime if you change your mind."
                await say(chat_id, rep)
                _log_slash_or_early_trace(chat_id, clean, "onboarding_consent_declined", rep)
                return
            elif cmd_token in {"/start", "start"}:
                w_text = await get_welcome_consent_text(chat_id)
                await say(chat_id, w_text)
                _log_slash_or_early_trace(chat_id, clean, "onboarding_start", w_text)
                return
            else:
                rep = "Shall we continue? Please reply yes or no."
                await say(chat_id, rep)
                _log_slash_or_early_trace(chat_id, clean, "onboarding_ask_retry", rep)
                return
        else:
            set_state(chat_id, "consent")
            w_text = await get_welcome_consent_text(chat_id)
            await say(chat_id, w_text)
            _log_slash_or_early_trace(chat_id, clean, "onboarding_prompt", w_text)
            return

    # Top-level command interruptions should ALWAYS be respected and immediately reset state and pending actions!
    if cmd_token in {"/start", "start"}:
        set_state(chat_id, None)
        PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
        rep = "Welcome back!\n\n" + HELP_TEXT
        await say(chat_id, rep)
        _log_slash_or_early_trace(chat_id, clean, "slash_start", rep, "/start")
        return
    if cmd_token in {"/cancel", "cancel"} or clean_lower in {"/cancel", "cancel", "rehne do", "chhoro", "radd karo", "cancel karo", "stop", "रद्द", "मत करो"}:
        set_state(chat_id, None)
        PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
        rep = "Cancelled. Kuch save ya schedule nahi hua."
        await say(chat_id, rep)
        _log_slash_or_early_trace(chat_id, clean, "slash_cancel", rep, "/cancel")
        return
    if cmd_token in {"/help", "help", "/menu", "menu"}:
        set_state(chat_id, None)
        await say(chat_id, HELP_TEXT)
        _log_slash_or_early_trace(chat_id, clean, "slash_help", HELP_TEXT, "/help")
        return

    # User Memory Controls (Phase 2)
    if cmd_token in {"/memories", "/memory", "memories"}:
        from memory.store import resolve_user_id, get_active_memories
        uid = resolve_user_id(chat_id)
        mems = get_active_memories(uid)
        if not mems:
            rep = "Mere paas abhi aapki koi active memory saved nahi hai."
            await say(chat_id, rep)
            _log_slash_or_early_trace(chat_id, clean, "slash_memories", rep, "/memories")
            return
        grouped = {}
        for m in mems:
            sub = m["subject"]
            grouped.setdefault(sub, []).append(m)
        lines = ["🧠 **Aapki Saved Memories:**\n"]
        for sub, items in grouped.items():
            lines.append(f"**[{sub}]**")
            for item in items:
                lines.append(f"• `[ID {item['id']}]` {item['content']} (saved {item['created_at']})")
            lines.append("")
        lines.append("Kisi memory ko delete karne ke liye: `/forget <ID>` ya `/forget <keyword>`")
        rep = "\n".join(lines).strip()
        await say(chat_id, rep)
        _log_slash_or_early_trace(chat_id, clean, "slash_memories", rep, "/memories")
        return

    if cmd_token == "/forget" or clean_lower.startswith("/forget "):
        from memory.store import resolve_user_id, forget_memory
        uid = resolve_user_id(chat_id)
        parts = clean.split(maxsplit=1)
        if len(parts) < 2:
            rep = "Kaunsi memory delete karni hai? Type: `/forget <ID>` ya `/forget <keyword>`"
            await say(chat_id, rep)
            _log_slash_or_early_trace(chat_id, clean, "slash_forget", rep, "/forget")
            return
        target = parts[1].strip()
        success = forget_memory(uid, target)
        if success:
            rep = f"✅ Memory '{target}' successfully delete kar di gayi hai (archived)."
        else:
            rep = f"⚠️ '{target}' se matching koi active memory nahi mili."
        await say(chat_id, rep)
        _log_slash_or_early_trace(chat_id, clean, "slash_forget", rep, "/forget")
        return

    # 1. Cognitive Health Score & Memory Decay Radar
    if cmd_token in {"/radar", "/health"} or clean_lower in {"health radar", "cognitive score", "memory health", "mera score", "mera health score", "radar score"}:
        from services.health_radar import compute_memory_health_score, format_health_radar_telegram_message
        score_data = compute_memory_health_score(chat_id)
        radar_msg = format_health_radar_telegram_message(score_data)
        await say(chat_id, radar_msg)
        return

    # 2. Shared Memory Bubbles: Invite Generation
    if cmd_token == "/share_bubble" or clean_lower.startswith("/share_bubble"):
        parts = clean.split(maxsplit=2)
        slug = parts[1].strip() if len(parts) > 1 else "everything"
        perm = parts[2].strip() if len(parts) > 2 else "read_write"
        from services.shared_bubble_service import create_bubble_share_invite
        inv_res = create_bubble_share_invite(chat_id, slug, permission=perm)
        invite_code = inv_res.get("invite_code") if isinstance(inv_res, dict) else inv_res
        await say(
            chat_id,
            f"🤝 **Shared Memory Bubble Invite Created!**\n\n"
            f"📂 Target Bubble: **{slug}**\n"
            f"🔐 Permission: `{perm}`\n"
            f"🎟️ **Invite Code:** `{invite_code}`\n\n"
            f"Jisko aap bubble share karna chahte hain, unse kahein ki vo ye command bhejein:\n"
            f"`/join_bubble {invite_code}`\n\n"
            f"*(Note: Shared user ko sirf is bubble ki memories ka access milega, aapke private emails ya baaki unshared bubbles unhe kabhi nahi dikhenge.)*"
        )
        return

    # 3. Shared Memory Bubbles: Join
    if cmd_token == "/join_bubble" or clean_lower.startswith("/join_bubble"):
        parts = clean.split(maxsplit=1)
        if len(parts) < 2:
            await say(chat_id, "Please provide the invite code. Example: `/join_bubble SAPTIC-WORK-A1B2`")
            return
        code = parts[1].strip()
        from services.shared_bubble_service import join_shared_bubble
        res = join_shared_bubble(chat_id, code)
        if res.get("status") == "success":
            await say(
                chat_id,
                f"🎉 **Bubble Successfully Joined!**\n\n"
                f"📂 Connected to Bubble: **{res.get('bubble_slug')}**\n"
                f"🔐 Permission: `{res.get('permission')}`\n\n"
                f"Ab aap is shared bubble ki memories search aur collaborate kar sakte hain!"
            )
        else:
            await say(chat_id, f"⚠️ Join failed: {res.get('error', 'Invalid or expired invite code.')}")
        return

    # 4. People CRM / Relationship Knowledge Graph
    if cmd_token in {"/relationships", "/people", "/family"}:
        from services.relationship_service import list_relationships
        rels = list_relationships(chat_id)
        if not rels:
            await say(chat_id, "Aapne abhi koi relationship map nahi kiya hai. Aap natural language me bol sakte hain jaise: 'Papa ka naam Ashok Kumar Sharma hai' ya 'Mera manager Devendra hai'.")
            return
        lines = ["👥 **Aapka People & Relationship Graph:**\n"]
        for r in rels:
            bday_str = f" | 🎂 Birthday: {r['birthday']}" if r.get('birthday') else ""
            phone_str = f" | 📞 {r['phone']}" if r.get('phone') else ""
            lines.append(f"• **{r['name']}** ({r['relationship']}){bday_str}{phone_str}")
        await say(chat_id, "\n".join(lines))
        return

    # 5. Subscription & Free-Trial Watchdog
    if cmd_token in {"/subscriptions", "/subs", "/trials"}:
        from services.subscription_service import get_active_subscriptions
        subs = get_active_subscriptions(chat_id)
        if not subs:
            await say(chat_id, "Koi active subscriptions ya free trials tracked nahi hain. Incoming emails se auto-detect honge ya aap mujhe bata sakte hain: 'Mera Netflix subscription 649 monthly hai'.")
            return
        lines = ["💳 **Active Subscriptions & Free Trials:**\n"]
        for s in subs:
            tag = "⏳ Trial" if s.get('is_trial') else "🔁 Renewal"
            date_val = s.get('trial_expiry_date') if s.get('is_trial') else s.get('next_renewal_date')
            amt_str = f" ₹{s['amount']}" if s.get('amount') else ""
            cycle = f" ({s.get('billing_cycle')})" if s.get('billing_cycle') else ""
            cancel_str = f" | [Cancel Link]({s['cancel_url']})" if s.get('cancel_url') else ""
            lines.append(f"• **{s['service_name']}** — {tag}: {date_val}{amt_str}{cycle}{cancel_str}")
        await say(chat_id, "\n".join(lines))
        return

    # 6. Follow-Up & Unanswered Email Watchdog
    if cmd_token in {"/followups", "/followup"}:
        from services.followup_service import list_active_followups
        items = list_active_followups(chat_id)
        if not items:
            await say(chat_id, "Abhi koi active follow-up pending nahi hai! Jab aap kisi ko email bhejenge ya query karenge, bot unhe 3 din tak track karega.")
            return
        lines = ["📬 **Active Follow-Up Trackers (Awaiting Reply):**\n"]
        for it in items:
            lines.append(f"• `[ID #{it['id']}]` **{it['recipient']}** — '{it['subject']}' (Due: {it['due_date']})")
        lines.append("\nKisi follow-up ko resolve karne ke liye: `/resolve_followup <ID>`")
        await say(chat_id, "\n".join(lines))
        return

    if cmd_token == "/resolve_followup" or clean_lower.startswith("/resolve_followup"):
        parts = clean.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await say(chat_id, "Please specify the follow-up ID. Example: `/resolve_followup 1`")
            return
        fid = int(parts[1].strip())
        from services.followup_service import resolve_followup
        ok = resolve_followup(chat_id, fid)
        if ok:
            await say(chat_id, f"✅ Follow-up #{fid} successfully marked as resolved!")
        else:
            await say(chat_id, f"⚠️ Follow-up #{fid} nahi mila.")
        return

    # Master Privacy Wipe State Handling
    if state == "confirm_master_wipe":
        from state.pending import classify_confirmation_intent_with_llm
        intent, _ = await classify_confirmation_intent_with_llm(clean, {"action_kind": "master_privacy_wipe"})
        yn = True if intent == "confirm" else (False if intent == "cancel" else None)
        if yn is False:
            set_state(chat_id, None)
            PENDING_WIPE_CODES.pop(chat_id, None)
            await say(chat_id, "Theek hai, memory wipe cancel kar diya gaya hai. Aapka saara data safe hai.")
            return
        elif yn is True:
            import time as _time
            code = "".join(secrets.choice("0123456789") for _ in range(6))
            expires_at = _time.time() + 300  # 5 minutes

            connected_email = None
            try:
                is_conn, g_email = await get_valid_google_connection_by_chat_id(chat_id)
                if is_conn and g_email:
                    connected_email = g_email.strip().lower()
            except Exception:
                pass
            if not connected_email:
                try:
                    linked = get_linked_email(chat_id)
                    if linked and linked[0]:
                        connected_email = linked[0].strip().lower()
                except Exception:
                    pass

            email_sent = False
            if connected_email:
                email_body = (
                    f"Hello,\n\n"
                    f"A permanent data wipe was requested for your PeculiarAI account.\n\n"
                    f"Your 6-Digit Verification Code is: {code}\n\n"
                    f"This code will expire in 5 minutes.\n"
                    f"Enter this code in your chat to confirm permanent deletion of all memories, chat history, and reminders.\n\n"
                    f"If you did NOT request this, please reply 'cancel' in chat or ignore this email.\n\n"
                    f"— PeculiarAI Security Team"
                )
                email_sent = await send_system_email(
                    to_email=connected_email,
                    subject=f"🔐 Security OTP for Permanent Data Wipe: {code}",
                    body=email_body
                )

                PENDING_WIPE_CODES[chat_id] = {
                    "code": code,
                    "expires_at": expires_at,
                    "email": connected_email
                }
                set_state(chat_id, "awaiting_wipe_email_verification", {"code": code, "expires_at": expires_at, "email": connected_email})
                email_msg = f"Maine aapke registered email (**{connected_email}**) par 6-digit verification code bhej diya hai! 📧\n\n" if email_sent else f"Aapka connected email **{connected_email}** hai.\n\n"
                await say(
                    chat_id,
                    f"🔐 **Security Verification Required (Valid for 5 mins)**\n\n"
                    f"{email_msg}"
                    f"🔑 **Verification Code: `{code}`**\n\n"
                    f"Aap ise 2 tareeqon me se kisi ek se verify kar sakte hain:\n"
                    f"1️⃣ **Yahan Chat Me**: Seedha ye code `{code}` yahan chat me reply karein.\n"
                    f"2️⃣ **Email Se**: Apne connected email (`{connected_email}`) se bot ki email (`{BOT_EMAIL}`) par ye code Subject ya Body me bhej kar verify karein.\n\n"
                    f"Code verify hote hi aapki Telegram, WhatsApp aur App ki saari memory permanently wipe kar di jayegi.\n"
                    f"(Ya reply `cancel` to abort)"
                )
                return
            else:
                PENDING_WIPE_CODES[chat_id] = {
                    "code": code,
                    "expires_at": expires_at,
                    "email": None
                }
                set_state(chat_id, "awaiting_wipe_chat_otp", {"code": code, "expires_at": expires_at})
                await say(
                    chat_id,
                    f"🔐 **Security Verification (Valid for 5 mins)**\n\n"
                    f"Saari memory permanently delete karne ke liye agle 5 minute ke andar ye 6-digit verification code yahan chat me reply karein ya bot ki email (`{BOT_EMAIL}`) par bhejein:\n\n"
                    f"🔑 **Verification Code: `{code}`**\n\n"
                    f"Reply `cancel` to abort."
                )
                return
        else:
            # User did not confirm master wipe; they asked a question or gave another command (e.g. "notion pe jo mene tumh kha vo krne h bs yh kyu?")
            # Master wipe is an irreversible nuclear action: any hesitation or diversion safely exits the wipe state
            # so the user is NEVER trapped in a confirmation loop.
            set_state(chat_id, None)
            PENDING_WIPE_CODES.pop(chat_id, None)
            logger.info(f"User {chat_id} did not confirm master wipe (message: '{clean[:50]}'); auto-clearing wipe state and falling through to agent loop.")
            await handle_active(chat_id, clean)
            return

    if state in ("awaiting_wipe_email_verification", "awaiting_wipe_chat_otp", "awaiting_wipe_verification"):
        import time as _time
        if cmd_token in {"/cancel", "cancel"} or clean_lower in {"cancel", "rehne do", "chhoro", "no", "nahi", "abort", "stop"}:
            set_state(chat_id, None)
            PENDING_WIPE_CODES.pop(chat_id, None)
            await say(chat_id, "Memory wipe verification cancel kar di gayi hai. Aapka data safe hai.")
            return

        pending_info = PENDING_WIPE_CODES.get(chat_id) or (data if isinstance(data, dict) else {})
        if pending_info and _time.time() > pending_info.get("expires_at", 0):
            set_state(chat_id, None)
            PENDING_WIPE_CODES.pop(chat_id, None)
            await say(chat_id, "⏳ Verification code expire ho gaya hai (5 minutes exceeded). Memory delete nahi hui. Agar wipe karna chahte hain to dobara request karein.")
            return

        expected_code = str(pending_info.get("code", "")).strip()
        email_addr = pending_info.get("email")

        # Allow user to reply directly with the OTP in chat!
        if expected_code and (expected_code in clean or clean.strip() == expected_code):
            set_state(chat_id, None)
            PENDING_WIPE_CODES.pop(chat_id, None)
            RECENT_CHAT_TURNS.pop(chat_id, None)
            RECENT_DOCS.pop(chat_id, None)
            PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
            wipe_all_user_memories(chat_id)
            await say(
                chat_id,
                "✅ **Privacy Wipe Complete**: Aapki saari memories, chat history, contacts, smart lists aur reminders permanently delete kar diye gaye hain (Telegram, WhatsApp aur App sabhi jagah se)."
            )
            return

        # Check if user's input looks like an attempted OTP (digits or explicit code mention)
        is_otp_attempt = bool(re.search(r'\b\d{4,8}\b', clean) or re.search(r'\b(code|otp|pin|verification)\b', clean_lower))
        if is_otp_attempt:
            if email_addr:
                await say(
                    chat_id,
                    f"⚠️ Sahi 6-digit code match nahi hua. Kripya code `{expected_code}` enter karein, ya apne email (`{email_addr}`) se `{BOT_EMAIL}` par bhejein, ya 'cancel' likhein."
                )
            else:
                await say(
                    chat_id,
                    f"⚠️ Sahi 6-digit code match nahi hua. Kripya code `{expected_code}` enter karein ya 'cancel' likhein."
                )
            return

        # If user is asking a normal conversational question (e.g. "meri github profile kya hai", "mwei jo link email ekbr btana"):
        # Reset state and handle their active request directly so they never get trapped!
        set_state(chat_id, None)
        await handle_active(chat_id, clean)
        return

    if cmd_token in {"/clear", "/reset"}:
        set_state(chat_id, None)
        PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
        try:
            with engine.begin() as conn:
                conn.execute(text("DELETE FROM chat_logs WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid)"), {"cid": chat_id})
        except Exception:
            pass
        await say(chat_id, "Maine aapki purani chat history memory se clear kar di hai! Ab hum fresh conversation start kar sakte hain. Bataiye kya madad chahiye?")
        return

    # Owner & Diagnostic Commands (Part 5: Live Observability)
    if cmd_token in {"/connectors", "connectors"}:
        from connectors.registry import get_all_connectors
        lines = ["🔌 **Connected Integrations & Capabilities:**\n"]
        for c in get_all_connectors():
            st = c.auth_state(chat_id)
            icon = "🟢" if st == "connected" else ("🟡" if st == "partial" else "⚪")
            tools_str = ", ".join([t.name for t in c.tools()])
            lines.append(f"{icon} **{c.display_name}** ({st.upper()}):\n  • Tools: `{tools_str}`")
        await say(chat_id, "\n\n".join(lines))
        return

    if cmd_token in {"/selftest", "selftest"}:
        from connectors.registry import get_all_connectors
        lines = ["🧪 **Live Connector Self-Test & Diagnostic:**\n"]
        for c in get_all_connectors():
            st = c.auth_state(chat_id)
            h = await c.health_check(chat_id)
            m = c.mock()
            m_ok = len(m.tools()) > 0
            h_icon = "✅" if h.ok else "⚠️"
            lines.append(f"{h_icon} **{c.display_name}**: State={st.upper()} | {h.message} ({h.latency_ms}ms) | Mock={'PASS' if m_ok else 'FAIL'}")
        await say(chat_id, "\n".join(lines))
        return

    if cmd_token in {"/debug", "debug"}:
        from connectors.trace import get_last_trace
        tr = get_last_trace(chat_id)
        if not tr:
            await say(chat_id, "No recent turn trace recorded.")
            return
        msg = (
            f"🔍 **Last Turn Observability Trace** (`{tr['trace_id']}`)\n\n"
            f"• **User Message:** {tr['message'][:150]}\n"
            f"• **Tools Called:** {tr['tools_called']}\n"
            f"• **Latency:** {tr['latency_ms']} ms | **Tokens:** {tr['tokens']}\n"
            f"• **Guard Status:** {tr['guard_results']}\n"
            f"• **Suspect Turn:** {tr['is_suspect']} ({tr['suspect_reason'] or 'Clean'})\n"
            f"• **Timestamp:** {tr['created_at']}"
        )
        await say(chat_id, msg)
        return

    # In AGENT_CORE=v2 mode, route directly to the unified Agent Loop (single brain for reasoning, tools, and pending state)
    if os.getenv("AGENT_CORE", "v2").lower() == "v2":
        await handle_active(chat_id, clean)
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
            parsed_time = parse_followup_time(clean, timeutil.now(IST))
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

        # Check if user is asking for their agenda, reminders, or schedule (not giving meeting details)
        clean_low = clean.lower()
        is_agenda_inquiry = any(w in clean_low for w in ("remidner", "reminder", "reminders", "agenda", "schedule", "task", "tasks")) and not any(w in clean_low for w in ("create", "set", "add", "laga", "bana", "banao"))
        if is_agenda_inquiry:
            set_state(chat_id, None)
            await handle_active(chat_id, clean)
            return

        # Check if user is asking an informational question about previous context/conversation
        is_context_q = "?" in clean or any(phrase in clean_low for phrase in ("kya naam", "kya bataya", "kya tha", "kya bola", "kya kaha", "what did i", "what was", "which project", "konsa project", "meeting ka title", "title kya", "kya rakhne", "kya rakhne ko")) or ("kya" in clean_low and "bataya" in clean_low)
        if is_context_q and not any(w in clean_low for w in ("baje", "pm", "am", "today", "tomorrow", "kal", "aaj", "parso")):
            history = list(RECENT_CHAT_TURNS.get(chat_id, []))
            reply = await answer_conversation_memory(clean, history, get_language(chat_id))
            record_chat_turn(chat_id, "assistant", reply)
            await say_raw(chat_id, reply)
            return

        # Check if user is actually providing date/time or meeting details
        parsed = parse_followup_time(clean, timeutil.now(IST))
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
        parsed = parse_followup_time(clean, timeutil.now(IST))
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
            'created_at': timeutil.now(IST),
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
            today = timeutil.today(IST)
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
        await say(chat_id, await get_welcome_consent_text(chat_id))
        return
    if not get_persona(chat_id):
        set_state(chat_id, "persona")
        await ask_persona(chat_id)
        return

    # Legacy v1 mode language tracking (only reached if AGENT_CORE=v1)
    detected = lang_hint or detect_text_language(clean)
    stored = get_language(chat_id)
    if detected and detected != stored:
        set_language(chat_id, detected)
        stored = detected
    reply_lang.set(stored)

    if chat_id in PENDING_GOOGLE_ACTIONS:
        pending = PENDING_GOOGLE_ACTIONS[chat_id]
        created_at = pending.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(created_at)
            except Exception:
                created_at = None
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=IST)
        age = (timeutil.now(IST) - created_at).total_seconds() if created_at else 0
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
                    parsed_new = parse_followup_time(clean, timeutil.now(IST))
                    if parsed_new and getattr(parsed_new, 'is_ambiguous', False):
                        amb_h = getattr(parsed_new, 'ambiguous_hour', None) or (parsed_new[0].hour % 12 or 12 if parsed_new[0] else 10)
                        await say(
                            chat_id,
                            f"Kya aap subah {amb_h}:00 AM ya raat {amb_h}:00 PM ki meeting chahte hain? Please specify (jaise '{amb_h} AM' ya '{amb_h} PM'). Pending meeting cancel nahi hui hai."
                        )
                        return
                    if parsed_new and parsed_new[0] is not None:
                        new_due_time, new_due_date = parsed_new
                        new_dt = datetime.combine(new_due_date, new_due_time, tzinfo=IST)
                        pending['start_iso'] = new_dt.isoformat()
                        pending['created_at'] = timeutil.now(IST)

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

                from state.pending import classify_confirmation_intent
                intent, _ = classify_confirmation_intent(clean, pending)
                if intent == "cross_question":
                    # User is asking a cross-question (e.g. checking schedule/free time)
                    # KEEP pending action intact so it can be confirmed afterwards!
                    await handle_active(chat_id, clean)
                    p_title = pending.get('title') or "Meeting"
                    await say(chat_id, f"📌 **Pending Confirmation:** Aapka '{p_title}' schedule karne ka request abhi bhi waiting hai. Kya main ise confirm kar doon? (Reply 'yes' ya 'cancel')")
                    return

                # User gave an entirely different command; pop pending action and route
                PENDING_GOOGLE_ACTIONS.pop(chat_id, None)
                await handle_active(chat_id, clean)
                return

    if clean.lower() == "/start":
        if has_consent(chat_id):
            await say(chat_id, "Welcome back!\n\n" + HELP_TEXT)
        else:
            set_state(chat_id, "consent")
            await say(chat_id, await get_welcome_consent_text(chat_id))
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
            await say(chat_id, await get_welcome_consent_text(chat_id))
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
        try:
            from state.active_context import get_active_document
            db_doc = get_active_document(chat_id)
            if db_doc:
                RECENT_DOCS[chat_id] = db_doc
                doc = db_doc
        except Exception:
            pass
    if not doc:
        return False
    ts = doc.get("timestamp")
    if ts:
        if isinstance(ts, str):
            try:
                from datetime import datetime
                ts = datetime.fromisoformat(ts)
            except Exception:
                ts = None
        if ts:
            try:
                age = (timeutil.now(IST) - ts).total_seconds()
                if age > 7200:  # 2 hours validity
                    return False
            except Exception:
                pass

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
            await say(chat_id, await get_welcome_consent_text(chat_id))
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

        from multimodal import LAST_DOWNLOADED_PHOTOS
        photo_path = LAST_DOWNLOADED_PHOTOS.get(file_id, "")

        RECENT_DOCS[chat_id] = {
            "filename": "Photo.jpg",
            "content": analysis,
            "path": photo_path,
            "file_id": file_id,
            "type": "image",
            "summary": analysis,
            "timestamp": timeutil.now(IST),
        }
        record_chat_turn(chat_id, "user", f"[Sent Photo] {caption or ''}")

        try:
            from state.active_context import set_active_document
            set_active_document(chat_id, {
                "filename": "Photo.jpg",
                "content": analysis,
                "path": photo_path,
                "file_id": file_id,
                "timestamp": timeutil.now(IST).isoformat(),
            })
        except Exception:
            pass

        # Case A: User sent photo WITHOUT an explicit caption
        if not (caption and caption.strip()):
            try:
                r = await understand(f"User sent image. Details: {analysis}. Caption: {caption or ''}")
                if r.get("intent") == "save_item" and r.get("item"):
                    item = r["item"]
                    title = item.get("title") or "Bill / Expense"
                    due_d = item.get("due_date") or ""
                    amt = item.get("amount") or ""
                    detail_lines = [f"• **Item:** {title}"]
                    if due_d:
                        detail_lines.append(f"• **Due Date:** {due_d}")
                    if amt:
                        detail_lines.append(f"• **Amount:** ₹{amt}")
                    detail_str = "\n".join(detail_lines)

                    PENDING_GOOGLE_ACTIONS[chat_id] = {
                        "kind": "save_item",
                        "item": item,
                        "created_at": timeutil.now(IST)
                    }
                    confirm_msg = (
                        f"🖼️ Maine aapke image se ye details note ki hain:\n\n"
                        f"{detail_str}\n\n"
                        f"👉 **Kya main iska reminder schedule kar du, ya is document se related kuch aur save/janna hai?**\n"
                        f"*(Bataiye: 'haan / schedule kar do' ya kisi contact ko bhejna hai)* 😊"
                    )
                    record_chat_turn(chat_id, "assistant", confirm_msg)
                    await say_raw(chat_id, confirm_msg)
                    return

                elif r.get("intent") == "create_meeting":
                    m_title = r.get("title") or "Meeting"
                    m_start = r.get("start_iso") or ""
                    PENDING_GOOGLE_ACTIONS[chat_id] = {
                        "kind": "create_meeting",
                        "title": m_title,
                        "start_iso": m_start,
                        "duration_minutes": r.get("duration_minutes", 30),
                        "attendees": r.get("attendees", []),
                        "created_at": timeutil.now(IST)
                    }
                    confirm_msg = (
                        f"🖼️ Maine aapke image se meeting details note ki hain:\n\n"
                        f"• **Title:** {m_title}\n"
                        f"• **Time:** {m_start}\n\n"
                        f"👉 **Kya main ye meeting Google Calendar me schedule kar du?** ('haan / schedule kar do' bolein) 😊"
                    )
                    record_chat_turn(chat_id, "assistant", confirm_msg)
                    await say_raw(chat_id, confirm_msg)
                    return
            except Exception as e_und:
                print("photo understand error:", repr(e_und))

            user_lang = get_language(chat_id)
            if user_lang == "hi":
                photo_ack = (
                    "🖼️ Maine aapka image analyze kar liya hai.\n\n"
                    "Aap iske saath kya karna chahte hain?\n"
                    "• Kisi contact ko bhejna (jaise `@handle ko bhej do`)\n"
                    "• Isme se text, details ya summary nikalna\n"
                    "• Reminder schedule karna ya note save karna"
                )
            else:
                photo_ack = (
                    "🖼️ I have analyzed your image.\n\n"
                    "What would you like me to do with it?\n"
                    "• Forward or share with a contact (e.g. *'Send to @handle'*)\n"
                    "• Extract details or summary\n"
                    "• Schedule a reminder or save as a note"
                )
            record_chat_turn(chat_id, "assistant", photo_ack)
            await say_raw(chat_id, photo_ack)
            return

        # Case B: User sent photo WITH a prompt/caption
        cap_low = caption.strip().lower()
        explicit_sched = any(w in cap_low for w in ("schedule", "reminder", "remind", "save", "laga do", "set kar", "add", "daal do", "bana do"))
        explicit_contact = any(w in cap_low for w in ("contact", "visiting card", "business card", "number save", "contact save"))

        if explicit_contact:
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
                    reply_msg = f"✅ Contact permanently saved in memory: {c_name} ({card_json.get('company') or 'Business Contact'})"
                    record_chat_turn(chat_id, "assistant", reply_msg)
                    await say(chat_id, reply_msg)
                    return
            except Exception as card_err:
                print("contact save from photo error:", repr(card_err))

        if explicit_sched:
            try:
                r = await understand(f"User sent image. Details: {analysis}. Caption: {caption or ''}")
                if r.get("intent") == "save_item" and r.get("item"):
                    await save_item_flow(chat_id, r["item"])
                    return
                elif r.get("intent") == "create_meeting":
                    await prepare_google_action(chat_id, r)
                    return
            except Exception as sched_err:
                print("photo sched error:", repr(sched_err))

        agent_prompt = (
            f"User uploaded photo.\n"
            f"[Attachment Available: filename='Photo.jpg', path='{photo_path}']\n\n"
            f"--- Image Analysis Details ---\n"
            f"{analysis[:3000]}\n"
            f"--- End Image Analysis ---\n\n"
            f"User instruction: {caption.strip()}"
        )
        await handle_active(chat_id, agent_prompt)
        return

    except Exception as e:
        print("photo processing error:", repr(e))
        await say(chat_id, "Sorry, I had trouble processing that image. Please try again or type.")


async def process_document(chat_id, doc, caption=""):
    try:
        st = get_state(chat_id)
        if st is None:
            ensure_user(chat_id)
            set_state(chat_id, "consent")
            await say(chat_id, await get_welcome_consent_text(chat_id))
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
        file_path = res.get("file_path", "")
        RECENT_DOCS[chat_id] = {
            "filename": file_name,
            "content": text_content,
            "path": file_path,
            "file_id": file_id,
            "mime_type": mime_type,
            "type": "document",
            "summary": "",
            "timestamp": timeutil.now(IST),
        }
        record_chat_turn(chat_id, "user", f"[Sent Document: {file_name}] {caption or ''}")

        try:
            from state.active_context import set_active_document
            set_active_document(chat_id, {
                "filename": file_name,
                "content": text_content,
                "path": file_path,
                "file_id": file_id,
                "mime_type": mime_type,
                "timestamp": timeutil.now(IST).isoformat(),
            })
        except Exception as act_err:
            print("set_active_document error:", repr(act_err))

        if caption.strip():
            agent_prompt = (
                f"User uploaded document '{file_name}'.\n"
                f"[Attachment Available: filename='{file_name}', path='{file_path}', file_id='{file_id}']\n\n"
                f"--- Extracted Document Content (first 5000 chars) ---\n"
                f"{text_content[:5000]}\n"
                f"--- End Document Content ---\n\n"
                f"User instruction: {caption.strip()}"
            )
            await handle_active(chat_id, agent_prompt)
            return

        # Clean acknowledgment when user sends document without a caption (no unsolicited text dumps)
        user_lang = get_language(chat_id)
        if user_lang == "hi":
            ack_msg = (
                f"📄 Maine aapka document **{file_name}** analyze kar liya hai.\n\n"
                f"Aap iske saath kya karna chahte hain?\n"
                f"• Kisi contact ko bhejna (jaise `@handle ko bhej do`)\n"
                f"• Summary ya main points nikalna\n"
                f"• Reminder schedule karna ya save karna"
            )
        else:
            ack_msg = (
                f"📄 I have analyzed your document **{file_name}**.\n\n"
                f"What would you like me to do with it?\n"
                f"• Forward to a contact (e.g. *'Send this to @handle'*)\n"
                f"• Summarize key points or extract details\n"
                f"• Schedule a reminder or save it"
            )
        record_chat_turn(chat_id, "assistant", ack_msg)
        await say_raw(chat_id, ack_msg)
        return

    except Exception as e:
        print("document processing error:", repr(e))
        await say(chat_id, "Sorry, I couldn't process that document. Please try again.")


# ---------- Reminders ----------

HEADS = {
    "day_before": "📅 Due tomorrow",
    "due_day": "☀️ Due today",
    "15min": "⏰ In 15 minutes",
    "5min": "⚡ In 5 minutes",
    "at_time": "🔔 It's time",
    "reminder": "🔔 Reminder",
}


def reminder_text(label, category, title, amount, due_time):
    head = HEADS.get(label, '🔔 Reminder')
    time_str = ""
    if due_time:
        if hasattr(due_time, "strftime"):
            time_str = f" at {due_time.strftime('%I:%M %p')}"
        else:
            time_str = f" at {due_time}"
    amt_str = f" | {amount}" if amount is not None else ""
    cat_str = f" [{CATEGORIES.get(category, category)}]" if category and category != "reminder" else ""
    return f"{head}:{cat_str} {title}{time_str}{amt_str}"


def validate_startup_environment():
    """
    Validates essential environment variables on application startup.
    Fails fast in production if critical configuration is missing or invalid.
    """
    is_prod = (
        os.getenv("ENV", "").lower() == "production"
        or os.getenv("RENDER", "").lower() == "true"
        or os.getenv("ENVIRONMENT", "").lower() == "production"
    )

    db_url = os.getenv("DATABASE_URL")
    if is_prod:
        if not db_url:
            raise RuntimeError("CRITICAL STARTUP ERROR: DATABASE_URL is not set in production!")
        if "sqlite" in db_url.lower():
            raise RuntimeError("CRITICAL STARTUP ERROR: SQLite database is forbidden in production! Provide a PostgreSQL connection.")

    is_staging = (
        os.getenv("ENV", "").lower() == "staging"
        or os.getenv("APP_ENV", "").lower() == "staging"
        or os.getenv("ENVIRONMENT", "").lower() == "staging"
        or os.getenv("STAGING_MODE", "").strip() == "1"
    )
    if is_staging:
        prod_fp = os.getenv("PROD_DB_FINGERPRINT", "").strip()
        if prod_fp and db_url:
            import urllib.parse
            parsed_db = urllib.parse.urlparse(db_url)
            host_or_path = f"{parsed_db.hostname or ''}/{parsed_db.path or ''}"
            if prod_fp in host_or_path or prod_fp in db_url:
                raise RuntimeError(
                    f"CRITICAL STAGING GUARD: Staging is configured to use production database ({prod_fp})! "
                    "Refusing to start. Staging must use its own isolated, empty database."
                )

    has_gemini = bool(os.getenv("GEMINI_API_KEY"))
    has_groq = bool(os.getenv("GROQ_API_KEY"))
    if not (has_gemini or has_groq):
        err_msg = "CRITICAL STARTUP ERROR: At least one LLM API key (GEMINI_API_KEY or GROQ_API_KEY) must be provided."
        if is_prod:
            raise RuntimeError(err_msg)
        else:
            logging.getLogger("uvicorn").warning(err_msg)

    active_core = os.getenv("AGENT_CORE", "v2").lower()
    logging.getLogger("uvicorn").info(f"STARTUP CONFIG: AGENT_CORE={active_core}")


def _parse_reminder_ist_dt(scheduled_at):
    if not scheduled_at:
        return None
    sched_dt = None
    if isinstance(scheduled_at, str):
        try:
            sched_dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        except Exception:
            try:
                sched_dt = datetime.strptime(scheduled_at.split(".")[0], "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
    elif isinstance(scheduled_at, datetime):
        sched_dt = scheduled_at
    if not sched_dt:
        return None
    if getattr(sched_dt, "tzinfo", None) is not None:
        return sched_dt.astimezone(IST)
    return sched_dt.replace(tzinfo=IST)


def _parse_due_time_obj(due_time):
    from datetime import time as dt_time
    if not due_time:
        return None
    if hasattr(due_time, "hour") and hasattr(due_time, "minute"):
        return dt_time(due_time.hour, due_time.minute, getattr(due_time, "second", 0))
    if isinstance(due_time, str):
        try:
            parts = [int(p) for p in due_time.strip().split(":")[:3]]
            return dt_time(parts[0], parts[1], parts[2] if len(parts) > 2 else 0)
        except Exception:
            return None
    return None


def _is_stale_prealert(item_id, label, sched_ist, due_time, now, at_time_item_ids) -> bool:
    """Returns True when a lead-time pre-alert is superseded by an already-past event or concurrent at_time alert."""
    if label not in ("day_before", "due_day", "15min", "5min"):
        return False
    if item_id is not None and item_id in at_time_item_ids:
        return True
    if not sched_ist:
        return False
    if label == "15min":
        return now >= (sched_ist + timedelta(minutes=15))
    if label == "5min":
        return now >= (sched_ist + timedelta(minutes=5))
    t_obj = _parse_due_time_obj(due_time)
    if t_obj is None:
        return False
    event_date = sched_ist.date() + (timedelta(days=1) if label == "day_before" else timedelta(days=0))
    event_dt = datetime.combine(event_date, t_obj, tzinfo=IST)
    return now >= event_dt


async def run_reminders(worker_id: str = "worker_default", is_catchup: bool = False) -> int:
    """
    DB-driven multi-instance reminder delivery.
    Claims rows atomically via SKIP LOCKED to prevent duplicate dispatches across instances.
    Coalesces stale lead-time pre-alerts and records delivery failures with bounded retries.
    """
    now = timeutil.now(IST)
    from db import (
        claim_pending_reminders,
        mark_reminder_superseded,
        record_reminder_failure,
        canonical_chat_id,
        get_all_linked_chat_ids,
    )

    claimed = claim_pending_reminders(worker_id=worker_id, now=now)
    if not claimed:
        return 0

    at_time_item_ids = {
        row[9]
        for row in claimed
        if len(row) > 9 and row[9] is not None and row[7] in ("at_time", "due")
    }

    sent_count = 0
    for row in claimed:
        rid = row[0]
        chat_id = row[1]
        category = row[2]
        title = row[3]
        amount = row[4]
        _due_date = row[5]
        due_time = row[6]
        label = row[7]
        scheduled_at = row[8] if len(row) > 8 else None
        item_id = row[9] if len(row) > 9 else None

        sched_ist = _parse_reminder_ist_dt(scheduled_at)

        # Suppress legacy 9 AM day_before / due_day reminders (8:00 AM morning briefing covers daily schedules)
        if label in ("day_before", "due_day"):
            try:
                mark_reminder_superseded(rid, sent_at=now)
            except Exception as sup_err:
                print(f"Error superseding 9am reminder {rid}:", sup_err)
                record_reminder_failure(rid, str(sup_err))
            continue

        # Suppress obsolete pre-alerts ("In 15 min", "In 5 min", "Due today") if event time already passed
        if _is_stale_prealert(item_id, label, sched_ist, due_time, now, at_time_item_ids):
            try:
                mark_reminder_superseded(rid, sent_at=now)
            except Exception as sup_err:
                print(f"Error superseding stale pre-alert {rid}:", sup_err)
                record_reminder_failure(rid, str(sup_err))
            continue

        # Determine if reminder was delayed (> 2 mins late due to server sleep/restart)
        is_delayed = bool(sched_ist and (now - sched_ist).total_seconds() > 120)

        reply_lang.set(get_language(chat_id))
        base_msg = reminder_text(label, category, title, amount, due_time)
        if is_delayed:
            msg_text = f"⚠️ [Delivered late — Server wake-up catch-up]\n{base_msg}"
        else:
            msg_text = base_msg

        # Deliver to user chat_id and linked canonical/desktop Telegram accounts
        target_ids = get_all_linked_chat_ids(chat_id)
        canon_cid = canonical_chat_id(chat_id)

        delivered_any = False
        last_err = None
        for cid in target_ids:
            try:
                await send_telegram_text(cid, msg_text)
                delivered_any = True
            except Exception as e:
                last_err = str(e)
                print(f"Error sending reminder {rid} to {cid}:", e)

        if delivered_any or not target_ids:
            if delivered_any:
                record_chat_turn(canon_cid, "assistant", msg_text)
            try:
                mark_sent(rid, sent_at=now)
                sent_count += 1
            except Exception as ack_err:
                print(f"Error acknowledging reminder {rid}:", ack_err)
                record_reminder_failure(rid, str(ack_err))
        else:
            record_reminder_failure(rid, last_err or "Telegram delivery failed for all linked targets")

    return sent_count


async def run_startup_catchup_pass():
    """
    Startup catch-up pass: delivers missed reminders that accrued while the server was asleep or restarting.
    """
    worker_id = f"startup_catchup_{os.getpid()}"
    try:
        count = await run_reminders(worker_id=worker_id, is_catchup=True)
        if count > 0:
            logging.getLogger("uvicorn").info(f"Startup catch-up pass delivered {count} missed reminder(s).")
    except Exception as exc:
        logging.getLogger("uvicorn").error(f"Startup catch-up pass error: {exc}")


async def reminder_loop():
    while True:
        try:
            await run_reminders(worker_id=f"worker_loop_{os.getpid()}")
        except Exception as e:
            print("reminder error:", e)
        await asyncio.sleep(30)


async def run_commitment_nudges():
    try:
        from memory.store import get_commitments_due_for_nudge, update_commitment_status
        due_items = get_commitments_due_for_nudge()
        for item in due_items:
            cid = item["id"]
            chat_id = item["chat_id"]
            title = item["title"]
            nudge_cnt = item["nudge_count"] + 1
            max_cnt = item["max_nudges"]

            msg_text = (
                f"🔔 **Commitment Follow-up (Nudge {nudge_cnt}/{max_cnt}):**\n"
                f"'{title}'\n\n"
                f"Kya yeh task/promise complete ho gaya hai? (Reply 'done' ya 'later')"
            )
            try:
                await say(chat_id, msg_text)
            except Exception as e:
                print(f"Error sending commitment nudge {cid} to {chat_id}:", e)

            update_commitment_status(
                commitment_id=cid,
                user_id=item["user_id"],
                status="nudged" if nudge_cnt >= max_cnt else "pending",
                increment_nudge=True
            )
    except Exception as exc:
        print("run_commitment_nudges error:", exc)


async def commitment_nudge_loop():
    while True:
        try:
            await run_commitment_nudges()
        except Exception as e:
            print("commitment loop error:", e)
        await asyncio.sleep(60)


@asynccontextmanager
async def lifespan(app):
    import sys
    validate_startup_environment()
    if os.getenv("PYTEST_CURRENT_TEST") or "pytest" in sys.modules or os.getenv("TESTING") == "1":
        # Avoid spawning daemon scheduler/catchup loops inside pytest sessions,
        # which would mutate shared SQLite tables or fire httpx mocks mid-test.
        yield
        return

    from morning_briefing import morning_briefing_loop
    from heartbeat import heartbeat_loop

    async def _warmup():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1")).fetchall()
            try:
                db._init_memory_items_status_column()
                db._init_memory_items_recurrence_column()
                db._init_memory_items_calendar_id_column()
                db._init_reminders_scheduler_columns()
            except Exception as mig_err:
                logging.getLogger("uvicorn").warning(f"Startup schema migration notice: {mig_err}")
            from memory.retrieval import assemble_memory_context
            from agent.prompt import build_runtime_system_prompt
            for cid in (1758056012, 1234828592):
                m_ctx = assemble_memory_context(cid, "ping")
                await build_runtime_system_prompt(cid, "ping", mem_ctx=m_ctx)
            # Immediate catch-up pass on startup for any missed reminders during sleep/reboot
            await run_startup_catchup_pass()
        except Exception as w_err:
            logging.getLogger("uvicorn").warning(f"Startup warmup notice: {w_err}")
    asyncio.create_task(_warmup())

    tasks = [
        asyncio.create_task(reminder_loop()),
        asyncio.create_task(commitment_nudge_loop()),
        asyncio.create_task(mailbox_loop()),
        asyncio.create_task(connected_user_gmail_monitor_loop()),
        asyncio.create_task(morning_briefing_loop()),
        asyncio.create_task(heartbeat_loop()),
    ]
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
            verified INTEGER NOT NULL DEFAULT 0,
            device_id TEXT
        )""")
        cols = {r[1] for r in conn.execute("PRAGMA table_info(web_verifications)").fetchall()}
        if "device_id" not in cols:
            conn.execute("ALTER TABLE web_verifications ADD COLUMN device_id TEXT")
        conn.commit()


def _otp_digest(token, otp):
    return hashlib.sha256(f"{token}:{otp}".encode("utf-8")).hexdigest()


def _create_web_verification(phone, device_id: str = None):
    import time as _time
    token = secrets.token_urlsafe(20).replace("-", "").replace("_", "")[:28]
    otp = f"{secrets.randbelow(1_000_000):06d}"
    _init_web_verify_db()
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        conn.execute("DELETE FROM web_verifications WHERE (verified = 0 AND created_at < ?) OR (created_at < ?)", (_time.time() - 1800, _time.time() - 86400))
        conn.execute(
            "INSERT INTO web_verifications(token, phone, otp_hash, created_at, device_id) VALUES (?, ?, ?, ?, ?)",
            (token, phone, _otp_digest(token, otp), _time.time(), device_id),
        )
        conn.commit()
    return token, otp


def _get_web_session_info(verification_id: str, max_age: float = 86400.0) -> dict:
    """Validate verification_id and return session details: chat_id, phone, device_id."""
    if not verification_id or not isinstance(verification_id, str):
        raise HTTPException(status_code=400, detail="Verification session ID is required")
    token = verification_id.strip()
    _init_web_verify_db()
    import time as _time
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        row = conn.execute(
            "SELECT telegram_chat_id, created_at, verified, phone, device_id FROM web_verifications WHERE token = ?",
            (token,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=400, detail="Verification session not found. Please verify again.")
    chat_id_str, created_at, verified, phone, dev_id = row
    if _time.time() - created_at > max_age:
        raise HTTPException(status_code=400, detail="Verification session expired. Please verify again.")
    if not verified or not chat_id_str:
        raise HTTPException(status_code=403, detail="Complete Telegram verification first")
    return {
        "chat_id": int(chat_id_str),
        "phone": phone,
        "device_id": dev_id,
    }


def _resolve_web_session(verification_id: str, max_age: float = 86400.0) -> int:
    """Validate verification_id and return the associated telegram_chat_id as int."""
    return _get_web_session_info(verification_id, max_age=max_age)["chat_id"]


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
    "http://localhost:8000",
    "http://127.0.0.1:8000",
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

from services.frontier_router import router as frontier_router
app.include_router(frontier_router)

from fastapi.staticfiles import StaticFiles
_assets_path = os.path.join(os.path.dirname(__file__), "assets")
if os.path.exists(_assets_path):
    app.mount("/assets", StaticFiles(directory=_assets_path), name="assets")

_static_path = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(_static_path, exist_ok=True)
app.mount("/static", StaticFiles(directory=_static_path), name="static")


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
    device_id = str(body.get("device_id", "")).strip() or request.headers.get("X-Device-Id", "").strip() or None
    if not re.fullmatch(r"\+?[0-9 ()-]{7,20}", phone):
        raise HTTPException(status_code=400, detail="Enter a valid phone number")
    if not OTP_BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Set TELEGRAM_OTP_BOT_TOKEN in .env")
    if not OTP_BOT_USERNAME:
        raise HTTPException(status_code=503, detail="Set TELEGRAM_OTP_BOT_USERNAME in .env")
    token, otp = _create_web_verification(phone, device_id=device_id)
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
    device_id = str(body.get("device_id", "")).strip() or request.headers.get("X-Device-Id", "").strip() or None
    if not token or not re.fullmatch(r"\d{6}", otp):
        raise HTTPException(status_code=400, detail="Enter the 6-digit code")
    _init_web_verify_db()
    with sqlite3.connect(WEB_VERIFY_DB) as conn:
        row = conn.execute("SELECT otp_hash, created_at, telegram_chat_id, attempts, verified, phone, device_id FROM web_verifications WHERE token = ?", (token,)).fetchone()
        if not row:
            raise HTTPException(status_code=400, detail="Verification request not found")
        otp_hash, created_at, chat_id, attempts, verified, phone, saved_device_id = row
        active_device_id = device_id or saved_device_id
        if verified:
            if chat_id and active_device_id:
                try:
                    ensure_user(int(chat_id))
                    with engine.connect() as u_conn:
                        u_row = u_conn.execute(
                            text("SELECT id FROM users WHERE telegram_chat_id = :chat_id"),
                            {"chat_id": int(chat_id)},
                        ).first()
                    if u_row:
                        from db import record_device_session
                        record_device_session(int(u_row[0]), phone=phone, device_id=active_device_id)
                except Exception as exc:
                    print("Error recording device session on verified check:", repr(exc))
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
        if device_id:
            conn.execute("UPDATE web_verifications SET device_id = ? WHERE token = ?", (device_id, token))
        conn.commit()

    if chat_id and active_device_id:
        try:
            ensure_user(int(chat_id))
            with engine.connect() as u_conn:
                u_row = u_conn.execute(
                    text("SELECT id FROM users WHERE telegram_chat_id = :chat_id"),
                    {"chat_id": int(chat_id)},
                ).first()
            if u_row:
                from db import record_device_session
                record_device_session(int(u_row[0]), phone=phone, device_id=active_device_id)
        except Exception as exc:
            print("Error recording device session on verify:", repr(exc))

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
    device_id = str(body.get("device_id", "")).strip() or request.headers.get("X-Device-Id", "").strip() or None
    return_url = str(body.get("return_url", "")).strip() or None

    session_info = _get_web_session_info(verification_id)
    chat_id = session_info["chat_id"]
    active_device_id = device_id or session_info.get("device_id")
    phone = session_info.get("phone")

    ensure_user(chat_id)
    give_consent(chat_id)

    is_connected, google_email = await get_valid_google_connection_by_chat_id(chat_id)
    force_change = bool(body.get("force") or body.get("change_account") or body.get("reconnect"))

    if is_connected and not force_change:
        with engine.connect() as conn:
            u_row = conn.execute(
                text("SELECT id FROM users WHERE telegram_chat_id = :chat_id"),
                {"chat_id": chat_id},
            ).first()
        user_id = int(u_row[0]) if u_row else None
        if user_id and active_device_id:
            from db import check_device_authorization, record_device_session
            record_device_session(user_id, phone=phone, device_id=active_device_id, google_email=google_email)
            return {
                "already_connected": True,
                "connected": True,
                "email": google_email,
                "message": f"Your Google account is already connected: {google_email}."
            }
        elif not active_device_id:
            return {
                "already_connected": True,
                "connected": True,
                "email": google_email,
                "message": f"Your Google account is already connected: {google_email}."
            }

    try:
        auth_url = build_auth_url(chat_id, source="web", return_url=return_url, device_id=active_device_id, phone=phone)
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
    device_id = str(body.get("device_id", "")).strip() or request.headers.get("X-Device-Id", "").strip() or None

    session_info = _get_web_session_info(verification_id)
    chat_id = session_info["chat_id"]
    active_device_id = device_id or session_info.get("device_id")
    phone = session_info.get("phone")

    ensure_user(chat_id)

    is_connected, google_email = await get_valid_google_connection_by_chat_id(chat_id)
    if not is_connected:
        return {
            "connected": False,
            "email": None,
            "requires_relogin": False,
            "message": "Google not connected",
        }

    with engine.connect() as conn:
        u_row = conn.execute(
            text("SELECT id FROM users WHERE telegram_chat_id = :chat_id"),
            {"chat_id": chat_id},
        ).first()
    user_id = int(u_row[0]) if u_row else None

    if user_id and active_device_id:
        from db import check_device_authorization, record_device_session
        auth_check = check_device_authorization(user_id, active_device_id, max_days=365)
        if not auth_check["valid"]:
            return {
                "connected": False,
                "email": google_email,
                "requires_relogin": True,
                "reason": auth_check["reason"],
                "message": auth_check["message"],
            }
        else:
            record_device_session(user_id, phone=phone, device_id=active_device_id, google_email=google_email)
            return {
                "connected": True,
                "email": google_email,
                "requires_relogin": False,
                "message": "Google connected",
            }

    return {
        "connected": is_connected,
        "email": google_email if is_connected else None,
        "requires_relogin": False,
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
        from db import canonical_chat_id, get_all_linked_chat_ids
        orig_chat_id = chat_id
        chat_id = canonical_chat_id(chat_id)
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
            "https://www.googleapis.com/auth/gmail.readonly",
        ])
        save_google_token(db_user_id, email, encrypted, scopes)
        if orig_chat_id and orig_chat_id != chat_id:
            with engine.connect() as conn:
                orig_user_row = conn.execute(
                    text("SELECT id FROM users WHERE telegram_chat_id = :chat_id"),
                    {"chat_id": orig_chat_id},
                ).first()
            if orig_user_row:
                save_google_token(int(orig_user_row[0]), email, encrypted, scopes)
        if chat_id:
            from google_services import clear_gmail_insufficient_scope_cache
            clear_gmail_insufficient_scope_cache(chat_id)
            if orig_chat_id:
                clear_gmail_insufficient_scope_cache(orig_chat_id)

            # Proactively baseline existing inbox emails so past emails are NEVER alerted
            async def _baseline_existing_emails(cid):
                try:
                    stubs = await fetch_user_inbox_messages(cid, max_results=20, query="in:inbox")
                    for s in stubs:
                        sid = s.get("id")
                        if sid:
                            await asyncio.to_thread(
                                record_processed_user_email,
                                cid,
                                sid,
                                sender=s.get("sender", ""),
                                subject=s.get("subject", ""),
                                category="initial_baseline",
                                is_important=False
                            )
                except Exception as b_err:
                    print(f"[OAuth] Baseline seed notice for {cid}: {b_err}")
            asyncio.create_task(_baseline_existing_emails(chat_id))

        # Record device session on successful Google OAuth
        callback_device_id = state_data.get("device_id")
        callback_phone = state_data.get("phone")
        if callback_device_id:
            try:
                from db import record_device_session
                record_device_session(db_user_id, phone=callback_phone, device_id=callback_device_id, google_email=email)
            except Exception as exc:
                print("Error recording device session in google callback:", repr(exc))

        # Notify user on Telegram if initiated from TG
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if tg_token and not is_web and orig_chat_id:
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{tg_token}/sendMessage",
                        json={
                            "chat_id": orig_chat_id,
                            "text": (
                                f"✅ **Google account successfully connect ho gaya!** 🌐\n\n"
                                f"📧 **Connected Email**: `{email}`\n"
                                f"📅 Google Calendar & Meet: Active\n"
                                f"📁 Google Drive: Active\n"
                                f"✉️ Gmail & Autonomous Intelligence: Active\n\n"
                                f"Ab aap mujhe bol sakte hain jaise: *'meri meetings check karo'*, *'kal 4 bje meeting schedule kar do'*, ya *'Drive par file save kar do'*."
                            ),
                            "parse_mode": "Markdown"
                        }
                    )
            except Exception as e:
                print(f"Failed to send Telegram Google notification to {orig_chat_id}:", repr(e))

        # Invalidate prompt cache so immediate next messages know Google is connected
        try:
            from agent.prompt import _USER_META_CACHE
            _USER_META_CACHE.pop(chat_id, None)
            if orig_chat_id:
                _USER_META_CACHE.pop(orig_chat_id, None)
        except Exception:
            pass

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


def _github_callback_page(title, message, success=False, redirect_url=None, bot_username=None):
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
          <a href="{safe_url}" style="display:inline-block;padding:10px 20px;background:#24292f;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">
            Return to PeculiarAI Website &rarr;
          </a>
        </p>
        <p style="font-size:13px;color:#64748b;">Redirecting automatically in 2 seconds...</p>
        """
    elif bot_username:
        clean_user = bot_username.lstrip("@")
        tg_url = f"https://t.me/{clean_user}"
        redirect_link = f"""
        <p style="margin-top:20px;">
          <a href="{tg_url}" style="display:inline-block;padding:10px 20px;background:#24292f;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;">
            Open Telegram Chat &rarr;
          </a>
        </p>
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


@app.get("/auth/github/callback", response_class=HTMLResponse)
@app.get("/github/callback", response_class=HTMLResponse)
async def github_oauth_callback(request: Request):
    """GitHub OAuth redirect endpoint. Exchanges code for full repo/user access token."""
    from github_auth import verify_state_payload, exchange_github_code, get_github_user_profile
    params = request.query_params
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    state_data = {}
    is_web = False
    return_url = None
    chat_id = None
    if state:
        try:
            state_data = verify_state_payload(state)
            chat_id = state_data.get("user_id")
            is_web = (state_data.get("source") == "web")
            return_url = state_data.get("return_url")
        except Exception:
            pass

    bot_user = os.getenv("TELEGRAM_BOT_USERNAME", "PeculiarAIBOT")

    if error:
        err_desc = params.get("error_description", "GitHub authorization was cancelled.")
        if is_web and return_url:
            target_url = _safe_web_redirect(return_url, f"github_status=error&github_message={err_desc}")
            return _github_callback_page("GitHub connection cancelled", err_desc, success=False, redirect_url=target_url)
        return _github_callback_page("GitHub connection cancelled", err_desc, success=False, bot_username=bot_user)

    if not code or not chat_id:
        return _github_callback_page("Invalid request", "Missing authorization code or session state.", success=False, bot_username=bot_user)

    try:
        from db import canonical_chat_id
        orig_chat_id = chat_id
        canon_chat_id = canonical_chat_id(chat_id)
        ensure_user(canon_chat_id)
        if orig_chat_id and orig_chat_id != canon_chat_id:
            ensure_user(orig_chat_id)
        if is_web and not has_consent(canon_chat_id):
            give_consent(canon_chat_id)

        token_data = await exchange_github_code(code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("GitHub did not return an access token.")

        profile = await get_github_user_profile(access_token)
        login = profile.get("login") or "GitHub User"
        public_repos = profile.get("public_repos", 0)

        config_json = json.dumps({
            "username": login,
            "profile_url": profile.get("html_url") or f"https://github.com/{login}",
            "public_repos": public_repos,
            "scopes": token_data.get("scope", "repo")
        })
        save_user_integration(canon_chat_id, "github", access_token, config_json)
        if orig_chat_id and orig_chat_id != canon_chat_id:
            save_user_integration(orig_chat_id, "github", access_token, config_json)

        # Notify user on Telegram if initiated from TG
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if tg_token and not is_web and orig_chat_id:
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{tg_token}/sendMessage",
                        json={
                            "chat_id": orig_chat_id,
                            "text": (
                                f"✅ **GitHub connected successfully!**\n\n"
                                f"Account: @{login}\n"
                                f"Ab main aapke repositories me files check, read aur directly commits push kar sakta hoon."
                            ),
                            "parse_mode": "Markdown"
                        }
                    )
            except Exception as tg_err:
                print("Error sending Telegram GitHub confirmation:", repr(tg_err))

        if is_web and return_url:
            target_url = _safe_web_redirect(return_url, f"github_status=connected&github_user={login}")
            return _github_callback_page(
                "GitHub connected successfully",
                f"Connected GitHub account: @{login}. Your assistant now has access to check and commit to repositories.",
                success=True,
                redirect_url=target_url
            )

        return _github_callback_page(
            "GitHub connected successfully",
            f"Connected GitHub account: @{login}. You can return to your Telegram chat to inspect files and commit changes.",
            success=True,
            bot_username=bot_user
        )

    except Exception as exc:
        print("GitHub OAuth callback error:", repr(exc))
        if is_web and return_url:
            from urllib.parse import quote
            target_url = _safe_web_redirect(return_url, f"github_status=error&github_message={quote(str(exc)[:100])}")
            return _github_callback_page("GitHub connection failed", str(exc), success=False, redirect_url=target_url)
        return _github_callback_page("GitHub connection failed", str(exc), success=False, bot_username=bot_user)


@app.get("/auth/github/start")
@app.get("/github/start")
async def github_oauth_start(request: Request):
    """Initiates 1-click GitHub OAuth via direct browser GET redirect."""
    from github_auth import is_github_oauth_configured, build_github_auth_url
    chat_id_raw = request.query_params.get("chat_id")
    source = request.query_params.get("source", "tg")
    return_url = request.query_params.get("return_url")

    if not chat_id_raw or not chat_id_raw.isdigit():
        raise HTTPException(status_code=400, detail="Valid chat_id query parameter is required.")

    if not is_github_oauth_configured():
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured on this server.")

    chat_id = int(chat_id_raw)
    ensure_user(chat_id)
    try:
        auth_url = build_github_auth_url(chat_id, source=source, return_url=return_url)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=auth_url, status_code=307)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/web/github/start")
async def web_github_start(request: Request):
    """Initiates 1-click GitHub OAuth for a web session."""
    from github_auth import is_github_oauth_configured, build_github_auth_url
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    verification_id = str(body.get("verification_id", "")).strip()
    return_url = str(body.get("return_url", "")).strip() or None
    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)

    if not is_github_oauth_configured():
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured on this server.")

    try:
        auth_url = build_github_auth_url(chat_id, source="web", return_url=return_url)
        return {"auth_url": auth_url, "already_connected": False}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ---------- Notion OAuth Endpoints ----------

@app.get("/auth/notion/callback", response_class=HTMLResponse)
@app.get("/notion/callback", response_class=HTMLResponse)
async def notion_oauth_callback(request: Request):
    """Notion OAuth redirect endpoint. Exchanges code for workspace access token."""
    from notion_auth import verify_state_payload, exchange_notion_code
    params = request.query_params
    code = params.get("code")
    state = params.get("state")
    error = params.get("error")

    state_data = {}
    is_web = False
    return_url = None
    chat_id = None
    if state:
        try:
            state_data = verify_state_payload(state)
            chat_id = state_data.get("user_id")
            is_web = (state_data.get("source") == "web")
            return_url = state_data.get("return_url")
        except Exception:
            pass

    bot_user = os.getenv("TELEGRAM_BOT_USERNAME", "PeculiarAIBOT")

    if error:
        err_desc = params.get("error_description", "Notion authorization was cancelled.")
        if is_web and return_url:
            target_url = _safe_web_redirect(return_url, f"notion_status=error&notion_message={err_desc}")
            return _github_callback_page("Notion connection cancelled", err_desc, success=False, redirect_url=target_url)
        return _github_callback_page("Notion connection cancelled", err_desc, success=False, bot_username=bot_user)

    if not code or not chat_id:
        return _github_callback_page("Invalid request", "Missing authorization code or session state.", success=False, bot_username=bot_user)

    try:
        from db import canonical_chat_id
        orig_chat_id = chat_id
        canon_chat_id = canonical_chat_id(chat_id)
        ensure_user(canon_chat_id)
        if orig_chat_id and orig_chat_id != canon_chat_id:
            ensure_user(orig_chat_id)
        if is_web and not has_consent(canon_chat_id):
            give_consent(canon_chat_id)

        token_data = await exchange_notion_code(code)
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError("Notion did not return an access token.")

        workspace_name = token_data.get("workspace_name") or "Notion Workspace"
        bot_id = token_data.get("bot_id", "")
        workspace_icon = token_data.get("workspace_icon", "")

        config_json = json.dumps({
            "workspace_name": workspace_name,
            "bot_id": bot_id,
            "workspace_icon": workspace_icon,
            "token_type": token_data.get("token_type", "bearer")
        })
        save_user_integration(canon_chat_id, "notion", access_token, config_json)
        if orig_chat_id and orig_chat_id != canon_chat_id:
            save_user_integration(orig_chat_id, "notion", access_token, config_json)

        # Notify user on Telegram if initiated from TG
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if tg_token and not is_web and orig_chat_id:
            try:
                async with httpx.AsyncClient(timeout=6.0) as client:
                    await client.post(
                        f"https://api.telegram.org/bot{tg_token}/sendMessage",
                        json={
                            "chat_id": orig_chat_id,
                            "text": (
                                f"✅ **Notion connected successfully!** 📝\n\n"
                                f"Workspace: **{workspace_name}**\n"
                                f"Ab aap mujhe bol sakte hain:\n"
                                f"• *'ye notes mere Notion mein save kar do'*\n"
                                f"• *'meeting summary Notion mein daal do'*\n"
                                f"• *'existing notes section mein ye points add karo'*"
                            ),
                            "parse_mode": "Markdown"
                        }
                    )
            except Exception as e:
                print("Failed to send Telegram Notion notification:", repr(e))

        success_msg = f"Your Notion workspace '{workspace_name}' has been securely linked to PeculiarAI."
        if is_web and return_url:
            target_url = _safe_web_redirect(return_url, f"notion_status=connected&workspace={workspace_name}")
            return _github_callback_page("Notion Connected!", success_msg, success=True, redirect_url=target_url)

        return _github_callback_page("Notion Connected!", success_msg, success=True, bot_username=bot_user)
    except Exception as exc:
        print("Notion OAuth callback error:", repr(exc))
        if is_web and return_url:
            target_url = _safe_web_redirect(return_url, f"notion_status=error&notion_message={str(exc)}")
            return _github_callback_page("Notion connection failed", str(exc), success=False, redirect_url=target_url)
        return _github_callback_page("Notion connection failed", str(exc), success=False, bot_username=bot_user)


@app.get("/auth/notion/start")
@app.get("/notion/start")
async def notion_oauth_start(request: Request):
    """Initiates 1-click Notion OAuth via direct browser GET redirect."""
    from notion_auth import is_notion_oauth_configured, build_notion_auth_url
    chat_id_raw = request.query_params.get("chat_id")
    source = request.query_params.get("source", "tg")
    return_url = request.query_params.get("return_url")

    if not chat_id_raw or not chat_id_raw.isdigit():
        raise HTTPException(status_code=400, detail="Valid chat_id query parameter is required.")

    if not is_notion_oauth_configured():
        raise HTTPException(status_code=503, detail="Notion OAuth is not configured on this server.")

    chat_id = int(chat_id_raw)
    ensure_user(chat_id)
    try:
        auth_url = build_notion_auth_url(chat_id, source=source, return_url=return_url)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=auth_url, status_code=307)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/web/notion/start")
async def web_notion_start(request: Request):
    """Initiates 1-click Notion OAuth for a web session."""
    from notion_auth import is_notion_oauth_configured, build_notion_auth_url
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    verification_id = str(body.get("verification_id", "")).strip()
    return_url = str(body.get("return_url", "")).strip() or None
    chat_id = _resolve_web_session(verification_id)
    ensure_user(chat_id)

    if not is_notion_oauth_configured():
        raise HTTPException(status_code=503, detail="Notion OAuth is not configured on this server.")

    try:
        auth_url = build_notion_auth_url(chat_id, source="web", return_url=return_url)
        return {"auth_url": auth_url, "already_connected": False}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/app", response_class=HTMLResponse)
@app.get("/companion", response_class=HTMLResponse)
@app.get("/companion.html", response_class=HTMLResponse)
async def serve_companion_app():
    """Serves the interactive Memorae companion app simulation for mobile preview & web testing."""
    html_path = os.path.join(os.path.dirname(__file__), "companion.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Companion app not found</h1>", status_code=404)


@app.get("/connect", response_class=HTMLResponse)
@app.get("/connect.html", response_class=HTMLResponse)
async def serve_connect_page():
    """Serves the connect & integrations portal."""
    html_path = os.path.join(os.path.dirname(__file__), "connect.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Connect portal not found</h1>", status_code=404)


@app.get("/index.html", response_class=HTMLResponse)
async def serve_index_page():
    """Serves the main landing page if requested."""
    html_path = os.path.join(os.path.dirname(__file__), "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse("<h1>Index page not found</h1>", status_code=404)


@app.get("/")
def root():
    return {"status": "ok", "app": "PeculiarAI Backend API", "companion_app": "/app", "health": "/health"}



@app.get("/health")
def health(response: Response):
    import time
    t0 = time.perf_counter()
    db_ok = False
    db_latency_ms = None
    db_error = None
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        db_ok = True
    except Exception as exc:
        db_error = str(exc)

    gemini_key = bool(os.getenv("GEMINI_API_KEY"))
    groq_key = bool(os.getenv("GROQ_API_KEY"))
    llm_ok = gemini_key or groq_key
    primary_provider = "Gemini" if gemini_key else ("Groq" if groq_key else None)
    fallback_provider = "Groq" if (gemini_key and groq_key) else None

    emb_ok = bool(os.getenv("GEMINI_API_KEY") or os.getenv("DISABLE_LOCAL_EMBEDDINGS") == "1")

    is_healthy = db_ok and llm_ok
    status_code = 200 if is_healthy else 503
    response.status_code = status_code

    return {
        "status": "healthy" if is_healthy else "degraded",
        "database": {
            "status": "connected" if db_ok else "disconnected",
            "latency_ms": db_latency_ms,
            "error": db_error
        },
        "llm": {
            "status": "configured" if llm_ok else "missing_keys",
            "primary": primary_provider,
            "fallback": fallback_provider
        },
        "embeddings": {
            "status": "ready" if emb_ok else "degraded"
        },
        "agent_core": os.getenv("AGENT_CORE", "v2").lower(),
        "timestamp": timeutil.now(IST).isoformat(),
        "version": "v2.4.0"
    }


@app.get("/debug/ping")
def debug_ping():
    return {"status": "pong", "version": "v2.3.0"}


@app.get("/debug/db-latency")
def debug_db_latency(samples: int = 20):
    import time, statistics
    samples = max(1, min(samples, 50))
    latencies = []
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as e:
        return {"status": "error", "error": str(e)}

    for _ in range(samples):
        t0 = time.perf_counter()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latencies.append(round((time.perf_counter() - t0) * 1000, 2))

    sorted_lats = sorted(latencies)
    p50 = sorted_lats[len(sorted_lats) // 2]
    p95_idx = int(len(sorted_lats) * 0.95)
    p95 = sorted_lats[min(p95_idx, len(sorted_lats) - 1)]

    return {
        "status": "ok",
        "sample_count": len(latencies),
        "min_ms": min(latencies),
        "max_ms": max(latencies),
        "mean_ms": round(statistics.mean(latencies), 2),
        "p50_ms": p50,
        "p95_ms": p95,
        "latencies_ms": latencies,
    }

@app.get("/debug/groq_ping")
async def debug_groq_ping():
    import httpx, os
    groq_key = os.getenv("GROQ_API_KEY")
    masked_key = (groq_key[:6] + "..." + groq_key[-4:]) if groq_key else None
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {groq_key}"},
            json={
                "model": "openai/gpt-oss-20b",
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 10
            }
        )
        tpm = r.headers.get("x-ratelimit-limit-tokens")
        rpm = r.headers.get("x-ratelimit-limit-requests")
        rem_tok = r.headers.get("x-ratelimit-remaining-tokens")
        reset_tok = r.headers.get("x-ratelimit-reset-tokens")
        return {
            "status_code": r.status_code,
            "masked_key": masked_key,
            "body": r.text,
            "tpm": tpm,
            "rpm": rpm,
            "rem_tokens": rem_tok,
            "reset_tokens": reset_tok
        }

@app.get("/debug/test_turn")
async def debug_test_turn(request: Request):
    import traceback
    try:
        from agent.loop import run_agent_turn
        q = request.query_params.get("q") or request.query_params.get("text") or "hi"
        res = await run_agent_turn(1758056012, q)
        return res
    except BaseException as e:
        return {"status": "error", "error": repr(e), "traceback": traceback.format_exc()}


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
        import llm.provider as llm_provider
        resp = await llm_provider.chat(
            messages=messages,
            temperature=0.4,
            timeout=30.0
        )
        reply = (resp.text or "").strip()
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


async def _safe_handle_user_text(chat_id: int, incoming_text: str):
    try:
        await handle_user_text(chat_id, incoming_text)
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        print("Webhook handle_user_text error:", repr(exc))
        import traceback
        traceback.print_exc()
        try:
            recent = db.get_recent_chat_history(chat_id, limit=2)
            has_recent_reply = any(r.get("role") == "assistant" for r in recent[-1:])
            if not has_recent_reply:
                await say(chat_id, "Kripya ek baar dobara batayein ki aap kya schedule ya update karna chahte hain? 🙂")
        except Exception:
            pass


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
        from_user = message.get("from") or {}
        fn = from_user.get("first_name")
        un = from_user.get("username")
        if fn or un:
            try:
                with engine.begin() as conn:
                    conn.execute(
                        text("UPDATE users SET first_name = COALESCE(:fn, first_name), username = COALESCE(:un, username) WHERE telegram_chat_id = :cid"),
                        {"fn": fn, "un": un, "cid": chat_id}
                    )
            except Exception:
                pass
        if "text" in message:
            incoming_text = message["text"].strip()
            # If user replied to a document or photo message
            reply_msg = message.get("reply_to_message") or {}
            reply_doc = reply_msg.get("document")
            if reply_doc and reply_doc.get("file_id"):
                r_fid = reply_doc["file_id"]
                r_fname = reply_doc.get("file_name", "document")
                r_mime = reply_doc.get("mime_type", "")
                RECENT_DOCS[chat_id] = {
                    "filename": r_fname,
                    "file_id": r_fid,
                    "mime_type": r_mime,
                    "type": "document",
                    "timestamp": timeutil.now(IST),
                }
                try:
                    from state.active_context import set_active_document
                    set_active_document(chat_id, {
                        "filename": r_fname,
                        "file_id": r_fid,
                        "mime_type": r_mime,
                        "timestamp": timeutil.now(IST).isoformat(),
                    })
                except Exception as r_err:
                    print("reply_to_message active document error:", repr(r_err))
            elif reply_msg.get("photo"):
                r_photos = reply_msg.get("photo")
                if isinstance(r_photos, list) and len(r_photos) > 0:
                    best_p = r_photos[-1]
                    r_fid = best_p.get("file_id")
                    if r_fid:
                        RECENT_DOCS[chat_id] = {
                            "filename": "Photo.jpg",
                            "file_id": r_fid,
                            "type": "image",
                            "timestamp": timeutil.now(IST),
                        }
                        try:
                            from state.active_context import set_active_document
                            set_active_document(chat_id, {
                                "filename": "Photo.jpg",
                                "file_id": r_fid,
                                "type": "image",
                                "timestamp": timeutil.now(IST).isoformat(),
                            })
                        except Exception as r_err:
                            print("reply_to_message active photo error:", repr(r_err))

            task = asyncio.create_task(_safe_handle_user_text(chat_id, incoming_text))
            BG_TASKS.add(task)
            task.add_done_callback(BG_TASKS.discard)
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


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print(f"Starting PeculiarAI Backend Server on http://0.0.0.0:{port} ...")
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
