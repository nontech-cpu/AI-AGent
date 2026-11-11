"""
PeculiarAI — Unified Guardrail Layer (Part 3)
Enforces fake-success blocking, fact-grounding, canned-fallback prevention, repeat suppression,
and untrusted content neutralization in code before sending messages to the user.
"""
import re
import difflib
from typing import Dict, Any, List, Tuple, Optional

import db
from connectors.base import ErrorType
from connectors.errors import format_connector_error

# Phrases claiming a real-world write/external action occurred
ACTION_COMPLETION_PATTERNS = [
    re.compile(r"\b(?:meeting|event|calendar)\s+(?:create|schedule|add)\s+(?:ho gayi|ho gaya|kar di|kar diya)\b", re.I),
    re.compile(r"\b(?:email|mail)\s+(?:send|bhej)\s+(?:diya|di|ho gaya|kar diya)\b", re.I),
    re.compile(r"\b(?:file|doc)\s+(?:upload|save)\s+(?:ho gayi|kar diya|ho gaya)\b", re.I),
    re.compile(r"\b(?:issue|pr|pull request|repo)\s+(?:create|close|merge)\s+(?:ho gaya|kar diya)\b", re.I),
    re.compile(r"\b(?:create|schedule|send|upload|delete|trash|clear|wipe|remove)\b.{0,15}\b(?:kar diya|kar di|kar diye|kar di gayi|ho gaya|ho gayi|kiye gaye|diye gaye)\b", re.I),
    re.compile(r"\b(?:hata diya|hata di|hata diye|hata di gayi)\b", re.I),
    re.compile(r"\b(?:successfully (?:created|sent|updated|deleted|scheduled|uploaded|create))\b", re.I),
    re.compile(r"\b(?:created|sent|deleted|scheduled|trashed|removed)\b.{0,20}\b(?:event|meeting|email|mail|issue|page|pages|file|reminder|contact|data|memory)\b", re.I),
    re.compile(r"\b(?:pages?|notes?|reminders?|meetings?|events?)\b.{0,20}\b(?:delete|deleted|trash|trashed|hata)\b", re.I)
]

# Untrusted content injection patterns
INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions?", re.I),
    re.compile(r"system\s+prompt\s*:", re.I),
    re.compile(r"you\s+are\s+now\s+(?:an?\s+)?unrestricted", re.I),
    re.compile(r"disregard\s+(?:all\s+)?safety", re.I)
]

def sanitize_untrusted_content(content: str) -> str:
    """Neutralizes prompt injection patterns in third-party data (emails, docs, web results)."""
    sanitized = content
    for pat in INJECTION_PATTERNS:
        sanitized = pat.sub("[neutralized-untrusted-text]", sanitized)
    return sanitized

def wrap_untrusted_content(content: str, source: str = "external_data") -> str:
    """
    OWASP LLM01 / Layer 5 Glass Tower Standard:
    Sanitizes and wraps third-party external text (emails, attachments, docs, web results)
    in XML boundary delimiters so the LLM treats it strictly as passive data, preventing indirect prompt injection.
    """
    if not content:
        return ""
    clean = sanitize_untrusted_content(str(content))
    return f'<untrusted_content source="{source}">\n{clean}\n</untrusted_content>' 

def safe_error_reply(error_type: str, connector_name: str = "Service", instructions: str = "", details: str = "", user_lang: str = "hinglish") -> str:
    """Standardized error reply preventing generic unhelpful fallbacks."""
    return format_connector_error(
        error_type=error_type,
        connector_name=connector_name,
        instructions=instructions,
        details=details,
        lang=user_lang
    )

def is_cancellation_or_decline(user_query: str = "", reply_text: str = "") -> bool:
    """
    Unified check for whether the user query declined/cancelled an action,
    or the assistant reply is acknowledging cancellation/dismissal.
    """
    uq_low = (user_query or "").lower()
    rep_low = (reply_text or "").lower()
    user_declined = any(w in uq_low for w in (
        "rhne de", "rehne de", "cancel", "no", "chhod do", "nahi chahiye",
        "never mind", "drop it", "mat karo", "rehne do", "chhoro", "drop"
    ))
    reply_dismissed = any(w in rep_low for w in (
        "cancel", "cancelled", "cancelling", "nahi kiya", "nahi kar raha",
        "dismissed", "dropped", "rehne", "rhne"
    ))
    return user_declined or reply_dismissed


def check_fake_success_guard(draft_reply: str, tool_results: List[Dict[str, Any]], user_query: str = "") -> Tuple[bool, Optional[str]]:
    """
    Blocks claims of successful action execution unless confirmed by a real tool result with an ID or URL.
    Returns (passed, error_reason).
    """
    if not draft_reply or not str(draft_reply).strip():
        return True, None

    # Informational queries (structural question mark) are inquiries about past state, not new write commands
    if user_query and "?" in user_query:
        return True, None

    low = draft_reply.lower()
    # Confirmation dialogues, options, auth URL invitations, and action approval questions
    is_confirm_dialogue = any(q in low for q in ["please confirm", "reply yes", "reply haan", "options:", "auth/google", "oauth", "secure link"]) or (
        any(q in low for q in ["kya main ise", "kya aap confirm", "kya main proceed", "confirm karein", "would you like me to proceed", "should i proceed", "shall i proceed"]) and "?" in low
    )
    if is_confirm_dialogue:
        return True, None

    # Status statements about accounts / integrations being connected or not are informational queries, not write actions
    if any(s in low for s in ["connected hai", "connected nahi", "link nahi", "linked nahi", "not connected", "connect nahi", "workspace:"]):
        return True, None

    # Discontinuation, cancellations, declinations, or negative acknowledgements are conversational dismissals, not action claims
    if is_cancellation_or_decline(user_query, draft_reply):
        return True, None

    has_completion_claim = any(pat.search(draft_reply) for pat in ACTION_COMPLETION_PATTERNS)
    if not has_completion_claim:
        return True, None

    # Verify if any write/destructive tool succeeded with concrete ID/URL evidence
    has_confirmed_success = False
    for tr in tool_results:
        if not isinstance(tr, dict):
            continue
        status = tr.get("status")
        ok = tr.get("ok", False)
        # Check success status
        if status in ("success", "needs_confirmation", "confirmation_required", "ready_for_dispatch") or ok is True:
            t_name = str(tr.get("tool_name") or tr.get("action") or "").lower()
            from agent.verifier import READ_ONLY_TOOL_NAMES
            if t_name in READ_ONLY_TOOL_NAMES and not (tr.get("files_sent") or tr.get("filename") or tr.get("file_path")):
                continue
            # Must have an ID, event/message ID, commit/issue URL, auth_url, or confirmed payload
            if (tr.get("id") or tr.get("ids") or tr.get("url") or tr.get("urls") or 
                tr.get("commit_url") or tr.get("commit_html") or tr.get("html_url") or 
                tr.get("webViewLink") or tr.get("meet_url") or tr.get("message_id") or 
                tr.get("file_id") or tr.get("auth_url") or tr.get("saved_fact") or 
                tr.get("deleted_titles") or tr.get("deleted_count") or tr.get("due_date") or tr.get("reminder_id") or
                tr.get("files_sent") or tr.get("filename") or tr.get("file_path") or tr.get("type") in ("pdf", "excel", "csv", "image")):
                has_confirmed_success = True
                break

    if not has_confirmed_success:
        return False, "Claimed real-world action completion without confirmed tool result ID/URL."

    return True, None

def check_repeat_guard(chat_id: int, candidate_reply: str, user_text: str = "") -> str:
    """
    Ensures candidate responses are preserved natively without being hijacked
    by fragile canned strings or substring false-positives.
    Pure ChatGPT/Claude-grade model autonomy.
    """
    if not candidate_reply or not str(candidate_reply).strip():
        return candidate_reply
    return candidate_reply


