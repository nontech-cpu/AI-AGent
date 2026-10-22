"""
PeculiarAI Memory Backfill & Migration Script (Phase 2)
Migrates legacy `contact_memories` and `user_facts` into the new `entities`,
`entity_aliases`, and `memories` tables with pgvector embeddings.
"""
import logging
from sqlalchemy import text
import db
from memory.store import (
    save_entity,
    add_entity_alias,
    save_memory_item
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("memory_backfill")

def backfill_legacy_memories():
    """Migrates all existing records from contact_memories and user_facts."""
    logger.info("Starting legacy memory backfill...")

    with db.engine.connect() as conn:
        # 1. Backfill contact_memories -> entities + commitments
        contact_rows = conn.execute(text("""
            SELECT id, user_id, name, company, role_title, email, phone, context_notes, commitments
            FROM contact_memories
        """)).fetchall()

        logger.info(f"Found {len(contact_rows)} contact_memories records to migrate.")
        migrated_contacts = 0
        migrated_commitments = 0

        for r in contact_rows:
            uid, name, comp, role, email, phone, notes, commitments = (
                r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8]
            )
            if not name:
                continue

            attrs = {
                "company": comp,
                "role_title": role,
                "email": email,
                "phone": phone,
                "context_notes": notes
            }
            # Clean empty attributes
            attrs = {k: v for k, v in attrs.items() if v}

            entity_id = save_entity(
                user_id=uid,
                name=name,
                entity_type="person",
                attributes=attrs
            )
            migrated_contacts += 1

            if commitments and commitments.strip():
                save_memory_item(
                    user_id=uid,
                    content=commitments.strip(),
                    memory_type="commitment",
                    subject_entity_id=entity_id,
                    structured={"legacy_source": "contact_memories"}
                )
                migrated_commitments += 1

        # 2. Backfill user_facts -> memories (semantic_fact)
        fact_rows = conn.execute(text("""
            SELECT id, user_id, fact, category, status
            FROM user_facts
            WHERE status = 'active'
        """)).fetchall()

        logger.info(f"Found {len(fact_rows)} user_facts records to migrate.")
        migrated_facts = 0

        for fr in fact_rows:
            uid, fact, cat, status = fr[1], fr[2], fr[3], fr[4]
            if not fact:
                continue
            save_memory_item(
                user_id=uid,
                content=fact.strip(),
                memory_type="semantic_fact",
                structured={"category": cat or "general"},
                status=status or "active",
                source_type="legacy_user_facts"
            )
            migrated_facts += 1

        logger.info(f"Backfill complete! Migrated {migrated_contacts} contacts, {migrated_commitments} commitments, {migrated_facts} facts.")

if __name__ == "__main__":
    backfill_legacy_memories()
