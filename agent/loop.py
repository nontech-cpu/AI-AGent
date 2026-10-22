"""
PeculiarAI V2 Agent Execution Loop
Native tool-calling loop using Groq (openai/gpt-oss-120b or llama-3.3-70b-versatile).
Strict Zero-Hallucination Grounding Contract: Model acts via tools or acknowledges missing data.
"""
import os
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
   - When user sends a GitHub URL or username (e.g. 'https://github.com/nontech-cpu', 'nontech-cpu') or asks to connect GitHub/Notion, ALWAYS call `connect_integration` with service='github' and the value. NEVER call `save_note_or_fact` for profile URLs or connectors!
   - When user asks to list or show their GitHub repositories (e.g. 'mere github ke sarre repositry naam btao'), ALWAYS call `list_github_repos`. If username was given or mentioned in recent turns (like 'nontech-cpu'), pass it as `username`.

CURRENT TEMPORAL CONTEXT:
- Current Time: {current_time_ist} (India Time / IST)
- Current Date & Weekday: {current_date_weekday}
"""

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
    from agent.prompt import build_runtime_system_prompt
    system_content = await build_runtime_system_prompt(chat_id, user_text, custom_system)

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

    # Layered Cognitive Memory (Entities, Aliases, Commitments, Facts)
    mem_ctx = assemble_memory_context(chat_id, user_text)

    # Context assembly: recent conversation turns
    messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
    recent_history = db.get_recent_chat_history(chat_id, limit=8)
    for turn in recent_history:
        messages.append({"role": turn["role"], "content": turn["text"]})

    # Append current user prompt
    messages.append({"role": "user", "content": user_text})

    tool_calls_executed = []
    tool_results_list = []
    pending_action = None
    final_reply = ""
    total_tokens = 0

    MAX_TOOL_ITERATIONS = 6
    current_model = model
    async with httpx.AsyncClient(timeout=30.0) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            payload = {
                "model": current_model,
                "temperature": 0.1,
                "messages": messages,
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "auto"
            }

            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json=payload
            )

            # Auto-fallback on 429 Rate Limit (e.g. 8k TPM limit on 120b)
            if resp.status_code == 429:
                print(f"Agent Loop 429 Rate Limit on {current_model}. Switching to fallback model openai/gpt-oss-20b...")
                if current_model != "openai/gpt-oss-20b":
                    current_model = "openai/gpt-oss-20b"
                    payload["model"] = current_model
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        json=payload
                    )
                if resp.status_code == 429:
                    await asyncio.sleep(2.0)
                    resp = await client.post(
                        "https://api.groq.com/openai/v1/chat/completions",
                        headers={"Authorization": f"Bearer {groq_key}"},
                        json=payload
                    )

            if resp.status_code != 200:
                print("Agent Loop Groq Error:", resp.status_code, resp.text[:300])
                if current_model != "openai/gpt-oss-20b":
                    current_model = "openai/gpt-oss-20b"
                    payload["model"] = current_model
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
            if not tool_calls:
                # No more tools requested; final conversational answer reached
                final_reply = choice_msg.get("content") or ""
                break

            # Append the assistant's tool-call request to messages
            messages.append(choice_msg)

            # Execute every tool call emitted
            for tc in tool_calls:
                call_id = tc["id"]
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
                    pending_action = tool_result
                    payload = {
                        "kind": tool_result["action"],
                        "title": tool_result.get("title", "Meeting"),
                        "start_iso": tool_result.get("start_iso"),
                        "duration_minutes": tool_result.get("duration_minutes", 30),
                        "attendees": tool_result.get("attendees", []),
                        "created_at": datetime.now(IST).isoformat()
                    }
                    save_pending_action(chat_id, tool_result["action"], payload)
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
            elif last_res.get("status") == "error" and "message" in last_res:
                final_reply = last_res["message"]
            else:
                final_reply = "Aapka request process ho gaya hai."
        else:
            final_reply = safe_error_reply("api_error", connector_name="Assistant", details="Response ready nahi ho paya, kripya dobara try karein.")

    # If user was asking a cross-question during a pending confirmation, append pending reminder
    if is_cross_q and active_pending:
        p_title = active_pending.get("payload", {}).get("title") or "Meeting"
        final_reply += f"\n\n📌 **Pending Confirmation:** Aapka '{p_title}' schedule karne ka request abhi bhi waiting hai. Kya main ise confirm kar doon? (Reply 'yes' ya 'cancel')"

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
        final_reply = "Action prepare kar liya gaya hai, lekin abhi tool dwara confirmed link ya ID prapt nahi hui hai."

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
