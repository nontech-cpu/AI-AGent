"""
Saptic AI — Recurring Subscription & Free-Trial Expiry Watchdog
Tracks active subscriptions (Netflix, Spotify, ChatGPT Plus, Canva, SaaS tools)
and monitors upcoming auto-debits and free trial expirations to prevent unwanted charges.
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import text
import db
from timing import IST
import core.timeutil as timeutil

logger = logging.getLogger("saptic_subscription")


def _resolve_uid(user_id_or_chat_id: int) -> int:
    from memory.store import resolve_user_id
    return resolve_user_id(user_id_or_chat_id)


def upsert_subscription(
    user_id_or_chat_id: int,
    service_name: str,
    amount: Optional[str] = None,
    billing_cycle: str = "monthly",
    next_renewal_date: Optional[str | date] = None,
    is_trial: bool = False,
    trial_expiry_date: Optional[str | date] = None,
    cancel_url: Optional[str] = None,
    status: str = "active",
) -> Dict[str, Any]:
    """Saves or updates a recurring subscription or free trial for a user."""
    uid = _resolve_uid(user_id_or_chat_id)
    svc = (service_name or "").strip()
    if not svc:
        return {"status": "error", "message": "Service name required."}

    def _parse_d(val):
        if not val:
            return None
        if isinstance(val, date):
            return val
        if isinstance(val, datetime):
            return val.date()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
            try:
                return datetime.strptime(str(val).split("T")[0].strip(), fmt).date()
            except Exception:
                pass
        return None

    ren_d = _parse_d(next_renewal_date)
    trial_d = _parse_d(trial_expiry_date)

    try:
        with db.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO user_subscriptions 
                        (user_id, service_name, amount, billing_cycle, next_renewal_date, is_trial, trial_expiry_date, cancel_url, status, updated_at)
                    VALUES 
                        (:uid, :svc, :amt, :cycle, :ren_d, :is_trial, :trial_d, :curl, :status, CURRENT_TIMESTAMP)
                    ON CONFLICT (user_id, service_name)
                    DO UPDATE SET
                        amount = COALESCE(EXCLUDED.amount, user_subscriptions.amount),
                        billing_cycle = COALESCE(EXCLUDED.billing_cycle, user_subscriptions.billing_cycle),
                        next_renewal_date = COALESCE(EXCLUDED.next_renewal_date, user_subscriptions.next_renewal_date),
                        is_trial = EXCLUDED.is_trial,
                        trial_expiry_date = COALESCE(EXCLUDED.trial_expiry_date, user_subscriptions.trial_expiry_date),
                        cancel_url = COALESCE(EXCLUDED.cancel_url, user_subscriptions.cancel_url),
                        status = EXCLUDED.status,
                        updated_at = CURRENT_TIMESTAMP
                """),
                {
                    "uid": uid,
                    "svc": svc,
                    "amt": amount,
                    "cycle": billing_cycle,
                    "ren_d": ren_d,
                    "is_trial": bool(is_trial),
                    "trial_d": trial_d,
                    "curl": cancel_url,
                    "status": status,
                }
            )
        return {
            "status": "success",
            "service_name": svc,
            "amount": amount,
            "next_renewal_date": str(ren_d) if ren_d else None,
            "is_trial": is_trial,
            "trial_expiry_date": str(trial_d) if trial_d else None,
        }
    except Exception as exc:
        logger.error(f"upsert_subscription error: {exc}")
        return {"status": "error", "message": str(exc)}


def get_user_subscriptions(user_id_or_chat_id: int) -> List[Dict[str, Any]]:
    """Retrieve all active subscriptions for a user."""
    uid = _resolve_uid(user_id_or_chat_id)
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, service_name, amount, billing_cycle, next_renewal_date, is_trial, trial_expiry_date, cancel_url, status
                    FROM user_subscriptions
                    WHERE user_id = :uid AND status = 'active'
                    ORDER BY COALESCE(trial_expiry_date, next_renewal_date) ASC
                """),
                {"uid": uid}
            ).mappings().all()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error(f"get_user_subscriptions error: {exc}")
        return []


def check_expiring_subscriptions(user_id_or_chat_id: int, days_ahead: int = 3) -> List[Dict[str, Any]]:
    """
    Checks if any free trials or recurring renewals are due in the next N days.
    Returns alerts that can be sent proactively to Telegram.
    """
    subs = get_user_subscriptions(user_id_or_chat_id)
    today = timeutil.today(IST)
    alerts = []

    for s in subs:
        # 1. Free Trial Expiry Check
        if s.get("is_trial") and s.get("trial_expiry_date"):
            t_date = s["trial_expiry_date"]
            if isinstance(t_date, str):
                t_date = date.fromisoformat(t_date)
            delta = (t_date - today).days
            if 0 <= delta <= days_ahead:
                alerts.append({
                    "kind": "trial_expiring",
                    "service": s["service_name"],
                    "days_remaining": delta,
                    "date": str(t_date),
                    "amount": s.get("amount") or "N/A",
                    "cancel_url": s.get("cancel_url"),
                })
        # 2. Upcoming Renewal Check
        elif s.get("next_renewal_date"):
            r_date = s["next_renewal_date"]
            if isinstance(r_date, str):
                r_date = date.fromisoformat(r_date)
            delta = (r_date - today).days
            if 0 <= delta <= days_ahead:
                alerts.append({
                    "kind": "renewal_upcoming",
                    "service": s["service_name"],
                    "days_remaining": delta,
                    "date": str(r_date),
                    "amount": s.get("amount") or "N/A",
                    "cancel_url": s.get("cancel_url"),
                })

    return alerts


# Aliases
get_active_subscriptions = get_user_subscriptions
