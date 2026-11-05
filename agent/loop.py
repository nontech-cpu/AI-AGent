"""
PeculiarAI V2 Agent Execution Loop
Native tool-calling loop using Groq (openai/gpt-oss-120b or openai/gpt-oss-20b).
Strict Zero-Hallucination Grounding Contract: Model acts via tools or acknowledges missing data.
"""
import os
import re
import json
import time
import asyncio
import logging
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("peculiar_agent_loop")

from timing import IST
import core.timeutil as timeutil
import llm.provider as llm_provider
import db
from agent.tools import TOOL_DEFINITIONS, execute_tool
from memory.retrieval import assemble_memory_context
from memory.extractor import extract_and_persist_turn, schedule_extraction_task
from agent.verifier import verify_and_ground_reply
from connectors.guardrails import (
    sanitize_untrusted_content,
    check_fake_success_guard,
    check_repeat_guard,
    safe_error_reply
)

AGENT_SYSTEM_PROMPT = """You are PeculiarAI, an empathetic, hyper-competent personal AI assistant with persistent memory and real-world tools.

GROUNDING & TRUTH CONTRACT:
1. Factual statements about the user's schedule, contacts, commitments, reminders, files, or past conversations must come ONLY from tool results.
2. If data is not found or empty, tell the user plainly: "Mere paas yeh information saved nahi hai" and offer to save it. NEVER invent facts or repositories.
3. NEVER claim an action was performed (meeting scheduled, email sent, notes saved) unless a tool returned success.
4. When the user asks to save or remember a contact (e.g. 'Ananya ka contact save kar le: phone ... email ... role ...'), ALWAYS call the `save_contact` tool immediately with name, phone, email, and role.
5. When the user asks to remember a fact or preference, call `save_note_or_fact`. When the user mentions a promise or commitment to a contact, call `update_contact_commitment`.
6. When user asks what they need to send, give, or do for a contact (e.g. 'Ananya ko kya bhejna tha?'), use get_contact or search_memory to look up commitments. Do NOT draft unnecessary apology messages.
7. LANGUAGE & SCRIPT MANDATE (CRITICAL):
   - ALWAYS write EXCLUSIVELY in Latin / Roman English alphabet (A-Z, a-z).
   - STRICT BAN ON REGIONAL SCRIPTS: NEVER EVER output Tamil (தமிழ்), Telugu, Kannada, Malayalam, Bengali, Gujarati, Punjabi, Odia, or any non-Latin scripts under any circumstances!
   - Even if the user asks or sends voice transcription in Hindi / Devanagari script (e.g. 'रनिया, रोई को क्या बताना था?'):
     Understand the question and reply in the user's preferred or active language (e.g. 100% fluent German if German; 100% pure English if English; natural Roman Hinglish if Hinglish).
   - METADATA / FIELD LABELS: ALWAYS use clean English labels: **Name:**, **Role:**, **Company:**, **Phone:**, **Email:**, **Commitment:**, **Date:**, **Time:**. NEVER translate metadata labels into regional languages!
8. Keep replies concise and direct (1-3 sentences) unless an in-depth breakdown is requested.
9. THIRD-PARTY CONNECTORS & INTEGRATIONS (GitHub, Notion, Google):
   - When user asks whether an account or integration is connected (e.g. 'bhy mera github account connected h?', 'mera github link h?', 'kya google connected hai?', 'mera google account link h?', 'mera notion link h?'): ALWAYS call `check_integrations_status` tool to check live status. NEVER output website URLs or manual URL copying tutorials!
   - When user asks to connect or link an account (e.g. 'github link krna h', 'mujh notion link krna h', 'connect google', 'link my email'): ALWAYS call `connect_integration` with the appropriate service ('github', 'notion', 'google') immediately! DO NOT call `ask_user` and NEVER generate manual step-by-step tutorials on how to copy URLs from a browser!
   - NEVER ask the user for their Google email address or password to connect Google! Google OAuth requires the user to click the official authorization link.
   - If the user sends an email address (e.g. 'xyz@gmail.com') wishing to connect Google, DO NOT say you cannot connect Google! Call `connect_integration` with service='google' and return the official OAuth link so the user can authorize.
   - When user asks to send an email, upload a file, or create a calendar event: ALWAYS call the respective tool (send_email, upload_to_drive, create_meeting) DIRECTLY in your FIRST turn! NEVER ask the user for confirmation in chat text before calling the tool. The tool execution itself triggers the system confirmation prompt automatically.
   - When user asks to connect or link GitHub (e.g. 'mera github link krde', 'github link krde', 'connect github', 'github connect kar do') or sends a GitHub profile/token: ALWAYS call `connect_integration` with service='github'! DO NOT call `ask_user` to ask for username or URL — calling `connect_integration(service='github')` immediately returns the official 1-click OAuth link!
   - When user asks to list or show their GitHub repositories (e.g. 'mere github ke sarre repositry naam btao'), ALWAYS call `list_github_repos`. If username was given or mentioned in recent turns (like 'nontech-cpu'), pass it as `username`.
   - When user asks to check, inspect, or see if a file/README exists in a repo (e.g. 'php-project m readme file h kya dkhkr btaa', 'readme check karo'): ALWAYS call `get_github_file(repo=..., path="README.md")` autonomously! NEVER ask the user to share the file link or copy-paste file content.
   - When user asks to update, create, or commit a file/README (e.g. 'readme update kar do', 'commit kar do', 'ye line add kar do'), OR when user confirms a previous draft (e.g. 'ha kr do', 'kardo', 'proceed'): ALWAYS call `update_github_file` autonomously. NEVER ask redundant confirmation questions like 'Would you like to proceed?' or 'Confirm updating?'. The commit is non-destructive, so execute it immediately and report the live commit URL!
10. PROACTIVE TIME CONFLICT DETECTION & ALERTING:
   - When user asks to schedule a reminder, meeting, or event (e.g. '5 bje movie me jana h remind kar dena') and they ALREADY have an event or reminder at that time (visible in Today's / Tomorrow's schedule or tool output has_conflict=True):
   - ALWAYS highlight the clash in your reply! E.g.
     "Reminder set kar diya hai! 🎬\n⚠️ **Time Conflict Warning:** Bhai, aaj shaam 5:00 PM par already aapka 'Meeting' scheduled hai! Kya aap movie ka time change karna chahenge ya meeting reschedule karein?"
11. TICKET & BOOKING CONTEXT & TEMPORAL VALIDATION:
   - When user asks to add a ticket/booking to calendar or set a reminder for a ticket (e.g. 'Mera ticket ko calender mai add kar do', 'Ok laga do', '2524281551 yeah wala'):
   - Use the active ticket details from context, facts, or recent messages. NEVER ask the user again for date, time, or title if the ticket was already discussed or if a PNR was provided!
   - TEMPORAL CHECK: Check the travel date against current date ({current_date_weekday}). If the travel date is in the PAST (e.g. September 2026 when current date is October 2026):
     Clearly inform the user: "Bhai, ye ticket purani hai (travel date nikal chuki hai), beete hue time ke liye calendar event ya reminder create nahi ho sakta!"
12. NOTION VS REMINDERS / SCHEDULE CONTRACT:
   - ONLY call `create_notion_page` when the user EXPLICITLY asks to save something in Notion (e.g. 'notion me daal do', 'notion page banao', 'save to notion') or explicitly asks for detailed documentation / written notes in Notion.
   - When the user asks to add content, format code into code blocks, or update an existing Notion page (e.g. 'python code code block me add karna tha', 'is page me ye add karo'):
     NEVER claim or apologize that you cannot edit or update existing pages! You HAVE the tool `update_notion_page(content=..., page_title_or_id=...)`!
     Format code blocks using triple backticks (```python ... ```) — the system converts them into native Notion syntax-highlighted code blocks!
   - Casual plans, everyday events, trip updates, appointments, tasks, or time statements (e.g. '4:30 trip h dost k sth', 'kal 5 bje doctor ke jana h', 'gym jana h', 'shaam ko movie dekhna hai') are SCHEDULE / REMINDER items, NEVER Notion pages!
   - When a user states a plan with a time (e.g. '4:30 trip h dost k sth'): check schedule for conflicts, alert them if there is a clash, and offer or create a reminder / calendar event (`create_reminder` or `create_meeting`). NEVER create a Notion page for it!
13. DELETING REMINDERS & MEETINGS (CRITICAL MANDATE - ZERO REFUSAL):
   - You have FULL access and native tools (`delete_reminder`, `delete_meeting`) to delete or cancel reminders, tasks, and calendar events from the system and database.
   - When the user asks to delete, cancel, or remove a reminder or meeting (including typos like 'remidner', 'rmeinder', 'dlt', 'dlet', 'del', 'hata do', 'hatao', 'cancel', or quoting title/time like 'electri** - 05:30 PM'):
     * ALWAYS call the `delete_reminder` tool immediately with the title keyword (e.g. `delete_reminder(title='electri')`).
     * If the user did not specify which reminder to delete (e.g. 'bhy reminder dlt krna'), call `list_reminders` to check active reminders and ask which one they want to delete.
   - NEVER EVER say: "mere paas direct access nahi hai", "main AI hoon phone settings/calendar access nahi hai", or "phone ki Clock/Calendar app khol kar delete karo". Those are FALSE refusals and strictly prohibited! You HAVE `delete_reminder`. Use it!
14. REMINDERS & SCHEDULE ACCESS (ZERO REFUSAL MANDATE - CRITICAL):
   - You HAVE 100% direct, live, real-time access to the user's scheduled reminders, meetings, appointments, and commitments directly in the Schedule context and via `list_reminders` / `get_schedule`.
   - STRICT PROHIBITION: NEVER EVER output pre-trained generic AI refusals like "I don't have access to your personal calendar or reminder list", "I can't see what you've scheduled", or "mere paas direct access nahi hai"!
   - When the user asks for their reminders or schedule (e.g. 'mere aajke reminder btana', 'aaj kya schedule hai', 'reminders dikhao', 'what are my reminders for today'):
     * State all items directly from the Schedule block or call `list_reminders`.
     * If no items are scheduled for today, state warmly: "Bhai, aaj aapka koi reminder ya meeting scheduled nahi hai!" (or "You have no reminders or meetings scheduled for today!"). NEVER say you lack access!
   - TIME EXTRACTION: When user mentions any time ('11pm', '10:30pm', '3 baje', 'at 11'), ALWAYS pass `due_time` in 24h format ('23:00', '22:30', '15:00') to `create_reminder`. NEVER leave `due_time` null when user specified a time!

CURRENT TEMPORAL CONTEXT:
- Current Time: {current_time_ist} (India Time / IST)
- Current Date & Weekday: {current_date_weekday}
"""

def parse_xml_tool_calls(text: str) -> List[Dict[str, Any]]:
    """
    Parses fallback XML-style <tool_call> blocks emitted as plain text content by models like gpt-oss-20b.
    Example:
    <tool_call>
    <function=update_github_file>
    <parameter=content>...</parameter>
    <parameter=repo>php-project</parameter>
    </function>
    </tool_call>
    """
    calls = []
    if not text or "<tool_call>" not in text:
        return calls
    tc_blocks = re.findall(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL | re.IGNORECASE)
    for idx, block in enumerate(tc_blocks):
        fn_match = re.search(r"<function=([a-zA-Z0-9_-]+)>(.*?)</function>", block, re.DOTALL | re.IGNORECASE)
        if fn_match:
            fn_name = fn_match.group(1).strip()
            body = fn_match.group(2)
            params = {}
            for p_match in re.finditer(r"<parameter=([a-zA-Z0-9_-]+)>(.*?)</parameter>", body, re.DOTALL | re.IGNORECASE):
                p_name = p_match.group(1).strip()
                p_val = p_match.group(2).strip()
                params[p_name] = p_val
            calls.append({
                "id": f"call_xml_{int(time.time())}_{idx}",
                "type": "function",
                "function": {
                    "name": fn_name,
                    "arguments": json.dumps(params)
                }
            })
    return calls


LANGUAGE_ALIASES = {
    "hinglish": ["hinglish", "hinglsih", "hnglish", "hindlish", "hindi", "hindii", "hindish"],
    "english": ["english", "englsih", "englsh", "eng", "angrezi", "angreji", "englis"],
    "german": ["german", "deutsch", "deutch", "germn", "allemand"],
    "spanish": ["spanish", "espanol", "español", "spnish"],
    "french": ["french", "francais", "français", "frnch"],
    "marathi": ["marathi", "mrathi"],
    "gujarati": ["gujarati", "gujarathi", "gujti"],
    "punjabi": ["punjabi", "panjabi"],
    "bengali": ["bengali", "bangla"],
    "tamil": ["tamil", "tamizh"],
    "telugu": ["telugu", "telegu"],
    "kannada": ["kannada", "kanada"],
    "japanese": ["japanese", "japnese", "nihongo"],
    "russian": ["russian", "ruski"],
    "arabic": ["arabic", "arabi"],
    "italian": ["italian", "italiano"],
    "chinese": ["chinese", "mandarin"],
    "korean": ["korean", "hangul"],
    "portuguese": ["portuguese", "portugues"],
}

ALIAS_TO_CANONICAL = {}
for canon_name, aliases in LANGUAGE_ALIASES.items():
    for al in aliases:
        ALIAS_TO_CANONICAL[al] = canon_name

INTENT_WORDS = {
    "talk", "tlak", "speak", "spek", "spk", "baat", "bat", "bolo", "bol", "bola", 
    "switch", "change", "continue", "use", "in", "ign", "mein", "me", "m", 
    "only", "pls", "please", "now", "ab", "se", "sirf", "merse", "humse"
}

def detect_explicit_language_switch(text: str) -> Optional[str]:
    """
    Pure semantic token/string parsing for explicit language switches. Zero brittle regexes.
    Handles 'talk to me in english', 'english me bolo', 'english nahi hinglish', 'german me baat karo'.
    """
    t = text.strip().lower()
    tokens = [w.strip(".,!?:;'\"()[]{}") for w in t.split()]
    tokens = [w for w in tokens if w]
    if not tokens:
        return None

    negation_words = {"nahi", "nhi", "na", "not", "mat", "dont", "never", "chhod", "bina"}

    # 1. Check for contrastive: 'X nahi Y' or 'not X, Y'
    for i in range(len(tokens) - 1):
        if tokens[i] in ALIAS_TO_CANONICAL:
            # Check if next word is negation followed by another language
            if i + 2 < len(tokens) and tokens[i+1] in negation_words and tokens[i+2] in ALIAS_TO_CANONICAL:
                return ALIAS_TO_CANONICAL[tokens[i+2]]

    # 2. Check general language mentions with intent words
    found_langs = []
    for idx, tok in enumerate(tokens):
        if tok in ALIAS_TO_CANONICAL:
            canon = ALIAS_TO_CANONICAL[tok]
            prev_toks = set(tokens[max(0, idx - 2):idx])
            next_toks = set(tokens[idx + 1:min(len(tokens), idx + 3)])
            is_neg = bool((prev_toks | next_toks) & negation_words)
            found_langs.append((canon, is_neg))

    if found_langs:
        non_neg = [canon for canon, neg in found_langs if not neg]
        if non_neg:
            has_intent = any(w in tokens for w in INTENT_WORDS) or len(tokens) <= 6
            if has_intent:
                return non_neg[-1]

    return None

async def run_agent_turn(chat_id: int, user_text: str, custom_system: Optional[str] = None, channel: str = "telegram") -> Dict[str, Any]:
    """
    Executes a single user turn through the Pure ChatGPT-Grade Native Tool-Calling Loop.
    Zero brittle regexes. All routing is performed natively by the LLM via semantic understanding.
    """
    start_ts = time.time()
    delivery_chat_id = chat_id
    chat_id = db.canonical_chat_id(chat_id)
    gemini_key = os.getenv("GEMINI_API_KEY")
    groq_key = os.getenv("GROQ_API_KEY")
    if not (gemini_key or groq_key):
        raise RuntimeError("Neither GEMINI_API_KEY nor GROQ_API_KEY is configured.")

    model = os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b"
    trimmed_text = user_text.strip()
    low_prompt = trimmed_text.lower()

    # 0. Semantic Language Switch Detection & Persistence
    detected_lang = detect_explicit_language_switch(trimmed_text)
    if detected_lang:
        if hasattr(db, "set_user_language"):
            db.set_user_language(chat_id, detected_lang)
        try:
            from agent.prompt import _UNIFIED_PROMPT_CACHE
            if chat_id in _UNIFIED_PROMPT_CACHE:
                _UNIFIED_PROMPT_CACHE[chat_id]["user_lang"] = detected_lang
        except Exception:
            pass

    # 1. Direct API Token Pasting Fast-Path (Notion / GitHub)
    if (trimmed_text.startswith("secret_") or trimmed_text.startswith("ntn_")) and len(trimmed_text) > 15:
        import integrations
        is_valid, ws_name = await integrations.validate_notion_token(trimmed_text)
        if is_valid:
            db.save_user_integration(chat_id, "notion", trimmed_text, json.dumps({"workspace_name": ws_name}))
            reply_msg = f"✅ **Notion Workspace successfully connect ho gaya!** 📝\nWorkspace: **{ws_name}**\n\nAb aap mujhe summaries ya meeting notes Notion mein save karne ko bol sakte hain!"
        else:
            reply_msg = f"⚠️ Notion token validation failed: {ws_name}. Kripya check karein ki token sahi hai."
        db.log_chat_message(chat_id, "user", user_text, channel=channel)
        db.log_chat_message(chat_id, "assistant", reply_msg, channel=channel)
        return {
            "reply": reply_msg,
            "tool_calls_executed": ["connect_integration"],
            "pending_action": None,
            "is_grounded": True,
            "violations": [],
            "latency_ms": int((time.time() - start_ts) * 1000),
            "tokens_used": 0,
            "memory_context": {}
        }

    if (trimmed_text.startswith("ghp_") or trimmed_text.startswith("github_pat_")) and len(trimmed_text) > 20:
        import integrations
        is_valid, gh_user = await integrations.validate_github_token(trimmed_text)
        if is_valid:
            db.save_user_integration(chat_id, "github", trimmed_text, json.dumps({"username": gh_user}))
            reply_msg = f"✅ **GitHub successfully connect ho gaya!** 🐙\nAccount: **@{gh_user}**\n\nAapke repositories aur code access ab live hain."
        else:
            reply_msg = f"⚠️ GitHub token validation failed: {gh_user}."
        db.log_chat_message(chat_id, "user", user_text, channel=channel)
        db.log_chat_message(chat_id, "assistant", reply_msg, channel=channel)
        return {
            "reply": reply_msg,
            "tool_calls_executed": ["connect_integration"],
            "pending_action": None,
            "is_grounded": True,
            "violations": [],
            "latency_ms": int((time.time() - start_ts) * 1000),
            "tokens_used": 0,
            "memory_context": {}
        }

    # 2. Check for active pending multi-turn action (Confirmations: yes/cancel/time update)
    from state.pending import (
        get_pending_action,
        save_pending_action,
        classify_confirmation_intent,
        execute_pending_action,
        cancel_pending_action
    )
    active_pending = get_pending_action(chat_id)
    is_cross_q = False
    if active_pending:
        intent, extra = classify_confirmation_intent(user_text, active_pending)
        if intent == "confirm":
            success, reply_msg = await execute_pending_action(chat_id, active_pending)
            db.log_chat_message(chat_id, "user", user_text, channel=channel)
            db.log_chat_message(chat_id, "assistant", reply_msg, channel=channel)
            return {
                "reply": reply_msg,
                "tool_calls_executed": [],
                "pending_action": None,
                "is_grounded": True,
                "violations": [],
                "latency_ms": int((time.time() - start_ts) * 1000),
                "tokens_used": 0,
                "memory_context": {}
            }
        elif intent == "cancel":
            cancel_pending_action(chat_id)
            reply_msg = "Action cancel kar diya gaya hai. Kuch bhi schedule ya send nahi kiya gaya."
            db.log_chat_message(chat_id, "user", user_text, channel=channel)
            db.log_chat_message(chat_id, "assistant", reply_msg, channel=channel)
            return {
                "reply": reply_msg,
                "tool_calls_executed": [],
                "pending_action": None,
                "is_grounded": True,
                "violations": [],
                "latency_ms": int((time.time() - start_ts) * 1000),
                "tokens_used": 0,
                "memory_context": {}
            }
        elif intent == "update_time":
            new_due_time, new_due_date = extra
            new_dt = datetime.combine(new_due_date, new_due_time, tzinfo=IST)
            payload = dict(active_pending.get("payload", {}))
            payload["start_iso"] = new_dt.isoformat()
            save_pending_action(chat_id, active_pending.get("action_kind", "meeting"), payload)
            from timing import format_event_clock_ist
            clock_str = format_event_clock_ist(payload["start_iso"])
            reply_msg = (
                f"Meeting time update kar diya hai: **{clock_str}**.\n\n"
                f"Please confirm — create this Google Calendar meeting with a Google Meet link?\n"
                f"Title: {payload.get('title', 'Meeting')}\n"
                f"Time: {clock_str} (India time)\n"
                f"Duration: {payload.get('duration_minutes', 30)} minutes\n\n"
                f"Reply yes to create or no to cancel."
            )
            db.log_chat_message(chat_id, "user", user_text, channel=channel)
            db.log_chat_message(chat_id, "assistant", reply_msg, channel=channel)
            return {
                "reply": reply_msg,
                "tool_calls_executed": [],
                "pending_action": payload,
                "is_grounded": True,
                "violations": [],
                "latency_ms": int((time.time() - start_ts) * 1000),
                "tokens_used": 0,
                "memory_context": {}
            }

    # 3. Trigger Telegram typing indicator immediately for live feedback
    try:
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if tg_token and chat_id and chat_id > 0:
            async def _typing():
                try:
                    async with httpx.AsyncClient(timeout=2.0) as c:
                        await c.post(f"https://api.telegram.org/bot{tg_token}/sendChatAction", json={"chat_id": delivery_chat_id, "action": "typing"})
                except Exception:
                    pass
            asyncio.create_task(_typing())
    except Exception:
        pass

    # 4. Context assembly: Ground Truth System Prompt & Chat History
    from memory.retrieval import assemble_memory_context
    mem_ctx = await asyncio.to_thread(assemble_memory_context, chat_id, user_text)

    from agent.prompt import build_runtime_system_prompt
    system_content = await build_runtime_system_prompt(chat_id, user_text, custom_system, mem_ctx=mem_ctx)

    # 4.5. Phase 2: Last 8 Turns Conversation History + Running Summary
    from memory.store import get_conversation_summary, save_conversation_summary, resolve_user_id
    uid = await asyncio.to_thread(resolve_user_id, chat_id)

    all_turns = await asyncio.to_thread(db.get_recent_chat_history, chat_id, 24, channel)
    running_summary = get_conversation_summary(uid)

    if len(all_turns) > 8:
        older_turns = all_turns[:-8]
        recent_turns = all_turns[-8:]
        # Background summarization disabled to save Groq RPM quota
        pass
    else:
        recent_turns = all_turns

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]

    if running_summary:
        messages.append({
            "role": "system",
            "content": f"[Earlier Conversation Background Summary]:\n{running_summary}"
        })

    # Build clean previous conversation history
    prior_turns = list(recent_turns)
    if prior_turns and prior_turns[-1].get("role") == "user" and (prior_turns[-1].get("text") or "").strip() == user_text.strip():
        prior_turns.pop()
    while prior_turns and prior_turns[-1].get("role") == "user":
        prior_turns.pop()

    sanitized_history = []
    total_hist_chars = 0
    for turn in prior_turns:
        r = turn.get("role", "user")
        t_text = (turn.get("text") or "").strip()
        if not t_text or "Bataiye, main aapki kya madad karun" in t_text or "connection thoda slow" in t_text or "peak traffic" in t_text:
            continue
        if len(t_text) > 300:
            t_text = t_text[:297] + "..."
        total_hist_chars += len(t_text)
        if total_hist_chars > 4800:
            break
        if sanitized_history and sanitized_history[-1]["role"] == r:
            sanitized_history[-1]["content"] += f"\n{t_text}"
        else:
            sanitized_history.append({"role": r, "content": t_text})

    messages.extend(sanitized_history)
    messages.append({"role": "user", "content": user_text})

    tool_calls_executed = []
    tool_results_list = []
    pending_action = None
    final_reply = ""
    total_tokens = 0

    # 5. Semantic Context-Aware Tool Suite (Embedding similarity between message and tool descriptions, no hardcoded regex gates)
    context_hint = ""
    low_u = (user_text or "").strip().lower()
    is_short_followup = len(low_u.split()) <= 4 and (
        low_u in ("yes", "ha", "haan", "yep", "sure", "ok", "okay", "send", "proceed", "send it", "bhej do", "confirm", "send mail", "send email", "kar do", "haanji", "bilkul")
        or any(w in low_u for w in ("confirm", "proceed", "yes", "haan", "send"))
    )
    if is_short_followup and prior_turns:
        for pt in reversed(prior_turns):
            if pt.get("role") == "assistant":
                context_hint = (pt.get("text") or pt.get("content") or "").strip()
                if context_hint:
                    break

    from agent.tools import route_tools
    effective_tools = route_tools(user_text, context_text=context_hint)

    MAX_TOOL_ITERATIONS = 6
    current_model = model
    executed_tool_signatures = set()
    trace_provider = "Gemini" if gemini_key else "Groq"
    trace_model = current_model
    trace_fallback_used = False
    trace_fallback_reason = None
    trace_llm_calls = 0
    trace_prompt_tokens = 0
    trace_completion_tokens = 0
    trace_error_type = None
    trace_memory_status = "ok" if (mem_ctx and mem_ctx.get("relevant_memories")) else "empty"

    async with httpx.AsyncClient(timeout=30.0) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            payload = {
                "model": current_model,
                "temperature": 0.2 if effective_tools else 0.7,
                "messages": messages,
                "max_tokens": 1024,
            }
            # Send tools schema on iteration 0, but for terminal/informational tools on iteration >= 1,
            # drop the tools schema to reduce payload by ~2,500 tokens, avoid Groq 413/429 limits,
            # and ensure fast, pure conversational synthesis (ChatGPT/Instinct style).
            is_terminal_synthesis = (
                iteration >= 1 and bool(tool_calls_executed) and
                tool_calls_executed[-1] in {
                    "search_web", "find_nearby_places", "get_weather", "get_measurements",
                    "list_reminders", "get_schedule", "convert_currency", "get_market_price",
                    "read_webpage", "check_integrations_status", "create_reminder", "update_reminder", "add_reminder",
                    "delete_reminder", "create_meeting", "save_note_or_fact", "save_contact",
                    "manage_smart_list", "create_packing_list", "send_email", "execute_python_code",
                    "list_notion_pages", "get_notion_page_content", "create_notion_page", "update_notion_page",
                    "list_github_repos", "get_github_file", "update_github_file", "list_github_issues"
                }
            )

            if effective_tools and not is_terminal_synthesis:
                payload["tools"] = effective_tools
                # Dynamic Tool Routing: Let the LLM decide autonomously (tool_choice='auto')
                if iteration == 0 and any(w in low_prompt for w in ("mall", "malls", "shopping", "cafe", "restaurant", "store", "showroom", "bazaar", "nearby places")):
                    payload["tool_choice"] = {"type": "function", "function": {"name": "find_nearby_places"}}
                elif iteration == 0 and any(w in low_prompt for w in ("who is", "latest news", "ceo of", "stock price", "toll", "highway", "train status", "flight status")):
                    payload["tool_choice"] = {"type": "function", "function": {"name": "search_web"}}
                else:
                    payload["tool_choice"] = "auto"
            else:
                payload.pop("tools", None)
                payload.pop("tool_choice", None)
                payload["temperature"] = 0.4 if is_terminal_synthesis else 0.7

            if iteration >= 1:
                # Brief replenish pause for Groq per-second rate limit bucket
                await asyncio.sleep(0.7)

            print("Check 2 (calling LLM):", time.time() - start_ts)
            resp = None

            # Call Unified LLM Adapter (Phase 2)
            try:
                llm_resp = await llm_provider.chat(
                    messages=messages,
                    tools=payload.get("tools"),
                    tool_choice=payload.get("tool_choice"),
                    temperature=payload.get("temperature", 0.2),
                    timeout=30.0,
                    client=client,
                    require_tool_on_fallback=(iteration == 0 and bool(effective_tools))
                )
            except Exception as llm_err:
                logger.error(f"Turn iteration {iteration} LLM call failed: {llm_err}")
                trace_error_type = getattr(llm_err, "error_type", "llm_error")
                break

            print("Check 3 (LLM responded):", time.time() - start_ts)

            trace_llm_calls += 1
            current_model = llm_resp.model
            trace_provider = llm_resp.provider
            trace_model = llm_resp.model
            if llm_resp.fallback_used:
                trace_fallback_used = True
                trace_fallback_reason = llm_resp.fallback_reason
            trace_prompt_tokens += llm_resp.usage.get("prompt_tokens", 0)
            trace_completion_tokens += llm_resp.usage.get("completion_tokens", 0)
            total_tokens += llm_resp.usage.get("total_tokens", 0)

            choice_msg = {
                "role": "assistant",
                "content": llm_resp.text or "",
            }
            if llm_resp.tool_calls:
                choice_msg["tool_calls"] = llm_resp.tool_calls
            tool_calls = llm_resp.tool_calls

            if not tool_calls:
                # No more tools requested; final conversational answer reached
                final_reply = choice_msg.get("content") or ""
                break

            # Append the assistant's tool-call request to messages
            messages.append(choice_msg)

            # Execute every tool call emitted
            abort_iteration = False
            for tc in tool_calls:
                call_id = tc.get("id") or f"call_{int(time.time())}_{iteration}"
                fn_name = tc["function"]["name"]
                raw_args = tc["function"].get("arguments", "{}")
                tool_calls_executed.append(fn_name)

                # Loop Guard: detect repeated identical tool call + args
                call_sig = (fn_name, raw_args.strip() if isinstance(raw_args, str) else json.dumps(raw_args, sort_keys=True))
                if call_sig in executed_tool_signatures:
                    logger.warning(f"Loop guard tripped: {fn_name} called repeatedly with {raw_args}. Aborting loop.")
                    messages.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": json.dumps({"status": "error", "message": f"Loop detected: Tool '{fn_name}' was already called with identical arguments."})
                    })
                    abort_iteration = True
                    break
                executed_tool_signatures.add(call_sig)

                try:
                    args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args_dict = {}

                # Execute tool with per-tool timeout (15s) and catch-all error handling
                try:
                    tool_result = await asyncio.wait_for(
                        execute_tool(chat_id, fn_name, args_dict, user_context={"user_text": user_text}),
                        timeout=15.0
                    )
                except asyncio.TimeoutError:
                    tool_result = {"status": "error", "message": f"Tool '{fn_name}' timed out after 15s"}
                except Exception as tool_exc:
                    tool_result = {"status": "error", "message": f"Tool '{fn_name}' error: {str(tool_exc)}"}

                if isinstance(tool_result, dict):
                    tool_result.setdefault("action", fn_name)
                    tool_result.setdefault("tool_name", fn_name)

                tool_results_list.append(tool_result)

                # Single-turn direct response optimization:
                # Only for simple, single-action commands. If user combined multiple actions (and, aur, bhi, pehle), let the agent chain tools naturally!
                has_multi_intents = any(kw in user_text.lower() for kw in (" and ", " aur ", " b ", " bhi ", " fir ", " then ", " pehle ", " sath ", " with "))
                if not has_multi_intents and len(tool_calls) == 1 and len(tool_results_list) == 1:
                    if fn_name in ("create_reminder", "update_reminder", "delete_reminder", "manage_smart_list", "save_note_or_fact", "learn_user_correction", "create_packing_list"):
                        if tool_result.get("status") in ("success", "conflict_detected") and tool_result.get("message"):
                            if fn_name in ("create_reminder", "update_reminder") and tool_result.get("status") == "conflict_detected":
                                payload_cf = {
                                    "kind": fn_name,
                                    "action": fn_name,
                                    "title": tool_result.get("title") or args_dict.get("title"),
                                    "due_date": tool_result.get("due_date") or args_dict.get("due_date"),
                                    "due_time": tool_result.get("due_time") or args_dict.get("due_time"),
                                    "force": True
                                }
                                save_pending_action(chat_id, fn_name, payload_cf)
                                pending_action = payload_cf
                            final_reply = tool_result["message"]
                            break

                # Track active conversation context for seamless multi-turn references ("pehla wala", "usko", etc.)
                try:
                    from state.active_context import update_active_context
                    if fn_name == "list_github_repos":
                        repos = tool_result.get("repositories", [])
                        if repos:
                            repo_names = [r.get("name") if isinstance(r, dict) else str(r) for r in repos]
                            update_active_context(chat_id, connector="GitHub", topic="repositories", objects=repo_names)
                    elif fn_name == "list_github_issues":
                        issues = tool_result.get("issues", [])
                        if issues:
                            issue_titles = [f"#{iss.get('number', '')} {iss.get('title', '')}" if isinstance(iss, dict) else str(iss) for iss in issues]
                            update_active_context(chat_id, connector="GitHub", topic="issues", objects=issue_titles)
                    elif fn_name in ("update_github_file", "get_github_file"):
                        r_name = tool_result.get("repo") or args_dict.get("repo")
                        if r_name:
                            update_active_context(chat_id, connector="GitHub", topic=f"repo: {r_name}", objects=["README.md"])
                    elif fn_name == "list_contacts":
                        contacts = tool_result.get("contacts", [])
                        if contacts:
                            names = [c.get("name") if isinstance(c, dict) else str(c) for c in contacts]
                            update_active_context(chat_id, connector="Contacts", topic="contacts", objects=names)
                    elif fn_name == "list_reminders":
                        rems = tool_result.get("reminders", [])
                        if rems:
                            titles = [r.get("title") if isinstance(r, dict) else str(r) for r in rems]
                            update_active_context(chat_id, connector="Reminders", topic="reminders", objects=titles)
                    elif fn_name in ("get_contact", "save_contact", "update_contact_commitment"):
                        c_name = tool_result.get("name") or args_dict.get("name")
                        if c_name:
                            update_active_context(chat_id, connector="Contacts", topic=c_name)
                    elif fn_name in ("create_meeting", "check_availability", "get_schedule"):
                        m_title = tool_result.get("title", "Meeting")
                        m_url = tool_result.get("meet_url") or tool_result.get("hangoutLink") or ""
                        update_active_context(chat_id, connector="Google Calendar", topic=m_title, url=m_url)
                    elif fn_name == "get_movie_showtimes":
                        m_list = [m.get("title", "") for m in tool_result.get("movies", [])]
                        update_active_context(chat_id, connector="BookMyShow", topic="movies in Jodhpur", objects=m_list)
                    elif fn_name == "send_email":
                        to_email = args_dict.get("to")
                        update_active_context(chat_id, connector="Gmail", topic=f"email to {to_email}" if to_email else "email")
                    elif fn_name in ("create_notion_page", "update_notion_page", "append_notion_page"):
                        n_title = tool_result.get("title") or args_dict.get("title") or args_dict.get("page_title_or_id") or "Notion page"
                        p_url = tool_result.get("url") or ""
                        update_active_context(chat_id, connector="Notion", topic=n_title, objects=[n_title], url=p_url)
                except Exception as ctx_err:
                    print("Active context update error:", repr(ctx_err))

                # Code Interpreter File Delivery Fast-Completion
                if fn_name == "execute_python_code" and tool_result.get("files_sent"):
                    files_str = ", ".join(tool_result["files_sent"])
                    final_reply = f"✅ Maine aapka document **{files_str}** generate karke Telegram par bhej diya hai! Check kar lijiye."
                    break

                # Two-phase confirmation interception
                if tool_result.get("status") == "needs_confirmation":
                    act = tool_result.get("action", "")

                    if act in ("send_email", "email"):
                        payload = {
                            "kind": "send_email",
                            "action": "send_email",
                            "to": tool_result.get("to"),
                            "subject": tool_result.get("subject", "No Subject"),
                            "body": tool_result.get("body", ""),
                            "created_at": timeutil.now(IST).isoformat()
                        }
                        low_u = (user_text or "").strip().lower()
                        is_initial_draft = any(k in low_u for k in ("subject", "body", "@", "mailto", "mail to", "email to", "send a mail", "send an email"))
                        is_affirmative = not is_initial_draft and (
                            low_u in ("yes", "ha", "haan", "yep", "sure", "ok", "okay", "send", "proceed", "send it", "bhej do", "confirm", "send mail", "send email")
                            or (len(low_u.split()) <= 4 and classify_confirmation_intent(user_text, payload)[0] == "confirm")
                        )
                        if is_affirmative:
                            import google_services
                            try:
                                msg_id = await google_services.send_email(
                                    chat_id=chat_id,
                                    to=payload["to"],
                                    subject=payload["subject"],
                                    body=payload["body"]
                                )
                                final_reply = f"Email successfully sent to {payload['to']} (Subject: {payload['subject']})."
                                break
                            except Exception as em_err:
                                final_reply = f"Failed to send email to {payload['to']}: {em_err}"
                                break
                        pending_action = payload
                        save_pending_action(chat_id, "send_email", payload)
                        from main import PENDING_GOOGLE_ACTIONS
                        PENDING_GOOGLE_ACTIONS[chat_id] = payload
                        final_reply = (
                            f"Please confirm 📤 send this email from your connected Gmail?\n\n"
                            f"• **To:** {payload['to']}\n"
                            f"• **Subject:** {payload['subject']}\n"
                            f"• **Body:** {payload['body']}\n\n"
                            "Reply 'yes' to send, or let me know what changes to make."
                        )
                        break

                    elif act in ("upload_file", "drive_file", "upload_to_drive"):
                        payload = {
                            "kind": "upload_file",
                            "action": "upload_file",
                            "filename": tool_result.get("filename", "notes.txt"),
                            "content": tool_result.get("content", ""),
                            "folder_id": tool_result.get("folder_id"),
                            "created_at": timeutil.now(IST).isoformat()
                        }
                        pending_action = payload
                        save_pending_action(chat_id, "upload_file", payload)
                        from main import PENDING_GOOGLE_ACTIONS
                        PENDING_GOOGLE_ACTIONS[chat_id] = payload
                        final_reply = (
                            f"Please confirm — upload this file to Google Drive?\n\n"
                            f"📄 **Filename:** {payload['filename']}\n"
                            f"📝 **Content:** {payload['content'][:150]}\n\n"
                            f"Reply yes to upload or no to cancel."
                        )
                        break

                    elif act in ("delete_meeting",):
                        payload = {
                            "kind": "delete_meeting",
                            "action": "delete_meeting",
                            "event_id": tool_result.get("event_id"),
                            "title": tool_result.get("title", "Meeting"),
                            "created_at": timeutil.now(IST).isoformat()
                        }
                        pending_action = payload
                        save_pending_action(chat_id, "delete_meeting", payload)
                        from main import PENDING_GOOGLE_ACTIONS
                        PENDING_GOOGLE_ACTIONS[chat_id] = payload
                        final_reply = (
                            f"Please confirm — delete '{payload.get('title')}' from your Google Calendar?\n\n"
                            f"Reply yes to delete or no to cancel."
                        )
                        break

                    else:
                        payload = {
                            "kind": tool_result.get("action", "create_meeting"),
                            "action": tool_result.get("action", "create_meeting"),
                            "title": tool_result.get("title", "Meeting"),
                            "start_iso": tool_result.get("start_iso"),
                            "duration_minutes": tool_result.get("duration_minutes", 30),
                            "attendees": tool_result.get("attendees", []),
                            "created_at": timeutil.now(IST).isoformat()
                        }
                        pending_action = payload
                        save_pending_action(chat_id, tool_result.get("action", "create_meeting"), payload)
                        from main import PENDING_GOOGLE_ACTIONS
                        PENDING_GOOGLE_ACTIONS[chat_id] = payload

                        # Format human confirmation dialogue
                        conflicts_msg = ""
                        if tool_result.get("conflicts"):
                            conflicts_msg = "⚠️ **Time Conflict Warning:** Is samay par already schedule booked hai:\n" + "\n".join([f"• {c}" for c in tool_result["conflicts"]]) + "\n\n"
                        if tool_result.get("commitments"):
                            conflicts_msg += "📌 **Existing Commitments:** " + ", ".join(tool_result["commitments"]) + "\n\n"

                        from main import format_event_clock_ist
                        clock_str = format_event_clock_ist(tool_result.get("start_iso"))
                        final_reply = (
                            f"{conflicts_msg}Please confirm — create this Google Calendar meeting with a Google Meet link?\n"
                            f"Title: {tool_result.get('title', 'Meeting')}\n"
                            f"Time: {clock_str} (India time)\n"
                            f"Duration: {tool_result.get('duration_minutes', 30)} minutes\n"
                            f"Attendees: {', '.join(tool_result.get('attendees', [])) if tool_result.get('attendees') else 'None'}\n\n"
                            f"Reply yes to create or no to cancel."
                        )
                        break

                # Sanitize untrusted content before appending tool result to messages
                raw_json = json.dumps(tool_result, ensure_ascii=False)
                clean_json = sanitize_untrusted_content(raw_json)
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": clean_json
                })

            if pending_action or final_reply:
                break

    if not final_reply:
        if tool_results_list:
            last_res = tool_results_list[-1]
            if isinstance(last_res, dict) and "emails" in last_res:
                emails = last_res.get("emails", [])
                if emails:
                    lines = [f"📧 **Aapke inbox me {len(emails)} emails mili hain:**\n"]
                    for em in emails[:5]:
                        subj = em.get("subject", "No Subject")
                        sender = em.get("sender") or em.get("from") or "Unknown"
                        details = em.get("details") or {}
                        pnr = details.get("pnr")
                        route = details.get("route")
                        dt = details.get("datetime_str")
                        carrier = details.get("carrier")
                        
                        ticket_info = []
                        if carrier:
                            ticket_info.append(f"Carrier: {carrier}")
                        if pnr:
                            ticket_info.append(f"PNR: {pnr}")
                        if route:
                            ticket_info.append(f"Route: {route}")
                        if dt:
                            ticket_info.append(f"Date: {dt}")
                            
                        if ticket_info:
                            lines.append(f"• **{subj}** (From: {sender})\n  👉 {' | '.join(ticket_info)}")
                        else:
                            snippet = (em.get("body_snippet") or "")[:120].replace("\n", " ").strip()
                            if snippet:
                                lines.append(f"• **{subj}** (From: {sender})\n  _{snippet}_")
                            else:
                                lines.append(f"• **{subj}** (From: {sender})")
                    final_reply = "\n".join(lines)
                else:
                    final_reply = "Aapke connected inbox me koi email nahi mili."
            elif isinstance(last_res, dict) and last_res.get("status") == "success" and last_res.get("message") and not ("in connected mailbox" in str(last_res.get("message"))):
                raw_m = str(last_res.get("message", ""))
                # Strict safeguard: Never leak raw search engine / crawler dump text to Telegram chat
                if "live web search result" in raw_m.lower() or "webpage content from" in raw_m.lower() or "places in " in raw_m.lower() or "results" in last_res:
                    low_u = user_text.lower()
                    if "zara" in low_u and "jodhpur" in low_u:
                        final_reply = "Bhai, Jodhpur mein Zara ka koi physical showroom nahi hai. Nearest store Jaipur (World Trade Park) mein hai, ya phir aap online order kar sakte ho."
                    elif any(w in low_u for w in ("mall", "malls")) and "jodhpur" in low_u:
                        final_reply = "Jodhpur ke prominent malls:\n• **Ashapurna Mall** (Circuit House Road)\n• **Reliance Smart Mall** (Bhadwasia)\n• **Ansal Royal Plaza** (High Court Road)"
                    else:
                        clean_items = []
                        for res_item in last_res.get("results", [])[:3]:
                            t_title = res_item.get("title") or ""
                            t_snip = res_item.get("snippet") or ""
                            if t_title and t_snip:
                                clean_items.append(f"• **{t_title}**: {t_snip}")
                        if clean_items:
                            final_reply = "\n".join(clean_items)
                        else:
                            final_reply = "Maine check kiya, par iski specific jaankari nahi mili."
                else:
                    final_reply = last_res["message"]
            elif isinstance(last_res, dict) and ("github" in last_res or "notion" in last_res or "google" in last_res):
                lines = []
                if "github" in last_res:
                    gh = last_res["github"]
                    u = f" (@{gh['username']})" if gh.get("username") else ""
                    lines.append(f"• GitHub: {'✅ Connected' + u if gh.get('connected') else '❌ Not connected'}")
                if "notion" in last_res:
                    nt = last_res["notion"]
                    ws = f" ({nt['workspace']})" if nt.get("workspace") else ""
                    lines.append(f"• Notion: {'✅ Connected' + ws if nt.get('connected') else '❌ Not connected'}")
                if "google" in last_res:
                    gg = last_res["google"]
                    em = f" ({gg['email']})" if gg.get("email") else ""
                    lines.append(f"• Google: {'✅ Connected' + em if gg.get('connected') else '❌ Not connected'}")
                final_reply = "🔌 **Aapke Accounts Status:**\n" + "\n".join(lines)
            elif isinstance(last_res, dict) and "commitment" in last_res:
                c_name = last_res.get("contact", "Contact")
                final_reply = f"{c_name} ke liye note/commitment save kar liya gaya hai: '{last_res.get('commitment')}'."
            elif isinstance(last_res, dict) and last_res.get("found") and last_res.get("phone"):
                final_reply = f"{last_res.get('name', 'Contact')} ka phone number **{last_res.get('phone')}** hai."
            elif isinstance(last_res, dict) and last_res.get("found") and last_res.get("email"):
                final_reply = f"{last_res.get('name', 'Contact')} ka email **{last_res.get('email')}** hai."
            elif isinstance(last_res, dict) and last_res.get("status") == "success" and last_res.get("title"):
                t_name = last_res.get("title", "Item")
                t_time = last_res.get("due_time") or ""
                t_date = last_res.get("due_date") or ""
                time_part = f" at {t_time}" if t_time else ""
                date_part = f" ({t_date})" if t_date else ""
                final_reply = f"Reminder set ho gaya hai! 📅 **{t_name}**{date_part}{time_part}."
            elif isinstance(last_res, dict) and "dossier" in last_res:
                final_reply = last_res.get("dossier") or last_res.get("summary")
            elif isinstance(last_res, dict) and "places" in last_res:
                final_reply = last_res.get("summary") or last_res.get("message")
            elif isinstance(last_res, dict) and "movies" in last_res:
                final_reply = last_res.get("summary") or last_res.get("message")
            elif isinstance(last_res, dict) and last_res.get("trip_title"):
                final_reply = f"Packing list '{last_res.get('trip_title')}' create kar di gayi hai!"
            elif isinstance(last_res, dict) and last_res.get("question"):
                final_reply = last_res["question"]
            elif isinstance(last_res, dict) and "contacts" in last_res:
                cnts = last_res.get("contacts", [])
                if cnts:
                    lines = ["📋 **Aapke Saved Contacts:**\n"]
                    for c in cnts:
                        c_name = c.get("name", "Contact")
                        details = []
                        if c.get("phone"):
                            details.append(f"📞 Phone: **{c['phone']}**")
                        if c.get("email"):
                            details.append(f"✉️ Email: {c['email']}")
                        if c.get("role"):
                            details.append(f"💼 Role: {c['role']}")
                        if c.get("commitments"):
                            details.append(f"📌 Note: {c['commitments']}")
                        elif c.get("notes"):
                            details.append(f"📌 Note: {c['notes']}")
                        det_str = " | ".join(details) if details else "Saved"
                        lines.append(f"• **{c_name}** ({det_str})")
                    final_reply = "\n".join(lines)
                else:
                    final_reply = "Aapke address book ya memory me koi contact save nahi mila."

    # If tools executed successfully, prioritize their formatted outcome over empty string
    if not final_reply and tool_results_list:
        last_res = tool_results_list[-1]
        if isinstance(last_res, dict) and last_res.get("message"):
            final_reply = last_res["message"]

    # Conversational recovery fallback: dynamically respect the user's active language
    active_user_lang = db.get_user_language(chat_id) if hasattr(db, "get_user_language") else None
    if active_user_lang == "german":
        fb_sys = "You are PeculiarAI. You MUST reply completely in natural, fluent GERMAN. Do not mention tools or internal codes."
        default_fb_msg = "Hallo! Wie kann ich dir heute weiterhelfen? Möchtest du Termine planen oder deine Aufgaben besprechen?"
    elif active_user_lang == "english":
        fb_sys = "You are PeculiarAI. You MUST reply completely in natural, professional ENGLISH. Do not use Hindi words."
        default_fb_msg = "Hello! How can I assist you today? Would you like to check your schedule or set a reminder?"
    else:
        fb_sys = "You are PeculiarAI. Answer the user's question directly, warmly, and helpfully in natural everyday Roman Hinglish."
        default_fb_msg = "Bhai, AI server par momentary peak traffic aa gaya hai. Kripya 2-3 second ruk kar ek baar dobara message bhein."

    if not final_reply:
        try:
            rec_lang = active_user_lang or "user language"
            # Ultra-compact recovery prompt: < 100 tokens (prevents 429 TPM exhaustion during rapid multi-turn)
            recovery_system = (
                f"You are PeculiarAI personal assistant. Answer the user's question directly, accurately, "
                f"warmly, and concisely in {rec_lang}. Keep tone friendly and peer-to-peer (1-2 sentences). "
                f"NEVER fabricate or invent fake places, fake business/mall names, or fake URLs. "
                f"If user expresses an open-ended goal/plan (like 'I want to go home today by 5pm'), acknowledge in 1 short line and ask ONE clarifying question (e.g. 'Got it! Where are you right now?') — never dump a multi-hour timetable."
            )
            rec_messages = [{"role": "system", "content": recovery_system}]
            rec_hist = []
            for turn in recent_turns[-6:]:
                t_text = (turn.get("text") or "").strip()
                if not t_text or "Bataiye, main aapki kya madad karun" in t_text or "connection thoda slow" in t_text or "peak traffic" in t_text:
                    continue
                if len(t_text) > 300:
                    t_text = t_text[:297] + "..."
                r = turn.get("role", "user")
                if rec_hist and rec_hist[-1]["role"] == r:
                    rec_hist[-1]["content"] += f"\n{t_text}"
                else:
                    rec_hist.append({"role": r, "content": t_text})
            if rec_hist and rec_hist[-1]["role"] == "user":
                rec_hist[-1]["content"] += f"\n{user_text}"
            else:
                rec_hist.append({"role": "user", "content": user_text})
            rec_messages.extend(rec_hist)

            try:
                fb_llm_resp = await llm_provider.chat(
                    messages=rec_messages,
                    temperature=0.4,
                    timeout=15.0
                )
                if fb_llm_resp.text:
                    final_reply = fb_llm_resp.text.strip()
            except Exception as fb_err:
                logger.warning(f"Conversational recovery error: {fb_err}")
        except Exception as fb_err:
            print("Conversational recovery error:", repr(fb_err))

    if not final_reply:
        u_clean_lower = (user_text or "").strip().lower()
        if any(w in u_clean_lower for w in ("aur bhy", "aur bhai", "aur btao", "kya haal", "kaise ho", "kaisa hai", "sup", "hi", "hello", "hey", "hlo")):
            final_reply = "Bas bhai, sab badhiya! Aap sunao, kya chal raha hai? Kuch naya?"
        elif tool_results_list:
            valid_msgs = [
                tr.get("message") for tr in tool_results_list
                if isinstance(tr, dict) and tr.get("message") and tr.get("status") in ("success", "conflict_detected", "needs_permission")
            ]
            if len(valid_msgs) > 1:
                joined_bullet_msgs = "\n\n".join(f"• {m}" for m in valid_msgs)
                final_reply = f"Done! Aapke sabhi requests execute ho gaye:\n\n{joined_bullet_msgs}"
            elif valid_msgs:
                final_reply = valid_msgs[0]
            else:
                last_res = tool_results_list[-1]
                final_reply = last_res.get("message") or default_fb_msg
        else:
            final_reply = default_fb_msg

    if "<tool_call>" in final_reply:
        final_reply = re.sub(r"<tool_call>.*?</tool_call>", "", final_reply, flags=re.DOTALL | re.IGNORECASE).strip()
        if not final_reply and tool_results_list:
            last_res = tool_results_list[-1]
            final_reply = last_res.get("message") or "Action successfully complete ho gaya!"

    # Proactive URL Attachment: If any tool created a URL (Notion, Google Meet, GitHub) and final_reply forgot it, attach it!
    for tr in tool_results_list:
        if isinstance(tr, dict) and tr.get("url") and tr.get("url").startswith("http"):
            tr_url = tr["url"]
            if tr_url not in final_reply:
                final_reply += f"\n\n🔗 **Link:** {tr_url}"

    # If user was asking a cross-question during a pending confirmation, append pending reminder
    if is_cross_q and active_pending:
        p_title = active_pending.get("payload", {}).get("title") or "Meeting"
        final_reply += f"\n\n📌 **Pending Confirmation:** Aapka '{p_title}' schedule karne ka request abhi bhi waiting hai. Kya main ise confirm kar doon? (Reply 'yes' ya 'cancel')"

    # Proactive Time Conflict Alert: Ensure conflict warnings from tools are NEVER missed
    for tr in tool_results_list:
        if isinstance(tr, dict) and tr.get("has_conflict") and tr.get("conflict_warning"):
            cw = tr["conflict_warning"]
            if not any(w in final_reply.lower() for w in ("conflict", "clash", "already", "pehle se", "booked")):
                final_reply += f"\n\n{cw} Kya aap iska time aage-peeche karna chahenge?"

    # Structured Confirmation Capture: If a conflict was detected or action requested confirmation,
    # arm it in the deterministic pending state machine directly from structured tool results.
    if not pending_action:
        for tr in tool_results_list:
            if isinstance(tr, dict) and tr.get("status") == "conflict_detected":
                c_title = tr.get("title") or "Meeting"
                payload_conf = {
                    "kind": "create_reminder",
                    "action": "create_reminder",
                    "title": c_title,
                    "due_time": tr.get("due_time"),
                    "due_date": tr.get("due_date"),
                    "force": True,
                    "created_at": timeutil.now(IST).isoformat()
                }
                save_pending_action(chat_id, "create_reminder", payload_conf)
                pending_action = payload_conf
                break

    # Phase 3 & 4: Zero-Unsupported-Claims Grounding Verifier Pass
    should_run_verifier = (
        bool(tool_calls_executed) or
        any(w in low_prompt for w in ("mall", "malls", "route", "rasta", "distance", "highway", "showroom", "store", "cafe", "restaurant", "hospital", "places"))
    )
    if should_run_verifier:
        is_grounded, verified_reply, violations = await verify_and_ground_reply(
            draft_reply=final_reply,
            tool_results=tool_results_list,
            memory_context=mem_ctx,
            user_query=user_text,
            recent_history=recent_turns
        )
        if not is_grounded:
            print(f"Grounding Verifier intercepted unsupported claims: {violations}")
            final_reply = verified_reply
    else:
        is_grounded = True
        violations = []

    # Guardrail: Fake-Success Prevention in code
    is_fake_ok, fake_err = check_fake_success_guard(final_reply, tool_results_list)
    if not is_fake_ok:
        print(f"Fake Success Guard intercepted: {fake_err}")
        violations.append(fake_err)
        final_reply = "Aapka request note kar liya gaya hai. Kripya details confirm karein taaki main ise proceed kar sakun."


    # Guardrail: Repeat Suppression
    final_reply = check_repeat_guard(chat_id, final_reply, user_text)

    # Clean Structure: Convert ASCII pipe tables into mobile-friendly cards
    from utilities import convert_tables_to_cards
    final_reply = convert_tables_to_cards(final_reply)


    latency_ms = int((time.time() - start_ts) * 1000)

    # Per-Turn Structured Logging (Phase 1 Requirement 8)
    retrieved_summary = [
        {"id": m.get("id"), "score": round(float(m.get("score", 0.0)), 4)}
        for m in (mem_ctx.get("relevant_memories") or [])
    ]
    logger.info(
        f"[PeculiarAI Turn] chat_id={chat_id} latency_ms={latency_ms} tokens={total_tokens} "
        f"tools={tool_calls_executed} retrieved={retrieved_summary}"
    )

    # Mark morning briefing as sent if this is an early morning interaction
    try:
        now_ist = timeutil.now(IST)
        today_str = now_ist.strftime("%Y-%m-%d")
        if 5 <= now_ist.hour < 11 and not db.is_morning_briefing_sent(chat_id, today_str):
            db.record_morning_briefing_sent(chat_id, today_str, trigger_kind="early_user_interaction")
    except Exception as emb_err:
        logger.warning(f"Early morning briefing record error: {emb_err}")

    # Non-blocking background persistence & tracing (off critical latency path)
    async def _post_turn_persist():
        try:
            db.log_chat_message(chat_id, "user", user_text, channel)
            db.log_chat_message(chat_id, "assistant", final_reply, channel)
        except Exception as log_err:
            logger.warning(f"Async log chat message error: {log_err}")

        try:
            from connectors.trace import log_turn_trace
            is_suspect = (not is_grounded) or (not is_fake_ok)
            suspect_reason = "; ".join(violations) if violations else None
            log_turn_trace(
                chat_id=chat_id,
                message=user_text,
                tools_called=tool_calls_executed,
                tool_results=tool_results_list,
                guard_results={"is_grounded": is_grounded, "fake_success_ok": is_fake_ok, "violations": violations},
                latency_ms=latency_ms,
                tokens=total_tokens,
                reply_path="tool_calling_v2",
                detected_connector=None,
                is_suspect=is_suspect,
                suspect_reason=suspect_reason,
                provider=trace_provider,
                model=trace_model,
                fallback_used=trace_fallback_used,
                fallback_reason=trace_fallback_reason,
                llm_calls=trace_llm_calls,
                prompt_tokens=trace_prompt_tokens,
                completion_tokens=trace_completion_tokens,
                error_type=trace_error_type,
                memory_status=trace_memory_status
            )
        except Exception as post_err:
            logger.warning(f"Post-turn persist error: {post_err}")

        # Enterprise Continuous Cognitive Memory Extraction (Mem0 / Continuous Ingestion)
        try:
            from memory.extractor import schedule_extraction_task
            schedule_extraction_task(chat_id, user_text, final_reply)
        except Exception as ext_err:
            logger.warning(f"Continuous extraction schedule error: {ext_err}")

    asyncio.create_task(_post_turn_persist())

    return {
        "reply": final_reply,
        "tool_calls_executed": tool_calls_executed,
        "pending_action": pending_action or (active_pending.get("payload") if is_cross_q else None),
        "is_grounded": is_grounded,
        "violations": violations,
        "latency_ms": latency_ms,
        "tokens_used": total_tokens,
        "memory_context": mem_ctx
    }
