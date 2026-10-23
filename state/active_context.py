"""
PeculiarAI — Active Context State Tracker
Tracks the conversational focus (last connector, last objects with numbered IDs, last topic/person)
so follow-ups like 'pehla wala', 'usko close kar do', 'dusra dikhao' work seamlessly.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
from timing import IST

# Thread-safe in-memory cache of active conversational context per chat
_ACTIVE_CONTEXT_STORE: Dict[int, Dict[str, Any]] = {}

def get_active_context(chat_id: int) -> Dict[str, Any]:
    """Retrieves the current active conversational context for a chat."""
    ctx = _ACTIVE_CONTEXT_STORE.get(chat_id)
    if not ctx:
        return {
            "connector": None,
            "topic": None,
            "objects": [],
            "updated_at": None
        }
    return ctx

def update_active_context(
    chat_id: int,
    connector: Optional[str] = None,
    topic: Optional[str] = None,
    objects: Optional[List[str]] = None
):
    """Updates the active focus when a tool emits results or topic changes."""
    ctx = _ACTIVE_CONTEXT_STORE.setdefault(chat_id, {
        "connector": None,
        "topic": None,
        "objects": [],
        "updated_at": None
    })
    if connector is not None:
        ctx["connector"] = connector
    if topic is not None:
        ctx["topic"] = topic
    if objects is not None:
        ctx["objects"] = objects[:15]
    ctx["updated_at"] = datetime.now(IST).isoformat()

def clear_active_context(chat_id: int):
    """Clears context when user explicitly switches topic."""
    _ACTIVE_CONTEXT_STORE.pop(chat_id, None)

def format_active_context(chat_id: int) -> str:
    """Formats active context into a concise string for runtime system prompt injection."""
    ctx = dict(get_active_context(chat_id))
    # If in-memory context has no connector or topic, check recent DB chat turns
    if not ctx.get("connector") or not ctx.get("topic"):
        try:
            import db, re
            recent = db.get_recent_chat_history(chat_id, limit=6)
            for t in reversed(recent):
                txt = t.get("text", "")
                m = re.search(r'\b([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+|[a-zA-Z0-9_-]+-project)\b', txt)
                if m:
                    ctx["connector"] = "GitHub"
                    ctx["topic"] = f"repo: {m.group(1)}"
                    break
        except Exception:
            pass

    parts = []
    if ctx.get("connector"):
        parts.append(f"Last Connector: {ctx['connector']}")
    if ctx.get("topic"):
        parts.append(f"Focus Topic/Person: {ctx['topic']}")
    if ctx.get("objects"):
        obj_list = ", ".join([f"[{i+1}] {obj}" for i, obj in enumerate(ctx["objects"])])
        parts.append(f"Last Displayed Objects: {obj_list}")

    if not parts:
        return "None (fresh topic/turn)"
    return " | ".join(parts)
