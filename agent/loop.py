"""
PeculiarAI V2 Agent Execution Loop
Native tool-calling loop using Groq (openai/gpt-oss-120b or llama-3.3-70b-versatile).
Strict Zero-Hallucination Grounding Contract: Model acts via tools or acknowledges missing data.
"""
import os
import re
import json
import time
import asyncio
import httpx
from datetime import datetime
from zoneinfo import ZoneInfo
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

from timing import IST
import db
from agent.tools import TOOL_DEFINITIONS, execute_tool
from memory.retrieval import assemble_memory_context
from memory.extractor import extract_and_persist_turn
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
7. Answer in natural, friendly Hinglish (Roman English script, e.g. 'Haan bhai, bilkul!').
8. Keep replies concise and direct (1-3 sentences) unless an in-depth breakdown is requested.
9. THIRD-PARTY CONNECTORS & INTEGRATIONS (GitHub, Notion, Google):
   - When user asks whether an account or integration is connected (e.g. 'bhy mera github account connected h?', 'kya google connected hai?'), ALWAYS call `check_integrations_status` tool to check live status.
   - When user asks to connect Google, or says 'connect google', 'link my email', or asks to schedule/host a Google Calendar / Meet meeting when Google is not connected: ALWAYS call `connect_integration` with service='google' immediately! DO NOT call `ask_user` to ask if they want to connect — they already asked!
   - NEVER ask the user for their Google email address or password to connect Google! Google OAuth requires the user to click the official authorization link.
   - If the user sends an email address (e.g. 'xyz@gmail.com') wishing to connect Google, DO NOT say you cannot connect Google! Call `connect_integration` with service='google' and return the official OAuth link so the user can authorize.
   - When user asks to send an email, upload a file, or create a calendar event: ALWAYS call the respective tool (send_email, upload_to_drive, create_meeting) directly! NEVER call `ask_user` to ask whether you should execute what the user just asked for.
   - When user asks to connect or link GitHub (e.g. 'mera github link krde', 'github link krde', 'connect github', 'github connect kar do') or sends a GitHub profile/token: ALWAYS call `connect_integration` with service='github'! DO NOT call `ask_user` to ask for username or URL — calling `connect_integration(service='github')` immediately returns the official 1-click OAuth link!
   - When user asks to list or show their GitHub repositories (e.g. 'mere github ke sarre repositry naam btao'), ALWAYS call `list_github_repos`. If username was given or mentioned in recent turns (like 'nontech-cpu'), pass it as `username`.
   - When user asks to check, inspect, or see if a file/README exists in a repo (e.g. 'php-project m readme file h kya dkhkr btaa', 'readme check karo'): ALWAYS call `get_github_file(repo=..., path="README.md")` autonomously! NEVER ask the user to share the file link or copy-paste file content.
   - When user asks to update, create, or commit a file/README (e.g. 'readme update kar do', 'commit kar do', 'ye line add kar do'), OR when user confirms a previous draft (e.g. 'ha kr do', 'kardo', 'proceed'): ALWAYS call `update_github_file` autonomously. NEVER ask redundant confirmation questions like 'Would you like to proceed?' or 'Confirm updating?'. The commit is non-destructive, so execute it immediately and report the live commit URL!
10. PROACTIVE TIME CONFLICT DETECTION & ALERTING:
   - When user asks to schedule a reminder, meeting, or event (e.g. '5 bje movie me jana h remind kar dena') and they ALREADY have an event or reminder at that time (visible in Today's / Tomorrow's schedule or tool output has_conflict=True):
   - ALWAYS highlight the clash in your reply! E.g.
     "Reminder set kar diya hai! 🎬\n⚠️ **Time Conflict Warning:** Bhai, aaj shaam 5:00 PM par already aapka 'Meeting' scheduled hai! Kya aap movie ka time change karna chahenge ya meeting reschedule karein?"

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


async def run_agent_turn(chat_id: int, user_text: str, custom_system: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a single user turn through the V2 Tool-Calling Agent Loop.
    Returns:
        {
            "reply": str,
            "tool_calls_executed": list[str],
            "pending_action": dict | None,
            "latency_ms": int,
            "tokens_used": int
        }
    """
    start_ts = time.time()
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("GROQ_API_KEY environment variable is missing.")

    model = os.getenv("GROQ_MODEL") or "openai/gpt-oss-20b"
    now_ist = datetime.now(IST)
    from memory.retrieval import assemble_memory_context
    mem_ctx = assemble_memory_context(chat_id, user_text)

    from agent.prompt import build_runtime_system_prompt
    system_content = await build_runtime_system_prompt(chat_id, user_text, custom_system, mem_ctx=mem_ctx)

    # Check if user sent just a raw email address (e.g. attempting to link Google)
    trimmed_text = user_text.strip()
    if re.fullmatch(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", trimmed_text):
        from main import get_valid_google_connection_by_chat_id
        is_g_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
        if not is_g_conn:
            from google_auth import build_auth_url
            try:
                auth_url = build_auth_url(int(chat_id), source="tg")
                reply_msg = (
                    f"Google account connect karne ke liye Google security guidelines ke anusaar official login link par authorize karna zaroori hota hai (chat mein email type karke access nahi hota).\n\n"
                    f"👉 Kripya is secure link par click karke Google account connect karein:\n{auth_url}\n\n"
                    f"Connect hone ke baad aap meetings host kar payenge aur calendar events create ho jayenge!"
                )
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
            except Exception as exc:
                print("Email auth url generation error:", repr(exc))

    # Check for active pending action (Phase 5: Persistent Multi-Turn State)
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
                f"Duration: {payload.get('duration_minutes', 30)} minutes\n"
                f"Attendees: {', '.join(payload.get('attendees', [])) if payload.get('attendees') else 'None'}\n\n"
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
        elif intent == "cross_question":
            is_cross_q = True

    # Fast-path for 1-click connector requests (ONLY if user is explicitly asking to connect/link, not performing repo/file actions)
    low_prompt_raw = user_text.lower().strip()
    words = low_prompt_raw.split()
    is_action_prompt = bool(re.search(
        r"\b(readme|update|commit|push|file|files|issue|issues|pr|prs|repo|repos|repository|repositories|replace|change|edit|check|dikh|btao|batao|kya hai|add|create|code|diff)\b",
        low_prompt_raw
    ))

    if len(words) <= 8 and not is_action_prompt:
        if re.search(r"\b(github|gh|git)\b", low_prompt_raw) and re.search(r"\b(connect|link|jod|jodo|authorize|oauth)\b", low_prompt_raw):
            res = await execute_tool(chat_id, "connect_integration", {"service": "github"})
            if res.get("status") == "success" and res.get("message"):
                db.log_chat_message(chat_id, "user", user_text)
                db.log_chat_message(chat_id, "assistant", res["message"])
                return {
                    "reply": res["message"],
                    "tool_calls_executed": ["connect_integration"],
                    "pending_action": None,
                    "is_grounded": True,
                    "violations": [],
                    "latency_ms": int((time.time() - start_ts) * 1000),
                    "tokens_used": 0,
                    "memory_context": {}
                }

        if re.search(r"\b(google|gmail|calendar)\b", low_prompt_raw) and re.search(r"\b(connect|link|jod|jodo|authorize|oauth)\b", low_prompt_raw):
            res = await execute_tool(chat_id, "connect_integration", {"service": "google"})
            if res.get("status") == "success" and res.get("message"):
                db.log_chat_message(chat_id, "user", user_text)
                db.log_chat_message(chat_id, "assistant", res["message"])
                return {
                    "reply": res["message"],
                    "tool_calls_executed": ["connect_integration"],
                    "pending_action": None,
                    "is_grounded": True,
                    "violations": [],
                    "latency_ms": int((time.time() - start_ts) * 1000),
                    "tokens_used": 0,
                    "memory_context": {}
                }

    # Trigger typing indicator on Telegram so user sees active thinking status ("Peculiaring...")
    try:
        tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
        if tg_token and chat_id and chat_id > 0:
            async def _typing():
                try:
                    async with httpx.AsyncClient(timeout=2.0) as c:
                        await c.post(f"https://api.telegram.org/bot{tg_token}/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
                except Exception:
                    pass
            asyncio.create_task(_typing())
    except Exception:
        pass

    # Context assembly: recent conversation turns (compact 6 turns to avoid prompt bloat)
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
    recent_history = db.get_recent_chat_history(chat_id, limit=6)
    for turn in recent_history:
        t_text = turn.get("text") or ""
        if len(t_text) > 250:
            t_text = t_text[:250] + "..."
        messages.append({"role": turn["role"], "content": t_text})

    # Append current user prompt
    messages.append({"role": "user", "content": user_text})

    tool_calls_executed = []
    tool_results_list = []
    pending_action = None
    final_reply = ""
    total_tokens = 0

    # Dynamic intent-based tool pruning to keep token usage within Groq 8,000 TPM limit
    low_prompt = user_text.lower()
    selected_tool_names = set()

    # Intent 1: Calendar / Meetings / Schedule
    if re.search(r"\b(meeting|meetings|schedule|call|calls|meet|slot|slots|free|busy|appointment|appointments|aaj|kal|parso|plan|plans|calendar|event|events|timing|time)\b", low_prompt):
        selected_tool_names.update(["create_meeting", "check_availability", "get_schedule", "delete_meeting", "create_reminder"])

    # Intent 2: Reminders / Tasks
    if re.search(r"\b(remind|reminder|reminders|yaad|task|tasks|movie|dinner|lunch|gym|workout|doctor|ticket|pnr|flight|hotel|booking|to-do|todo)\b", low_prompt):
        selected_tool_names.update(["create_reminder", "list_reminders", "delete_reminder"])

    # Intent 3: Contacts & Commitments
    if re.search(r"\b(contact|contacts|number|phone|bhejna|bhejo|send|promise|commitment|commitments|lead|designer|ananya|annaya|devender|rahul)\b", low_prompt):
        selected_tool_names.update(["get_contact", "save_contact", "update_contact_commitment", "list_contacts"])

    # Intent 4: GitHub / Notion / Code
    if re.search(r"\b(github|git|gh|repo|repos|repository|repositories|issue|issues|notion|commit|commits|code|pr|prs|pull|branch|readme|page|database|push)\b", low_prompt):
        selected_tool_names.update(["list_github_repos", "get_github_file", "list_github_issues", "update_github_file", "create_notion_page"])

    # Intent 5: Lists & Packing
    if re.search(r"\b(pack|packing|list|lists|trip|goa|bag|bags|travel|item|items)\b", low_prompt):
        selected_tool_names.update(["create_packing_list"])

    # Intent 6: Memory deletion & clearing
    if re.search(r"\b(delete|hata|remove|wipe|clear|bhul|bhula|erase)\b", low_prompt):
        selected_tool_names.update(["delete_note_or_fact", "delete_reminder", "clear_chat_history"])

    # Intent 7: Email / Drive / Documents / Export
    if re.search(r"\b(email|mail|gmail|inbox|drive|file|upload|pdf|excel|sheet|spreadsheet|export|document|docs)\b", low_prompt):
        selected_tool_names.update(["send_email", "upload_to_drive", "generate_pdf", "generate_excel"])

    # Intent 8: Inherit tools from active conversation context
    try:
        from state.active_context import get_active_context
        act_ctx = get_active_context(chat_id)
        if act_ctx:
            conn = (act_ctx.get("connector") or "").lower()
            top = (act_ctx.get("focus_topic") or "").lower()
            if "github" in conn or "github" in top or "repo" in top or "issue" in top:
                selected_tool_names.update(["list_github_repos", "get_github_file", "list_github_issues", "update_github_file"])
            if "notion" in conn or "notion" in top:
                selected_tool_names.update(["create_notion_page"])
            if "calendar" in conn or "meeting" in top or "schedule" in top:
                selected_tool_names.update(["create_meeting", "check_availability", "get_schedule", "delete_meeting"])
            if "gmail" in conn or "email" in top:
                selected_tool_names.update(["send_email"])
    except Exception:
        pass

    # Intent 9: Follow-up confirmation / affirmative intent for GitHub / Notion / Meeting actions
    recent_turns_text = " ".join([t.get("text", "") for t in recent_history[-4:]]).lower()
    has_recent_github = bool(re.search(r"\b(github|repo|repos|repository|readme|commit|issue|pull|pr|php-project)\b", recent_turns_text))
    is_affirmative = bool(re.search(r"\b(yes|yeah|haan|ha|han|kar do|kardo|kr do|krdo|confirm|proceed|ok|okay|kar de|karde|kr de|krde|kr bhy|kro|kr)\b", low_prompt))

    if has_recent_github and (is_affirmative or is_action_prompt):
        selected_tool_names.update(["list_github_repos", "get_github_file", "list_github_issues", "update_github_file"])

    # Always allow connection / asking user if needed
    selected_tool_names.update(["connect_integration", "check_integrations_status", "ask_user", "search_memory"])
    if is_action_prompt:
        selected_tool_names.discard("connect_integration")
        selected_tool_names.discard("ask_user")

    # If no specific intent was triggered, allow core essentials
    if len(selected_tool_names) <= 4:
        selected_tool_names.update(["create_reminder", "get_schedule", "get_contact", "save_note_or_fact", "list_github_repos", "update_github_file"])

    effective_tools = [
        t for t in TOOL_DEFINITIONS
        if t["function"]["name"] in selected_tool_names
    ]

    MAX_TOOL_ITERATIONS = 4
    current_model = model
    async with httpx.AsyncClient(timeout=30.0) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            payload = {
                "model": current_model,
                "temperature": 0.1,
                "messages": messages,
            }
            if effective_tools:
                payload["tools"] = effective_tools
                payload["tool_choice"] = "auto"

            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json=payload
            )

            # Multi-tier auto-fallback on 429 Rate Limit (e.g. 8k TPM limit on gpt-oss models)
            if resp.status_code == 429:
                for fb_model in ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]:
                    if current_model != fb_model:
                        print(f"Agent Loop 429 Rate Limit on {current_model}. Switching to fallback model {fb_model}...")
                        current_model = fb_model
                        payload["model"] = current_model
                        await asyncio.sleep(1.0)
                        resp = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {groq_key}"},
                            json=payload
                        )
                        if resp.status_code == 200:
                            break

            if resp.status_code != 200:
                print("Agent Loop Groq Error:", resp.status_code, resp.text[:300])
                for fb_model in ["qwen/qwen3.8-27b", "openai/gpt-oss-20b"]:
                    if current_model != fb_model:
                        current_model = fb_model
                        payload["model"] = current_model
                        resp = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {groq_key}"},
                            json=payload
                        )
                        if resp.status_code == 200:
                            break
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
            for tc in tool_calls:
                call_id = tc.get("id") or f"call_{int(time.time())}"
                fn_name = tc["function"]["name"]
                raw_args = tc["function"].get("arguments", "{}")
                tool_calls_executed.append(fn_name)

                try:
                    args_dict = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except Exception:
                    args_dict = {}

                # Execute tool
                tool_result = await execute_tool(chat_id, fn_name, args_dict)
                tool_results_list.append(tool_result)

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
                        update_active_context(chat_id, connector="Google Calendar", topic="schedule")
                    elif fn_name == "send_email":
                        to_email = args_dict.get("to")
                        update_active_context(chat_id, connector="Gmail", topic=f"email to {to_email}" if to_email else "email")
                    elif fn_name == "create_notion_page":
                        n_title = args_dict.get("title", "Notion page")
                        update_active_context(chat_id, connector="Notion", topic=n_title, objects=[n_title])
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

            if pending_action:
                break

    if not final_reply:
        if tool_results_list:
            last_res = tool_results_list[-1]
            if last_res.get("status") == "success" and "message" in last_res:
                final_reply = last_res["message"]
            elif "commitment" in last_res:
                c_name = last_res.get("contact", "Contact")
                final_reply = f"{c_name} ke liye note/commitment save kar liya gaya hai: '{last_res.get('commitment')}'."
            elif last_res.get("found") and last_res.get("phone"):
                final_reply = f"{last_res.get('name', 'Contact')} ka phone number **{last_res.get('phone')}** hai."
            elif last_res.get("found") and last_res.get("email"):
                final_reply = f"{last_res.get('name', 'Contact')} ka email **{last_res.get('email')}** hai."
            elif last_res.get("status") == "success" and last_res.get("title"):
                t_name = last_res.get("title", "Item")
                t_time = last_res.get("due_time") or ""
                t_date = last_res.get("due_date") or ""
                time_part = f" at {t_time}" if t_time else ""
                date_part = f" ({t_date})" if t_date else ""
                final_reply = f"Reminder set ho gaya hai! 📅 **{t_name}**{date_part}{time_part}."
            elif last_res.get("trip_title"):
                final_reply = f"Packing list '{last_res.get('trip_title')}' create kar di gayi hai!"
            elif last_res.get("status") == "error" and "message" in last_res:
                final_reply = last_res["message"]
            else:
                final_reply = "Aapka request note kar liya gaya hai aur update ho gaya hai!"
        else:
            final_reply = "Main aapki reminders, meetings aur contacts track karne me madad karta hu. Aap bataiye kya karna hai?"

    if "<tool_call>" in final_reply:
        final_reply = re.sub(r"<tool_call>.*?</tool_call>", "", final_reply, flags=re.DOTALL | re.IGNORECASE).strip()
        if not final_reply and tool_results_list:
            last_res = tool_results_list[-1]
            final_reply = last_res.get("message") or "Action successfully complete ho gaya!"

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
    is_grounded, verified_reply, violations = await verify_and_ground_reply(
        draft_reply=final_reply,
        tool_results=tool_results_list,
        memory_context=mem_ctx,
        user_query=user_text
    )
    if not is_grounded:
        print(f"Grounding Verifier intercepted unsupported claims: {violations}")
        final_reply = verified_reply

    # Guardrail: Fake-Success Prevention in code
    is_fake_ok, fake_err = check_fake_success_guard(final_reply, tool_results_list)
    if not is_fake_ok:
        print(f"Fake Success Guard intercepted: {fake_err}")
        violations.append(fake_err)
        final_reply = "Aapka request note kar liya gaya hai. Kripya details confirm karein taaki main ise proceed kar sakun."

    # Guardrail: Repeat Suppression
    final_reply = check_repeat_guard(chat_id, final_reply)

    latency_ms = int((time.time() - start_ts) * 1000)

    # Record turn
    db.log_chat_message(chat_id, "user", user_text)
    db.log_chat_message(chat_id, "assistant", final_reply)

    # Continuous background memory extraction
    try:
        asyncio.create_task(extract_and_persist_turn(chat_id, user_text, final_reply))
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
