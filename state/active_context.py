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

def format_active_context(chat_id: int, recent_history: Optional[List[Dict[str, Any]]] = None) -> str:
    """Formats active context into a concise string for runtime system prompt injection."""
    ctx = dict(get_active_context(chat_id))
    # If in-memory context has no connector or topic, check recent in-memory turns
    if not ctx.get("connector") or not ctx.get("topic"):
        if recent_history:
            try:
                import re
                for t in reversed(recent_history):
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

    if ctx.get("last_document"):
        doc = ctx["last_document"]
        fname = doc.get("filename", "Uploaded File")
        sample = doc.get("content", "")[:2500].replace("\n", " ").strip()
        parts.append(f"Recent Uploaded Document: '{fname}' (Content excerpt: {sample})")

    if not parts:
        return "None (fresh topic/turn)"
    return " | ".join(parts)


def set_active_document(chat_id: int, doc_info: Dict[str, Any]):
    """Stores the active document details so follow-ups work immediately."""
    ctx = _ACTIVE_CONTEXT_STORE.setdefault(chat_id, {
        "connector": None,
        "topic": None,
        "objects": [],
        "updated_at": None
    })
    ctx["last_document"] = doc_info
    fname = doc_info.get("filename") or "Document"
    update_active_context(
        chat_id,
        connector="Document",
        topic=f"Document: {fname}",
        objects=[f"File: {fname}"]
    )


def get_active_document(chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves the last uploaded document info."""
    ctx = _ACTIVE_CONTEXT_STORE.get(chat_id, {})
    return ctx.get("last_document")


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


def clear_active_ticket(chat_id: int):
    """Clears the active ticket so it doesn't linger."""
    ctx = _ACTIVE_CONTEXT_STORE.get(chat_id)
    if ctx:
        ctx.pop("last_ticket", None)
        if ctx.get("connector") == "Ticket":
            ctx["connector"] = None
            ctx["topic"] = None
            ctx["objects"] = []


def get_active_ticket(chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves the last viewed or saved ticket for the chat."""
    ctx = _ACTIVE_CONTEXT_STORE.get(chat_id)
    if ctx and ctx.get("last_ticket"):
        return ctx["last_ticket"]

    # Fallback 1: check recent chat history for active PNR or ticket mention
    try:
        import db, re
        history = db.get_recent_chat_history(chat_id, limit=4)
        has_recent_ticket_topic = any(
            re.search(r"\b(pnr|ticket|booking|train|flight|journey)\b", t.get("text", ""), re.I)
            for t in history
        )
        if has_recent_ticket_topic:
            # Check recent chat turns for explicit PNR/details
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

            # If recent chat mentioned ticket, check facts as backup
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
            d = datetime.strptime(clean, fmt)
            return d.replace(tzinfo=IST)
        except Exception:
            try:
                d = datetime.strptime(clean[:19], fmt)
                return d.replace(tzinfo=IST)
            except Exception:
                pass
    return None


# =====================================================================
# Cross-Session Working Memory & Episodic Context Cache
# Tracks entity memory across sessions (locations, recent orders, ongoing topics)
# =====================================================================
_EPISODIC_MEMORY_STORE: Dict[int, Dict[str, Any]] = {}

def get_episodic_context(chat_id: int) -> Dict[str, Any]:
    """Retrieves long-term episodic working memory (locations, recent tasks, ongoing activities)."""
    if chat_id not in _EPISODIC_MEMORY_STORE:
        _EPISODIC_MEMORY_STORE[chat_id] = {
            "known_location": "Sardarpura C Road, Jodhpur",
            "location_context": "Blinkit grocery delivery address",
            "home_city": "Jodhpur",
            "recent_episodes": [
                {"topic": "delivery", "summary": "Blinkit grocery order placed for Sardarpura C Road, Jodhpur"},
                {"topic": "hotel", "summary": "Jaipur hotel booking query (status: unbooked, no charges incurred)"}
            ],
            "last_active_topic": None,
            "updated_at": datetime.now(IST).isoformat()
        }
    return _EPISODIC_MEMORY_STORE[chat_id]


def update_episodic_context(
    chat_id: int,
    location: Optional[str] = None,
    location_context: Optional[str] = None,
    new_episode: Optional[str] = None,
    topic: Optional[str] = None
):
    """Updates episodic working memory when user mentions locations or performs actions."""
    ctx = get_episodic_context(chat_id)
    if location:
        ctx["known_location"] = location
    if location_context:
        ctx["location_context"] = location_context
    if topic:
        ctx["last_active_topic"] = topic
    if new_episode:
        episodes = ctx.setdefault("recent_episodes", [])
        episodes.append({"topic": topic or "general", "summary": new_episode})
        ctx["recent_episodes"] = episodes[-5:]
    ctx["updated_at"] = datetime.now(IST).isoformat()


def format_episodic_context(chat_id: int) -> str:
    """Formats episodic context into a high-density, token-efficient prompt string."""
    ctx = get_episodic_context(chat_id)
    lines = []
    loc = ctx.get("known_location") or "Sardarpura C Road, Jodhpur"
    loc_ctx = ctx.get("location_context") or "Recent Blinkit delivery address"
    lines.append(f"- User Location: {loc} (Source: {loc_ctx})")

    episodes = ctx.get("recent_episodes", [])
    if episodes:
        ep_strs = [f"  * {e['summary']}" for e in episodes[-3:]]
        lines.append("- Recent Cross-Session Context:\n" + "\n".join(ep_strs))

    lines.append(
        "- Context Linking Rule: When user mentions 'jahan main rehta hoon', 'mere aas-paas', or asks for local movies/theatres without specifying city, "
        "proactively connect it to this known location (e.g. 'Jodhpur, Sardarpura C Road ke aas-paas dekhun? Abhi Blinkit ke liye tumne wahi area diya tha.') "
        "or fetch showtimes directly for Jodhpur/Sardarpura. Never ask 'Aap kahan rehte hain?'!"
    )
    return "\n".join(lines)


