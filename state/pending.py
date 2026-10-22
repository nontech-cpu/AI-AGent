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

logger = logging.getLogger("peculiar_state")

_TABLE_INITIALIZED = False


def ensure_pending_table():
    """Ensure the pending_actions table exists in PostgreSQL or SQLite."""
    global _TABLE_INITIALIZED
    if _TABLE_INITIALIZED:
        return
    dialect = db.engine.dialect.name
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
    """
    Saves or replaces a pending confirmation action in the database.
    Previous unconfirmed actions for the user are marked as 'superseded'.
    """
    ensure_pending_table()
    now_utc = datetime.now(timezone.utc)
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

    return action_id


def get_pending_action(chat_id: int) -> Optional[Dict[str, Any]]:
    """
    Retrieves the current pending action for chat_id from the database.
    Returns None if no action is pending or if it has expired.
    """
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
            return None

        action_id, c_id, action_kind, payload_raw, status, created_at, expires_at = row

        # Check expiration
        if expires_at is not None:
            now_utc = datetime.now(timezone.utc)
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

        return {
            "id": action_id,
            "chat_id": c_id,
            "action_kind": action_kind,
            "payload": payload,
            "status": status,
            "created_at": created_at,
            "expires_at": expires_at
        }


def complete_pending_action(action_id: int, status: str = "confirmed") -> None:
    """Marks a pending action as confirmed, cancelled, or superseded in the DB."""
    ensure_pending_table()
    with db.engine.begin() as conn:
        conn.execute(text("""
            UPDATE pending_actions
            SET status = :status
            WHERE id = :id
        """), {"id": action_id, "status": status})


def cancel_pending_action(chat_id: int) -> Optional[Dict[str, Any]]:
    """Cancels the active pending action for chat_id."""
    pending = get_pending_action(chat_id)
    if pending:
        complete_pending_action(pending["id"], status="cancelled")
    return pending


# Regular expressions for affirmative and negative intent in English and Roman Hinglish
AFFIRMATIVE_REGEX = re.compile(
    r"^(yes|yeah|yep|yup|haan|haanji|han|ha|kar do|kardo|kr do|krdo|bilkul|sure|proceed|confirm|ok|okay|create it|send it|kar dena|kardena|bana do|banado|schedule kar do|schedule krdo)\b",
    re.I
)

NEGATIVE_REGEX = re.compile(
    r"^(no|nope|cancel|mat karo|mat kro|rehne do|rehnde|nahi|nahin|nhin|na|galat|stop|don't|dont|drop it)\b|meeting ka ni|ye nahi|ye mat|nhi chahiye|nahi chahiye",
    re.I
)

QUESTION_REGEX = re.compile(
    r"(\?|\bkya\b|\bkaun\b|\bkab\b|\bkahan\b|\bkitna\b|\bkitne\b|\bkyu\b|\bkyun\b|\bwhat\b|\bwhen\b|\bwhere\b|\bwho\b|\bhow\b|\bwhy\b|\bfree time\b|\bavailable\b|\bcheck\b|\bpehle\b|\bbaad\b|\bempty\b|\bslot\b|\bfree slot\b|\bschedule\b|\bcalendar\b|\bplan\b|\bbatao\b|\bdikhao\b|\btell\b|\bshow\b)",
    re.I
)


def classify_confirmation_intent(user_text: str, pending_action: dict) -> Tuple[str, Optional[Any]]:
    """
    Classifies the user's intent when a pending action is waiting:
    Returns:
        ("confirm", None)           -> User explicitly confirmed
        ("cancel", None)            -> User explicitly cancelled
        ("update_time", parsed_res) -> User requested a new time for the meeting
        ("cross_question", None)    -> User asked a cross-question (MUST NOT LOSE PENDING ACTION!)
        ("unrelated", None)         -> Unrelated command
    """
    clean = user_text.strip()
    low = clean.lower()

    # 1. Affirmative confirmation
    if AFFIRMATIVE_REGEX.search(low):
        return ("confirm", None)

    # 2. Negative cancellation
    if NEGATIVE_REGEX.search(low):
        return ("cancel", None)

    # 3. Time update for meetings (e.g. "monday 5 bje", "5pm kar do", "time 6pm rakhna")
    kind = pending_action.get("action_kind") or pending_action.get("payload", {}).get("action") or pending_action.get("payload", {}).get("kind")
    if kind in ("create_meeting", "meeting", "update_meeting"):
        try:
            from timing import parse_followup_time
            parsed = parse_followup_time(clean, datetime.now(IST))
            if parsed and parsed[0] is not None:
                return ("update_time", parsed)
        except Exception:
            pass

    # 4. Cross-Question Detection
    # If the user asks an informational question (e.g. "Tuesday ko Ananya se meeting ke pehle kya mujhe free time milega?")
    if QUESTION_REGEX.search(clean):
        return ("cross_question", None)

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

            result = await google_services.create_meeting(
                chat_id=chat_id,
                summary=title,
                start_iso=start_iso,
                duration_minutes=duration_minutes,
                attendees=attendees
            )

            clock_str = format_event_clock_ist(start_iso)
            meet_url = result.get("meet_url") or result.get("hangoutLink", "")
            calendar_link = result.get("htmlLink", "")

            msg = f"✅ **Meeting successfully scheduled!**\n\n📌 **Title:** {title}\n🕒 **Time:** {clock_str} (India Time)\n⏱️ **Duration:** {duration_minutes} mins"
            if meet_url:
                msg += f"\n📹 **Google Meet:** {meet_url}"
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
            if action_id:
                complete_pending_action(action_id, status="confirmed")
            return True, reply

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
