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
        "about", "some", "any", "tell", "show", "give", "want", "need", "your", "yours", "their", "there", "here", "this", "that", "these", "those",
        "merse", "mujhse", "tumse", "isse", "usse", "baat", "english", "hinglish", "hindi", "german", "deutsch", "french", "spanish", "language", "bhasha",
        "detail", "detial", "explain", "exlain", "ml", "dl", "ai", "machine", "learning", "deep", "neural", "network", "code", "coding", "meaning", "define", "definition", "samjhao", "samjha", "smjhao", "smjha",
        "hi", "hii", "hiii", "good", "morning", "evening", "night", "afternoon", "bye", "ok", "okay", "test", "ping",
        "pr", "par", "mene", "maine", "kh", "kaha", "th", "tha", "thi", "the", "phir", "fir", "ghnte", "ghante", "kyu", "kyun", "lgya", "lagaya", "lgaya", "na", "ne", "se", "me", "mein", "pe",
        "min", "minute", "minutes", "sec", "second", "seconds", "hr", "hrs", "hour", "hours", "ek", "do", "teen", "chaar", "paanch", "chhe", "saat", "aath", "nau", "das"
    }
    candidates = [w for w in tokens if w.lower() not in stopwords and not w.isdigit()][:4]
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

    # Fast-path for greetings & simple acknowledgments (0ms - zero DB queries)
    if low in ("hi", "hello", "hey", "hlo", "hii", "hiii", "namaste", "namaskar", "salam", "ping", "test", "ok", "okay", "bye") or (len(low.split()) == 1 and low in ("hi", "hello", "hey", "hlo", "hii")):
        return {
            "formatted_block": "",
            "matched_entities": [],
            "measurements": [],
            "relevant_memories": [],
            "facts": []
        }

    # 1. Look for entity matches in text via clean candidate tokens (supports Devanagari & short names)
    candidates = extract_potential_entity_tokens(user_text)
    for cand in candidates:
        ent = find_entity(user_id, cand, threshold=0.28)
        if ent and ent["id"] not in seen_entity_ids:
            seen_entity_ids.add(ent["id"])
            ent_commitments = get_commitments_for_user(user_id, entity_id=ent["id"])
            ent["commitments"] = ent_commitments
            try:
                from memory.store import get_recent_actions_for_user
                ent["recent_actions"] = get_recent_actions_for_user(user_id, entity_id=ent["id"], limit=3)
            except Exception:
                ent["recent_actions"] = []
            matched_entities.append(ent)

    # 1.5 Contextual Anaphora & Pronoun Entity Resolution ('un dono', 'unko', 'in dono', 'dono contacts', 'call', 'purpose', 'reason')
    if not matched_entities and any(w in low for w in ("un dono", "unko", "in dono", "inko", "dono", "contact", "contacts", "person", "call", "baat", "phone", "number", "commitment", "purpose", "reason", "kaam")):
        try:
            from memory.store import list_entities
            recent_ents = list_entities(user_id, entity_type="person")
            for ent in recent_ents[:4]:
                if ent["id"] not in seen_entity_ids:
                    seen_entity_ids.add(ent["id"])
                    ent_commitments = get_commitments_for_user(user_id, entity_id=ent["id"])
                    ent["commitments"] = ent_commitments
                    try:
                        from memory.store import get_recent_actions_for_user
                        ent["recent_actions"] = get_recent_actions_for_user(user_id, entity_id=ent["id"], limit=3)
                    except Exception:
                        ent["recent_actions"] = []
                    matched_entities.append(ent)
        except Exception:
            pass

    # 2. Hybrid RAG retrieval: vector + tsvector full-text + trigram merged via RRF (k=60)
    relevant_mems = []
    _MEM_INTENT_KWS = (
        "glucose", "sugar", "reading", "bp", "weight", "height", "diet", "medicine",
        "doctor", "report", "saved", "note", "yaad", "remember", "record", "kya tha",
        "kya thi", "mera", "meri", "mere", "my", "mine", "maine", "usne", "profile",
        "habit", "kl ke", "kal ke", "aaj ke", "pichla", "previous", "history", "blood",
        "fasting", "pp", "test", "check", "contact", "contacts", "call", "baat", "commitment",
        "kaam", "purpose", "reason", "unko", "un dono", "mail", "email", "sent", "bheja", "bheji", "subject"
    )
    should_search_hybrid = any(kw in low for kw in _MEM_INTENT_KWS) or len(user_text.split()) > 10
    if should_search_hybrid:
        try:
            raw_mems = search_memories_hybrid(user_id, user_text, limit=6, min_score=0.015)
            for rm in raw_mems:
                if rm.get("status") == "archived":
                    continue
                if rm.get("memory_type") in ("reminder", "alarm", "task") or "remind" in (rm.get("content") or "").lower():
                    continue
                relevant_mems.append(rm)
                if len(relevant_mems) >= 4:
                    break

            # Also query pgvector memory bubbles (travel, health, finance, work)
            try:
                bubble_mems = db.search_bubble_memories_cosine(chat_id, user_text, limit=3, min_similarity=0.45)
                seen_ids = {m.get("id") for m in relevant_mems}
                for bm in bubble_mems:
                    bm_content = (bm.get("content") or "").lower()
                    if "remind" in bm_content or bm.get("bubble_slug") == "reminder":
                        continue
                    if bm.get("id") not in seen_ids:
                        relevant_mems.append({
                            "id": bm["id"],
                            "memory_type": bm.get("bubble_slug", "note"),
                            "title": bm.get("title"),
                            "content": bm.get("content"),
                            "bubble_slug": bm.get("bubble_slug"),
                            "ocr_extracted_text": bm.get("ocr_extracted_text"),
                            "created_at": bm.get("created_at")
                        })
                        if len(relevant_mems) >= 6:
                            break
            except Exception:
                pass
        except Exception:
            relevant_mems = []

    # 2.5 Structured Measurements lookup: Always include latest measurements for instant recall & short follow-ups (e.g. 'kl ke?')
    measurements = []
    try:
        from memory.store import get_measurements
        measurements = get_measurements(user_id, limit=5)
    except Exception:
        measurements = []

    # 2.8 Core User Profile (~300 tokens always-on profile, no retrieval needed)
    core_profile = ""
    try:
        from memory.store import get_core_profile
        core_profile = get_core_profile(user_id)
    except Exception:
        core_profile = ""

    # 3. User facts & preferences (always provide top 3 relevant facts for context grounding)
    facts = []
    try:
        facts = get_user_semantic_facts(user_id)[:3]
    except Exception:
        facts = []

    # Include active bookings/travel facts if query relates to travel, tickets, documents, or morning context
    try:
        lower_q = (user_text or "").lower()
        if any(w in lower_q for w in ("ticket", "flight", "train", "pnr", "booking", "plane", "travel", "sub", "subah", "bheja", "bhji", "document", "documents")):
            top_user_facts = db.get_user_facts(chat_id, limit=5)
            for uf in top_user_facts:
                f_text = uf.get("fact", "")
                if any(w in f_text.lower() for w in ("booking", "ticket", "flight", "pnr", "train", "airways")):
                    facts.append({"id": uf.get("id"), "fact": f_text, "category": uf.get("category", "booking")})
    except Exception:
        pass

    # Format into context string
    lines = ["<memory>"]

    if core_profile:
        lines.append("[Core User Profile & Hard Constraints]:")
        lines.append(core_profile)
        lines.append("")

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
            if attrs.get("context_notes"):
                lines.append(f"  Context / Purpose / Reason: {attrs.get('context_notes')}")
            if e.get("commitments"):
                for c in e["commitments"]:
                    lines.append(f"  Pending Promise/Commitment: \"{c['commitment']}\"")
            if e.get("recent_actions"):
                for a in e["recent_actions"]:
                    lines.append(f"  Recent Action / Sent Mail: {a['summary']} ({str(a['created_at'])[:16]})")
        lines.append("")

    # Global Recent Actions Lookup (When user asks about sent emails or past actions without naming entity)
    if any(w in low for w in ("mail", "email", "sent", "bheja", "bheji", "send", "subject", "kya bheja")):
        try:
            from memory.store import get_recent_actions_for_user
            global_acts = get_recent_actions_for_user(user_id, limit=3)
            if global_acts:
                lines.append("[Recent Action Ledger (Emails & Tasks Executed by Bot)]:")
                for ga in global_acts:
                    lines.append(f"• {ga['summary']} (Date: {str(ga['created_at'])[:16]})")
                lines.append("")
        except Exception:
            pass

    if measurements:
        lines.append("[Structured Measurements & Health Readings]:")
        for meas in measurements:
            sub = meas.get("subject") or meas.get("subject_name") or "User"
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
            bubble_name = m.get("bubble_slug")
            b_tag = f"[{bubble_name.upper()}] " if bubble_name and bubble_name != "everything" else ""
            tag = f"{b_tag}{event_d}, {subj}" if event_d else f"{b_tag}{m.get('memory_type', 'note')}"
            title_prefix = f"({m['title']}) " if m.get("title") else ""
            lines.append(f"• [{tag}] {title_prefix}{m['content']}")
            if m.get("ocr_extracted_text") and len(m.get("ocr_extracted_text", "")) > 10:
                lines.append(f"  [Document/OCR Detail]: {m['ocr_extracted_text'][:200]}")
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
    _MEM_CTX_CACHE[ck] = {"exp": now_ts + 15.0, "data": res}
    return res
