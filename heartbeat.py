"""
PeculiarAI — Proactive Heartbeat Daemon
Autonomously monitors schedule, reminders, weather anomalies, and evening wrap-up.
Sends proactive executive pings to Telegram & Web Companion without waiting for user to speak.
"""
import os
import asyncio
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Dict, Any, List
import httpx

from timing import IST
import db
from utilities import get_weather

logger = logging.getLogger("peculiar_heartbeat")

# In-memory tracking of dispatched alerts to avoid repeats within the same day
_DISPATCHED_PRE_ALERTS = set()
_EVENING_WRAPUP_SENT = set()

TARGET_CHAT_IDS = [1234828592, 1758056012]

async def send_proactive_telegram(chat_id: int, message: str) -> bool:
    """Dispatches proactive message directly to user's Telegram."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not chat_id:
        return False
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": message,
                    "parse_mode": "Markdown"
                }
            )
            return resp.status_code == 200
    except Exception as exc:
        logger.error(f"Proactive telegram send error for {chat_id}: {repr(exc)}")
        return False


async def broadcast_alert(message: str, specific_chat_id: Optional[int] = None):
    """Sends alert to Telegram and persists in DB so Web Companion sees it immediately."""
    targets = [specific_chat_id] if specific_chat_id else TARGET_CHAT_IDS
    for cid in targets:
        try:
            # Send to Telegram (only positive valid telegram IDs)
            if cid > 0:
                await send_proactive_telegram(cid, message)
            # Log to DB chat history
            db.log_chat_message(cid, "assistant", message)
        except Exception as exc:
            logger.error(f"Broadcast error for {cid}: {repr(exc)}")


async def check_15min_upcoming_alerts(now_ist: datetime):
    """Detects any reminder due within the next 15 minutes and sends a proactive ping."""
    today = now_ist.date()
    now_time = now_ist.time()
    current_minutes = now_time.hour * 60 + now_time.minute

    for cid in TARGET_CHAT_IDS:
        cid_can = db.canonical_chat_id(cid)
        items = db.items_for_date(cid_can, today) or []
        for it in items:
            rem_id = it.get("id") if isinstance(it, dict) else (it[0] if len(it) > 0 else None)
            title = it.get("title") if isinstance(it, dict) else (it[1] if len(it) > 1 else "Reminder")
            due_time = it.get("due_time") if isinstance(it, dict) else (it[3] if len(it) > 3 else None)

            if not due_time:
                continue

            try:
                due_mins = due_time.hour * 60 + due_time.minute
                diff = due_mins - current_minutes
                # Trigger when 5 to 15 minutes remaining
                if 0 <= diff <= 15:
                    alert_key = f"{today}_{rem_id}_15min"
                    if alert_key not in _DISPATCHED_PRE_ALERTS:
                        _DISPATCHED_PRE_ALERTS.add(alert_key)
                        clock_str = due_time.strftime("%I:%M %p")
                        msg = f"🔔 **Proactive Reminder (in {diff} mins):**\nBhai, aapka **{title}** scheduled hai at **{clock_str}**!"
                        await broadcast_alert(msg, specific_chat_id=cid)
            except Exception as e:
                pass


async def check_evening_wrapup(now_ist: datetime):
    """Sends a warm 9:30 PM reflection on pending checklists and day's accomplishments."""
    today_str = str(now_ist.date())
    # Target window: 21:30 - 21:35 IST
    if now_ist.hour == 21 and 30 <= now_ist.minute <= 35:
        for cid in TARGET_CHAT_IDS:
            key = f"{today_str}_{cid}_evening"
            if key in _EVENING_WRAPUP_SENT:
                continue
            _EVENING_WRAPUP_SENT.add(key)

            cid_can = db.canonical_chat_id(cid)
            lists = db.get_smart_lists(cid_can) or []
            pending_items = []
            for l in lists:
                for item in l.get("items", []):
                    if not item.get("checked"):
                        pending_items.append(f"{item.get('title')} ({l.get('name')})")

            if pending_items:
                preview = ", ".join(pending_items[:3])
                msg = (
                    f"🌙 **Evening Wrap-up & Status Check:**\n"
                    f"Bhai, aaj ke din ke ye {len(pending_items)} items lists mein pending hain: **{preview}**.\n"
                    f"Kya inhe complete mark karna hai ya kal ke liye carry forward karoon?"
                )
            else:
                msg = (
                    f"🌙 **Good Evening Neerav bhai!**\n"
                    f"Aaj ke saare major tasks aur schedule complete hain. Kal ke din ki planning karni ho toh batana, aaram se rest karo! 😊"
                )
            await broadcast_alert(msg, specific_chat_id=cid)


async def heartbeat_loop():
    """Continuous background monitoring daemon running inside FastAPI lifespan."""
    logger.info("Starting Proactive Heartbeat Daemon...")
    await asyncio.sleep(15)  # initial delay for startup
    while True:
        try:
            now_ist = datetime.now(IST)
            await check_15min_upcoming_alerts(now_ist)
            await check_evening_wrapup(now_ist)
        except Exception as exc:
            logger.error(f"Heartbeat loop iteration error: {repr(exc)}")
        await asyncio.sleep(60)  # tick every 60 seconds
