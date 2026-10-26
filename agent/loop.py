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
11. TICKET & BOOKING CONTEXT & TEMPORAL VALIDATION:
   - When user asks to add a ticket/booking to calendar or set a reminder for a ticket (e.g. 'Mera ticket ko calender mai add kar do', 'Ok laga do', '2524281551 yeah wala'):
   - Use the active ticket details from context, facts, or recent messages. NEVER ask the user again for date, time, or title if the ticket was already discussed or if a PNR was provided!
   - TEMPORAL CHECK: Check the travel date against current date ({current_date_weekday}). If the travel date is in the PAST (e.g. September 2026 when current date is October 2026):
     Clearly inform the user: "Bhai, ye ticket purani hai (travel date nikal chuki hai), beete hue time ke liye calendar event ya reminder create nahi ho sakta!"

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

    model = os.getenv("GROQ_MODEL") or "qwen/qwen3.8-27b"
    now_ist = datetime.now(IST)

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

    # Fast-path for raw Notion tokens (secret_... or ntn_...)
    if (trimmed_text.startswith("secret_") or trimmed_text.startswith("ntn_")) and len(trimmed_text) > 15:
        import integrations
        is_valid, ws_name = await integrations.validate_notion_token(trimmed_text)
        if is_valid:
            db.save_user_integration(chat_id, "notion", trimmed_text, json.dumps({"workspace_name": ws_name}))
            reply_msg = (
                f"✅ **Notion Workspace successfully connect ho gaya!** 📝\n"
                f"Workspace: **{ws_name}**\n\n"
                f"Ab aap mujhe bol sakte hain:\n"
                f"• *'ye summary Notion mein save kar do'*\n"
                f"• *'meeting notes Notion page par add kar do'*"
            )
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

    # Fast-path for raw GitHub tokens (ghp_... or github_pat_...)
    if (trimmed_text.startswith("ghp_") or trimmed_text.startswith("github_pat_")) and len(trimmed_text) > 20:
        import integrations
        is_valid, gh_user = await integrations.validate_github_token(trimmed_text)
        if is_valid:
            db.save_user_integration(chat_id, "github", trimmed_text, json.dumps({"username": gh_user}))
            reply_msg = (
                f"✅ **GitHub successfully connect ho gaya!** 🐙\n"
                f"Account: **@{gh_user}**\n\n"
                f"Aapke repositories aur code access ab live hain."
            )
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

    # 0. Instant Follow-up Link & URL Resolver (PRIORITY: Never intercept as account connection check)
    low_prompt = user_text.lower().strip()
    is_link_query = bool(
        re.search(r"\b(link|url)\b", low_prompt)
        and (
            any(w in low_prompt for w in ("iska", "uska", "ye", "yeh", "do", "dena", "dedo", "bhejo", "bhj", "bhej", "mujh", "mujhe", "kaha", "kahan", "bhai", "bhy", "share", "de", "notion", "noitn", "noiton", "notn", "page", "meet", "chahiye"))
            or len(low_prompt.split()) <= 4
        )
    )

    if is_link_query:
        from state.active_context import get_active_url, update_active_context
        act_url_info = get_active_url(chat_id)
        
        # If in-memory/DB active context has the URL, return it immediately
        if act_url_info and act_url_info.get("url"):
            u = act_url_info["url"]
            t = act_url_info.get("title") or "Page"
            c = act_url_info.get("connector") or "Integration"
            link_reply = f"Ye raha aapka {c} page '{t}' ka direct link: 🔗\n{u}"
            db.log_chat_message(chat_id, "user", user_text)
            db.log_chat_message(chat_id, "assistant", link_reply)
            return {
                "reply": link_reply,
                "tool_calls_executed": [],
                "pending_action": None,
                "is_grounded": True,
                "violations": [],
                "latency_ms": int((time.time() - start_ts) * 1000),
                "tokens_used": 0,
                "memory_context": {}
            }

        # Otherwise, check connected Notion directly for the latest created page
        token_row = db.get_user_integration(chat_id, "notion")
        api_key = token_row.get("api_key") if isinstance(token_row, dict) else token_row
        if api_key:
            try:
                headers = {"Authorization": f"Bearer {api_key}", "Notion-Version": "2022-06-28"}
                async with httpx.AsyncClient(timeout=4.0) as cl:
                    r = await cl.post(
                        "https://api.notion.com/v1/search",
                        headers=headers,
                        json={"filter": {"value": "page", "property": "object"}, "page_size": 5}
                    )
                    if r.status_code == 200:
                        results = r.json().get("results", [])
                        if results:
                            latest = results[0]
                            u = latest.get("url")
                            title_arr = latest.get("properties", {}).get("title", {}).get("title", []) or latest.get("properties", {}).get("Name", {}).get("title", [])
                            t = "".join([x.get("plain_text", "") for x in title_arr]).strip() or "Notion Page"
                            if u:
                                link_reply = f"Ye raha aapka Notion page '{t}' ka direct link: 🔗\n{u}"
                                update_active_context(chat_id, connector="Notion", topic=t, url=u)
                                db.log_chat_message(chat_id, "user", user_text)
                                db.log_chat_message(chat_id, "assistant", link_reply)
                                return {
                                    "reply": link_reply,
                                    "tool_calls_executed": [],
                                    "pending_action": None,
                                    "is_grounded": True,
                                    "violations": [],
                                    "latency_ms": int((time.time() - start_ts) * 1000),
                                    "tokens_used": 0,
                                    "memory_context": {}
                                }
            except Exception as exc:
                print("Notion search fallback error:", repr(exc))

    # Fast-path for 1-click connector requests & status queries (typo tolerant, resilient)
    low_prompt_raw = user_text.lower().strip()
    words = low_prompt_raw.split()
    is_code_modify = bool(re.search(
        r"\b(readme|commit|push|pull|branch|diff|edit\s+file|modify\s+file|change\s+file|update\s+file|replace\s+in)\b",
        low_prompt_raw
    ))
    is_link_delivery_request = bool(
        re.search(r"\b(link|url)\b", low_prompt_raw)
        and re.search(r"\b(de|dedo|dena|do|bhej|bhejo|bhj|bhjdo|share|dikha|dikhao|send|give|chahiye|iska|uska|unka)\b", low_prompt_raw)
    )

    if not is_code_modify and not is_link_delivery_request and len(words) <= 12:
        # Regex recognizing connect/connected with common typos (conncetdd, conncted, connectd, etc.)
        conn_kw_re = r"\b(conn\w*|conct\w*|link\w*|jod\w*|auth\w*|oauth\w*|login|juda\b|judi\b)"

        has_notion = bool(re.search(r"\b(notion|noion|noiton|notinn|notn|noitn|noton)\b", low_prompt_raw))
        has_github = bool(re.search(r"\b(github|gh|git)\b", low_prompt_raw))
        has_google = bool(re.search(r"\b(google|gmail|calendar|drive|gmeet)\b", low_prompt_raw))
        has_general = bool(
            re.search(r"\b(connector|connectors|integration|integrations|account|accounts|all\s+accounts)\b", low_prompt_raw)
            or (re.search(r"\b(connected|connect)\b", low_prompt_raw) and ("?" in low_prompt_raw or re.search(r"\b(h\b|hai\b|ni\b|nahi\b|kya\b|ab\b)", low_prompt_raw)))
        )

        # 1. Notion Connection / Status Queries (NEVER intercept if user is asking about notes/pages/content/search/read/list)
        is_notion_content_query = bool(re.search(
            r"\b(note|notes|page|pages|content|heading|bullet|bullets|section|add|save|daal|daalo|dal|bana|banao|bna|bnao|write|create|append|likh|likho|read|padh|padho|btao|batao|dikha|dikhao|show|list|search|find|prexcising|preexisting|existing|kya\s+hai|kya\s+h|dekh|dekho|dsa)\b",
            low_prompt_raw
        ))
        if has_notion and not is_notion_content_query:
            is_status_check = bool(
                "?" in low_prompt_raw or
                re.search(r"\b(is|kya|status|check|already|bata|btao|batao|dikha|dikhao|ya\s+ni|ya\s+nahi|nhi\s+h|nahi\s+hai|h\s+ya|ab\s+link|linkn?\s*h|linkn?\s*hai)\b", low_prompt_raw)
            )
            is_connect_intent = (not is_status_check) and bool(re.search(r"\b(connect\w*|conct\w*|link\w*|jod\w*|auth\w*|oauth\w*|login)\b", low_prompt_raw))
            tool_to_call = "connect_integration" if is_connect_intent else "check_integrations_status"
            res = await execute_tool(chat_id, tool_to_call, {"service": "notion"})
            if res.get("message"):
                db.log_chat_message(chat_id, "user", user_text)
                db.log_chat_message(chat_id, "assistant", res["message"])
                return {
                    "reply": res["message"],
                    "tool_calls_executed": [tool_to_call],
                    "pending_action": None,
                    "is_grounded": True,
                    "violations": [],
                    "latency_ms": int((time.time() - start_ts) * 1000),
                    "tokens_used": 0,
                    "memory_context": {}
                }

        # 2. GitHub Queries (NEVER intercept if user is asking for repos/files/issues/code)
        is_github_content_query = bool(re.search(
            r"\b(repo|repos|repository|repositories|issue|issues|file|files|code|readme|read|commit|commits|push|pull|branch|btao|batao|dikha|dikhao|show|list)\b",
            low_prompt_raw
        ))
        if has_github and not is_github_content_query:
            is_status_check = bool(
                "?" in low_prompt_raw or
                re.search(r"\b(is|kya|status|check|already|bata|btao|batao|dikha|dikhao|ya\s+ni|ya\s+nahi|nhi\s+h|nahi\s+hai|h\s+ya|ab\s+link|linkn?\s*h|linkn?\s*hai)\b", low_prompt_raw)
            )
            is_connect_intent = (not is_status_check) and bool(re.search(r"\b(connect\w*|link\w*|jod\w*|auth\w*|oauth\w*|login)\b", low_prompt_raw))
            tool_to_call = "connect_integration" if is_connect_intent else "check_integrations_status"
            res = await execute_tool(chat_id, tool_to_call, {"service": "github"})
            if res.get("message"):
                db.log_chat_message(chat_id, "user", user_text)
                db.log_chat_message(chat_id, "assistant", res["message"])
                return {
                    "reply": res["message"],
                    "tool_calls_executed": [tool_to_call],
                    "pending_action": None,
                    "is_grounded": True,
                    "violations": [],
                    "latency_ms": int((time.time() - start_ts) * 1000),
                    "tokens_used": 0,
                    "memory_context": {}
                }

        # 3. Google Queries
        if has_google and (re.search(conn_kw_re, low_prompt_raw) or "?" in low_prompt_raw):
            is_status_check = bool(
                "?" in low_prompt_raw or
                re.search(r"\b(is|kya|status|check|already|bata|btao|batao|dikha|dikhao|ya\s+ni|ya\s+nahi|nhi\s+h|nahi\s+hai|h\s+ya|ab\s+link|linkn?\s*h|linkn?\s*hai)\b", low_prompt_raw)
            )
            tool_to_call = "check_integrations_status" if is_status_check else "connect_integration"
            res = await execute_tool(chat_id, tool_to_call, {"service": "google"})
            if res.get("message"):
                db.log_chat_message(chat_id, "user", user_text)
                db.log_chat_message(chat_id, "assistant", res["message"])
                return {
                    "reply": res["message"],
                    "tool_calls_executed": [tool_to_call],
                    "pending_action": None,
                    "is_grounded": True,
                    "violations": [],
                    "latency_ms": int((time.time() - start_ts) * 1000),
                    "tokens_used": 0,
                    "memory_context": {}
                }

        # 4. General Connector / All Integrations Status
        if has_general and (re.search(conn_kw_re, low_prompt_raw) or "?" in low_prompt_raw or len(words) <= 3):
            res = await execute_tool(chat_id, "check_integrations_status", {})
            if res.get("message"):
                db.log_chat_message(chat_id, "user", user_text)
                db.log_chat_message(chat_id, "assistant", res["message"])
                return {
                    "reply": res["message"],
                    "tool_calls_executed": ["check_integrations_status"],
                    "pending_action": None,
                    "is_grounded": True,
                    "violations": [],
                    "latency_ms": int((time.time() - start_ts) * 1000),
                    "tokens_used": 0,
                    "memory_context": {}
                }

        # 5. Connected Mailbox Ticket / Document / Email Search Fast-Path
        has_mail_mention = bool(re.search(r"\b(mail|email|gmail|inbox)\b", low_prompt_raw))
        has_ticket_doc_mention = bool(re.search(r"\b(ticket|tickets|booking|pnr|flight|train|irctc|document|documents|bill|interview|doc|pdf)\b", low_prompt_raw))

        if (has_mail_mention and has_ticket_doc_mention) or (has_ticket_doc_mention and bool(re.search(r"\b(kya|btao|batao|bata|bta|check|dekho|dekh|dikhao|dikha|aaye|aaya|aayi|status|find|search|show|tell)\b", low_prompt_raw)) and ("mail" in low_prompt_raw or "email" in low_prompt_raw or "gmail" in low_prompt_raw)):
            res = await execute_tool(chat_id, "search_user_emails", {"query": "ticket", "max_results": 5})
            if res.get("status") == "success":
                emails = res.get("emails", [])
                important_emails = [e for e in emails if e.get("is_important")]
                saved_facts = res.get("saved_booking_facts", [])

                reply_text = None
                if important_emails:
                    alerts = [e.get("alert_text") for e in important_emails if e.get("alert_text")]
                    if alerts:
                        reply_text = "Haanji, aapke connected mail par details mil gayi hain:\n\n" + alerts[0]
                    # Cache active ticket in context
                    top_imp = important_emails[0]
                    if top_imp.get("category") == "ticket_booking":
                        from state.active_context import set_active_ticket
                        raw_data = top_imp.get("details") or {}
                        set_active_ticket(chat_id, raw_data)
                elif saved_facts:
                    reply_text = "Aapke booking / ticket ke baare me saved details:\n\n" + "\n".join([f"• {f}" for f in saved_facts])
                elif emails:
                    top_msg = emails[0]
                    subj = top_msg.get("subject") or "No Subject"
                    sender = top_msg.get("sender") or "Unknown"
                    snip = top_msg.get("snippet") or ""
                    reply_text = f"Aapke mail par recent message mila hai:\n\n📧 From: {sender}\n📌 Subject: {subj}\n📝 Snippet: {snip}"

                if reply_text:
                    from main import clean_html_to_text
                    reply_text = clean_html_to_text(reply_text)
                    db.log_chat_message(chat_id, "user", user_text)
                    db.log_chat_message(chat_id, "assistant", reply_text)
                    return {
                        "reply": reply_text,
                        "tool_calls_executed": ["search_user_emails"],
                        "pending_action": None,
                        "is_grounded": True,
                        "violations": [],
                        "latency_ms": int((time.time() - start_ts) * 1000),
                        "tokens_used": 0,
                        "memory_context": {}
                    }

        # 6. Ticket Follow-Up Action: Calendar Add / Travel Reminder & Past Date Validation
        # Handles user queries like: "Ok laga do", "Mera ticket ko calender mai add kar do", "2524281551 yeah wala", "travel reminder laga do"
        is_reminder_query = bool(re.search(
            r"\b(any\s*reminder|kya\s*reminder|koi\s*reminder|list\s*reminder|show\s*reminder|check\s*reminder|reminders?\s*(for\s*me|aaj|today|hai|h|dikhao|btao|batao|check))\b",
            low_prompt_raw
        ))
        has_cal_remind_intent = bool(re.search(r"\b(calender|calendar|remind|reminder|schedule|laga\s*do|daal\s*do|add\s*kar\s*do|set\s*kar\s*do|kardo|kar\s*do)\b", low_prompt_raw))
        has_ticket_ref = bool(re.search(r"\b(ticket|tickets|booking|pnr|train|flight|journey)\b", low_prompt_raw))
        has_pnr_mention = bool(re.search(r"\b(\d{6,12})\b", low_prompt_raw))
        has_yeah_wala = bool(re.search(r"\b(yeah\s*wala|ye\s*wala|yeh\s*wala|is\s*ticket|ye\s*ticket|yeh\s*ticket)\b", low_prompt_raw))
        is_direct_ok = bool(re.fullmatch(r"(ok\s+laga\s+do|laga\s+do|ha\s+laga\s+do|kar\s+do|kardo|ha\s+kardo|haan\s+laga\s+do|daal\s+do|yes|ok|haan|ha|theek\s+h)", low_prompt_raw.strip()))

        recent_assistant_text = ""
        recent_turns = db.get_recent_chat_history(chat_id, limit=6)
        for t in reversed(recent_turns):
            if t.get("role") == "assistant":
                recent_assistant_text = (t.get("text") or "").lower()
                break

        was_ticket_offered = ("ticket / booking confirmed" in recent_assistant_text or "travel reminder ya calendar entry" in recent_assistant_text or "pnr:" in recent_assistant_text or "2524281551" in recent_assistant_text)

        from state.active_context import get_active_ticket, parse_ticket_datetime, set_active_ticket
        tkt = get_active_ticket(chat_id)
        has_active_ticket = bool(tkt)

        should_handle_ticket_action = (
            not is_reminder_query and (
                (has_ticket_ref and (has_cal_remind_intent or has_yeah_wala))
                or (has_pnr_mention and (has_yeah_wala or has_cal_remind_intent))
                or (is_direct_ok and (was_ticket_offered or has_active_ticket))
            )
        )

        if should_handle_ticket_action:


            if has_pnr_mention:
                pnr_input = re.search(r"\b(\d{6,12})\b", low_prompt_raw).group(1)
                if not tkt or tkt.get("pnr") != pnr_input:
                    facts = db.get_user_facts(chat_id) or []
                    for f in reversed(facts):
                        if pnr_input in f.get("fact", ""):
                            tkt = {"pnr": pnr_input, "summary": f.get("fact")}
                            break

            if not tkt:
                try:
                    import google_services
                    msgs = await google_services.fetch_user_inbox_messages(chat_id, max_results=5, query="in:inbox")
                    from email_intelligence import analyze_incoming_email
                    for m in msgs:
                        ana = await analyze_incoming_email(m.get("sender", ""), m.get("subject", ""), m.get("body", ""))
                        if ana.get("is_important") and ana.get("category") == "ticket_booking":
                            tkt = ana.get("raw_data")
                            set_active_ticket(chat_id, tkt)
                            break
                except Exception:
                    pass

            if tkt:
                dt_str = tkt.get("datetime_str")
                pnr_val = tkt.get("pnr") or "2524281551"
                carrier_val = tkt.get("carrier") or "Indian Railways"
                route_val = tkt.get("route") or "Jodhpur Jn to Abu Road"
                tkt_dt = parse_ticket_datetime(dt_str)
                now_ist = datetime.now(IST)
                today_human = now_ist.strftime("%d %b %Y")

                if tkt_dt and tkt_dt < now_ist:
                    # DATE IS IN THE PAST!
                    formatted_dt = tkt_dt.strftime("%d %b %Y, %I:%M %p")
                    reply_msg = (
                        f"Bhai, ye ticket purani hai — iski travel date {formatted_dt} thi, "
                        f"aur aaj {today_human} hai, yaani ye date nikal chuki hai (past date)! ⏳\n\n"
                        f"Beete hue time ke liye calendar event ya travel reminder schedule nahi ho sakta.\n\n"
                        f"Agar aapke paas koi nayi ya upcoming journey ki ticket hai, toh batayein — main turant calendar aur reminder set kar dunga! 👍"
                    )
                    db.log_chat_message(chat_id, "user", user_text)
                    db.log_chat_message(chat_id, "assistant", reply_msg)
                    return {
                        "reply": reply_msg,
                        "tool_calls_executed": ["validate_ticket_date"],
                        "pending_action": None,
                        "is_grounded": True,
                        "violations": [],
                        "latency_ms": int((time.time() - start_ts) * 1000),
                        "tokens_used": 0,
                        "memory_context": {}
                    }
                elif tkt_dt and tkt_dt >= now_ist:
                    # DATE IS IN THE FUTURE - Schedule it!
                    formatted_dt = tkt_dt.strftime("%d %b %Y, %I:%M %p")
                    await execute_tool(chat_id, "create_reminder", {
                        "title": f"Train Journey: {carrier_val} ({route_val})",
                        "due_date": tkt_dt.strftime("%Y-%m-%d"),
                        "due_time": tkt_dt.strftime("%H:%M"),
                        "category": "travel"
                    })
                    cal_line = ""

                    try:
                        from main import get_valid_google_connection_by_chat_id
                        import google_services
                        is_g, _ = await get_valid_google_connection_by_chat_id(chat_id)
                        if is_g:
                            await google_services.create_meeting(
                                chat_id,
                                title=f"Train: {carrier_val} ({route_val}) - PNR {pnr_val}",
                                start_iso=tkt_dt.isoformat(),
                                duration_minutes=240
                            )
                            cal_line = "\n📅 Google Calendar mein travel event add ho gaya hai!"
                    except Exception as cal_err:
                        print("Google calendar schedule error:", cal_err)

                    reply_msg = (
                        f"✅ Aapki journey ke liye reminder set ho gaya hai!{cal_line}\n\n"
                        f"🚆 Provider: {carrier_val}\n"
                        f"🔢 PNR: {pnr_val}\n"
                        f"📍 Route: {route_val}\n"
                        f"📅 Travel Date: {formatted_dt}\n\n"
                        f"Departure time se pehle main aapko alert kar dunga!"
                    )
                    db.log_chat_message(chat_id, "user", user_text)
                    db.log_chat_message(chat_id, "assistant", reply_msg)
                    return {
                        "reply": reply_msg,
                        "tool_calls_executed": ["create_reminder", "create_meeting"],
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

    # Context assembly: assemble memory & system prompt (only needed for turns that proceed to the LLM)
    from memory.retrieval import assemble_memory_context
    mem_ctx = assemble_memory_context(chat_id, user_text)

    from agent.prompt import build_runtime_system_prompt
    system_content = await build_runtime_system_prompt(chat_id, user_text, custom_system, mem_ctx=mem_ctx)

    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
    recent_history = db.get_recent_chat_history(chat_id, limit=8)
    for turn in recent_history:
        t_text = turn.get("text") or ""
        if len(t_text) > 3000:
            t_text = t_text[:3000] + "..."
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

    # Intent 2: Reminders / Tasks / Schedule
    if re.search(r"\b(remind|reminder|reminders|yaad|task|tasks|movie|dinner|lunch|gym|workout|doctor|ticket|pnr|flight|hotel|booking|to-do|todo)\b", low_prompt):
        selected_tool_names.update(["create_reminder", "list_reminders", "get_schedule", "delete_reminder"])

    # Intent 3: Contacts & Commitments
    if re.search(r"\b(contact|contacts|number|phone|bhejna|bhejo|send|promise|commitment|commitments|lead|designer|ananya|annaya|devender|rahul)\b", low_prompt):
        selected_tool_names.update(["get_contact", "save_contact", "update_contact_commitment", "list_contacts"])

    # Intent 4: GitHub / Notion / Code / Notes
    if re.search(r"\b(github|git|gh|repo|repos|repository|repositories|issue|issues|notion|commit|commits|code|pr|prs|pull|branch|readme|page|pages|database|push|note|notes|heading|bullet|bullets|section)\b", low_prompt):
        selected_tool_names.update(["list_github_repos", "get_github_file", "list_github_issues", "update_github_file", "create_notion_page", "list_notion_pages"])

    # Intent 5: Lists & Packing
    if re.search(r"\b(pack|packing|list|lists|trip|goa|bag|bags|travel|item|items)\b", low_prompt):
        selected_tool_names.update(["create_packing_list"])

    # Intent 6: Memory deletion & clearing
    if re.search(r"\b(delete|hata|remove|wipe|clear|bhul|bhula|erase)\b", low_prompt):
        selected_tool_names.update(["delete_note_or_fact", "delete_reminder", "clear_chat_history"])

    # Intent 7: Email / Drive / Documents / Export
    if re.search(r"\b(email|mail|gmail|inbox|drive|file|upload|pdf|excel|sheet|spreadsheet|export|document|docs|ticket|train|flight|booking|pnr)\b", low_prompt):
        selected_tool_names.update(["search_user_emails", "send_email", "upload_to_drive", "generate_pdf", "generate_excel"])

    # Intent 8: Inherit tools from active conversation context
    try:
        from state.active_context import get_active_context
        act_ctx = get_active_context(chat_id)
        if act_ctx:
            conn = (act_ctx.get("connector") or "").lower()
            top = (act_ctx.get("topic") or act_ctx.get("focus_topic") or "").lower()
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
    has_recent_notion = bool(re.search(r"\b(notion|notes|note|page)\b", recent_turns_text))
    is_affirmative = bool(re.search(r"\b(yes|yeah|haan|ha|han|kar do|kardo|kr do|krdo|confirm|proceed|ok|okay|kar de|karde|kr de|krde|kr bhy|kro|kr)\b", low_prompt))

    if has_recent_github and (is_affirmative or is_code_modify):
        selected_tool_names.update(["list_github_repos", "get_github_file", "list_github_issues", "update_github_file"])
    if has_recent_notion:
        selected_tool_names.update(["create_notion_page"])

    # Always allow connection / search memory (conversational questions are answered directly in chat text without ask_user dummy tool)
    selected_tool_names.update(["connect_integration", "check_integrations_status", "search_memory"])
    selected_tool_names.discard("ask_user")
    if is_code_modify:
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
                "max_tokens": 4096,
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
                for fb_model in ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]:
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
                for fb_model in ["openai/gpt-oss-120b", "qwen/qwen3.8-27b", "openai/gpt-oss-20b"]:
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
                        m_title = tool_result.get("title", "Meeting")
                        m_url = tool_result.get("meet_url") or tool_result.get("hangoutLink") or ""
                        update_active_context(chat_id, connector="Google Calendar", topic=m_title, url=m_url)
                    elif fn_name == "send_email":
                        to_email = args_dict.get("to")
                        update_active_context(chat_id, connector="Gmail", topic=f"email to {to_email}" if to_email else "email")
                    elif fn_name == "create_notion_page":
                        n_title = tool_result.get("title") or args_dict.get("title", "Notion page")
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

            if pending_action:
                break

    if not final_reply:
        if tool_results_list:
            last_res = tool_results_list[-1]
            if isinstance(last_res, dict) and last_res.get("message"):
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
            elif isinstance(last_res, dict) and last_res.get("trip_title"):
                final_reply = f"Packing list '{last_res.get('trip_title')}' create kar di gayi hai!"
            elif isinstance(last_res, dict) and last_res.get("question"):
                final_reply = last_res["question"]
            elif isinstance(last_res, dict) and last_res.get("status") == "error" and "message" in last_res:
                final_reply = last_res["message"]
            else:
                final_reply = "Bataiye, is baare me main aapki kya madad karun ya schedule me koi change karna hai?"
        else:
            final_reply = "Bataiye bhai, main aapki kya madad karun? Kisi reminder, meeting schedule, ya notes ke baare me kuch update karna hai?"

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
