"""
PeculiarAI — Active Context State Tracker
Tracks the conversational focus (last connector, last objects with numbered IDs, last topic/person)
so follow-ups like 'pehla wala', 'usko close kar do', 'dusra dikhao' work seamlessly.
"""
from typing import Dict, Any, List, Optional
from datetime import datetime
import json
import logging
from sqlalchemy import text
import db
from timing import IST
import core.timeutil as timeutil

logger = logging.getLogger(__name__)

# Thread-safe in-memory cache of active conversational context per chat
_ACTIVE_CONTEXT_STORE: Dict[int, Dict[str, Any]] = {}
_ACTIVE_CONTEXT_TABLE_INITIALIZED = False

def _ensure_active_context_table():
    global _ACTIVE_CONTEXT_TABLE_INITIALIZED
    if _ACTIVE_CONTEXT_TABLE_INITIALIZED and db.engine.dialect.name != "sqlite":
        return
    try:
        is_pg = db.engine.dialect.name == "postgresql"
        with db.engine.begin() as conn:
            if is_pg:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS active_contexts (
                        chat_id BIGINT PRIMARY KEY,
                        context_data JSONB NOT NULL,
                        updated_at TIMESTAMPTZ DEFAULT now()
                    )
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS active_contexts (
                        chat_id INTEGER PRIMARY KEY,
                        context_data TEXT NOT NULL,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
        _ACTIVE_CONTEXT_TABLE_INITIALIZED = True
    except Exception as exc:
        logger.debug(f"active_contexts table init notice: {exc}")

def get_active_context(chat_id: int) -> Dict[str, Any]:
    """Retrieves the current active conversational context for a chat, rebuilding from DB if needed."""
    ctx = _ACTIVE_CONTEXT_STORE.get(chat_id)
    if not ctx:
        # Attempt rebuild from database
        try:
            _ensure_active_context_table()
            with db.engine.connect() as conn:
                row = conn.execute(
                    text("SELECT context_data FROM active_contexts WHERE chat_id = :c"),
                    {"c": chat_id}
                ).first()
                if row and row[0]:
                    raw_data = row[0]
                    if isinstance(raw_data, str):
                        ctx = json.loads(raw_data)
                    elif isinstance(raw_data, dict):
                        ctx = raw_data
                    if ctx:
                        _ACTIVE_CONTEXT_STORE[chat_id] = ctx
        except Exception as exc:
            logger.debug(f"Active context DB fetch notice for {chat_id}: {exc}")

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
    """Updates the active focus when a tool emits results or topic changes, persisting to DB."""
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
    ctx["updated_at"] = timeutil.now(IST).isoformat()

    # Persist to database
    try:
        _ensure_active_context_table()
        is_pg = db.engine.dialect.name == "postgresql"
        data_json = json.dumps(ctx)
        with db.engine.begin() as conn:
            if is_pg:
                conn.execute(text("""
                    INSERT INTO active_contexts (chat_id, context_data, updated_at)
                    VALUES (:cid, CAST(:data AS jsonb), now())
                    ON CONFLICT (chat_id) DO UPDATE
                    SET context_data = EXCLUDED.context_data,
                        updated_at = EXCLUDED.updated_at
                """), {"cid": chat_id, "data": data_json})
            else:
                conn.execute(text("""
                    INSERT INTO active_contexts (chat_id, context_data, updated_at)
                    VALUES (:cid, :data, CURRENT_TIMESTAMP)
                    ON CONFLICT (chat_id) DO UPDATE
                    SET context_data = EXCLUDED.context_data,
                        updated_at = CURRENT_TIMESTAMP
                """), {"cid": chat_id, "data": data_json})
    except Exception as exc:
        logger.debug(f"Active context DB persist notice for {chat_id}: {exc}")

def clear_active_context(chat_id: int):
    """Clears context when user explicitly switches topic."""
    _ACTIVE_CONTEXT_STORE.pop(chat_id, None)
    try:
        _ensure_active_context_table()
        with db.engine.begin() as conn:
            conn.execute(text("DELETE FROM active_contexts WHERE chat_id = :cid"), {"cid": chat_id})
    except Exception as exc:
        logger.debug(f"Active context DB delete notice for {chat_id}: {exc}")

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

    if ctx.get("last_event"):
        ev = ctx["last_event"]
        ev_title = ev.get("title", "Event")
        ev_time = ev.get("time_str") or ev.get("due_time") or ""
        ev_date = ev.get("date_str") or ev.get("due_date") or ""
        parts.append(f"Recent Active Event/Meeting: '{ev_title}' ({ev_time} {ev_date})".strip())

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


def set_active_event(chat_id: int, event_info: Dict[str, Any]):
    """Stores the active meeting/event details so conversational follow-ups work immediately."""
    ctx = _ACTIVE_CONTEXT_STORE.setdefault(chat_id, {
        "connector": None,
        "topic": None,
        "objects": [],
        "updated_at": None
    })
    ctx["last_event"] = event_info
    title = event_info.get("title") or "Meeting"
    time_str = event_info.get("time_str") or event_info.get("due_time") or ""
    date_str = event_info.get("date_str") or event_info.get("due_date") or ""
    update_active_context(
        chat_id,
        connector="Calendar",
        topic=f"Event: {title}",
        objects=[f"Event: {title}", f"Time: {time_str}", f"Date: {date_str}"]
    )


def get_active_event(chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves the last active meeting/event details."""
    ctx = _ACTIVE_CONTEXT_STORE.get(chat_id)
    if ctx and ctx.get("last_event"):
        return ctx["last_event"]
    return None


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

    # Structured Context Lookup: check persisted structured ticket memories
    try:
        import db
        with db.engine.connect() as conn:
            row = conn.execute(
                db.text("""
                    SELECT structured FROM memories 
                    WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid)
                      AND source_type IN ('ticket', 'booking', 'travel')
                      AND status = 'active'
                    ORDER BY id DESC LIMIT 1
                """),
                {"cid": chat_id}
            ).mappings().first()
            if row and row.get("structured") and isinstance(row["structured"], dict) and row["structured"].get("pnr"):
                return row["structured"]
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
    try:
        from dateutil import parser as dt_parser
        parsed = dt_parser.parse(clean)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=IST)
        return parsed.astimezone(IST)
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
        known_loc = None
        home_city = None
        try:
            import db
            loc_state = db.get_user_location_state(chat_id)
            if loc_state and loc_state.get("city"):
                home_city = loc_state["city"]
                area = loc_state.get("area") or ""
                known_loc = f"{area}, {home_city}".strip(", ") if area else home_city
        except Exception:
            pass

        _EPISODIC_MEMORY_STORE[chat_id] = {
            "known_location": known_loc,
            "location_context": "Verified user profile" if known_loc else None,
            "home_city": home_city,
            "recent_episodes": [],
            "last_active_topic": None,
            "updated_at": timeutil.now(IST).isoformat()
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
    ctx["updated_at"] = timeutil.now(IST).isoformat()


def format_episodic_context(chat_id: int) -> str:
    """Formats episodic context into a high-density, token-efficient prompt string."""
    ctx = get_episodic_context(chat_id)
    lines = []
    loc = ctx.get("known_location")
    if loc:
        loc_ctx = ctx.get("location_context") or "Verified user profile"
        lines.append(f"- User Location: {loc} (Source: {loc_ctx})")
        lines.append(
            f"- Context Linking Rule: When user mentions 'jahan main rehta hoon', 'mere aas-paas', or asks for local places without specifying city, "
            f"proactively connect it to this verified location ({loc})."
        )

    episodes = ctx.get("recent_episodes", [])
    if episodes:
        ep_strs = [f"  * {e['summary']}" for e in episodes[-3:]]
        lines.append("- Recent Cross-Session Context:\n" + "\n".join(ep_strs))
    return "\n".join(lines)


