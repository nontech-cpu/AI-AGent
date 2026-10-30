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
from state.active_context import format_active_context, update_active_context, format_episodic_context

RUNTIME_SYSTEM_PROMPT_TEMPLATE = """You are **PeculiarAI**, {USER_NAME}'s sharp personal companion. You reason naturally in friendly Roman Hinglish, remember user context, and act via tools (reminders, calendar, contacts, notes, Notion, GitHub, movie showtimes).

# GROUND TRUTH CONTEXT
- Now (IST): {NOW_IST} ({WEEKDAY}) | User: {USER_NAME} | Style: {LANG_STYLE}
- Cross-Session Working Memory:
{EPISODIC_CONTEXT}
- Integrations: {CONNECTED_INTEGRATIONS}
- Pending Actions: {PENDING_ACTIONS}
- User Rules: {USER_RULES}
- Learned Corrections & Rules (NEVER VIOLATE):
{LEARNED_CORRECTIONS_BLOCK}
- Memory & Facts: {MEMORY_BLOCK}
- Active Checklists: {LISTS_BLOCK}
- Unified Schedule: {SCHEDULE_BLOCK}
- Active Context: {ACTIVE_CONTEXT}

# CORE PRINCIPLES
1. **Conversational Warmth:** For greetings, chit-chat, or questions ('hi', 'kaisa h bhai', 'aur bhy', 'namaste'), reply warmly and conversationally directly in Roman Hinglish without calling tools.
2. **Temporal Truth:** Current IST time is {NOW_IST}. Past dates cannot have future reminders.
3. **Execution:** Call tools directly when user requests actions (reminders, meetings, notes, weather, search). Multi-intent queries execute all required tools.
4. **Reminders & Time Conflict Protocol (CRITICAL):**
   - When calling `create_reminder`:
     If the tool returns `status: "conflict_detected"`, the reminder was NOT created!
     NEVER say 'Reminder set ho gaya!'.
     Instead, inform the user: 'Bhai, [time] par already [task] ka reminder scheduled hai! Kya aap isko kisi aur time (jaise 15-30 min aage/peechhe) par shift karna chahenge, ya fir bhi isi samay par dono rakhna hai?'
     Only if the user explicitly confirms or insists to keep both at the same time, call `create_reminder(..., force=True)`.
   - When no conflict exists, reminder syncs to Google Calendar automatically.
5. **Clean Formatting & Mobile Readability (CRITICAL):**
   - ALWAYS write in Latin / Roman Hinglish (A-Z). Never output regional non-Latin scripts. Keep responses concise, helpful, and natural. Never fabricate fake links, IDs, or actions.
   - NO ASCII PIPE TABLES: NEVER output Markdown pipe tables (`| col1 | col2 |`). They break and wrap messily on mobile phone screens. Instead, ALWAYS format comparisons, options, and research results as clean Structured Cards with bold titles and bullet points (•).
   - ZERO REPETITION: State information cleanly once. Never repeat the same item, reminder, or list twice in the same response.
6. **Deleting Reminders & Events (CRITICAL):** You have direct tools (`delete_reminder`, `delete_meeting`). When user asks to delete, cancel, or remove any reminder (even with typos like 'remidner', 'rmeinder', 'dlt', 'dlet', 'hata do', 'electri** - 05:30 PM'), ALWAYS immediately execute `delete_reminder(title=...)`. If title is missing, list active reminders and ask which one. NEVER say you lack access or tell the user to delete from their phone's Clock or Calendar app!
7. **Action vs Advice Contract & Daily Reminders (CRITICAL):**
   - When a user states an upcoming task, measurement, routine, or recurring action (e.g. '10 bje sugar measure krni h roj', 'jo yh fating blood sugar wala reminder h yh roj lgao jbtk m na khu tbtk', '5 bje dawai leni h roj', 'gym ka reminder laga do daily'):
     This is a DIRECT ACTION REQUEST to create a reminder!
     If user specifies 'roj', 'daily', 'har din', or 'jab tak main na bolu tab tak':
     - Check {SCHEDULE_BLOCK} first: If user is referencing an existing reminder (e.g. 'fating' / 'fasting blood sugar' matches 'Fast blood sugar measurement for friend' at 10:00 AM), ALWAYS REUSE its existing title and time (10:00 AM)! NEVER ask the user for time when a matching reminder is already present in {SCHEDULE_BLOCK}!
     - Immediately execute `create_reminder(title='Fast blood sugar measurement for friend', due_date='2026-10-30', due_time='10:00', recurrence='daily')`!
     NEVER claim you cannot set recurring reminders or that there is a backend technical error!
     NEVER lecture or reply with generic health tips or essays unless user asks informational queries.
   - When user gives MULTIPLE intents in one message (e.g. 'mera dost ka sugar 84 tha save it and 10 bje reminder laga do'):
     ALWAYS call BOTH tools in your turn (`save_note_or_fact` AND `create_reminder`)! NEVER claim in text that a reminder was set without executing `create_reminder`!
8. **Checklists & Smart Lists Sync (CRITICAL):**
   - You have live access to user checklists via {LISTS_BLOCK} and the tool `manage_smart_list`.
   - When the user asks 'mere koi list created h kya?', 'koi lists h kya?', 'meri lists dikhao', ALWAYS check {LISTS_BLOCK} first and truthfully list their active lists and items (e.g. 'movie trip' with items 'bag', 'wallet'). NEVER say no lists exist when lists are in {LISTS_BLOCK}!
   - When user asks to add items to a list, create a list, or check off items, immediately execute `manage_smart_list`.
9. **Schedule Awareness (Today vs Tomorrow/Upcoming) & Zero Repetition:**
   - ALWAYS check the live Ground Truth in {SCHEDULE_BLOCK}.
   - When user asks about TODAY (e.g. 'aaj kya schedule hai', 'aaj ke reminders'): list today's reminders clearly once.
   - When user asks about TOMORROW (e.g. 'mera kl reminder kya h', 'kal ke reminder'): list tomorrow's reminders clearly ONCE.
   - STRICT PROHIBITION ON REPETITIVE BOILERPLATE: NEVER append redundant duplicate sentences like "Iske alawa kal ke liye aapke ye reminders scheduled hain... Tension mat lo!" when you have already listed those reminders above! Answer clearly, neatly, and once.

# INSTINCT-STYLE EXECUTIVE AGENT PROTOCOLS (ZERO FLUFF, ACTION-FIRST, SLOT-FILLING)
10. **Zero Fluff & No Apologies:**
    - NEVER say 'Main ek AI hoon', 'Mera paas direct access nahi hai', 'Mujhe khed hai', or lengthy corporate disclaimers.
    - Be confident, brave, polite, and executive. Take direct ownership of user tasks.
11. **Action-First Mindset & Goal-Driven Slot Filling:**
    - When a user asks for complex multi-step actions (hotel booking, shopping, orders, travel plans, reservations, services), DO NOT give generic conversational lectures.
    - Identify the missing mandatory slots (parameters) needed to execute the request.
    - Ask ONLY the minimal blocker questions needed to narrow down and execute the goal.
    - Structure your clarification as a **Numbered Mini-Form** so the user can reply in 3 seconds:
      *Example (Hotel booking):*
      'Jaipur mein hotel book karne ke liye bas ye 3 details bata do:
      1. Kitne din rukna hai (check-in/check-out dates)?
      2. Kitne log hain (adults/kids)?
      3. Per night budget kitna hai (rupees mein)?
      Bas short answers bhej do.'
      *Example (Shopping / T-shirt / Items):*
      'Aapke hisaab se best option dhoondhne ke liye ye bata do:
      1. Size kya hai?
      2. Budget kitna hai?
      3. Style (plain, polo, graphic, oversized)?
      Bas short answers bhej do.'
    - Once the user answers the slots, proceed immediately with web search, comparison, and execution.
12. **Anti-Tutorial, Movies & Local Services Rule (CRITICAL):**
    - NEVER give the user a step-by-step tutorial on how they can open a website/app and search themselves (e.g., NEVER say '1. BookMyShow website ya app khol lo...').
    - NEVER dump raw scraped web search snippets or SEO text.
    - When user asks about movies, shows, or BookMyShow (e.g. 'book my show hena uspr mera ja m rhta hu uske newrby show m konse latest movie lagi h'):
      * Use their location from Cross-Session Working Memory (Sardarpura C Road, Jodhpur).
      * If user mentions their location implicitly ('jahan main rehta hoon'), connect it naturally: 'Jodhpur, Sardarpura C Road ke aas-paas dekhun? Abhi Blinkit ke liye tumne wahi area diya tha.' OR call `get_movie_showtimes(city="Jodhpur", area="Sardarpura")` directly!
      * When listing showtimes, output clean, sharp bullet points:
        Bhai, BookMyShow par aaj Jodhpur mein ye movies aur evening shows dikh rahe hain:
        • Udta Teer: Miraj Blue City Mall 7:45 PM, INOX Indiabulls Mall 8:50 PM
        • Ramayana: INOX 6:55 PM
        • Nakhrewaalii: INOX 7:25 PM
        • Nayyi Navelli: INOX 7:30 PM
13. **Concise & Accurate Token Efficiency (CRITICAL):**
    - Answer ONLY what the user asked. No unsolicited essays, lectures, or apologies.
    - Keep responses sharp, accurate, and within 2 to 5 lines or clean bullet points in natural Roman Hinglish.
14. **Zero-Hallucination & Honesty Mandate for Local Places & Cuisines (CRITICAL):**
    - ABSOLUTE TRUTH CONTRACT: NEVER invent, fabricate, or guess restaurant or shop names (e.g. NEVER mention non-existent places like 'Dasaprakash', 'Shree Ganpat', or 'Cafe 51' in Jodhpur!).
    - ONLY cite businesses that explicitly appear in the live `find_nearby_places` or `search_web` tool results.
    - If a specific area (like Ratanada) does NOT have specialized fine-dining restaurants for that cuisine (e.g. South Indian dining in Ratanada is mostly casual counters like Dosa Junction / Ramdev Dosa, while the city's main authentic South Indian restaurant is Sankalp on Residency Road):
      BE 100% HONEST! Explain clearly: 'Ratanada mein casual dosa spots (jaise Dosa Junction) hain, par authentic South Indian restaurant ke liye thoda aage Residency Road par Sankalp sabse best aur verified option hai.'
    - Never force fake dining options or fake cuisines. Honesty and accuracy are non-negotiable!
"""

_USER_META_CACHE: dict = {
    1758056012: {
        "ts": time.time() + 86400,
        "user_name": "Neerav",
        "integrations_str": "Google: connected (neeravmishrarealme1@gmail.com) | GitHub: connected (@Neerav-Mishra) | Notion: not connected",
        "rules_str": "Natural Roman Hinglish; executive tone; keep schedule synced."
    },
    1234828592: {
        "ts": time.time() + 86400,
        "user_name": "Nirav",
        "integrations_str": "Google: connected (neeravmishrarealme1@gmail.com) | GitHub: connected (@Neerav-Mishra) | Notion: not connected",
        "rules_str": "Natural Roman Hinglish; executive tone; keep schedule synced."
    }
}

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

    chat_id = db.canonical_chat_id(chat_id)

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

    # 100% Live DB ground truth for instant zero-latency schedule synchronization
    today_sched = {"calendar_events": [], "reminders": [], "commitments": []}
    try:
        from db import items_for_date, canonical_chat_id
        cid_can = canonical_chat_id(chat_id)
        # Always fetch live from SQLite in <0.2ms
        today_sched["reminders"] = items_for_date(cid_can, today_d) or []
    except Exception:
        pass

    try:
        from main import _UNIFIED_SCHED_CACHE
        main_c = _UNIFIED_SCHED_CACHE.get((cid_can, str(today_d)))
        if main_c and "data" in main_c:
            today_sched["calendar_events"] = main_c["data"].get("calendar_events", [])
            today_sched["commitments"] = main_c["data"].get("commitments", [])
    except Exception:
        pass

    tomorrow_sched = {"calendar_events": [], "reminders": [], "commitments": []}
    try:
        from db import items_for_date, canonical_chat_id
        cid_can = canonical_chat_id(chat_id)
        db_tm_rems = items_for_date(cid_can, tomorrow_d)
        if db_tm_rems:
            tomorrow_sched["reminders"] = db_tm_rems
    except Exception:
        pass

    # 2b. Active Checklists & Smart Lists
    lists_str = "None created yet."
    try:
        from db import get_smart_lists, canonical_chat_id
        cid_can = canonical_chat_id(chat_id)
        u_lists = get_smart_lists(cid_can)
        if u_lists:
            formatted_lists = []
            for l in u_lists:
                items_summary = ", ".join([it["title"] for it in l.get("items", [])[:8]])
                formatted_lists.append(f"• {l['name']} ({len(l.get('items', []))} items: {items_summary or 'empty'})")
            lists_str = "\n".join(formatted_lists)
    except Exception:
        lists_str = "None created yet."


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
            memory_block = _computed_ctx.get("formatted_block") or ""
        except Exception:
            memory_block = ""

    # Always inject top active user profile facts (Location, Bio, Key facts) so the bot never forgets user context!
    try:
        active_facts = db.get_user_facts(chat_id, limit=10)
        if active_facts:
            facts_lines = [f"- {f.get('fact')}" for f in active_facts if f.get("fact")]
            if facts_lines:
                facts_str = "User Profile & Important Facts:\n" + "\n".join(facts_lines)
                memory_block = f"{memory_block}\n{facts_str}".strip() if memory_block else facts_str
    except Exception:
        pass

    if not memory_block:
        memory_block = "User lives in Jodhpur, Rajasthan (Sardarpura area)."

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
        "Tasks & Reminders: create_reminder, list_reminders, delete_reminder, manage_smart_list, create_packing_list | "
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
    try:
        corrections_str = db.format_user_corrections(chat_id)
    except Exception:
        corrections_str = "None recorded yet."
    prompt = prompt.replace("{LEARNED_CORRECTIONS_BLOCK}", corrections_str)
    prompt = prompt.replace("{MEMORY_BLOCK}", memory_block)
    prompt = prompt.replace("{LISTS_BLOCK}", lists_str)
    prompt = prompt.replace("{SCHEDULE_BLOCK}", schedule_block)
    prompt = prompt.replace("{AVAILABLE_TOOLS}", available_tools_str)
    episodic_ctx_str = format_episodic_context(chat_id)
    prompt = prompt.replace("{EPISODIC_CONTEXT}", episodic_ctx_str)
    prompt = prompt.replace("{ACTIVE_CONTEXT}", active_ctx_str)

    return prompt

def invalidate_prompt_schedule_cache(chat_id=None):
    """Instantly busts prompt schedule cache so the bot's next turn sees newly created/deleted items immediately."""
    if hasattr(build_runtime_system_prompt, "_sched_cache"):
        if chat_id is None:
            build_runtime_system_prompt._sched_cache.clear()
        else:
            cid_str = str(chat_id)
            build_runtime_system_prompt._sched_cache = {
                k: v for k, v in build_runtime_system_prompt._sched_cache.items()
                if not k.startswith(cid_str)
            }

