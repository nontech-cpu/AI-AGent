"""
Saptic AI — Follow-Up & Unanswered Email Watchdog
Tracks outgoing critical emails, proposals, and queries awaiting replies.
Alerts the user if a recipient hasn't responded within the expected window (default 3 days).
0 LLM tokens, 0 MB extra RAM: all state in Neon PostgreSQL table `pending_followups`.
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import text
import db
from timing import IST
import core.timeutil as timeutil

logger = logging.getLogger("saptic_followup")


def _resolve_uid(user_id_or_chat_id: int) -> int:
    from memory.store import resolve_user_id
    return resolve_user_id(user_id_or_chat_id)


def record_followup(
    user_id_or_chat_id: int,
    recipient: str,
    subject: str,
    notes: Optional[str] = None,
    days_ahead: int = 3
) -> Dict[str, Any]:
    """Records a critical communication to watch for reply."""
    uid = _resolve_uid(user_id_or_chat_id)
    today = timeutil.today(IST)
    due_date = today + timedelta(days=days_ahead)

    try:
        with db.engine.begin() as conn:
            res = conn.execute(
                text("""
                    INSERT INTO pending_followups (user_id, recipient, subject, notes, sent_date, due_date, status)
                    VALUES (:uid, :rec, :sub, :notes, :s_date, :d_date, 'pending')
                    RETURNING id
                """),
                {
                    "uid": uid,
                    "rec": recipient.strip(),
                    "sub": (subject or "Communication").strip(),
                    "notes": notes,
                    "s_date": today,
                    "d_date": due_date,
                }
            )
            fid = res.scalar()
            return {
                "status": "success",
                "followup_id": fid,
                "recipient": recipient,
                "subject": subject,
                "due_date": str(due_date),
            }
    except Exception as exc:
        logger.error(f"record_followup error: {exc}")
        return {"status": "error", "message": str(exc)}


def get_due_followups(user_id_or_chat_id: int) -> List[Dict[str, Any]]:
    """Retrieves follow-ups where recipient hasn't replied and due_date <= today."""
    uid = _resolve_uid(user_id_or_chat_id)
    today = timeutil.today(IST)

    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, recipient, subject, notes, sent_date, due_date
                    FROM pending_followups
                    WHERE user_id = :uid AND status = 'pending' AND due_date <= :today
                    ORDER BY due_date ASC
                """),
                {"uid": uid, "today": today}
            ).mappings().all()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error(f"get_due_followups error: {exc}")
        return []


def resolve_followup(user_id_or_chat_id: int, followup_id: int, resolution_status: str = "resolved") -> bool:
    """Marks a follow-up as resolved (reply received or closed)."""
    uid = _resolve_uid(user_id_or_chat_id)
    try:
        with db.engine.begin() as conn:
            res = conn.execute(
                text("UPDATE pending_followups SET status = :st WHERE id = :fid AND user_id = :uid"),
                {"st": resolution_status, "fid": followup_id, "uid": uid}
            )
            return res.rowcount > 0
    except Exception as exc:
        logger.error(f"resolve_followup error: {exc}")
        return False


def list_active_followups(user_id_or_chat_id: int) -> List[Dict[str, Any]]:
    """Retrieves all active pending follow-ups for user."""
    uid = _resolve_uid(user_id_or_chat_id)
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, recipient, subject, notes, sent_date, due_date
                    FROM pending_followups
                    WHERE user_id = :uid AND status = 'pending'
                    ORDER BY due_date ASC
                """),
                {"uid": uid}
            ).mappings().all()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error(f"list_active_followups error: {exc}")
        return []

