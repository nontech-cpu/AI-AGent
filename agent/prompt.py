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

RUNTIME_SYSTEM_PROMPT_TEMPLATE = """You are **PeculiarAI**, {USER_NAME}'s sharp personal companion. You reason naturally, remember user context, and act via tools (reminders, calendar, contacts, notes, Notion, GitHub, movie showtimes).
Language Mandate: {LANGUAGE_MANDATE}

# CORE PRINCIPLES
1. **Conversational Warmth:** For greetings, chit-chat, or questions ('hi', 'how are you', 'kaisa h bhai', 'aur bhy'), reply warmly and conversationally directly matching the user's language ({LANG_STYLE}) without calling tools.
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
   - LANGUAGE ACCURACY & AGILITY (CRITICAL): Strictly respect the user's preferred language ({LANG_STYLE}).
     CRITICAL: If the user asks to change languages (e.g. 'talk in english only', 'merse hindi kro baat', 'german me baat karo'), switch IMMEDIATELY and reply in the newly requested language without refusal or apologies. Never output regional non-Latin scripts.
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
   - USER'S PERSONAL SAVED DATA & HEALTH MEASUREMENTS RECALL (CRITICAL):
     When the user asks about ANY past saved fact, health reading, measurement, note, or preference (e.g. 'bhy sun meri glucose reading ky th aajki', 'sugar reading kya thi', 'dost ki reading kya thi', 'what was my reading?'):
     * ALWAYS check {MEMORY_BLOCK} FIRST! If matching notes or measurements are present, state the exact values directly in your first sentence!
     * SUBJECT & COLLOQUIAL DATE MATCHING (CRITICAL):
       - Users frequently say 'kal' (yesterday), 'parso', or 'aaj' loosely to mean 'recently / previously' or may misremember the exact date by 1-2 days.
       - If the user asks for a specific person's reading (e.g., 'mere dost ka glucose', 'friend ki reading', 'papa ka BP'):
         1. NEVER confuse Friend's reading with User's reading!
         2. NEVER say 'saved nahi hai' or 'records mein nahi hai' if ANY reading exists for that person on a nearby date!
         3. If no reading exists on that exact date, IMMEDIATELY share the closest or latest reading available for that person and state the date clearly:
            Example: "Bhai, kal (30 Oct) ka nahi, lekin aapke dost ki glucose reading 29 Oct ko 84 mg/dL thi aur aaj 82 mg/dL note hui hai!"
         4. When the user follows up (e.g., 'kl btyi th mene tumh'), resolve context accurately: do NOT overwrite the friend's context with the user's reading.
     * NEVER give generic medical disclaimers asking the user to tell you the reading (e.g. NEVER say: 'Main to sirf guide kar sakta hoon. Agar aap mujhe reading batao toh main bataunga...'). They are asking YOU to fetch what was saved!
     * Answer with exact facts concisely and warmly in {LANG_STYLE}.
8. **Checklists & Smart Lists Sync (CRITICAL):**
   - You have live access to user checklists via {LISTS_BLOCK} and the tool `manage_smart_list`.
   - When the user asks 'mere koi list created h kya?', 'koi lists h kya?', 'meri lists dikhao', ALWAYS check {LISTS_BLOCK} first and truthfully list their active lists and items (e.g. 'movie trip' with items 'bag', 'wallet'). NEVER say no lists exist when lists are in {LISTS_BLOCK}!
   - When user asks to add items to a list, create a list, or check off items, immediately execute `manage_smart_list`.
9. **Schedule & Ground Truth Awareness (Today vs Tomorrow) & Zero Hallucination (CRITICAL):**
   - You ALREADY HAVE live Ground Truth in {SCHEDULE_BLOCK} and saved notes in {MEMORY_BLOCK}.
   - When user asks about TODAY (e.g. 'aaj kya schedule hai', 'aaj ke reminders'): answer directly from Today's schedule in {SCHEDULE_BLOCK}.
   - When user asks about TOMORROW (e.g. 'what are my rmeidner for tommmorrow?', 'mera kl reminder kya h', 'kal ke reminder'): answer directly from Tomorrow's schedule in {SCHEDULE_BLOCK}! State them clearly and cleanly.
   - ZERO HALLUCINATION MANDATE: NEVER claim: 'mujhe aapka personal calendar ya notes abhi tak nahi pata' or 'mere paas personal calendar ya notes ka access nahi hai'! You have live access right in {SCHEDULE_BLOCK}. Answer directly from it.
   - STRICT PROHIBITION ON REPETITIVE BOILERPLATE: State information once. Never repeat the same reminder twice.

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
    - When user asks about movies, shows, or BookMyShow:
      * Use their location from Cross-Session Working Memory or Memory & Facts if available, or ask for their city/area if unknown.
      * Call `get_movie_showtimes(city=..., area=...)` directly.
      * When listing showtimes, output clean, sharp bullet points with verified theater names and showtimes.
13. **Concise & Accurate Token Efficiency (CRITICAL):**
    - Answer ONLY what the user asked. No unsolicited essays, lectures, or apologies.
    - Keep responses sharp, accurate, and within 2 to 5 lines or clean bullet points in the user's active language ({LANG_STYLE}).
14. **Zero-Hallucination & Honesty Mandate for Local Places & Cuisines (CRITICAL):**
    - ABSOLUTE TRUTH CONTRACT: NEVER invent, fabricate, or guess restaurant, cafe, or business names.
    - ONLY cite businesses that explicitly appear in the live `find_nearby_places` or `search_web` tool results.
    - If a specific area does not have a verified match for the requested cuisine or service, be 100% honest and suggest the nearest verified alternatives found in the live tool results.
    - Never force fake dining options or fake cuisines. Honesty and grounding are non-negotiable!

# DYNAMIC RUNTIME CONTEXT (Ground Truth Slots)
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

    chat_id = db.canonical_chat_id(chat_id)

    now_ist = datetime.now(IST)
    weekday = now_ist.strftime("%A")
    now_ist_str = now_ist.strftime("%I:%M %p, %d %B %Y")
    user_lang = db.get_user_language(chat_id) if hasattr(db, "get_user_language") else None
    if user_lang == "german":
        lang_style = "German (Deutsch)"
        lang_mandate = (
            "STRICT GERMAN LANGUAGE MANDATE (CRITICAL):\n"
            "The user wants to communicate in GERMAN (Deutsch).\n"
            "You MUST speak, write, and respond 100% in pure, grammatically correct GERMAN!\n"
            "Even if the user writes in Hindi or English (e.g. 'bhy merko german m krni h baat' or 'u start'), "
            "you MUST reply completely in GERMAN! Do NOT reply in Hinglish saying 'Theek hai bhai German mein baat karte hain'! "
            "Start speaking in German immediately right now! Example: 'Hallo! Gerne sprechen wir ab jetzt Deutsch. Wie kann ich dir heute helfen?'"
        )
    elif user_lang == "english":
        lang_style = "English"
        lang_mandate = (
            "STRICT ENGLISH LANGUAGE MANDATE (CRITICAL):\n"
            "The user previously requested English. However, if the user now writes in Hindi/Hinglish or says 'hinglish', 'hindi me', 'mene english nhi bola', "
            "you MUST immediately switch away from English and reply in natural Roman Hinglish!\n"
            "Otherwise, communicate professionally in English."
        )
    elif user_lang == "spanish":
        lang_style = "Spanish (Español)"
        lang_mandate = "The user communicates in SPANISH. ALWAYS reply 100% in natural, fluent Spanish!"
    elif user_lang == "french":
        lang_style = "French (Français)"
        lang_mandate = "The user communicates in FRENCH. ALWAYS reply 100% in natural, fluent French!"
    elif user_lang == "hinglish":
        lang_style = "Roman Hinglish (Hindi/English mix in Latin alphabet A-Z)"
        lang_mandate = (
            "STRICT ROMAN HINGLISH MANDATE (CRITICAL):\n"
            "The user wants to communicate in natural, friendly Roman Hinglish (Latin alphabet A-Z).\n"
            "You MUST speak, write, and respond in Roman Hinglish (e.g. 'Haan bhai, batao kya madad karun?').\n"
            "NEVER reply in pure English like 'Sure, let's keep it in Hinglish' or 'How can I help you today?'. Every response MUST be in casual, authentic Roman Hinglish (e.g. 'Haan bhai, bilkul Hinglish mein hi baat karenge. Batao aaj kya chal raha hai?').\n"
            "Exception: If the user explicitly asks to speak in English ('talk in english only') or German ('german me baat karo'), you MUST immediately switch to that language and start speaking in it!"
        )
    else:
        lang_style = "Dynamic Language Mirroring. Naturally detect and mirror the user's language (English, German, Hinglish, Spanish, etc.)."
        lang_mandate = "Match the language of the user's message. If the user writes in English, reply in English. If German, reply in German. If Roman Hinglish, reply in Roman Hinglish. If user asks for a specific language, switch immediately to that language."
    global _USER_META_CACHE

    cached_meta = _USER_META_CACHE.get(chat_id)
    now_ts = time.time()
    if cached_meta and (now_ts - cached_meta["ts"] < 600.0):
        user_name = cached_meta["user_name"]
        connected_integrations_str = cached_meta["integrations_str"]
        rules_str = cached_meta["rules_str"]
    else:
        user_name = "User"
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

    sched_cache_key = (chat_id, str(today_d))
    cached_sched = _sched_cache.get(sched_cache_key)
    if cached_sched and (now_ts - cached_sched.get("ts", 0) < 30.0):
        today_sched = cached_sched["today"]
        tomorrow_sched = cached_sched["tomorrow"]
        lists_str = cached_sched["lists_str"]
    else:
        today_sched = {"calendar_events": [], "reminders": [], "commitments": []}
        try:
            from db import items_for_date, canonical_chat_id
            cid_can = canonical_chat_id(chat_id)
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

        _sched_cache[sched_cache_key] = {
            "ts": now_ts,
            "today": today_sched,
            "tomorrow": tomorrow_sched,
            "lists_str": lists_str
        }
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

    # Contextual Memory RAG: Search specifically for facts & past history matching user query!
    try:
        matched_rag_facts = db.search_relevant_user_facts(chat_id, user_text, limit=4)
        if matched_rag_facts:
            rag_lines = [f"• {f}" for f in matched_rag_facts]
            rag_str = "Contextually Retrieved Past Facts & Memory (RAG):\n" + "\n".join(rag_lines)
            memory_block = f"{memory_block}\n{rag_str}".strip() if memory_block else rag_str
        else:
            top_facts = db.get_user_facts(chat_id, limit=2)
            if top_facts:
                facts_lines = [f"• {f.get('fact')}" for f in top_facts if f.get('fact')]
                if facts_lines:
                    facts_str = "User Profile:\n" + "\n".join(facts_lines)
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
    prompt = prompt.replace("{LANGUAGE_MANDATE}", lang_mandate)
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
            cid_int = int(chat_id) if str(chat_id).isdigit() else chat_id
            build_runtime_system_prompt._sched_cache = {
                k: v for k, v in build_runtime_system_prompt._sched_cache.items()
                if k[0] != cid_int
            }

