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
            "focus_topic": None,
            "objects": [],
            "last_url": None,
            "updated_at": None
        }
    if "focus_topic" not in ctx:
        ctx["focus_topic"] = ctx.get("topic")
    return ctx

def update_active_context(
    chat_id: int,
    connector: Optional[str] = None,
    topic: Optional[str] = None,
    objects: Optional[List[str]] = None,
    url: Optional[str] = None
):
    """Updates the active focus when a tool emits results or topic changes."""
    ctx = _ACTIVE_CONTEXT_STORE.setdefault(chat_id, {
        "connector": None,
        "topic": None,
        "focus_topic": None,
        "objects": [],
        "last_url": None,
        "updated_at": None
    })
    if connector is not None:
        ctx["connector"] = connector
    if topic is not None:
        ctx["topic"] = topic
        ctx["focus_topic"] = topic
    if objects is not None:
        ctx["objects"] = objects[:15]
    if url is not None:
        ctx["last_url"] = url
    ctx["updated_at"] = datetime.now(IST).isoformat()

def get_active_url(chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves the last active URL (Notion page, Meet link, GitHub file, Drive doc)."""
    ctx = get_active_context(chat_id)
    if ctx.get("last_url"):
        return {
            "url": ctx["last_url"],
            "title": ctx.get("topic") or "Document/Page",
            "connector": ctx.get("connector") or "Integration"
        }

    # Fallback: scan recent assistant messages in DB for created Notion / GitHub / Meet links
    try:
        import db, re
        recent = db.get_recent_chat_history(chat_id, limit=8)
        for t in reversed(recent):
            txt = t.get("text", "")
            # Look for explicit Markdown or plaintext URLs
            m = re.search(r'(https?://[^\s)\]]+)', txt)
            if m:
                u = m.group(1).rstrip(".,;")
                # Ignore OAuth / connect auth URLs
                if "oauth" not in u.lower() and "authorize" not in u.lower() and "connect.html" not in u.lower():
                    title_match = re.search(r"'(.*?)'", txt) or re.search(r'"(.*?)"', txt)
                    t_name = title_match.group(1) if title_match else "Item"
                    conn = "Notion" if "notion" in u.lower() else ("GitHub" if "github" in u.lower() else "Meeting/Link")
                    return {"url": u, "title": t_name, "connector": conn}
    except Exception:
        pass
    return None

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
    if ctx.get("last_url"):
        parts.append(f"Last Created URL/Link: {ctx['last_url']}")
    if ctx.get("objects"):
        obj_list = ", ".join([f"[{i+1}] {obj}" for i, obj in enumerate(ctx["objects"])])
        parts.append(f"Last Displayed Objects: {obj_list}")

    if not parts:
        return "None (fresh topic/turn)"
    return " | ".join(parts)


def set_active_ticket(chat_id: int, ticket_info: Dict[str, Any]):
    """Stores the active ticket details so follow-ups work immediately."""
    ctx = _ACTIVE_CONTEXT_STORE.setdefault(chat_id, {
        "connector": None,
        "topic": None,
        "objects": [],
        "updated_at": None
    })
    ctx["last_ticket"] = ticket_info
    pnr = ticket_info.get("pnr") or "Ticket"
    carrier = ticket_info.get("carrier") or "Travel Provider"
    route = ticket_info.get("route") or ""
    dt = ticket_info.get("datetime_str") or ""
    update_active_context(
        chat_id,
        connector="Ticket",
        topic=f"Ticket PNR {pnr}",
        objects=[f"Provider: {carrier}", f"PNR: {pnr}", f"Route: {route}", f"Travel Date: {dt}"]
    )


def get_active_ticket(chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves the last viewed or saved ticket for the chat."""
    ctx = _ACTIVE_CONTEXT_STORE.get(chat_id)
    if ctx and ctx.get("last_ticket"):
        return ctx["last_ticket"]

    # Fallback 1: check recent DB facts for ticket PNR
    try:
        import db, re
        facts = db.get_user_facts(chat_id) or []
        for f in reversed(facts):
            f_text = f.get("fact", "")
            if "pnr" in f_text.lower() or "booking/ticket" in f_text.lower():
                pnr_m = re.search(r"pnr\s*[:\-#]?\s*([a-zA-Z0-9]+)", f_text, re.I)
                route_m = re.search(r"route\s*[:\-#]?\s*([^|]+)", f_text, re.I)
                date_m = re.search(r"date\s*[:\-#]?\s*([^|]+)", f_text, re.I)
                carrier_m = re.search(r"(?:booking/ticket|ticket|provider)\s*[:\-#]?\s*([^|]+)", f_text, re.I)
                pax_m = re.search(r"passengers\s*[:\-#]?\s*([^|]+)", f_text, re.I)
                seat_m = re.search(r"seats\s*[:\-#]?\s*([^|]+)", f_text, re.I)
                if pnr_m:
                    return {
                        "pnr": pnr_m.group(1).strip(),
                        "route": route_m.group(1).strip() if route_m else None,
                        "datetime_str": date_m.group(1).strip() if date_m else None,
                        "carrier": carrier_m.group(1).strip() if carrier_m else "Travel Provider",
                        "passengers": pax_m.group(1).strip() if pax_m else None,
                        "seats": seat_m.group(1).strip() if seat_m else None,
                        "summary": f_text
                    }
    except Exception:
        pass

    # Fallback 2: check recent chat history for PNR
    try:
        import db, re
        history = db.get_recent_chat_history(chat_id, limit=8)
        for t in reversed(history):
            txt = t.get("text", "")
            if "pnr" in txt.lower():
                pnr_m = re.search(r"pnr\s*[:\-#]?\s*([a-zA-Z0-9]{6,12})", txt, re.I)
                route_m = re.search(r"route\s*[:\-#]?\s*([^\n]+)", txt, re.I)
                date_m = re.search(r"(?:travel\s*date|date\s*&\s*time|date)\s*[:\-#]?\s*([^\n]+)", txt, re.I)
                carrier_m = re.search(r"(?:provider|service)\s*[:\-#]?\s*([^\n]+)", txt, re.I)
                if pnr_m:
                    return {
                        "pnr": pnr_m.group(1).strip(),
                        "route": route_m.group(1).strip() if route_m else None,
                        "datetime_str": date_m.group(1).strip() if date_m else None,
                        "carrier": carrier_m.group(1).strip() if carrier_m else "Travel Provider",
                        "summary": txt[:200]
                    }
    except Exception:
        pass

    return None


def parse_ticket_datetime(dt_str: Optional[str]) -> Optional[datetime]:
    """Parses various ticket date formats into an IST-aware datetime object."""
    if not dt_str:
        return None
    clean = str(dt_str).strip()
    formats = [
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%b-%Y %H:%M",
        "%d-%b-%Y",
        "%d %b %Y, %I:%M %p",
        "%d %b %Y %H:%M",
        "%d %b %Y",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
    ]
    for fmt in formats:
        try:
            d = datetime.strptime(clean[:19], fmt)
            return d.replace(tzinfo=IST)
        except Exception:
            pass
    return None

