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

from timing import IST
import db
from agent.tools import TOOL_DEFINITIONS, execute_tool
from memory.retrieval import assemble_memory_context
from memory.extractor import extract_and_persist_turn
from agent.verifier import verify_and_ground_reply

AGENT_SYSTEM_PROMPT = """You are PeculiarAI, an empathetic, hyper-competent personal AI assistant with persistent memory and real-world tools.

GROUNDING & TRUTH CONTRACT:
1. Factual statements about the user's schedule, contacts, commitments, reminders, files, or past conversations must come ONLY from tool results.
2. If data is not found or empty, tell the user plainly: "Mere paas yeh information saved nahi hai" and offer to save it. NEVER invent facts or repositories.
3. NEVER claim an action was performed (meeting scheduled, email sent, notes saved) unless a tool returned success.
4. When user asks what they need to send, give, or do for a contact (e.g. 'Ananya ko kya bhejna tha?'), use get_contact or search_memory to look up commitments. Do NOT draft unnecessary apology messages.
5. Answer in natural, friendly Hinglish (Roman English script, e.g. 'Haan bhai, bilkul!').
6. Keep replies concise and direct (1-3 sentences) unless an in-depth breakdown is requested.

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

    model = os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b"
    now_ist = datetime.now(IST)

    system_content = (custom_system or AGENT_SYSTEM_PROMPT).format(
        current_time_ist=now_ist.strftime("%I:%M %p"),
        current_date_weekday=now_ist.strftime("%A, %d %B %Y")
    )

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
    if mem_ctx.get("formatted_block"):
        system_content += "\n\nRELEVANT USER COGNITIVE MEMORY & ENTITIES:\n" + mem_ctx["formatted_block"]

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
    async with httpx.AsyncClient(timeout=30.0) as client:
        for iteration in range(MAX_TOOL_ITERATIONS):
            payload = {
                "model": model,
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

            if resp.status_code != 200:
                print("Agent Loop Groq Error:", resp.status_code, resp.text[:300])
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

                # Append tool result to messages
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": json.dumps(tool_result, ensure_ascii=False)
                })

            if pending_action:
                break

    if not final_reply:
        final_reply = "Bhai, main aapka personal AI assistant hu. Reminders, schedule, contacts aur tasks ke baare mein kuch bhi bataiye!"

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

    latency_ms = int((time.time() - start_ts) * 1000)

    # Record turn
    db.log_chat_message(chat_id, "user", user_text)
    db.log_chat_message(chat_id, "assistant", final_reply)

    # Continuous background memory extraction
    try:
        asyncio.create_task(extract_and_persist_turn(chat_id, user_text, final_reply))
    except Exception as exc:
        print("Background extractor task launch error:", exc)

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
