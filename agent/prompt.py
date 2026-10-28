"""
PeculiarAI — Production Runtime System Prompt Engine
Injects dynamic real-world context on every single turn (Time, Memory, Integrations, Schedule, Active Context).
Token-optimized for Groq high-speed execution within TPM boundaries.
"""
import os
import json
import re
import asyncio
import time
from datetime import datetime, date, timedelta
from typing import Dict, Any, Optional
from sqlalchemy import text

from timing import IST
import db
from state.active_context import format_active_context, update_active_context

RUNTIME_SYSTEM_PROMPT_TEMPLATE = """You are **PeculiarAI**, {USER_NAME}'s sharp personal companion. You reason naturally in friendly Roman Hinglish, remember user context, and act via tools (reminders, calendar, contacts, notes, Notion, GitHub).

# GROUND TRUTH CONTEXT
- Now (IST): {NOW_IST} ({WEEKDAY}) | User: {USER_NAME} | Style: {LANG_STYLE}
- Integrations: {CONNECTED_INTEGRATIONS}
- Pending Actions: {PENDING_ACTIONS}
- User Rules: {USER_RULES}
- Recent Chat: {CONVERSATION_SUMMARY}
- Memory & Facts: {MEMORY_BLOCK}
- Unified Schedule: {SCHEDULE_BLOCK}
- Active Context: {ACTIVE_CONTEXT}

# CORE PRINCIPLES
1. **Conversational Warmth:** For greetings, chit-chat, or questions ('hi', 'kaisa h bhai', 'aur bhy', 'namaste'), reply warmly and conversationally directly in Roman Hinglish without calling tools.
2. **Temporal Truth:** Current IST time is {NOW_IST}. Past dates cannot have future reminders.
3. **Execution:** Call tools directly when user requests actions (reminders, meetings, notes, weather, search). Multi-intent queries execute all required tools.
4. **Reminders & Calendar Sync:** When creating a reminder with date/time, it syncs to Google Calendar automatically. Check {SCHEDULE_BLOCK} for conflict alerts.
5. **Language & Output:** ALWAYS write in Latin / Roman Hinglish (A-Z). Never output regional non-Latin scripts. Keep responses concise, helpful, and natural. Never fabricate fake links, IDs, or actions.
"""

_USER_META_CACHE: dict = {}

async def build_runtime_system_prompt(
    chat_id: int,
    user_text: str,
    custom_system: Optional[str] = None,
    mem_ctx: Optional[Dict[str, Any]] = None
) -> str:
    """
    Constructs the PeculiarAI production prompt by dynamically resolving all 10 context placeholders.
    """
    if custom_system:
        return custom_system

    now_ist = datetime.now(IST)
    weekday = now_ist.strftime("%A")
    now_ist_str = now_ist.strftime("%I:%M %p, %d %B %Y")
    lang_style = "Hinglish (natural Roman Hindi/English mix)"

    global _USER_META_CACHE

    cached_meta = _USER_META_CACHE.get(chat_id)
    now_ts = time.time()
    if cached_meta and (now_ts - cached_meta["ts"] < 600.0):
        user_name = cached_meta["user_name"]
        connected_integrations_str = cached_meta["integrations_str"]
        rules_str = cached_meta["rules_str"]
    else:
        user_name = "Nirav"
        google_str = "Google: not connected"
        gh_str = "GitHub: not connected"
        notion_str = "Notion: not connected"
        rules_str = "None defined"
        try:
            with db.engine.connect() as conn:
                u_row = conn.execute(
                    text("""
                        SELECT u.id, u.first_name, u.username,
                               gt.email as google_email, gt.refresh_token_enc
                        FROM users u
                        LEFT JOIN google_tokens gt ON gt.user_id = u.id
                        WHERE u.telegram_chat_id = :cid
                    """),
                    {"cid": chat_id}
                ).first()

                if u_row:
                    uid = u_row[0]
                    user_name = u_row[1] or (f"@{u_row[2]}" if u_row[2] else "User")
                    has_google_oauth = bool(u_row[3] and u_row[4])
                    has_linked_email = False
                    try:
                        l_row = conn.execute(text("SELECT email FROM email_links WHERE user_id = :uid AND verified = true"), {"uid": uid}).first()
                        if l_row and l_row[0]:
                            has_linked_email = True
                    except Exception:
                        pass

                    if has_google_oauth and has_linked_email:
                        google_str = f"Google: connected & email linked ({u_row[3]})"
                    elif has_google_oauth:
                        google_str = f"Google: connected ({u_row[3]})"
                    elif has_linked_email:
                        google_str = "Google: email linked, OAuth not connected"
                    else:
                        google_str = "Google: not connected"

                    # Integrations
                    summary = db.get_user_integrations_summary(chat_id)
                    if summary.get("github", {}).get("connected"):
                        gh_info = db.get_user_integration(chat_id, "github")
                        gh_u = None
                        if isinstance(gh_info, dict):
                            try:
                                gh_u = json.loads(gh_info.get("config_data") or "{}").get("username")
                            except Exception:
                                pass
                        gh_str = f"GitHub: connected (@{gh_u})" if gh_u else "GitHub: connected"
                    if summary.get("notion", {}).get("connected"):
                        notion_str = "Notion: connected"

                    # Rules
                    try:
                        r_rows = conn.execute(
                            text("SELECT rule_text FROM user_rules WHERE user_id = :uid AND is_active = true"),
                            {"uid": uid}
                        ).fetchall()
                        if r_rows:
                            rules_str = "; ".join([r[0] for r in r_rows])
                    except Exception:
                        pass
        except Exception as exc:
            print("Prompt user meta fetch error:", repr(exc))

        connected_integrations_str = f"{google_str} | {gh_str} | {notion_str}"
        _USER_META_CACHE[chat_id] = {
            "ts": now_ts,
            "user_name": user_name,
            "integrations_str": connected_integrations_str,
            "rules_str": rules_str
        }

    # 2. Today's and Tomorrow's Unified Schedule (Non-blocking high-speed injection)
    today_d = now_ist.date()
    tomorrow_d = today_d + timedelta(days=1)

    if not hasattr(build_runtime_system_prompt, "_sched_cache"):
        build_runtime_system_prompt._sched_cache = {}
    _sched_cache = build_runtime_system_prompt._sched_cache

    cache_key_today = f"{chat_id}_{today_d.isoformat()}"
    cached_today = _sched_cache.get(cache_key_today)
    now_ts = time.time()

    if cached_today and (now_ts - cached_today["ts"]) < 180:
        today_sched = cached_today["data"]
    else:
        # Non-blocking: check main unified cache or warm in background
        from main import _UNIFIED_SCHED_CACHE
        ck = (chat_id, str(today_d))
        main_c = _UNIFIED_SCHED_CACHE.get(ck)
        if main_c and (now_ts < main_c["exp"]):
            today_sched = main_c["data"]
            _sched_cache[cache_key_today] = {"data": today_sched, "ts": now_ts}
        else:
            today_sched = {"calendar_events": [], "reminders": [], "commitments": []}
            try:
                from main import get_unified_schedule
                asyncio.create_task(get_unified_schedule(chat_id, today_d))
            except Exception:
                pass

    tomorrow_sched = {"calendar_events": [], "reminders": [], "commitments": []}


    # 3. Pending Actions
    pending_str = "None"
    try:
        from state.pending import get_pending_action
        p_act = get_pending_action(chat_id)
        if p_act:
            pending_str = f"Awaiting confirmation for '{p_act.get('action_kind', 'action')}': {json.dumps(p_act.get('payload', {}), ensure_ascii=False)}"
    except Exception:
        pass

    # 4. Context Memory Block (Always-on cognitive memory)
    memory_block = "No specific entity/promise retrieved for this turn."
    if mem_ctx and isinstance(mem_ctx, dict):
        memory_block = mem_ctx.get("formatted_block") or memory_block
    else:
        try:
            from memory.retrieval import assemble_memory_context
            _computed_ctx = await asyncio.wait_for(asyncio.to_thread(assemble_memory_context, chat_id, user_text), timeout=0.6)
            memory_block = _computed_ctx.get("formatted_block") or memory_block
        except Exception:
            pass

    conv_summary = "Recent conversation active in chat dialogue."


    def _format_rem_item(r):
        if isinstance(r, dict):
            title = r.get("title", "Reminder")
            d_time = r.get("due_time")
        else:
            title = r[1] if len(r) > 1 else "Reminder"
            d_time = r[3] if len(r) > 3 else None
        time_str = d_time.strftime("%I:%M %p") if hasattr(d_time, "strftime") else (str(d_time) if d_time else "all day")
        return f"{title} ({time_str})"

    def _format_cal_event(ev):
        if not isinstance(ev, dict):
            return str(ev)
        summ = ev.get("summary") or "Meeting"
        st_str = ev.get("start", {}).get("dateTime")
        if st_str:
            try:
                st_dt = datetime.fromisoformat(st_str)
                st_ist = st_dt.astimezone(IST)
                return f"{summ} ({st_ist.strftime('%I:%M %p')})"
            except Exception:
                pass
        return summ

    # 7. Today's and Tomorrow's Unified Schedule
    schedule_parts = []
    # Today summary
    t_evs = [_format_cal_event(ev) for ev in today_sched.get("calendar_events", [])]
    t_rems = [_format_rem_item(r) for r in today_sched.get("reminders", [])]
    t_comms = [f"{c['name']}: {c['commitment']}" for c in today_sched.get("commitments", [])]
    today_line = f"Today ({today_d.strftime('%A, %d %b')}): " + (f"Calendar: {', '.join(t_evs)} | " if t_evs else "") + (f"Reminders: {', '.join(t_rems)} | " if t_rems else "") + (f"Commitments: {', '.join(t_comms)}" if t_comms else "")
    if not (t_evs or t_rems or t_comms):
        today_line += "No meetings or reminders scheduled (Free)."
    schedule_parts.append(today_line)

    # Tomorrow summary
    tm_evs = [_format_cal_event(ev) for ev in tomorrow_sched.get("calendar_events", [])]
    tm_rems = [_format_rem_item(r) for r in tomorrow_sched.get("reminders", [])]
    tm_comms = [f"{c['name']}: {c['commitment']}" for c in tomorrow_sched.get("commitments", [])]
    tm_line = f"Tomorrow ({tomorrow_d.strftime('%A, %d %b')}): " + (f"Calendar: {', '.join(tm_evs)} | " if tm_evs else "") + (f"Reminders: {', '.join(tm_rems)} | " if tm_rems else "") + (f"Commitments: {', '.join(tm_comms)}" if tm_comms else "")
    if not (tm_evs or tm_rems or tm_comms):
        tm_line += "No meetings or reminders scheduled (Free)."
    schedule_parts.append(tm_line)

    schedule_block = "\n".join(schedule_parts)

    # 8. Available Tools Catalog
    available_tools_str = (
        "Memory & Contacts: save_contact, get_contact, list_contacts, update_contact_commitment, get_commitments, save_note_or_fact, search_memory, clear_chat_history | "
        "Tasks & Reminders: create_reminder, list_reminders, delete_reminder | "
        "Calendar & Schedule: get_schedule, check_availability, create_meeting, update_meeting, delete_meeting, get_meet_link | "
        "Communications & Files: send_email, upload_to_drive, generate_pdf, generate_excel | "
        "Third-Party Connectors: check_integrations_status, connect_integration, list_github_repos, get_github_file, update_github_file, list_github_issues, create_notion_page, update_notion_page, list_notion_pages | "
        "Real-Time Utilities & Web Search: search_web, read_webpage, get_weather, convert_currency, get_market_price, calculate"
    )

    # 9. Active Context
    active_ctx_str = format_active_context(chat_id)

    # Build prompt replacing all placeholders safely
    prompt = RUNTIME_SYSTEM_PROMPT_TEMPLATE
    prompt = prompt.replace("{USER_NAME}", user_name)
    prompt = prompt.replace("{NOW_IST}", now_ist_str)
    prompt = prompt.replace("{WEEKDAY}", weekday)
    prompt = prompt.replace("{LANG_STYLE}", lang_style)
    prompt = prompt.replace("{CONNECTED_INTEGRATIONS}", connected_integrations_str)
    prompt = prompt.replace("{PENDING_ACTIONS}", pending_str)
    prompt = prompt.replace("{USER_RULES}", rules_str)
    prompt = prompt.replace("{CONVERSATION_SUMMARY}", conv_summary)
    prompt = prompt.replace("{MEMORY_BLOCK}", memory_block)
    prompt = prompt.replace("{SCHEDULE_BLOCK}", schedule_block)
    prompt = prompt.replace("{AVAILABLE_TOOLS}", available_tools_str)
    prompt = prompt.replace("{ACTIVE_CONTEXT}", active_ctx_str)

    return prompt
