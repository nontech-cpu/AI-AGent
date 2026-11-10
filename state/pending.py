"""
PeculiarAI Persistent Multi-Turn State (Phase 5)
Database-backed persistence for pending confirmations and multi-turn conversational actions.
Allows user cross-questions without losing pending action state, surviving server restarts.
"""
import json
import re
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, Tuple
from sqlalchemy import text

import db
from timing import IST
import core.timeutil as timeutil

logger = logging.getLogger("peculiar_state")

_TABLE_INITIALIZED = (db.engine.dialect.name != "sqlite")


def ensure_pending_table():
    """Ensure the pending_actions table exists in PostgreSQL or SQLite."""
    global _TABLE_INITIALIZED
    dialect = db.engine.dialect.name
    if _TABLE_INITIALIZED and dialect != "sqlite":
        return
    with db.engine.begin() as conn:
        if dialect == "sqlite":
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pending_actions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    action_kind TEXT NOT NULL,
                    payload TEXT NOT NULL DEFAULT '{}',
                    status TEXT NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_pending_chat_status ON pending_actions (chat_id, status)
            """))
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS pending_actions (
                    id SERIAL PRIMARY KEY,
                    chat_id BIGINT NOT NULL,
                    action_kind VARCHAR(64) NOT NULL,
                    payload JSONB NOT NULL DEFAULT '{}',
                    status VARCHAR(32) NOT NULL DEFAULT 'pending',
                    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP WITH TIME ZONE
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_pending_chat_status ON pending_actions (chat_id, status)
            """))
    _TABLE_INITIALIZED = True


def save_pending_action(chat_id: int, action_kind: str, payload: dict, expires_minutes: int = 60) -> int:
    _PENDING_ACTION_CACHE.pop(chat_id, None)
    """
    Saves or replaces a pending confirmation action in the database.
    Previous unconfirmed actions for the user are marked as 'superseded'.
    """
    ensure_pending_table()
    now_utc = timeutil.now(timezone.utc)
    expires_utc = now_utc + timedelta(minutes=expires_minutes)
    dialect = db.engine.dialect.name

    payload_clean = dict(payload)
    # Ensure created_at in payload is JSON serializable
    if "created_at" in payload_clean and isinstance(payload_clean["created_at"], datetime):
        payload_clean["created_at"] = payload_clean["created_at"].isoformat()

    payload_json = json.dumps(payload_clean, default=str)

    with db.engine.begin() as conn:
        # Supersede any active pending actions for this user
        conn.execute(text("""
            UPDATE pending_actions
            SET status = 'superseded'
            WHERE chat_id = :chat_id AND status = 'pending'
        """), {"chat_id": chat_id})

        if dialect == "sqlite":
            conn.execute(text("""
                INSERT INTO pending_actions (chat_id, action_kind, payload, status, created_at, expires_at)
                VALUES (:chat_id, :action_kind, :payload, 'pending', :created_at, :expires_at)
            """), {
                "chat_id": chat_id,
                "action_kind": action_kind,
                "payload": payload_json,
                "created_at": now_utc,
                "expires_at": expires_utc
            })
            action_id = conn.execute(text("SELECT last_insert_rowid()")).scalar()
        else:
            action_id = conn.execute(text("""
                INSERT INTO pending_actions (chat_id, action_kind, payload, status, created_at, expires_at)
                VALUES (:chat_id, :action_kind, CAST(:payload AS jsonb), 'pending', :created_at, :expires_at)
                RETURNING id
            """), {
                "chat_id": chat_id,
                "action_kind": action_kind,
                "payload": payload_json,
                "created_at": now_utc,
                "expires_at": expires_utc
            }).scalar()

    _PENDING_ACTION_CACHE.pop(chat_id, None)
    return action_id


_PENDING_ACTION_CACHE: dict = {}

def get_pending_action(chat_id: int) -> Optional[Dict[str, Any]]:
    """
    Retrieves the current pending action for chat_id from the database.
    Returns None if no action is pending or if it has expired.
    """
    import time as _pytime
    now_ts = _pytime.time()
    cached = _PENDING_ACTION_CACHE.get(chat_id)
    if cached and (now_ts < cached["exp"]):
        return cached["val"]
    ensure_pending_table()
    with db.engine.connect() as conn:
        row = conn.execute(text("""
            SELECT id, chat_id, action_kind, payload, status, created_at, expires_at
            FROM pending_actions
            WHERE chat_id = :chat_id AND status = 'pending'
            ORDER BY id DESC
            LIMIT 1
        """), {"chat_id": chat_id}).fetchone()

        if not row:
            _PENDING_ACTION_CACHE[chat_id] = {"exp": now_ts + 60.0, "val": None}
            return None

        action_id, c_id, action_kind, payload_raw, status, created_at, expires_at = row

        # Check expiration
        if expires_at is not None:
            now_utc = timeutil.now(timezone.utc)
            if isinstance(expires_at, str):
                try:
                    exp_dt = datetime.fromisoformat(expires_at)
                except Exception:
                    exp_dt = None
            else:
                exp_dt = expires_at

            if exp_dt is not None:
                if hasattr(exp_dt, "tzinfo") and exp_dt.tzinfo is not None:
                    exp_utc = exp_dt.astimezone(timezone.utc)
                else:
                    exp_utc = exp_dt.replace(tzinfo=timezone.utc)
                if now_utc > exp_utc:
                    with db.engine.begin() as upd_conn:
                        upd_conn.execute(text("UPDATE pending_actions SET status = 'expired' WHERE id = :id"), {"id": action_id})
                    return None

        if isinstance(payload_raw, str):
            try:
                payload = json.loads(payload_raw)
            except Exception:
                payload = {}
        elif isinstance(payload_raw, dict):
            payload = payload_raw
        else:
            payload = {}

        res = {
            "id": action_id,
            "chat_id": c_id,
            "action_kind": action_kind,
            "payload": payload,
            "status": status,
            "created_at": created_at,
            "expires_at": expires_at
        }
        _PENDING_ACTION_CACHE[chat_id] = {"exp": now_ts + 60.0, "val": res}
        return res


def complete_pending_action(action_id: int, status: str = "confirmed") -> None:
    """Marks a pending action as confirmed, cancelled, or superseded in the DB."""
    _PENDING_ACTION_CACHE.clear()
    ensure_pending_table()
    with db.engine.begin() as conn:
        conn.execute(text("""
            UPDATE pending_actions
            SET status = :status
            WHERE id = :id
        """), {"id": action_id, "status": status})


def cancel_pending_action(chat_id: int) -> Optional[Dict[str, Any]]:
    """Cancels the active pending action for chat_id."""
    _PENDING_ACTION_CACHE.pop(chat_id, None)
    pending = get_pending_action(chat_id)
    if pending:
        complete_pending_action(pending["id"], status="cancelled")
    return pending


# Deterministic structural commands (Telegram callback buttons & slash commands & explicit system actions)
STRUCTURAL_CONFIRM_COMMANDS = {
    "confirm", "action_confirm", "cb:confirm", "/confirm", "yes"
}
STRUCTURAL_CANCEL_COMMANDS = {
    "cancel", "action_cancel", "cb:cancel", "/cancel", "no"
}


async def classify_confirmation_intent_with_llm(user_text: str, pending_action: dict) -> Tuple[str, Optional[Any]]:
    """
    Classifies the user's intent when a pending action is waiting using LLM semantic understanding.
    Zero meaning regexes. Deterministic structural buttons/commands are handled upfront.
    Returns:
        ("confirm", None)           -> User confirmed action
        ("cancel", None)            -> User cancelled action
        ("update_time", parsed_res) -> User requested a new time for the meeting
        ("cross_question", None)    -> User asked a cross-question (preserves pending state)
        ("unrelated", None)         -> Unrelated command, greeting or fresh task
    """
    clean = user_text.strip()
    low = clean.lower()

    # 1. Deterministic Structural / Button Commands (ALLOWED)
    if low in STRUCTURAL_CONFIRM_COMMANDS:
        return ("confirm", None)
    if low in STRUCTURAL_CANCEL_COMMANDS:
        return ("cancel", None)

    # 2. Structural Question Detection
    if "?" in clean:
        return ("cross_question", None)

    # 3. Time update for meetings (structural ISO/time parsing)
    kind = pending_action.get("action_kind") or pending_action.get("payload", {}).get("action") or pending_action.get("payload", {}).get("kind")
    if kind in ("create_meeting", "meeting", "update_meeting"):
        try:
            from timing import parse_followup_time
            parsed = parse_followup_time(clean, timeutil.now(IST))
            if parsed and parsed[0] is not None:
                return ("update_time", parsed)
        except Exception:
            pass

    # 4. LLM Semantic Classification for Free-Text Nuances
    try:
        import llm.provider as llm_provider
        action_desc = f"{kind}: {pending_action.get('payload', {})}"
        prompt = (
            f"A pending action is awaiting user confirmation: {action_desc}\n"
            f"User message: \"{clean}\"\n\n"
            "Classify user confirmation intent into exactly one category:\n"
            "- 'confirm': User agrees, approves, confirms, or wants to proceed.\n"
            "- 'cancel': User rejects, declines, cancels, or aborts.\n"
            "- 'cross_question': User asks an informational question or schedule query before deciding.\n"
            "- 'unrelated': Completely unrelated task, greeting (e.g. 'hi', 'hello', 'hey'), or fresh query.\n\n"
            "Return JSON only: {\"intent\": \"confirm\"|\"cancel\"|\"cross_question\"|\"unrelated\"}"
        )

        resp = await llm_provider.chat(
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0,
            timeout_seconds=5.0
        )
        data = json.loads(resp.text or "{}")
        intent = data.get("intent", "unrelated")
        if intent in ("confirm", "cancel", "cross_question", "unrelated"):
            return (intent, None)
    except Exception as exc:
        logger.warning(f"Confirmation intent LLM classification error: {exc}")

    return ("unrelated", None)


def classify_confirmation_intent(user_text: str, pending_action: dict) -> Tuple[str, Optional[Any]]:
    """
    Synchronous compatibility facade backed by classify_confirmation_intent_with_llm.
    """
    clean = user_text.strip()
    low = clean.lower()

    if low in STRUCTURAL_CONFIRM_COMMANDS:
        return ("confirm", None)
    if low in STRUCTURAL_CANCEL_COMMANDS:
        return ("cancel", None)

    if "?" in clean:
        return ("cross_question", None)

    kind = pending_action.get("action_kind") or pending_action.get("payload", {}).get("action") or pending_action.get("payload", {}).get("kind")
    if kind in ("create_meeting", "meeting", "update_meeting"):
        try:
            from timing import parse_followup_time
            parsed = parse_followup_time(clean, timeutil.now(IST))
            if parsed and parsed[0] is not None:
                return ("update_time", parsed)
        except Exception:
            pass

    try:
        import asyncio
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(classify_confirmation_intent_with_llm(user_text, pending_action))).result(timeout=6.0)
        else:
            return asyncio.run(classify_confirmation_intent_with_llm(user_text, pending_action))
    except Exception:
        pass

    return ("unrelated", None)


async def execute_pending_action(chat_id: int, pending: dict) -> Tuple[bool, str]:
    """
    Executes a confirmed pending action through Google Services.
    Updates the database row to 'confirmed'.
    """
    kind = pending.get("action_kind") or pending.get("payload", {}).get("kind") or pending.get("payload", {}).get("action")
    payload = pending.get("payload", {})
    action_id = pending.get("id")

    try:
        import google_services
        from timing import format_event_clock_ist

        if kind in ("create_meeting", "meeting"):
            title = payload.get("title", "Meeting")
            start_iso = payload.get("start_iso") or payload.get("start")
            duration_minutes = payload.get("duration_minutes") or payload.get("duration", 30)
            attendees = payload.get("attendees", [])

            # Industry-grade date sanity: Ensure start_iso is not anchored to a past year due to LLM date hallucination
            try:
                from datetime import datetime as _dt, timedelta as _td
                from zoneinfo import ZoneInfo
                from timing import IST
                user_tz_str = await google_services.get_user_calendar_timezone(chat_id)
                try:
                    u_tz = ZoneInfo(user_tz_str)
                except Exception:
                    u_tz = IST
                cur_now = _dt.now(u_tz)
                parsed_dt = _dt.fromisoformat(start_iso)
                if parsed_dt.tzinfo is None:
                    parsed_dt = parsed_dt.replace(tzinfo=u_tz)
                else:
                    parsed_dt = parsed_dt.astimezone(u_tz)
                if parsed_dt < cur_now - _td(minutes=2):
                    target_d = cur_now.date() + _td(days=1)
                    parsed_dt = _dt.combine(target_d, parsed_dt.time(), tzinfo=u_tz)
                    start_iso = parsed_dt.isoformat()
            except Exception:
                pass

            result = await google_services.create_meeting(
                chat_id=chat_id,
                title=title,
                start_iso=start_iso,
                duration_minutes=duration_minutes,
                attendees=attendees
            )

            clock_str = format_event_clock_ist(start_iso)
            meet_url = result.get("meet_url") or result.get("hangoutLink", "")
            calendar_link = result.get("htmlLink", "")

            try:
                from memory.store import record_action_event
                import time as _t
                cal_src_id = f"cal_{result.get('id') or action_id or int(_t.time())}"
                record_action_event(
                    chat_id,
                    action_type="create_meeting",
                    summary=f"Scheduled Google Calendar meeting '{title}' for {clock_str} ({duration_minutes} mins)",
                    details={"title": title, "start": start_iso, "duration": duration_minutes, "meet_url": meet_url},
                    source_type="calendar",
                    source_id=cal_src_id,
                    importance=3
                )
            except Exception:
                pass

            msg = f"✅ **Meeting successfully scheduled!**\n\n📌 **Title:** {title}\n🕒 **Time:** {clock_str} (India Time)\n⏱️ **Duration:** {duration_minutes} mins"
            if meet_url:
                msg += f"\n📹 **Google Meet:** {meet_url}"
            if calendar_link:
                msg += f"\n📅 **Calendar Event:** [Google Calendar]({calendar_link})"

            if action_id:
                complete_pending_action(action_id, status="confirmed")
            return True, msg

        elif kind in ("create_calendar_event", "calendar_event"):
            title = payload.get("title", "Event")
            start_iso = payload.get("start_iso") or payload.get("start")
            duration_minutes = payload.get("duration_minutes") or payload.get("duration", 30)
            rec = payload.get("recurrence") or "none"
            g_rec = ["RRULE:FREQ=DAILY"] if rec == "daily" else (["RRULE:FREQ=WEEKLY"] if rec == "weekly" else None)

            result = await google_services.create_calendar_event(
                chat_id=chat_id,
                summary=title,
                start_time=start_iso,
                duration_minutes=duration_minutes,
                is_video_meeting=False,
                recurrence=g_rec
            )

            clock_str = format_event_clock_ist(start_iso)
            calendar_link = result.get("htmlLink", "") if isinstance(result, dict) else ""

            try:
                from memory.store import record_action_event
                import time as _t
                cal_src_id = f"cal_{result.get('id') if isinstance(result, dict) else action_id or int(_t.time())}"
                record_action_event(
                    chat_id,
                    action_type="create_calendar_event",
                    summary=f"Added event to Google Calendar '{title}' for {clock_str} ({duration_minutes} mins)",
                    details={"title": title, "start": start_iso, "duration": duration_minutes, "recurrence": rec},
                    source_type="calendar",
                    source_id=cal_src_id,
                    importance=3
                )
            except Exception:
                pass

            user_lang = db.get_user_language(chat_id)
            if user_lang == "english":
                msg = f"✅ **Event added to Google Calendar!**\n\n📌 **Title:** {title}\n🕒 **Time:** {clock_str} (India Time)\n⏱️ **Duration:** {duration_minutes} mins"
            else:
                msg = f"✅ **Calendar event successfully scheduled!**\n\n📌 **Title:** {title}\n🕒 **Time:** {clock_str} (India Time)\n⏱️ **Duration:** {duration_minutes} mins"
            if calendar_link:
                msg += f"\n📅 **Calendar Event:** [Google Calendar]({calendar_link})"

            if action_id:
                complete_pending_action(action_id, status="confirmed")
            return True, msg

        elif kind in ("send_email", "email"):
            to = payload.get("to")
            subject = payload.get("subject", "No Subject")
            body = payload.get("body", "")

            msg_id = await google_services.send_email(
                chat_id=chat_id,
                to=to,
                subject=subject,
                body=body
            )
            reply = f"✅ **Email sent successfully!**\n\n📤 **To:** {to}\n📝 **Subject:** {subject}"
            try:
                from memory.store import record_action_event
                import time as _t
                email_src_id = f"email_{action_id or msg_id or int(_t.time())}"
                record_action_event(
                    chat_id,
                    action_type="send_email",
                    summary=f"Sent email to {to} with Subject: '{subject}'",
                    details={"to": to, "subject": subject, "body_preview": (body or "")[:200]},
                    subject_name=to,
                    source_type="email",
                    source_id=email_src_id,
                    importance=3
                )
            except Exception:
                pass
            if action_id:
                complete_pending_action(action_id, status="confirmed")
            return True, reply

        elif kind in ("master_privacy_wipe", "wipe"):
            import secrets, time as _time
            from main import PENDING_WIPE_CODES, BOT_EMAIL
            code = "".join(secrets.choice("0123456789") for _ in range(6))
            expires_at = _time.time() + 300  # 5 minutes
            connected_email = None
            try:
                from main import get_valid_google_connection_by_chat_id
                is_conn, g_email = await get_valid_google_connection_by_chat_id(chat_id)
                if is_conn and g_email:
                    connected_email = g_email.strip().lower()
            except Exception:
                pass
            if not connected_email:
                try:
                    linked = db.get_linked_email(chat_id)
                    if linked and linked[0]:
                        connected_email = linked[0].strip().lower()
                except Exception:
                    pass

            PENDING_WIPE_CODES[chat_id] = {
                "code": code,
                "expires_at": expires_at,
                "email": connected_email
            }
            db.set_state(chat_id, "awaiting_wipe_chat_otp", {"code": code, "expires_at": expires_at, "email": connected_email})
            if action_id:
                complete_pending_action(action_id, status="confirmed")

            msg = (
                f"🔐 **Security Verification Required (Valid for 5 mins)**\n\n"
                f"Permanent memory deletion ke liye agle 5 minute ke andar ye 6-digit verification code yahan chat me reply karein:\n\n"
                f"🔑 **Verification Code: `{code}`**\n\n"
                f"Reply `cancel` to abort."
            )
            return True, msg

        elif kind in ("upload_file", "drive_file"):
            filename = payload.get("filename", "notes.txt")
            content = payload.get("content", "")
            folder_id = payload.get("folder_id")

            res = await google_services.upload_text_file(
                chat_id=chat_id,
                filename=filename,
                content=content,
                folder_id=folder_id
            )
            file_link = res.get("webViewLink") or ""
            reply = f"✅ **File successfully uploaded to Google Drive!**\n📄 **Name:** {filename}"
            if file_link:
                reply += f"\n🔗 **Link:** {file_link}"

            if action_id:
                complete_pending_action(action_id, status="confirmed")
            return True, reply

        elif kind in ("delete_meeting",):
            event_id = payload.get("event_id")
            title = payload.get("title", "Meeting")
            if event_id:
                await google_services.delete_calendar_event(chat_id, event_id)
            reply = f"✅ **Meeting successfully deleted!**\n🗑️ '{title}' aapke Google Calendar se remove kar di gayi hai."
            if action_id:
                complete_pending_action(action_id, status="confirmed")
            return True, reply

        elif kind in ("delete_reminder", "reminder_delete"):
            target_ids = payload.get("target_ids")
            if not target_ids and payload.get("reminder_id"):
                target_ids = [payload.get("reminder_id")]
            from agent.tools import execute_tool
            res = await execute_tool(
                chat_id,
                "delete_reminder",
                {"target_ids": target_ids},
                user_context={"_from_pending_executor": True, "bypass_quote_check": True}
            )
            if action_id:
                complete_pending_action(action_id, status="confirmed")
            user_lang = db.get_user_language(chat_id)
            if res.get("status") == "success":
                del_count = res.get("facts", {}).get("deleted_count", len(target_ids) if target_ids else 1)
                if user_lang == "english":
                    msg = f"✅ Successfully deleted {del_count} reminder(s)."
                else:
                    msg = f"✅ {del_count} reminder(s) delete kar diye gaye hain."
                return True, msg
            else:
                if user_lang == "english":
                    msg = "Reminder(s) could not be deleted."
                else:
                    msg = "Reminder delete nahi ho saka."
                return False, msg

        elif kind in ("create_reminder", "reminder_create"):
            title = payload.get("title", "")
            due_date = payload.get("due_date")
            due_time = payload.get("due_time")
            from agent.tools import execute_tool
            res = await execute_tool(
                chat_id,
                "create_reminder",
                {
                    "title": title,
                    "due_date": due_date,
                    "due_time": due_time,
                    "force": True
                },
                user_context={"_from_pending_executor": True, "bypass_quote_check": True}
            )
            if action_id:
                complete_pending_action(action_id, status="confirmed")
            if res.get("status") in ("success", "conflict_detected"):
                msg = res.get("message") or f"✅ Done! '{title}' reminder set kar diya hai."
                return True, msg
            else:
                return False, res.get("message") or "Reminder set nahi ho saka."

        else:
            if action_id:
                complete_pending_action(action_id, status="confirmed")
            return True, f"Action '{kind}' confirmed and processed."

    except Exception as exc:
        logger.error(f"Error executing pending action {action_id}: {exc}", exc_info=True)
        err_str = str(exc)
        if "not connected" in err_str.lower() or "token" in err_str.lower():
            try:
                from google_auth import build_auth_url
                auth_url = build_auth_url(int(chat_id), source="tg")
                return False, f"⚠️ Google account connected nahi hai. Action complete karne ke liye pehle is link se Google connect karein:\n\n{auth_url}"
            except Exception:
                return False, "⚠️ Google account connected nahi hai. Kripya pehle Google account connect karein."
        return False, f"Failed to execute action: {str(exc)}"


class PersistentPendingDict(dict):
    """
    A dict-compatible proxy that mirrors pending actions to the DB pending_actions table.
    Ensures 100% backward-compatibility with main.PENDING_GOOGLE_ACTIONS and existing tests,
    while persisting across server restarts.
    """
    def __setitem__(self, chat_id, val):
        super().__setitem__(chat_id, val)
        try:
            if isinstance(val, dict):
                action_kind = val.get("kind") or val.get("action") or "pending_action"
                save_pending_action(int(chat_id), action_kind, val)
        except Exception:
            pass

    def __getitem__(self, chat_id):
        if chat_id in self:
            return super().__getitem__(chat_id)
        try:
            db_act = get_pending_action(int(chat_id))
            if db_act:
                payload = db_act.get("payload") or {}
                super().__setitem__(chat_id, payload)
                return payload
        except Exception:
            pass
        return super().__getitem__(chat_id)

    def get(self, chat_id, default=None):
        if chat_id in self:
            return super().get(chat_id, default)
        try:
            db_act = get_pending_action(int(chat_id))
            if db_act:
                payload = db_act.get("payload") or {}
                super().__setitem__(chat_id, payload)
                return payload
        except Exception:
            pass
        return default

    def __contains__(self, chat_id):
        if super().__contains__(chat_id):
            return True
        try:
            db_act = get_pending_action(int(chat_id))
            if db_act:
                super().__setitem__(chat_id, db_act.get("payload") or {})
                return True
        except Exception:
            pass
        return False

    def pop(self, chat_id, default=None):
        val = super().pop(chat_id, None)
        try:
            db_act = cancel_pending_action(int(chat_id))
            if val is None and db_act:
                val = db_act.get("payload")
        except Exception:
            pass
        return val if val is not None else default

    def clear(self):
        super().clear()
        try:
            ensure_pending_table()
            with db.engine.begin() as conn:
                conn.execute(text("UPDATE pending_actions SET status = 'cancelled' WHERE status = 'pending'"))
        except Exception:
            pass


_PROXY_INSTANCE = None

def get_pending_actions_proxy() -> PersistentPendingDict:
    global _PROXY_INSTANCE
    if _PROXY_INSTANCE is None:
        _PROXY_INSTANCE = PersistentPendingDict()
    return _PROXY_INSTANCE
