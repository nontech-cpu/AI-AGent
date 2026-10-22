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
    re.compile(r"\b(?:ho gaya|kar diya|bhej diya|add kar diya|update ho gaya|create ho gaya|schedule ho gaya|delete kar diya)\b", re.I),
    re.compile(r"\b(?:successfully (?:created|sent|updated|deleted|scheduled|uploaded))\b", re.I),
    re.compile(r"\b(?:created|sent|deleted|scheduled)\b.{0,20}\b(?:event|meeting|email|mail|issue|page|file)\b", re.I)
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

def safe_error_reply(error_type: str, connector_name: str = "Service", instructions: str = "", user_lang: str = "hinglish") -> str:
    """Standardized error reply preventing generic unhelpful fallbacks."""
    return format_connector_error(
        error_type=error_type,
        connector_name=connector_name,
        instructions=instructions,
        lang=user_lang
    )

def check_fake_success_guard(draft_reply: str, tool_results: List[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    """
    Blocks claims of successful action execution unless confirmed by a real tool result with an ID or URL.
    Returns (passed, error_reason).
    """
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
        if status == "success" or ok is True:
            # Must have an ID, event/message ID, commit/issue URL or confirmation payload
            if tr.get("id") or tr.get("ids") or tr.get("url") or tr.get("urls") or tr.get("meet_url") or tr.get("message_id") or tr.get("file_id"):
                has_confirmed_success = True
                break

    if not has_confirmed_success:
        return False, "Claimed real-world action completion without confirmed tool result ID/URL."

    return True, None

def check_repeat_guard(chat_id: int, candidate_reply: str) -> str:
    """
    Suppresses identical consecutive replies. If identical to last turn, provides a fresh, actionable variation.
    """
    try:
        recent = db.get_recent_chat_history(chat_id, limit=2)
        if recent:
            last_assistant = next((r["text"] for r in reversed(recent) if r.get("role") == "assistant"), None)
            if last_assistant:
                # Compare similarity
                ratio = difflib.SequenceMatcher(None, candidate_reply.strip().lower(), last_assistant.strip().lower()).ratio()
                if ratio > 0.88:
                    return f"{candidate_reply.strip()}\n\n(Aap chahein toh agla step bataiye ya koi doosra option try karein.)"
    except Exception:
        pass
    return candidate_reply
