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
   - When user asks whether an account or integration is connected (e.g. 'bhy mera github account connected h?', 'kya google connected hai?'), ALWAYS call `check_integrations_status` tool to check live status.
   - When user asks to connect Google, or says 'connect google', 'link my email', or asks to schedule/host a Google Calendar / Meet meeting when Google is not connected: ALWAYS call `connect_integration` with service='google' immediately! DO NOT call `ask_user` to ask if they want to connect — they already asked!
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
    t = text.strip().lower()

    # 1. Contrastive: 'X nahi Y' or 'not X, Y' (e.g. 'english nhi hinglish', 'merse hinglsih m baat kro english m nhi')
    contrast = re.search(r'\b([a-z]{3,15})\s+(?:me\s+|m\s+)?(?:nahi|nhi|na|not|mat|chhod\s+ke|ke\s+alawa)\s+(?:balki\s+|par\s+|sirf\s+)?([a-z]{3,15})\b', t)
    if contrast:
        rej, sel = contrast.groups()
        if sel in ALIAS_TO_CANONICAL:
            return ALIAS_TO_CANONICAL[sel]

    # 2. Dynamic patterns: "talk to me in X", "speak in X", "X me baat karo", "X me bolo"
    dyn_match = re.search(r'\b(?:talk|tlak|speak|spek|chat|write|continue)\s+(?:to\s+me\s+)?(?:the\s+)?(?:in|ign)\s+([a-z]{3,15})\b', t)
    if dyn_match:
        cand = dyn_match.group(1)
        canon = ALIAS_TO_CANONICAL.get(cand, cand)
        return canon

    dyn_match_hi = re.search(r'\b([a-z]{3,15})\s+(?:me|mein|m)\s+(?:baat|bat|bolo|bol|likho)\b', t)
    if dyn_match_hi:
        cand = dyn_match_hi.group(1)
        canon = ALIAS_TO_CANONICAL.get(cand, cand)
        return canon

    # 3. Check for negated languages vs preferred languages
    found_langs = []
    for word in re.findall(r'\b[a-z]{3,15}\b', t):
        if word in ALIAS_TO_CANONICAL:
            canon = ALIAS_TO_CANONICAL[word]
            if canon not in [l[0] for l in found_langs]:
                is_neg = bool(re.search(rf'\b{word}\s+(?:me\s+|m\s+)?(?:nahi|nhi|na|not|mat)\b', t)) or bool(re.search(rf'\b(?:mat|dont|never)\s+{word}\b', t))
                found_langs.append((canon, is_neg))

    if found_langs:
        non_neg = [canon for canon, neg in found_langs if not neg]
        if non_neg:
            has_intent = any(w in t.split() for w in INTENT_WORDS) or len(t.split()) <= 6
            if has_intent:
                return non_neg[-1]

    return None

async def run_agent_turn(chat_id: int, user_text: str, custom_system: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a single user turn through the Pure ChatGPT-Grade Native Tool-Calling Loop.
    Zero brittle regexes. All routing is performed natively by the LLM via semantic understanding.
    """
    start_ts = time.time()
    delivery_chat_id = chat_id
    chat_id = db.canonical_chat_id(chat_id)
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("GROQ_API_KEY environment variable is missing.")

    model = os.getenv("GROQ_MODEL") or "openai/gpt-oss-20b"
    trimmed_text = user_text.strip()
    low_prompt = trimmed_text.lower()

    # 0. Semantic Language Switch Detection & Persistence
    detected_lang = detect_explicit_language_switch(trimmed_text)
    if detected_lang and hasattr(db, "set_user_language"):
        db.set_user_language(chat_id, detected_lang)

    # 1. Direct API Token Pasting Fast-Path (Notion / GitHub)
    if (trimmed_text.startswith("secret_") or trimmed_text.startswith("ntn_")) and len(trimmed_text) > 15:
        import integrations
        is_valid, ws_name = await integrations.validate_notion_token(trimmed_text)
        if is_valid:
            db.save_user_integration(chat_id, "notion", trimmed_text, json.dumps({"workspace_name": ws_name}))
            reply_msg = f"✅ **Notion Workspace successfully connect ho gaya!** 📝\nWorkspace: **{ws_name}**\n\nAb aap mujhe summaries ya meeting notes Notion mein save karne ko bol sakte hain!"
        else:
            reply_msg = f"⚠️ Notion token validation failed: {ws_name}. Kripya check karein ki token sahi hai."
        db.log_chat_message(chat_id, "user", user_text)
        db.log_chat_message(chat_id, "assistant", reply_msg)
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
        db.log_chat_message(chat_id, "user", user_text)
        db.log_chat_message(chat_id, "assistant", reply_msg)
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
            db.log_chat_message(chat_id, "user", user_text)
            db.log_chat_message(chat_id, "assistant", reply_msg)
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
            db.log_chat_message(chat_id, "user", user_text)
            db.log_chat_message(chat_id, "assistant", reply_msg)
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
            db.log_chat_message(chat_id, "user", user_text)
            db.log_chat_message(chat_id, "assistant", reply_msg)
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

    all_turns = await asyncio.to_thread(db.get_recent_chat_history, chat_id, 24)
    running_summary = get_conversation_summary(uid)

    if len(all_turns) > 8:
        older_turns = all_turns[:-8]
        recent_turns = all_turns[-8:]
        if not running_summary and len(older_turns) >= 2:
            try:
                summary_prompt = "Summarize the following earlier conversation turns between user and assistant into a concise 2-3 sentence background context:\n" + "\n".join([f"{t['role']}: {t['text'][:200]}" for t in older_turns])
                async with httpx.AsyncClient(timeout=10.0) as sm_client:
                    sm_res = await sm_client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        json={
                            "model": "openai/gpt-oss-20b",
                            "messages": [{"role": "user", "content": summary_prompt}],
                            "max_tokens": 150,
                            "temperature": 0.2
                        }
                    )
                    if sm_res.status_code == 200:
                        running_summary = sm_res.json()["choices"][0]["message"]["content"].strip()
                        save_conversation_summary(uid, running_summary)
            except Exception as sm_exc:
                logger.warning(f"Error computing conversation summary: {sm_exc}")
    else:
        recent_turns = all_turns

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]

    if running_summary:
        messages.append({
            "role": "system",
            "content": f"[Earlier Conversation Background Summary]:\n{running_summary}"
        })

    # Append up to last 8 turns, truncated to 300 chars per turn (total capped at ~1200 tokens)
    total_hist_chars = 0
    for turn in recent_turns:
        t_text = (turn.get("text") or "").strip()
        if len(t_text) > 300:
            t_text = t_text[:297] + "..."
        total_hist_chars += len(t_text)
        if total_hist_chars > 4800:
            break
        messages.append({"role": turn["role"], "content": t_text})

    messages.append({"role": "user", "content": user_text})

    tool_calls_executed = []
    tool_results_list = []
    pending_action = None
    final_reply = ""
    total_tokens = 0

    # 5. Semantic Context-Aware Tool Suite (Embedding similarity between message and tool descriptions, no hardcoded regex gates)
    from agent.tools import route_tools
    effective_tools = route_tools(user_text)

    MAX_TOOL_ITERATIONS = 6
    current_model = model
    executed_tool_signatures = set()

    async with httpx.AsyncClient(timeout=30.0) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            payload = {
                "model": current_model,
                "temperature": 0.2 if effective_tools else 0.5,
                "messages": messages,
                "max_tokens": 1024,
            }
            # Send tools schema on EVERY iteration to support multi-step chaining
            if effective_tools:
                payload["tools"] = effective_tools
                payload["tool_choice"] = "auto"

            print("Check 2 (calling Groq):", time.time() - start_ts)
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json=payload
            )
            print("Check 3 (Groq responded):", time.time() - start_ts)



            # Intelligent Fallback for 429 Rate Limits / Transient Errors
            if resp.status_code == 429 or resp.status_code != 200:
                print(f"Agent Loop error {resp.status_code} on {current_model}. Falling back to openai/gpt-oss-20b...")
                current_model = "openai/gpt-oss-20b"
                payload["model"] = current_model
                await asyncio.sleep(0.5)
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json=payload
                )
                if resp.status_code != 200:
                    print(f"Fallback retry status: {resp.status_code}")
                    await asyncio.sleep(1.5)
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        json=payload
                    )
                    if resp.status_code != 200:
                        break

            resp_data = resp.json()
            choice = resp_data["choices"][0]
            choice_msg = choice["message"]
            total_tokens += resp_data.get("usage", {}).get("total_tokens", 0)

            # Check if model wants to call tools
            tool_calls = choice_msg.get("tool_calls")
            if not tool_calls and choice_msg.get("content"):
                xml_calls = parse_xml_tool_calls(choice_msg["content"])
                if xml_calls:
                    tool_calls = xml_calls
                    choice_msg["tool_calls"] = xml_calls

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
                        execute_tool(chat_id, fn_name, args_dict),
                        timeout=15.0
                    )
                except asyncio.TimeoutError:
                    tool_result = {"status": "error", "message": f"Tool '{fn_name}' timed out after 15s"}
                except Exception as tool_exc:
                    tool_result = {"status": "error", "message": f"Tool '{fn_name}' error: {str(tool_exc)}"}

                tool_results_list.append(tool_result)

                # Pillar 1 Optimization: Single-turn direct response (Saves ~3,500 tokens on redundant 2nd LLM call!)
                if fn_name in ("create_reminder", "delete_reminder", "manage_smart_list", "save_note_or_fact", "learn_user_correction", "create_packing_list", "find_nearby_places"):
                    if tool_result.get("status") in ("success", "conflict_detected") and tool_result.get("message"):
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
                            "created_at": datetime.now(IST).isoformat()
                        }
                        pending_action = payload
                        save_pending_action(chat_id, "send_email", payload)
                        from main import PENDING_GOOGLE_ACTIONS
                        PENDING_GOOGLE_ACTIONS[chat_id] = payload
                        final_reply = (
                            f"Please confirm — send this email from your connected Gmail?\n\n"
                            f"📤 **To:** {payload['to']}\n"
                            f"📝 **Subject:** {payload['subject']}\n"
                            f"💬 **Message:** {payload['body']}\n\n"
                            f"Reply yes to send or no to cancel."
                        )
                        break

                    elif act in ("upload_file", "drive_file", "upload_to_drive"):
                        payload = {
                            "kind": "upload_file",
                            "action": "upload_file",
                            "filename": tool_result.get("filename", "notes.txt"),
                            "content": tool_result.get("content", ""),
                            "folder_id": tool_result.get("folder_id"),
                            "created_at": datetime.now(IST).isoformat()
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
                            "created_at": datetime.now(IST).isoformat()
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
                            "created_at": datetime.now(IST).isoformat()
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
                    lines = [f"📧 **Aapke inbox me {len(emails)} emails mili hain:**"]
                    for em in emails[:3]:
                        subj = em.get("subject", "No Subject")
                        sender = em.get("from", "")
                        lines.append(f"• **{subj}** (From: {sender})")
                    final_reply = "\n".join(lines)
                else:
                    final_reply = "Aapke connected inbox me koi email nahi mili."
            elif isinstance(last_res, dict) and last_res.get("status") == "success" and last_res.get("message") and not ("in connected mailbox" in str(last_res.get("message"))):
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
        default_fb_msg = "Bataiye, main aapki kya madad karun? Schedule ya kisi update ke baare me bataiye!"

    if not final_reply:
        try:
            # Fallback must ALWAYS preserve the ground truth system prompt and memory block!
            recovery_system = f"{system_content}\n\nCRITICAL INSTRUCTION: Answer the user directly based on the memory and context above in {active_user_lang or "user language"}. Zero medical disclaimers or refusals."
            async with httpx.AsyncClient(timeout=10.0) as fb_client:
                fb_resp = await fb_client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}"},
                    json={
                        "model": "openai/gpt-oss-20b",
                        "temperature": 0.3,
                        "messages": [
                            {"role": "system", "content": recovery_system},
                            {"role": "user", "content": user_text}
                        ],
                        "max_tokens": 512,
                    }
                )
                if fb_resp.status_code == 200:
                    fb_data = fb_resp.json()
                    final_reply = fb_data["choices"][0]["message"].get("content", "").strip()
        except Exception as fb_err:
            print("Conversational recovery error:", repr(fb_err))

    if not final_reply:
        if tool_results_list:
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

    # Phase 3: Zero-Unsupported-Claims Grounding Verifier Pass
    if tool_calls_executed:
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
    final_reply = check_repeat_guard(chat_id, final_reply)

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

    # Record turn
    await asyncio.to_thread(db.log_chat_message, chat_id, "user", user_text)
    await asyncio.to_thread(db.log_chat_message, chat_id, "assistant", final_reply)

    # Continuous background memory extraction
    try:
        schedule_extraction_task(chat_id, user_text, final_reply)
    except Exception as exc:
        print("Background extractor task launch error:", exc)

    # Observability Turn Trace
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
            suspect_reason=suspect_reason
        )
    except Exception as t_err:
        print("Trace logging error:", repr(t_err))

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
