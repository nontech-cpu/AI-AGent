"""
PeculiarAI Cognitive Memory Retrieval & Context Assembler (Phase 2)
Pulls Layered Cognitive Memory (Entities, Commitments, Episodic Search, Semantic Facts)
and formats it into an injection-safe <memory> block for the Agent Loop.
"""
import re
from typing import Dict, Any, List, Optional
from sqlalchemy import text
import logging
import db
logger = logging.getLogger('memory.retrieval')
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
            except Exception as act_err:
                logger.warning(f"Failed to fetch recent actions for entity {ent.get('id')}: {act_err}")
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
                    except Exception as act_err:
                        logger.warning(f"Failed to fetch recent actions for pronoun entity {ent.get('id')}: {act_err}")
                        ent["recent_actions"] = []
                    matched_entities.append(ent)
        except Exception as ana_err:
            logger.warning(f"Anaphora entity resolution error: {ana_err}")

    # 2. Hybrid RAG retrieval: vector (pgvector) + tsvector full-text + trigram merged via RRF (k=60)
    # Zero brittle keyword gating: natural questions (projects, architecture, past decisions) are searched directly
    relevant_mems = []
    projects = []
    working_ctx = {}
    try:
        from memory.store import search_project_memory, get_working_context
        projects = search_project_memory(user_id, user_text, limit=3)
        working_ctx = get_working_context(chat_id)
    except Exception as prj_err:
        logger.warning(f"Project memory/context retrieval error: {prj_err}")

    try:
        raw_mems = search_memories_hybrid(user_id, user_text, limit=8, min_score=0.010)
        seen_ids = set()
        for rm in raw_mems:
            if rm.get("status") in ("archived", "deleted"):
                continue
            if rm.get("memory_type") in ("reminder", "alarm", "task") or "remind" in (rm.get("content") or "").lower():
                continue
            if rm["id"] not in seen_ids:
                seen_ids.add(rm["id"])
                relevant_mems.append(rm)
            if len(relevant_mems) >= 5:
                break

        # Also query pgvector memory bubbles (travel, health, finance, work)
        try:
            bubble_mems = db.search_bubble_memories_cosine(chat_id, user_text, limit=3, min_similarity=0.40)
            for bm in bubble_mems:
                bm_content = (bm.get("content") or "").lower()
                if "remind" in bm_content or bm.get("bubble_slug") == "reminder":
                    continue
                if bm.get("id") not in seen_ids:
                    seen_ids.add(bm["id"])
                    relevant_mems.append({
                        "id": bm["id"],
                        "memory_type": bm.get("bubble_slug", "note"),
                        "title": bm.get("title"),
                        "content": bm.get("content"),
                        "bubble_slug": bm.get("bubble_slug"),
                        "ocr_extracted_text": bm.get("ocr_extracted_text"),
                        "created_at": bm.get("created_at")
                    })
                    if len(relevant_mems) >= 7:
                        break
        except Exception as b_err:
            logger.warning(f"Bubble memory cosine search error: {b_err}")
    except Exception as rrf_err:
        logger.warning(f"Hybrid memory search error: {rrf_err}")
        relevant_mems = []

    # 2.1 Exact Temporal Date Filtered Retrieval (e.g. '10 oct ko kya khaya tha?', '15 nov ko kya hua tha')
    date_match = re.search(r'\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\b', user_text, re.I)
    if date_match:
        try:
            from dateutil import parser as dt_parser
            target_d = dt_parser.parse(date_match.group(1)).strftime('%Y-%m-%d')
            with db.engine.connect() as conn:
                d_rows = conn.execute(text("""
                    SELECT id, memory_type, content, structured, created_at
                    FROM memories
                    WHERE user_id = :uid AND status = 'active' AND valid_to IS NULL
                      AND (content LIKE :d OR CAST(structured AS text) LIKE :d)
                    ORDER BY created_at DESC LIMIT 4
                """), {"uid": user_id, "d": f"%{target_d}%"}).fetchall()
                seen_m_ids = {m.get("id") for m in relevant_mems}
                for dr in d_rows:
                    if dr[0] not in seen_m_ids:
                        relevant_mems.append({
                            "id": dr[0],
                            "memory_type": dr[1],
                            "content": dr[2],
                            "structured": dr[3] if isinstance(dr[3], dict) else {},
                            "created_at": str(dr[4])
                        })
        except Exception as date_err:
            logger.warning(f"Temporal memory search error: {date_err}")

    # 2.5 Structured Measurements lookup: Always include latest measurements for instant recall & short follow-ups (e.g. 'kl ke?')
    measurements = []
    try:
        from memory.store import get_measurements
        measurements = get_measurements(user_id, limit=5)
    except Exception as meas_err:
        logger.warning(f"Measurements retrieval error: {meas_err}")
        measurements = []

    # 2.8 Core User Profile (~300 tokens always-on profile, no retrieval needed)
    core_profile = ""
    try:
        from memory.store import get_core_profile
        core_profile = get_core_profile(user_id)
    except Exception as cp_err:
        logger.warning(f"Core profile retrieval error: {cp_err}")
        core_profile = ""

    # 3. User facts & preferences (always provide top 3 relevant facts for context grounding)
    facts = []
    try:
        facts = get_user_semantic_facts(user_id)[:3]
    except Exception as fact_err:
        logger.warning(f"User facts retrieval error: {fact_err}")
        facts = []

    # Include active bookings/travel/address facts if query relates to travel, tickets, documents, address, or morning context
    try:
        lower_q = (user_text or "").lower()
        if any(w in lower_q for w in ("ticket", "flight", "train", "pnr", "booking", "plane", "travel", "sub", "subah", "bheja", "bhji", "document", "documents", "address", "location", "pincode", "delivery", "ghar", "home", "blinkit")):
            top_user_facts = db.get_user_facts(chat_id, limit=8)
            for uf in top_user_facts:
                f_text = uf.get("fact", "")
                if any(w in f_text.lower() for w in ("booking", "ticket", "flight", "pnr", "train", "airways", "address", "pincode", "road", "jodhpur", "delivery", "blinkit", "home")):
                    facts.append({"id": uf.get("id"), "fact": f_text, "category": uf.get("category", "personal")})
    except Exception as uf_err:
        logger.warning(f"Active travel/booking facts search error: {uf_err}")

    # Format into context string
    lines = ["<memory>"]

    if working_ctx and (working_ctx.get("active_project") or working_ctx.get("current_task")):
        lines.append("[Active Working Memory & State (Survives Restarts)]:")
        if working_ctx.get("active_project"):
            lines.append(f"• Active Project: {working_ctx['active_project']}")
        if working_ctx.get("current_topic"):
            lines.append(f"• Current Topic: {working_ctx['current_topic']}")
        if working_ctx.get("current_task"):
            lines.append(f"• Ongoing Task: {working_ctx['current_task']}")
        lines.append("")

    if projects:
        lines.append("[User Active Projects & Architectures]:")
        for p in projects:
            p_arch = f" | Architecture/Stack: {p['tech_stack']}" if p.get("tech_stack") else ""
            p_obj = f" | Objective: {p['objective']}" if p.get("objective") else ""
            p_tasks = f" | Tasks: {p['current_tasks']}" if p.get("current_tasks") else ""
            p_dec = f" | Key Decisions: {p['decisions']}" if p.get("decisions") else ""
            lines.append(f"• Project: {p['name']}{p_obj}{p_arch}{p_tasks}{p_dec}")
        lines.append("")

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
