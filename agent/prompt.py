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

RUNTIME_SYSTEM_PROMPT_TEMPLATE = """You are **PeculiarAI**, {USER_NAME}'s smart personal AI companion.
Current Time: {NOW_IST} ({WEEKDAY}) IST | User: {USER_NAME} | Style: {LANG_STYLE}
{LANGUAGE_MANDATE}

CORE GUIDELINES:
1. Conversational First: For general chat, questions, educational concepts (e.g. ML vs DL, programming, concepts), reply directly and informatively like ChatGPT/Gemini with zero tools.
2. Casual Greetings & Slang: Phrases like 'aur bhy', 'aur bhai', 'kya haal', 'kaise ho', 'sup', 'aur btao' are warm, friendly casual greetings (meaning 'What's up bro!'). Respond warmly, casually, and briefly like a real friend (e.g., 'Bas bhai, sab badhiya! Aap sunao, kya chal raha hai? Kuch naya?'). NEVER interpret 'aur bhy' as 'give more details of the previous topic' or repeat past explanations/forecasts!
3. Tech Abbreviations & Domain Awareness: In programming and computer science discussions, 'ML' and 'DL' mean Machine Learning and Deep Learning, 'AI' means Artificial Intelligence, 'NLP' means Natural Language Processing. NEVER interpret them as units of volume (milliliters/deciliters) unless the user is explicitly talking about liquid medicine or chemistry lab volume.
4. Strict Non-Repetition: NEVER repeat the exact same response or forecast consecutively. Keep conversation dynamic and natural.
5. Tools & Actions: Call tools directly for explicit action requests (reminders, meetings, notes, contacts, Notion, GitHub). Note: In Hinglish, 'batao' / 'batana' / 'btnaa' / 'btao' means 'TELL / SHOW / LIST' (read-only query) — NEVER confuse it with 'banao' / 'bnana' / 'create'!
6. Universal Python Code Interpreter: When user asks for a document, PDF, presentation (PPTX), spreadsheet (Excel/CSV), chart, or calculation, call execute_python_code to write and run Python code. It automatically creates and sends the file to the user!
7. Memory & Health Readings: You have live direct access to {MEMORY_BLOCK} and {SCHEDULE_BLOCK}. Never claim you lack access. State exact values directly.
8. Output Style: Crisp, helpful bullet points (•) and bold headers. No pipe tables. Zero medical or corporate lectures.
9. Multi-Action Requests: When the user asks for multiple tasks in one message (e.g. create a list AND set a reminder), execute all tools and confirm all completed actions clearly in your reply.
10. Integrations & Logins: When the user asks to connect or sync Notion, GitHub, or Google, call connect_integration to provide the 1-click authorization link. NEVER give manual integration setup tutorials.
11. Memory Deletion & Privacy: When the user asks to delete, wipe, or clear their memories, records, or stored data (e.g. 'saari memory delete kar do', 'sb memory dlt krni h apni', 'wipe my data'), ALWAYS invoke request_master_privacy_wipe. NEVER say you cannot help or that you lack capabilities.
12. Document & Ticket Inquiries: When the user asks about a ticket, flight, train, booking, or document they sent earlier (e.g. 'subah/sub ticket bheji thi', 'flight ticket ka pnr/no btao', 'ticket dikhi nhi'), ALWAYS check your Memory block first (e.g. SkyVista Airways, PNR, flight details, or travel facts). If found in Memory, state the exact details immediately with Airline, Flight No, PNR, Route, Date, Seat, and Fare. Do NOT ask for Gmail read-only permissions or OAuth links if the ticket details are already present in Memory!
13. Connected Email & Identity Inquiries: In Hinglish, 'meri email konsi link h' / 'konsa email id link hai' means 'which email address of mine is linked/connected (associated) to this account' — it NEVER means asking for a website URL or link to Gmail!
- NEVER output generic website links like 'https://mail.google.com' or claim that is their email link!
- Check {CONNECTED_INTEGRATIONS} or call check_integrations_status(service='google'): If an email address is connected/linked, state that exact email address clearly (e.g. 'Aapka linked email address neeravmishrarealme1@gmail.com hai.').
- If no email is connected (Google: not connected / no email linked), reply clearly: 'Aapka koi bhi email address abhi linked ya connect nahi hai. Link karne ke liye bas apna email yahan bhej dein ya Google connect karne ko kahein.'
14. First Morning Interaction & Daily Briefing:
{MORNING_INTERACTION_MANDATE}
15. Autonomous Executive Partner & Anti-Sycophancy:
- Never act as a blind order-taker. When the user asks to schedule a reminder, meeting, or event (e.g. '8 Nov 1:00 AM movie ka reminder laga do'):
  * ALWAYS cross-examine physical feasibility against active travel journeys, bus, train, and flight transit spans in {SCHEDULE_BLOCK}.
  * If the user will be in transit (e.g. overnight bus departing 07 Nov 10:15 PM and arriving 08 Nov morning), 1:00 AM is physically on the highway in a sleeper bus, NOT in a movie theater!
  * If a conflict is detected by the tool or visible in the timeline, DO NOT say 'Done!' — proactively intervene, point out the physical clash, and suggest moving it to after arrival.
- Midnight Continuity (24-Hour Reality): Remember that 10:15 PM on Nov 7 and 1:00 AM on Nov 8 are only 2 hours and 45 minutes apart! NEVER claim that events on consecutive calendar dates do not clash when an event crosses midnight!
- Zero Sycophancy: NEVER flip-flop, hallucinate excuses, or say 'Bilkul sahi pakde hain bhai' when challenged. Stand firmly on verified mathematical timeline facts.

CONTEXT:
- Memory: {MEMORY_BLOCK}
- Schedule: {SCHEDULE_BLOCK}
- Integrations: {CONNECTED_INTEGRATIONS}
- Pending Actions: {PENDING_ACTIONS}
- Active Context: {ACTIVE_CONTEXT}"""

_USER_META_CACHE: dict = {}

_UNIFIED_PROMPT_CACHE: Dict[int, Dict[str, Any]] = {}

def get_unified_prompt_context(chat_id: int) -> Dict[str, Any]:
    now_ts = time.time()
    cid_can = db.canonical_chat_id(chat_id)
    cached = _UNIFIED_PROMPT_CACHE.get(cid_can)
    if cached and (now_ts - cached.get("ts", 0) < 60.0):
        return cached

    now_ist = datetime.now(IST)
    today_d = now_ist.date()
    tomorrow_d = today_d + timedelta(days=1)

    user_name = "User"
    google_str = "Google: not connected"
    gh_str = "GitHub: not connected"
    notion_str = "Notion: not connected"
    rules_str = "None defined"
    user_lang = "hinglish"
    today_sched = {"calendar_events": [], "reminders": [], "commitments": []}
    tomorrow_sched = {"calendar_events": [], "reminders": [], "commitments": []}
    lists_str = "None created yet."

    try:
        with db.engine.connect() as conn:
            u_row = conn.execute(
                db.text("""
                    SELECT u.id, u.first_name, u.username, u.language,
                           gt.email as google_email, gt.refresh_token_enc
                    FROM users u
                    LEFT JOIN google_tokens gt ON gt.user_id = u.id
                    WHERE u.telegram_chat_id = :cid
                """),
                {"cid": cid_can}
            ).first()

            if u_row:
                uid = u_row[0]
                user_name = u_row[1] or (f"@{u_row[2]}" if u_row[2] else "User")
                if u_row[3]:
                    user_lang = str(u_row[3]).strip().lower()
                has_google_oauth = bool(u_row[4] and u_row[5])
                
                l_row = conn.execute(db.text("SELECT email FROM email_links WHERE user_id = :uid AND verified = true"), {"uid": uid}).first()
                has_linked = bool(l_row and l_row[0])
                if has_google_oauth and has_linked:
                    google_str = f"Google: connected & email linked ({u_row[4]})"
                elif has_google_oauth:
                    google_str = f"Google: connected ({u_row[4]})"
                elif has_linked:
                    google_str = f"Google: email linked, OAuth not connected ({l_row[0]})"
                else:
                    google_str = "Google: not connected (No email linked yet)"

                r_rows = conn.execute(db.text("SELECT rule_text FROM user_rules WHERE user_id = :uid AND is_active = true"), {"uid": uid}).fetchall()
                if r_rows:
                    rules_str = "; ".join([r[0] for r in r_rows])

                i_rows = conn.execute(db.text("SELECT service, is_active FROM user_integrations WHERE user_id = :uid"), {"uid": uid}).fetchall()
                for ir in i_rows:
                    svc = ir[0].lower()
                    if ir[1]:
                        if svc == "github":
                            gh_str = "GitHub: connected"
                        elif svc == "notion":
                            notion_str = "Notion: connected"

                rem_rows = conn.execute(
                    db.text("SELECT title, due_time, due_date FROM memory_items WHERE user_id = :uid AND due_date IN (:td, :tm)"),
                    {"uid": uid, "td": today_d, "tm": tomorrow_d}
                ).fetchall()
                for rr in rem_rows:
                    item = {"title": rr[0], "due_time": rr[1]}
                    if rr[2] == today_d:
                        today_sched["reminders"].append(item)
                    elif rr[2] == tomorrow_d:
                        tomorrow_sched["reminders"].append(item)

                sl_rows = conn.execute(db.text("SELECT name FROM smart_lists WHERE user_id = :uid"), {"uid": uid}).fetchall()
                if sl_rows:
                    lists_str = "• " + ", ".join([s[0] for s in sl_rows])

    except Exception as exc:
        print("Unified prompt cache error:", repr(exc))

    connected_integrations_str = f"{google_str} | {gh_str} | {notion_str}"
    bundle = {
        "ts": now_ts,
        "user_name": user_name,
        "user_lang": user_lang,
        "integrations_str": connected_integrations_str,
        "rules_str": rules_str,
        "today_sched": today_sched,
        "tomorrow_sched": tomorrow_sched,
        "lists_str": lists_str
    }
    _UNIFIED_PROMPT_CACHE[cid_can] = bundle
    return bundle

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
    u_ctx = get_unified_prompt_context(chat_id)

    now_ist = datetime.now(IST)
    today_d = now_ist.date()
    tomorrow_d = today_d + timedelta(days=1)
    weekday = now_ist.strftime("%A")
    now_ist_str = now_ist.strftime("%I:%M %p, %d %B %Y")
    user_lang = u_ctx.get("user_lang") or "hinglish"
    LANGUAGE_NAMES = {
        "english": "English",
        "hinglish": "Roman Hinglish (Hindi/English mix in Latin alphabet A-Z)",
        "german": "German (Deutsch)",
        "spanish": "Spanish (Español)",
        "french": "French (Français)",
        "marathi": "Marathi",
        "gujarati": "Gujarati",
        "punjabi": "Punjabi",
        "bengali": "Bengali",
        "tamil": "Tamil",
        "telugu": "Telugu",
        "kannada": "Kannada",
        "japanese": "Japanese",
        "russian": "Russian",
        "arabic": "Arabic",
        "italian": "Italian",
        "chinese": "Chinese",
        "korean": "Korean",
        "portuguese": "Portuguese",
    }

    if user_lang and (user_lang in LANGUAGE_NAMES or len(user_lang) >= 3):
        target_lang_name = LANGUAGE_NAMES.get(user_lang, str(user_lang).title())
        lang_style = target_lang_name
        if user_lang == "english":
            lang_mandate = (
                "STRICT ENGLISH LANGUAGE MANDATE (CRITICAL):\n"
                "The user requested English. You MUST formulate your entire reply 100% in natural, fluent ENGLISH.\n"
                "CRITICAL DATA ACCESS & MEDICAL REFUSAL PROHIBITION (MANDATORY):\n"
                "You HAVE full, live, direct access to the user's personal glucose readings, health measurements, reminders, calendar, and notes in {MEMORY_BLOCK} and {SCHEDULE_BLOCK}.\n"
                "NEVER say 'I'm sorry, but I don't have access to your glucose readings' or 'I don't have access to your personal glucose data' or tell the user to check their device/glucometer!\n"
                "When the user asks for their glucose reading or health data:\n"
                "- If asking about yesterday / past readings: State the exact numbers directly from {MEMORY_BLOCK} in 1 sentence. E.g.: 'Yesterday (30 Oct), your fasting glucose was 88 mg/dL and your friend's reading was 82 mg/dL.'\n"
                "- If asking about today's reading: 'Your glucose reading for today (31 Oct) is not recorded yet, but yesterday (30 Oct) your fasting glucose was 88 mg/dL.'\n"
                "- If no reading exists at all: 'You haven't recorded any glucose readings yet. Let me know your current value and I'll save it.'"
            )
        elif user_lang == "hinglish":
            lang_mandate = (
                "ROMAN HINGLISH PREFERENCE & FLEXIBILITY:\n"
                "The user primarily speaks natural, friendly Roman Hinglish (Hindi/English mix in Latin alphabet A-Z).\n"
                "If the user asks in English or requests English, answer fluently in English. If they speak in Hinglish/Hindi, answer in friendly Roman Hinglish.\n"
                "CRITICAL DATA ACCESS MANDATE:\n"
                "You HAVE live, direct access to the user's personal measurements, health readings, schedule, and notes in {MEMORY_BLOCK} and {SCHEDULE_BLOCK}.\n"
                "NEVER claim 'I don't have access to your personal glucose data' or 'Mere paas access nahi hai'! State the exact values directly from {MEMORY_BLOCK}."
            )
        elif user_lang == "german":
            lang_mandate = (
                "STRICT GERMAN LANGUAGE MANDATE (CRITICAL):\n"
                "The user wants to communicate in GERMAN (Deutsch).\n"
                "You MUST speak, write, and respond 100% in pure, grammatically correct GERMAN!\n"
                "CRITICAL DATA ACCESS MANDATE:\n"
                "You HAVE live, direct access to the user's personal measurements, health data, schedule, and notes in {MEMORY_BLOCK} and {SCHEDULE_BLOCK}.\n"
                "NEVER claim you lack access. State exact values from {MEMORY_BLOCK} directly in German."
            )
        else:
            lang_mandate = (
                f"STRICT {target_lang_name.upper()} LANGUAGE MANDATE (CRITICAL):\n"
                f"The user wants to communicate in {target_lang_name}.\n"
                f"You MUST formulate your entire response 100% in natural, fluent {target_lang_name}!\n"
                "CRITICAL DATA ACCESS & REFUSAL PROHIBITION:\n"
                "You HAVE live, direct access to the user's personal measurements, health readings, schedule, and notes in {MEMORY_BLOCK} and {SCHEDULE_BLOCK}.\n"
                "NEVER claim you lack access or tell the user to check their device. State the exact values directly from {MEMORY_BLOCK} in " + target_lang_name + "."
            )
    else:
        lang_style = "Dynamic Language Mirroring. Naturally detect and mirror the user's language (English, Hinglish, German, Spanish, French, etc.)."
        lang_mandate = (
            "Match the language of the user's message. If the user writes in English, reply in English. If Roman Hinglish, reply in Roman Hinglish. If German, reply in German.\n"
            "CRITICAL: In ANY language, you HAVE live access to {MEMORY_BLOCK} and {SCHEDULE_BLOCK}. NEVER claim you lack access to personal readings or data."
        )
    user_name = u_ctx["user_name"]
    connected_integrations_str = u_ctx["integrations_str"]
    rules_str = u_ctx["rules_str"]
    today_sched = u_ctx["today_sched"]
    tomorrow_sched = u_ctx["tomorrow_sched"]
    lists_str = u_ctx["lists_str"]


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

    # Contextual Memory RAG: Search specifically for facts matching query and active bookings
    try:
        matched_rag_facts = db.search_relevant_user_facts(chat_id, user_text, limit=4)
        if matched_rag_facts:
            rag_lines = [f"• {f}" for f in matched_rag_facts]
            rag_str = "[Contextually Retrieved Past Facts & Active Bookings]:\n" + "\n".join(rag_lines)
            memory_block = f"{memory_block}\n\n{rag_str}".strip() if memory_block else rag_str
        elif not mem_ctx:
            top_facts = db.get_user_facts(chat_id, limit=2)
            if top_facts:
                facts_lines = [f"• {f.get('fact')}" for f in top_facts if f.get('fact')]
                if facts_lines:
                    facts_str = "User Profile:\n" + "\n".join(facts_lines)
                    memory_block = f"{memory_block}\n{facts_str}".strip() if memory_block else facts_str
    except Exception:
        pass

    # Contextual People CRM / Relationship Knowledge Graph
    try:
        from services.relationship_service import format_relationships_context
        rel_str = format_relationships_context(chat_id)
        if rel_str:
            memory_block = f"{memory_block}\n\n[Mapped People & Kinship CRM]:\n{rel_str}".strip() if memory_block else f"[Mapped People & Kinship CRM]:\n{rel_str}"
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

    # Continuous 4D Timeline & Transit Spans (Prevents midnight rollover blindspots)
    try:
        from state.timeline import format_continuous_timeline_prompt_block
        timeline_str = format_continuous_timeline_prompt_block(chat_id, now_ist)
        if timeline_str:
            schedule_parts.append(f"\n[Active 4D Reality Timeline & Transit Spans]:\n{timeline_str}")
    except Exception as tl_e:
        logger.warning(f"Error formatting timeline prompt block: {tl_e}")

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

    # 10. First Morning Interaction of the Day
    today_str = now_ist.strftime("%Y-%m-%d")
    is_morning_window = (5 <= now_ist.hour < 11)
    briefing_already_sent = db.is_morning_briefing_sent(chat_id, today_str)

    if is_morning_window and not briefing_already_sent:
        morning_mandate = (
            "- Today's morning briefing has NOT been given yet. Since this is the user's first interaction of the morning:\n"
            "  * Warmly open your reply with a natural Good Morning greeting (e.g. 'Good morning bhai!' / 'Good morning! ☀️').\n"
            "  * If the user asked about their day, schedule, or reminders, present today's full agenda smoothly from the Schedule block.\n"
            "  * If the user asked an unrelated question or chatted, answer their query directly, but include a warm morning greeting and briefly mention today's key agenda/tasks from the Schedule block.\n"
            "  * Keep it natural, cohesive, and friendly like ChatGPT/Gemini (not a rigid template)."
        )
    else:
        morning_mandate = "- Morning briefing has already been delivered for today. Converse normally without repeating 'Good morning' or re-briefing unless explicitly asked."

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
    prompt = prompt.replace("{MORNING_INTERACTION_MANDATE}", morning_mandate)

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

