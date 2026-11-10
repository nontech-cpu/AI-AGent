import re
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
import core.timeutil as timeutil
import db
from utilities import get_weather

logger = logging.getLogger("peculiar_heartbeat")

# In-memory tracking of dispatched alerts to avoid repeats within the same day
_EVENING_WRAPUP_SENT = set()

def get_active_target_chat_ids() -> list:
    """Dynamically fetches all registered Telegram chat IDs so all users receive briefings & reminders."""
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(db.text("SELECT DISTINCT telegram_chat_id FROM users WHERE telegram_chat_id > 0")).fetchall()
            ids = [int(r[0]) for r in rows if r[0]]
            if ids:
                return ids
    except Exception:
        pass
    return [1758056012]

TARGET_CHAT_IDS = [1234828592, 1758056012]

async def send_proactive_telegram(chat_id: int, message: str) -> bool:
    """Dispatches proactive message directly to user's Telegram with clean HTML rendering."""
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token or not chat_id:
        return False
    from utilities import convert_tables_to_cards
    from main import format_for_telegram_html, strip_markdown_formatting
    clean_msg = convert_tables_to_cards(message)
    html_msg = format_for_telegram_html(clean_msg)
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": html_msg,
                    "parse_mode": "HTML"
                }
            )
            if resp.status_code == 200:
                return True
            plain = strip_markdown_formatting(clean_msg)
            resp2 = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": plain
                }
            )
            return resp2.status_code == 200
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


async def check_evening_wrapup(now_ist: datetime):
    """Sends a warm 9:30 PM reflection on pending checklists and day's accomplishments."""
    today_str = str(now_ist.date())
    # Target window: 21:30 - 21:35 IST
    if now_ist.hour == 21 and 30 <= now_ist.minute <= 35:
        for cid in get_active_target_chat_ids():
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

            name = db.get_user_display_name(cid)
            greeting_name = f" {name}" if name else ""

            # Check pending commitments due today
            pending_comms = []
            try:
                with db.engine.connect() as conn:
                    rows = conn.execute(
                        db.text("SELECT title FROM commitments WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) AND status = 'pending' AND (target_date IS NULL OR target_date <= :d)"),
                        {"cid": cid_can, "d": now_ist.date()}
                    ).fetchall()
                    pending_comms = [r[0] for r in rows if r[0]]
            except Exception:
                pass

            msg_parts = []
            user_lang = db.get_user_language(cid)
            is_eng = (user_lang == "english")
            if pending_items:
                preview = ", ".join(pending_items[:3])
                if is_eng:
                    user_salutation = f"{name}, y" if name else "Y"
                    msg_parts.append(
                        f"🌙 **Evening Wrap-up & Status Check:**\n"
                        f"{user_salutation}ou have {len(pending_items)} items pending in your lists: **{preview}**."
                    )
                else:
                    user_salutation = f"{name}, a" if name else "A"
                    msg_parts.append(
                        f"🌙 **Evening Wrap-up & Status Check:**\n"
                        f"{user_salutation}aj ke din ke ye {len(pending_items)} items lists mein pending hain: **{preview}**."
                    )
            else:
                if is_eng:
                    msg_parts.append(
                        f"🌙 **Good Evening{greeting_name}!**\n"
                        f"All major tasks and schedule for today are complete."
                    )
                else:
                    msg_parts.append(
                        f"🌙 **Good Evening{greeting_name}!**\n"
                        f"Aaj ke saare major tasks aur schedule complete hain."
                    )

            if pending_comms:
                comm_preview = ", ".join(pending_comms[:2])
                if is_eng:
                    msg_parts.append(f"\n🤝 **Commitment Check:** You planned: **{comm_preview}**.\nWas this completed or should I carry it forward to tomorrow?")
                else:
                    msg_parts.append(f"\n🤝 **Commitment Check:** Aaj ke liye aapne promise/plan kiya tha: **{comm_preview}**.\nKya ye complete ho gaya hai ya kal carry-forward karoon?")
            else:
                if is_eng:
                    msg_parts.append("\nLet me know if you would like to plan for tomorrow. Have a great rest! 😊")
                else:
                    msg_parts.append("\nKal ke din ki planning karni ho toh batana, aaram se rest karo! 😊")

            await broadcast_alert("\n".join(msg_parts), specific_chat_id=cid)


_LAST_RECURRING_ROLL_DATE = None

async def roll_daily_recurring_reminders(now_ist: datetime):
    """
    Dynamically advances daily recurring reminders day-by-day.
    Runs at most once per calendar day to avoid locking DB connections in a continuous 60-second loop.
    """
    global _LAST_RECURRING_ROLL_DATE
    today = now_ist.date()
    if _LAST_RECURRING_ROLL_DATE == today:
        return
    _LAST_RECURRING_ROLL_DATE = today

    def _do_roll_sync():
        tomorrow = today + timedelta(days=1)
        for cid in get_active_target_chat_ids():
            try:
                cid_can = db.canonical_chat_id(cid)
                rules = db.get_user_rules(cid_can) or []
                for r in rules:
                    rule_str = r.get("rule", "") if isinstance(r, dict) else str(r)
                    if "daily recurring reminder:" in rule_str.lower():
                        m = re.search(r"daily recurring reminder:\s*(.+?)\s+at\s+(\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)?)", rule_str, re.IGNORECASE)
                        if not m:
                            continue
                        rem_title = m.group(1).strip()
                        rem_time_str = m.group(2).strip()

                        rem_time = None
                        for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M", "%H:%M:%S"):
                            try:
                                rem_time = datetime.strptime(rem_time_str, fmt).time()
                                break
                            except Exception:
                                pass
                        if not rem_time:
                            continue

                        tomorrow_items = db.items_for_date(cid_can, tomorrow) or []
                        has_tomorrow = False
                        for it in tomorrow_items:
                            it_title = it.get("title", "") if isinstance(it, dict) else (it[1] if len(it) > 1 else "")
                            if it_title.strip().lower() == rem_title.lower():
                                has_tomorrow = True
                                break

                        if has_tomorrow:
                            continue

                        try:
                            with db.engine.begin() as conn:
                                conn.execute(
                                    db.text(
                                        "DELETE FROM reminders WHERE item_id IN ("
                                        "  SELECT id FROM memory_items WHERE user_id IN (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                                        "  AND LOWER(title) = LOWER(:t) AND due_date > :tom"
                                        ")"
                                    ),
                                    {"cid": cid_can, "t": rem_title, "tom": tomorrow}
                                )
                                conn.execute(
                                    db.text(
                                        "DELETE FROM memory_items WHERE user_id IN (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                                        "AND LOWER(title) = LOWER(:t) AND due_date > :tom"
                                    ),
                                    {"cid": cid_can, "t": rem_title, "tom": tomorrow}
                                )
                        except Exception as ce:
                            logger.warning(f"Error cleaning future entries during auto-roll: {ce}")

                        cid_mem = db.save_memory(
                            cid_can,
                            category="appointments",
                            title=rem_title,
                            amount=None,
                            due_date=tomorrow,
                            due_time=rem_time
                        )
                        try:
                            from timing import build_plan
                            from db import add_reminders
                            plan = build_plan(tomorrow, rem_time)
                            if plan.get("reminders") and cid_mem:
                                add_reminders(cid_mem, plan["reminders"])
                        except Exception as pe:
                            logger.error(f"Build plan error during recurring roll: {pe}")

                        logger.info(f"Dynamically rolled recurring reminder '{rem_title}' for {tomorrow} for user {cid_can}")
            except Exception as ex:
                logger.error(f"Error rolling daily recurring reminders for {cid}: {ex}")

    await asyncio.to_thread(_do_roll_sync)


_SUB_WATCHDOG_SENT = set()
_BDAY_WATCHDOG_SENT = set()
_FOLLOWUP_SENT = set()
_WARMTH_SENT = set()

async def check_subscription_watchdog_daily(now_ist: datetime):
    """Daily check for free-trials and subscriptions expiring in the next 3 days."""
    today_str = str(now_ist.date())
    if now_ist.hour == 10 and 0 <= now_ist.minute <= 10:
        from services.subscription_service import check_expiring_subscriptions
        for cid in get_active_target_chat_ids():
            key = f"{today_str}_{cid}_sub"
            if key in _SUB_WATCHDOG_SENT:
                continue
            _SUB_WATCHDOG_SENT.add(key)
            try:
                expiring = await asyncio.to_thread(check_expiring_subscriptions, cid, 3)
                if expiring:
                    lines = ["⚠️ **Subscription & Free-Trial Watchdog Alert:**\n"]
                    for it in expiring:
                        if it.get("kind") == "trial_expiring":
                            lines.append(f"• ⏳ **{it['service']}** free trial {it['days_remaining']} din me expire ho raha hai ({it['date']})!")
                            if it.get("cancel_url"):
                                lines.append(f"  👉 Cancel Link: {it['cancel_url']}")
                        else:
                            amt = f"₹{it['amount']}" if it.get('amount') else ""
                            lines.append(f"• 💳 **{it['service']}** auto-renew hone wala hai {it['days_remaining']} din me ({it['date']}) {amt}!")
                            if it.get("cancel_url"):
                                lines.append(f"  👉 Manage: {it['cancel_url']}")
                    lines.append("\nAgar renew nahi karna hai toh samay par cancel kar lein taaki auto-debit na ho.")
                    await broadcast_alert("\n".join(lines), specific_chat_id=cid)
            except Exception as e:
                logger.error(f"Subscription watchdog error for {cid}: {e}")

async def check_birthday_watchdog_daily(now_ist: datetime):
    """Daily check for upcoming birthdays in the user's relationship graph."""
    today_str = str(now_ist.date())
    if now_ist.hour == 9 and 0 <= now_ist.minute <= 10:
        from services.relationship_service import get_upcoming_birthdays
        for cid in get_active_target_chat_ids():
            key = f"{today_str}_{cid}_bday"
            if key in _BDAY_WATCHDOG_SENT:
                continue
            _BDAY_WATCHDOG_SENT.add(key)
            try:
                bdays = await asyncio.to_thread(get_upcoming_birthdays, cid, 2)
                if bdays:
                    lines = ["🎂 **Upcoming Birthday Watchdog Alert:**\n"]
                    for b in bdays:
                        when_str = "aaj hai! 🎉" if b['days_remaining'] == 0 else (f"kal ({b['date'].strftime('%d %b')}) hai!" if b['days_remaining'] == 1 else f"{b['days_remaining']} din me ({b['date'].strftime('%d %b')}) hai!")
                        lines.append(f"• **{b['name']}** ({b['relationship']}) ka birthday {when_str}")
                    lines.append("\nKya aapko koi reminder lagana hai ya wish prepare karni hai?")
                    await broadcast_alert("\n".join(lines), specific_chat_id=cid)
            except Exception as e:
                logger.error(f"Birthday watchdog error for {cid}: {e}")


async def check_pending_followups_daily(now_ist: datetime):
    """Daily check at 11:00 AM IST for communications waiting for reply past 3 days."""
    today_str = str(now_ist.date())
    if now_ist.hour == 11 and 0 <= now_ist.minute <= 10:
        from services.followup_service import get_due_followups
        for cid in get_active_target_chat_ids():
            key = f"{today_str}_{cid}_followup"
            if key in _FOLLOWUP_SENT:
                continue
            _FOLLOWUP_SENT.add(key)
            try:
                due = await asyncio.to_thread(get_due_followups, cid)
                if due:
                    lines = ["📬 **Follow-up & Ghosting Watchdog Alert:**\n"]
                    for d in due:
                        lines.append(f"• **{d['recipient']}** ko '{d['subject']}' bheje 3+ din ho chuke hain, abhi tak reply nahi aaya.")
                    lines.append("\nKya polite follow-up draft karun ya ise resolve mark karein? (`/resolve_followup <ID>`)")
                    await broadcast_alert("\n".join(lines), specific_chat_id=cid)
            except Exception as e:
                logger.error(f"Followup watchdog error for {cid}: {e}")


async def check_relationship_warmth_weekly(now_ist: datetime):
    """Weekly check on weekends at 11:30 AM IST for contacts not interacted with in 30 days."""
    if now_ist.weekday() in (5, 6) and now_ist.hour == 11 and 30 <= now_ist.minute <= 40:
        week_str = f"{now_ist.year}_W{now_ist.isocalendar()[1]}"
        from services.relationship_service import get_dormant_relationships
        for cid in get_active_target_chat_ids():
            key = f"{week_str}_{cid}_warmth"
            if key in _WARMTH_SENT:
                continue
            _WARMTH_SENT.add(key)
            try:
                dormant = await asyncio.to_thread(get_dormant_relationships, cid, 30)
                if dormant:
                    lines = ["❤️ **Relationship Warmth & Catch-up Radar:**\n"]
                    for d in dormant:
                        lines.append(f"• **{d['name']}** ({d['relationship']}) se pichle 30+ din se koi contact nahi hua.")
                    lines.append("\nWeekend hai! Ek quick 'Hey, how have you been?' message bhej kar catch-up karein? 😊")
                    await broadcast_alert("\n".join(lines), specific_chat_id=cid)
            except Exception as e:
                logger.error(f"Warmth check error for {cid}: {e}")


async def heartbeat_loop():
    """Continuous background monitoring daemon running inside FastAPI lifespan."""
    logger.info("Starting Proactive Heartbeat Daemon...")
    await asyncio.sleep(15)  # initial delay for startup
    while True:
        try:
            now_ist = timeutil.now(IST)
            await check_evening_wrapup(now_ist)
            await check_subscription_watchdog_daily(now_ist)
            await check_birthday_watchdog_daily(now_ist)
            await check_pending_followups_daily(now_ist)
            await check_relationship_warmth_weekly(now_ist)
            await roll_daily_recurring_reminders(now_ist)
        except Exception as exc:
            logger.error(f"Heartbeat loop iteration error: {repr(exc)}")
        await asyncio.sleep(60)  # tick every 60 seconds

