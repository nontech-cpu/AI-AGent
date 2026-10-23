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
    """Extracts candidate names or keywords from user prompt (ignoring common stopwords)."""
    words = re.findall(r'[a-zA-Z]{3,}', text.lower())
    stopwords = {
        "hai", "bhy", "bhai", "mera", "meri", "mere", "mujhe", "kya", "tha", "thi",
        "the", "ko", "koo", "ka", "ki", "ke", "aur", "and", "karo", "bhejna", "kar",
        "bolo", "batao", "batana", "meeting", "reminder", "today", "tomorrow", "time",
        "contact", "save", "phone", "email", "role", "lead", "designer", "example", "com",
        "bhejo", "rakh", "rakhna", "karna", "number", "notes", "detail", "details",
        "info", "information", "dekh", "dekho", "please", "help", "with", "from"
    }
    candidates = [w for w in words if w not in stopwords][:4]
    return candidates


def assemble_memory_context(chat_id: int, user_text: str) -> Dict[str, Any]:
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

    # 1. Look for entity matches in text
    candidates = extract_potential_entity_tokens(user_text)
    for cand in candidates:
        ent = find_entity(user_id, cand, threshold=0.28)
        if ent and ent["id"] not in seen_entity_ids:
            seen_entity_ids.add(ent["id"])
            # Pull active commitments for this entity
            ent_commitments = get_commitments_for_user(user_id, entity_id=ent["id"])
            ent["commitments"] = ent_commitments
            matched_entities.append(ent)

    # 2. Hybrid search for relevant memories (only for informational queries, skipping for simple imperatives)
    relevant_mems = []
    if re.search(r"\b(search|yaad|dhundo|kya|batao|pata|fact|preference|info|ticket|booking|flight|hotel)\b", user_text, re.I):
        try:
            relevant_mems = search_memories_hybrid(user_id, user_text, limit=3)
        except Exception:
            relevant_mems = []

    # 3. User facts
    facts = get_user_semantic_facts(user_id)[:3]

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

    if relevant_mems:
        lines.append("[Relevant Past Notes & Memories]:")
        for m in relevant_mems:
            lines.append(f"• [{m['memory_type']}] {m['content']}")
        lines.append("")

    if facts:
        lines.append("[User Life Preferences & Facts]:")
        for f in facts:
            lines.append(f"• {f['fact']}")
        lines.append("")

    lines.append("</memory>")

    formatted = "\n".join(lines) if (matched_entities or relevant_mems or facts) else ""

    return {
        "formatted_block": formatted,
        "matched_entities": matched_entities,
        "relevant_memories": relevant_mems,
        "facts": facts
    }
