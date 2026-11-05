from sqlalchemy import bindparam as text_bindparam
"""
Saptic AI — Relationship Knowledge Graph & People CRM Engine
Tracks kinship, roles, professional relationships, birthdays, and anniversaries.
Enables natural resolution of entities like "my manager", "papa", "gym trainer", "investor".
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import text
import db
from timing import IST
import core.timeutil as timeutil

logger = logging.getLogger("saptic_relationship")

COMMON_RELATIONSHIP_ALIASES = {
    "father": ["father", "papa", "pitaji", "dad", "daddy", "bapuji"],
    "mother": ["mother", "mummy", "maa", "mom", "mataji"],
    "brother": ["brother", "bhai", "bhaiya", "bro"],
    "sister": ["sister", "didi", "behen", "sis"],
    "manager": ["manager", "boss", "lead", "team lead", "reporting manager"],
    "colleague": ["colleague", "coworker", "teammate"],
    "friend": ["friend", "dost", "yaar", "buddy", "roommate", "flatmate"],
    "wife": ["wife", "patni", "biwi", "spouse"],
    "husband": ["husband", "pati"],
    "doctor": ["doctor", "physician", "therapist"],
    "investor": ["investor", "angel", "vc"],
    "trainer": ["trainer", "coach", "gym trainer", "fitness coach"],
}


def _resolve_uid(user_id_or_chat_id: int) -> int:
    from memory.store import resolve_user_id
    return resolve_user_id(user_id_or_chat_id)


def add_or_update_relationship(
    user_id_or_chat_id: int,
    name: str,
    relationship: str,
    phone: Optional[str] = None,
    email: Optional[str] = None,
    birthday: Optional[str | date] = None,
    notes: Optional[str] = None,
) -> Dict[str, Any]:
    """Upsert a relationship record into the knowledge graph."""
    uid = _resolve_uid(user_id_or_chat_id)
    clean_name = (name or "").strip()
    clean_rel = (relationship or "").strip().lower()

    if not clean_name or not clean_rel:
        return {"status": "error", "message": "Name and relationship are required."}

    bday_date = None
    if birthday:
        if isinstance(birthday, (datetime, date)):
            bday_date = birthday if isinstance(birthday, date) else birthday.date()
        elif isinstance(birthday, str):
            try:
                bday_date = datetime.strptime(birthday.strip(), "%Y-%m-%d").date()
            except Exception:
                try:
                    bday_date = datetime.strptime(birthday.strip(), "%d-%m-%Y").date()
                except Exception:
                    pass

    try:
        with db.engine.begin() as conn:
            existing = conn.execute(
                text("SELECT id FROM user_relationships WHERE user_id = :uid AND LOWER(name) = LOWER(:name)"),
                {"uid": uid, "name": clean_name}
            ).first()

            if existing:
                conn.execute(
                    text("""
                        UPDATE user_relationships
                        SET relationship = :rel,
                            phone = COALESCE(:ph, phone),
                            email = COALESCE(:em, email),
                            birthday = COALESCE(:bday, birthday),
                            notes = COALESCE(:notes, notes),
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :rid
                    """),
                    {
                        "rid": existing[0],
                        "rel": clean_rel,
                        "ph": phone,
                        "em": email,
                        "bday": bday_date,
                        "notes": notes,
                    }
                )
                rid = existing[0]
            else:
                res = conn.execute(
                    text("""
                        INSERT INTO user_relationships (user_id, name, relationship, phone, email, birthday, notes)
                        VALUES (:uid, :name, :rel, :ph, :em, :bday, :notes)
                        RETURNING id
                    """),
                    {
                        "uid": uid,
                        "name": clean_name,
                        "rel": clean_rel,
                        "ph": phone,
                        "em": email,
                        "bday": bday_date,
                        "notes": notes,
                    }
                )
                rid = res.scalar()

        return {
            "status": "success",
            "relationship_id": rid,
            "name": clean_name,
            "relationship": clean_rel,
            "birthday": str(bday_date) if bday_date else None,
        }
    except Exception as exc:
        logger.error(f"Error adding relationship: {exc}")
        return {"status": "error", "message": str(exc)}


def get_user_relationships(user_id_or_chat_id: int) -> List[Dict[str, Any]]:
    """Retrieve all mapped relationships for a user."""
    uid = _resolve_uid(user_id_or_chat_id)
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, name, relationship, phone, email, birthday, notes
                    FROM user_relationships
                    WHERE user_id = :uid
                    ORDER BY relationship ASC, name ASC
                """),
                {"uid": uid}
            ).mappings().all()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error(f"get_user_relationships error: {exc}")
        return []


def resolve_relationship_in_text(user_id_or_chat_id: int, query_text: str) -> List[Dict[str, Any]]:
    """
    Scans incoming user query for relationship keywords (e.g. 'mere manager', 'papa', 'bhai')
    and returns matching people from the knowledge graph.
    """
    text_low = (query_text or "").lower()
    all_rels = get_user_relationships(user_id_or_chat_id)
    if not all_rels:
        return []

    matched = []
    for r in all_rels:
        rel_kind = r["relationship"].lower()
        person_name = r["name"].lower()
        # Direct name match (full name or first name if >= 3 chars)
        first_name = person_name.split()[0] if person_name else ""
        if person_name in text_low or (len(first_name) >= 3 and f" {first_name} " in f" {text_low} "):
            matched.append(r)
            continue
        # Direct relationship match
        if rel_kind in text_low:
            matched.append(r)
            continue
        # Alias matches (e.g. 'boss' -> 'manager')
        for canonical, aliases in COMMON_RELATIONSHIP_ALIASES.items():
            if canonical == rel_kind or rel_kind in aliases:
                if any(f" {alias} " in f" {text_low} " or f"mere {alias}" in text_low for alias in aliases):
                    matched.append(r)
                    break

    return matched


def format_relationships_context(user_id_or_chat_id: int) -> str:
    """Returns a compact context string for system prompt injection (10-15 tokens max)."""
    rels = get_user_relationships(user_id_or_chat_id)
    if not rels:
        return ""
    items = []
    for r in rels[:8]:  # Limit to top 8 to stay strictly within tiny token budget
        bday_raw = r.get("birthday")
        if bday_raw:
            b_obj = date.fromisoformat(bday_raw) if isinstance(bday_raw, str) else bday_raw
            bday_str = f", bday: {b_obj.strftime('%d %b')}" if hasattr(b_obj, "strftime") else f", bday: {bday_raw}"
        else:
            bday_str = ""
        items.append(f"{r['name']} ({r['relationship']}{bday_str})")
    return "Known Relationships: " + "; ".join(items)


def get_upcoming_birthdays(user_id_or_chat_id: int, days_ahead: int = 2) -> List[Dict[str, Any]]:
    """Returns people whose birthday falls in the next N days."""
    uid = _resolve_uid(user_id_or_chat_id)
    rels = get_user_relationships(uid)
    today = timeutil.today(IST)
    upcoming = []

    for r in rels:
        bday = r.get("birthday")
        if not bday:
            continue
        try:
            if isinstance(bday, str):
                bday = date.fromisoformat(bday)
            # Check birthday this year
            bday_this_year = date(today.year, bday.month, bday.day)
            delta = (bday_this_year - today).days
            if 0 <= delta <= days_ahead:
                upcoming.append({
                    "name": r["name"],
                    "relationship": r["relationship"],
                    "date": bday_this_year,
                    "days_remaining": delta,
                })
        except ValueError:
            # Leap day 29 Feb edge case
            pass

    return upcoming


def record_relationship_interaction(user_id_or_chat_id: int, text_sample: str) -> int:
    """Updates last_interacted_at for any person mentioned in conversational text."""
    if not text_sample or len(text_sample) < 3:
        return 0
    uid = _resolve_uid(user_id_or_chat_id)
    matches = resolve_relationship_in_text(uid, text_sample)
    if not matches:
        return 0
    matched_ids = [m["id"] for m in matches if m.get("id")]
    if not matched_ids:
        return 0
    try:
        with db.engine.begin() as conn:
            if db.engine.dialect.name == "sqlite":
                conn.execute(
                    text("UPDATE user_relationships SET last_interacted_at = CURRENT_TIMESTAMP WHERE id IN :ids AND user_id = :uid").bindparams(text_bindparam("ids", expanding=True)),
                    {"ids": matched_ids, "uid": uid}
                )
            else:
                conn.execute(
                    text("UPDATE user_relationships SET last_interacted_at = CURRENT_TIMESTAMP WHERE id = ANY(:ids) AND user_id = :uid"),
                    {"ids": matched_ids, "uid": uid}
                )
            return len(matched_ids)
    except Exception as exc:
        logger.error(f"record_relationship_interaction error: {exc}")
        return 0


def get_dormant_relationships(user_id_or_chat_id: int, days_threshold: int = 30) -> List[Dict[str, Any]]:
    """Returns close contacts not interacted with in the last N days."""
    uid = _resolve_uid(user_id_or_chat_id)
    try:
        with db.engine.connect() as conn:
            if db.engine.dialect.name == "sqlite":
                cutoff = timeutil.now(IST) - timedelta(days=days_threshold)
                rows = conn.execute(
                    text("""
                        SELECT id, name, relationship, phone, last_interacted_at,
                               CAST((julianday('now') - julianday(COALESCE(last_interacted_at, created_at))) AS INTEGER) as days_since
                        FROM user_relationships
                        WHERE user_id = :uid
                          AND COALESCE(last_interacted_at, created_at) < :cutoff
                        ORDER BY COALESCE(last_interacted_at, created_at) ASC
                        LIMIT 3
                    """),
                    {"uid": uid, "cutoff": cutoff}
                ).mappings().all()
            else:
                rows = conn.execute(
                    text("""
                        SELECT id, name, relationship, phone, last_interacted_at,
                               DATE_PART('day', NOW() - COALESCE(last_interacted_at, created_at)) as days_since
                        FROM user_relationships
                        WHERE user_id = :uid
                          AND COALESCE(last_interacted_at, created_at) < (NOW() - (:d || ' days')::INTERVAL)
                        ORDER BY COALESCE(last_interacted_at, created_at) ASC
                        LIMIT 3
                    """),
                    {"uid": uid, "d": days_threshold}
                ).mappings().all()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error(f"get_dormant_relationships error: {exc}")
        return []


# Aliases for flexible developer & bot invocation
upsert_relationship = add_or_update_relationship
list_relationships = get_user_relationships
