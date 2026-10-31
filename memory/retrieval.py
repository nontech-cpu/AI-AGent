"""
PeculiarAI Cognitive Memory Retrieval & Context Assembler (Phase 2)
Pulls Layered Cognitive Memory (Entities, Commitments, Episodic Search, Semantic Facts)
and formats it into an injection-safe <memory> block for the Agent Loop.
"""
import re
from typing import Dict, Any, List, Optional
import db
from memory.store import (
    resolve_user_id,
    find_entity,
    search_memories_hybrid,
    get_commitments_for_user,
    get_user_semantic_facts
)


def extract_potential_entity_tokens(text: str) -> List[str]:
    """Extracts candidate names or keywords from user prompt (supporting Devanagari, English, and short names >= 2 chars)."""
    tokens = re.findall(r'[\u0900-\u097Fa-zA-Z0-9_\-\']{2,}', text)
    stopwords = {
        "hai", "bhy", "bhai", "mera", "meri", "mere", "mujhe", "kya", "tha", "thi",
        "the", "ko", "koo", "ka", "ki", "ke", "aur", "and", "karo", "bhejna", "kar",
        "bolo", "batao", "batana", "meeting", "reminder", "today", "tomorrow", "time",
        "contact", "save", "phone", "email", "role", "lead", "designer", "example", "com",
        "bhejo", "rakh", "rakhna", "karna", "number", "notes", "detail", "details",
        "info", "information", "dekh", "dekho", "please", "help", "with", "from",
        "toh", "bje", "baje", "krdo", "kardo", "krna", "mtlb", "matlab", "diff", "phrk",
        "fark", "set", "jana", "aana", "aaj", "kal", "parso", "subah", "shaam", "sham",
        "dopahar", "raat", "sthh", "sth", "saath", "sath", "bhi", "yeh", "kuch", "shmko",
        "movie", "dinner", "lunch", "kisko", "uska", "uski", "unka", "unki", "pehle",
        "baad", "waqt", "nhi", "nahi", "nahin", "hain", "naam", "sirf",
        "kesa", "kaisa", "kaise", "kaisi", "theek", "thik", "badhiya", "badia", "mast",
        "haal", "hal", "chal", "rha", "raha", "rahe", "rahi", "hlo", "hello", "hey", "namaste",
        "reminders", "schedule", "events", "calendar", "weather", "notion", "github", "task", "tasks", "list", "lists", "calc", "calculate",
        "what", "when", "where", "which", "whose", "whom", "will", "would", "could", "should", "have", "has", "had", "does", "done", "doing", "been",
        "are", "for", "the", "you", "all", "not", "can", "how", "let", "may", "was", "were",
        "about", "some", "any", "tell", "show", "give", "want", "need", "your", "yours", "their", "there", "here", "this", "that", "these", "those"
    }
    candidates = [w for w in tokens if w.lower() not in stopwords][:4]
    return candidates


_MEM_CTX_CACHE: dict = {}

def assemble_memory_context(chat_id: int, user_text: str) -> Dict[str, Any]:
    import time as _pytime
    now_ts = _pytime.time()
    ck = (chat_id, user_text.strip().lower())
    cached = _MEM_CTX_CACHE.get(ck)
    if cached and (now_ts < cached["exp"]):
        return cached["data"]
    """
    Assembles layered cognitive memory for the current turn:
    1. Entity lookup (Fuzzy / phonetic matching on candidate words).
    2. Active commitments for any matched entity or general commitments.
    3. Hybrid semantic episodic memory search.
    4. Relevant user facts & life preferences.

    Returns:
        {
            "formatted_block": str,  # XML formatted <memory> block
            "matched_entities": list,
            "relevant_memories": list,
            "active_commitments": list
        }
    """
    user_id = resolve_user_id(chat_id)
    matched_entities = []
    seen_entity_ids = set()
    low = user_text.lower().strip()

    # 1. Look for entity matches in text via clean candidate tokens (supports Devanagari & short names)
    candidates = extract_potential_entity_tokens(user_text)
    for cand in candidates:
        ent = find_entity(user_id, cand, threshold=0.28)
        if ent and ent["id"] not in seen_entity_ids:
            seen_entity_ids.add(ent["id"])
            ent_commitments = get_commitments_for_user(user_id, entity_id=ent["id"])
            ent["commitments"] = ent_commitments
            matched_entities.append(ent)

    # 2. Hybrid RAG retrieval on EVERY turn: vector + tsvector full-text + trigram merged via RRF (k=60)
    relevant_mems = []
    try:
        raw_mems = search_memories_hybrid(user_id, user_text, limit=6, min_score=0.015)
        for rm in raw_mems:
            if rm.get("status") == "archived":
                continue
            if rm.get("memory_type") == "reminder":
                continue
            relevant_mems.append(rm)
            if len(relevant_mems) >= 4:
                break
    except Exception:
        relevant_mems = []

    # 2.5 Structured Measurements lookup if query touches health/measurements
    measurements = []
    if any(w in low for w in ("sugar", "glucose", "reading", "readings", "bp", "weight", "fasting", "post-meal", "test", "kal ki", "aaj ki", "trend")):
        try:
            from memory.store import get_measurements
            measurements = get_measurements(user_id, limit=5)
        except Exception:
            measurements = []

    # 3. User facts & preferences (always provide top 3 relevant facts for context grounding)
    facts = []
    try:
        facts = get_user_semantic_facts(user_id)[:3]
    except Exception:
        facts = []

    # Format into context string
    lines = ["<memory>"]

    if matched_entities:
        lines.append("[Identified Contacts & Entities]:")
        for e in matched_entities:
            attrs = e.get("attributes", {})
            company_str = f" ({attrs.get('company')})" if attrs.get("company") else ""
            lines.append(f"• Name: {e['name']}{company_str}")
            if attrs.get("role_title"):
                lines.append(f"  Role: {attrs.get('role_title')}")
            if attrs.get("email"):
                lines.append(f"  Email: {attrs.get('email')}")
            if attrs.get("phone"):
                lines.append(f"  Phone: {attrs.get('phone')}")
            if e.get("commitments"):
                for c in e["commitments"]:
                    lines.append(f"  Pending Promise/Commitment: \"{c['commitment']}\"")
        lines.append("")

    if measurements:
        lines.append("[Structured Measurements & Health Readings]:")
        for meas in measurements:
            sub = meas.get("subject_name") or "User"
            metric = meas.get("metric", "reading")
            val = meas.get("value")
            unit = meas.get("unit", "")
            ctx = f" ({meas['context']})" if meas.get("context") else ""
            m_date = str(meas.get("measured_at") or meas.get("created_at") or "")[:10]
            lines.append(f"• [{m_date}] {sub}'s {metric}: {val} {unit}{ctx}".strip())
        lines.append("")

    if relevant_mems:
        lines.append("[Relevant Past Notes & Memories]:")
        for m in relevant_mems:
            structured = m.get("structured") or {}
            event_d = structured.get("event_date") or (str(m.get("created_at"))[:10] if m.get("created_at") else None)
            subj = structured.get("subject") or m.get("entity_name") or "User"
            tag = f"{event_d}, {subj}" if event_d else f"{m['memory_type']}"
            lines.append(f"• [{tag}] {m['content']}")
        lines.append("")

    if facts:
        lines.append("[User Life Preferences & Facts]:")
        for f in facts:
            lines.append(f"• {f['fact']}")
        lines.append("")

    lines.append("</memory>")

    formatted = "\n".join(lines) if (matched_entities or measurements or relevant_mems or facts) else ""
    # Cap memory block at ~600 tokens (~2400 characters) to prevent context pollution
    if len(formatted) > 2400:
        formatted = formatted[:2390] + "\n...</memory>"

    res = {
        "formatted_block": formatted,
        "matched_entities": matched_entities,
        "measurements": measurements,
        "relevant_memories": relevant_mems,
        "facts": facts
    }
    _MEM_CTX_CACHE[ck] = {"exp": now_ts + 90.0, "data": res}
    return res
