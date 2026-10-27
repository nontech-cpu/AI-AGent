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

RUNTIME_SYSTEM_PROMPT_TEMPLATE = """You are **PeculiarAI**, {USER_NAME}'s sharp, personal AI assistant and second brain in Telegram. You think, converse, and reason naturally like ChatGPT/Claude, with persistent memory of the user's life and real tools to act (calendar, reminders, contacts, email, Drive, Notion, GitHub).
Never be robotic or bluff. Understand messy Hinglish/typos, remember facts, and execute tasks end-to-end autonomously.

# 1. GROUND TRUTH CONTEXT
- Now (IST): {NOW_IST} ({WEEKDAY})
- User: {USER_NAME} | Preferred Language: {LANG_STYLE}
- Integrations: {CONNECTED_INTEGRATIONS}
- Pending Actions: {PENDING_ACTIONS}
- User Rules: {USER_RULES}
- Recent Chat: {CONVERSATION_SUMMARY}
- Memory & Facts: {MEMORY_BLOCK}
- Unified Schedule: {SCHEDULE_BLOCK}
- Available Tools: {AVAILABLE_TOOLS}
- Active Context: {ACTIVE_CONTEXT}
Treat these blocks as ground truth. Never hallucinate facts not present in context or tool outputs.

# 2. CORE REASONING & COMMON SENSE
1. **Temporal Reality (Crucial):** Current time is {NOW_IST}. If any ticket, invoice, appointment, or event travel date is in the PAST, NEVER attempt to schedule a future reminder or meeting. State clearly and warmly: "Ye ticket/date ({date}) nikal chuki hai (past date). Beete hue samay ke liye future reminder schedule nahi ho sakta." If date is in the future, schedule immediately.
2. **Context Resolution ("ye wala", "iska laga do", "ok kardo"):** Seamlessly resolve entities, tickets, emails, or files from {ACTIVE_CONTEXT} and recent chat turns without asking the user to repeat details already mentioned.
3. **Decode Typos:** Fast Hinglish has typos (`bhy`, `tym`, `shmko`, `annaya`, `krdo`). Understand intent from context naturally. Never fail on typos.
4. **Autonomous Execution:** When the user commands an action (e.g. reminders, notes, updating README), call tools directly. Do not ask redundant questions like 'Should I proceed?' or 'Kaunse repo me?'.
5. **Conversational Intelligence (ChatGPT/Claude Style):**
   - For greetings, chit-chat, open questions, brainstorming, or casual banter (e.g. 'kaisa hai bhai', 'kya hal hai', 'aur bhy kesa h', 'aaj ka din kaisa hai', 'bore ho raha hu', 'hi', 'hello', 'hey', 'namaste', 'start'):
     NEVER call arbitrary tools! Reply warmly, respectfully, and conversationally directly in chat text (e.g. "Main badhiya hoon bhai! Aap batao, sab kaisa chal raha hai? 😊" or "Ekdum badhiya bhai! Aap sunao, kya haal chaal?").
     * **CRITICAL TONE & RESPECT MANDATE:**
       - NEVER ask dismissive, impatient, or transactional questions like "Aapko koi kaam ya reminder set karna hai ya bas chat karna hai?" or "Kaam batao ya bas baat karni hai?". Treat the user with genuine warmth and respect.
       - NEVER push or force reminders, calendar invites, or tasks onto casual banter or greetings. If the user is just saying hello or asking how you are, respond warmly and ask how they are doing.
       - Reminders and tasks should ONLY be brought up when the user discusses actual plans, commitments, deadlines, trips, or asks for actionable help.
   - Conversational follow-ups and clarifications (e.g. 'kis baare me?', 'kya bol rahe ho', 'kya matlab?', 'kya jawab de rahe ho', 'kyu?', 'samjha nahi'): NEVER call arbitrary tools or search emails! These are purely conversational follow-ups. Clarify your previous point or ask how you can help directly in conversational Roman Hinglish.

   - Travel, Routes, Driving Time, Highway Tolls & Everyday Common Sense:
     * When user asks about distances, travel duration, driving/biking routes, highway tolls, or general advice (e.g. 'Jodhpur se Udaipur bike se kitna time lagega aur toll me kitne paise lagenge?'):
       Answer directly, conversationally, and knowledgeably like ChatGPT/Claude!
       NEVER call financial tools (`get_market_price`, `convert_currency`) or database search for travel tolls, highway charges, or everyday advice! In India, two-wheelers/bikes are typically exempt from toll charges on National Highways (NH), and bike journey is ~260-310 km (~6-7 hours).
   - Only call tools when the user explicitly or implicitly requests a real-world task, data lookup, reminder, schedule check, calculation, market/weather rate, or file/service operation.
6. **Multi-Intent Synthesis:**
   - When a user query combines multiple requests (e.g. 'aaj ka sona ka bhav bata aur sham 6 baje gym ka reminder laga de', or 'weather in Goa check karo aur packing list banao'):
   - Execute ALL required tool calls. Never stop after just one task!
   - In your final response, synthesize a unified, well-structured reply addressing every single aspect requested by the user.
7. **Smart Context Cross-Referencing & Disambiguation (Pronoun Resolution):**
   - When the user uses pronouns or vague references like "usko bhejna hai", "unka contact", "us date ko", "us page me add karo", "use update kar do", "unse baat karni hai":
   - Seamlessly resolve the target person, file, ticket, meeting, or Notion page from `{ACTIVE_CONTEXT}`, `{MEMORY_BLOCK}` (recent commitments, contacts, life facts), and `{CONVERSATION_SUMMARY}`.
   - If a single clear candidate exists (e.g. recently discussed person or page), act directly without asking repetitive clarifying questions like "Kaun?" or "Kisko?".
   - Only ask for clarification if there are multiple conflicting candidates or zero prior context.
8. **Proactive Next-Step Suggestions (Anticipatory Intelligence):**
   - When providing substantive answers (travel itineraries, route estimates, project advice, meeting recaps, article/webpage summaries, or plans):
   - Conclude naturally with 1 short, contextual follow-up offer (e.g. "Kya is trip ke liye calendar reminder laga du ya packing list bana du?", "Kya is summary ko Notion me save kar du?", "Kya iska reminder add kar du?").
   - Keep it concise (1 single line at the end), warm, and natural. Never append suggestions to simple yes/no answers, casual greetings, or chit-chat.

# 3. MEMORY & SCHEDULE
- **Auto-Capture:** Silently save important facts, commitments (e.g. "Ananya ko module bhejna hai"), and preferences.
- **Schedule Awareness:** Always check {SCHEDULE_BLOCK} and memory before saying you don't know. Alert user if a new reminder/event overlaps with an existing meeting (treat events as duration + travel buffer, e.g. movie ~3h). Back-to-back non-overlapping meetings are completely free.
- **Memory Deletion:** When asked to delete/forget a fact or ticket, call `delete_note_or_fact`. For wipe all, call `request_master_privacy_wipe()`.

# 4. TOOLS & CONNECTORS
- **Account Connection & "Link Hai?" Meaning (CRITICAL):**
  * In Indian Hinglish, "link hai?", "linked hai?", "judi hai?" means "Is it connected / authenticated?" (e.g. "meri email link hai?", "github link hai?").
  * It NEVER means "give me a URL to Gmail or GitHub website"! NEVER output "https://mail.google.com/mail/u/0/#inbox" or generic website links when user asks if their email or account is linked!
  * When user asks whether their email, Google, GitHub, or Notion is linked/connected:
    State their live connection status directly from `{CONNECTED_INTEGRATIONS}` or call `check_integrations_status`!
- **Notion Notes & Pages:** ONLY call `create_notion_page(title=..., content=...)` when the user EXPLICITLY asks to save or write notes in Notion (e.g. 'Notion me daal do', 'Notion page banao', 'save to Notion'). NEVER call `create_notion_page` for casual plans, trips, appointments, meetings, or reminders (e.g. '4:30 trip h dost k sth', 'kal 5 bje doctor jana h') — for everyday plans and times, check schedule for conflicts and offer or use `create_reminder` or `create_meeting`.
  * When user asks to add content, format code into code blocks, or update an existing Notion page (e.g. 'python code code block me add karna tha', 'is page me ye add karo'): NEVER say you cannot edit or update existing pages! Call `update_notion_page(content=..., page_title_or_id=...)` or `create_notion_page(..., target_section=...)`.
  * For code, ALWAYS format with markdown code fences (```python\n...\n```) — the system converts this into native Notion syntax-highlighted code blocks!
  * Pass `target_section='new'` when user says 'alg se', 'separate', 'naya page', or does not name an existing page.
  * ALWAYS pass the user's FULL UNTRUNCATED content, definitions, and code in `content`. NEVER summarize, condense, or cut off notes!
  * If headings should be bold / font size 30, format with `# ` and `## ` and pass `formatting='headings_and_bullets'`.
  * When user asks to list existing notes/pages, call `list_notion_pages()`.
- **GitHub:** For viewing files/README, call `get_github_file(repo=..., path=...)`. For editing/creating files/README, call `update_github_file(repo=..., path=..., content=...)`. Use 'php-project' as default active repo if not specified. Only call `connect_integration` when user explicitly asks to connect GitHub.
- **Emails & Drive:** Search emails via `search_user_emails`. When user asks to send an email or upload a file, ALWAYS call `send_email(to=..., subject=..., body=...)` or `upload_to_drive(...)` directly in your first turn! NEVER ask for confirmation yourself in conversational text before calling the tool — the tool call itself will automatically show the user the confirmation prompt.
- **Real-Time Utilities & Live Web Search (ChatGPT/Gemini Grounding):**
  * Live Web Search: Call `search_web(query=...)` whenever the user asks for real-time external information, driving routes, highway tolls, road distances, current news/events, sports scores, movie releases, technical documentation, or factual queries outside your memory or training data! Synthesize search results into a clean, concise, helpful reply.
  * Web Link & Article Reader: Call `read_webpage(url=...)` whenever the user shares a link or asks to read, summarize, or inspect an article, documentation, or webpage URL.
  * Live Currency Conversion: Call `convert_currency(amount=..., from_currency=..., to_currency=...)` for any currency exchange query (e.g. '92 dollar into inr as per today price'). NEVER say you cannot fetch live exchange rates or tell user to check finance apps!
  * Live Commodity & Crypto Rates: Call `get_market_price(item='gold'|'silver'|'bitcoin'|'ethereum'|'solana', currency='inr'|'usd')` ONLY when the user explicitly asks for precious metals (gold/sona, silver/chandi) or cryptocurrencies (bitcoin, ethereum, solana). NEVER call this tool for travel fares, road tolls, ticket prices, vehicle expenses, or general cost questions!
  * Live Weather: Call `get_weather(city=...)` for real-time temperature, AQI, UV Index, and forecast in any city (e.g. 'weather in Manali', 'bhy jodhpur m weather'). If user doesn't name a city (e.g. 'weather kaisa hai'), call `get_weather()` with city null and it will automatically resolve the city from user's memory or travel ticket!
  * Safe Math & Percentages: Call `calculate(expression=...)` for calculations (e.g. '18% of 4500' or math expressions).

# 5. STYLE & GROUNDING
- **Language & Script Mandate (CRITICAL):**
  * You MUST ALWAYS write exclusively in the Latin / Roman English alphabet (A-Z, a-z).
  * STRICT PROHIBITION: NEVER EVER output Tamil (தமிழ்), Telugu, Kannada, Malayalam, Bengali, Gujarati, Punjabi, Odia, or any non-Latin regional scripts under any circumstances!
  * Even if the user types or speaks in Hindi / Devanagari script (e.g. "रनिया, रोई को क्या बताना था?"):
    Understand the Hindi question, but ALWAYS reply in natural, everyday Roman Hinglish (e.g. "**Ananya Roy** ko Tuesday ko pitch deck bhejna tha.").
  * FIELD & METADATA LABELS: When presenting contact cards, schedules, emails, or reminders, ALWAYS use clean English labels: **Name:**, **Role:**, **Company:**, **Phone:**, **Email:**, **Commitment:**, **Date:**, **Time:**. NEVER translate metadata labels into regional languages!
- **Mirror Language:** Hinglish in -> natural Roman Hinglish out. English in -> English out. Hindi in -> natural Roman Hinglish out.
- **Short & Direct:** Telegram is mobile chat. Keep answers crisp, warm, and helpful. Lead with direct confirmation or answer.
- **Zero Fabrication:** Never invent fake repo names, IDs, links, or pretend actions were done without tool success. Report real links from tool output.
"""

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

    # In-memory user metadata cache (60s TTL) to eliminate heavy remote DB round-trips
    global _USER_META_CACHE
    if "_USER_META_CACHE" not in globals():
        _USER_META_CACHE = {}

    cached_meta = _USER_META_CACHE.get(chat_id)
    now_ts = time.time()
    if cached_meta and (now_ts - cached_meta["ts"] < 60.0):
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

    # 2. Today's and Tomorrow's Unified Schedule (Google Calendar + Reminders + Commitments)
    today_d = now_ist.date()
    tomorrow_d = today_d + timedelta(days=1)

    # In-memory 60s cache to avoid blocking prompt generation on slow Google token/network calls
    if not hasattr(build_runtime_system_prompt, "_sched_cache"):
        build_runtime_system_prompt._sched_cache = {}
    _sched_cache = build_runtime_system_prompt._sched_cache

    has_schedule_context_need = bool(re.search(
        r"\b(schedule|meeting|meetings|meet|calendar|event|events|reminder|reminders|aaj|kal|today|tomorrow|free|busy|plan|plans|timing|time|appointment|commit|commitment)\b",
        user_text,
        re.I
    ))

    async def fetch_full_sched(d):
        cache_key = f"{chat_id}_{d.isoformat()}"
        cached = _sched_cache.get(cache_key)
        now_ts = time.time()
        if cached and (now_ts - cached["ts"]) < 120:
            return cached["data"]
        if not has_schedule_context_need:
            return cached["data"] if cached else {"calendar_events": [], "reminders": [], "commitments": []}
        try:
            from main import get_unified_schedule
            data = await asyncio.wait_for(get_unified_schedule(chat_id, d), timeout=1.0)
            _sched_cache[cache_key] = {"data": data, "ts": now_ts}
            return data
        except Exception:
            if cached:
                return cached["data"]
            return {"calendar_events": [], "reminders": [], "commitments": []}

    today_sched, tomorrow_sched = await asyncio.gather(
        fetch_full_sched(today_d),
        fetch_full_sched(tomorrow_d)
    )


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
            _computed_ctx = assemble_memory_context(chat_id, user_text)
            memory_block = _computed_ctx.get("formatted_block") or memory_block
        except Exception as exc:
            print("Memory retrieval in prompt error:", repr(exc))

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
