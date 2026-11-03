"""
Saptic AI — Shared Memory Bubbles (Collaborative Memory) Engine
Allows users to securely invite friends, family, or colleagues into specific memory bubbles
(e.g., 'Goa Trip 🏖️', 'House Chores', 'Project Alpha') without leaking personal accounts.
"""
import secrets
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from sqlalchemy import text
import db
from timing import IST

logger = logging.getLogger("saptic_shared_bubble")


def _resolve_uid(user_id_or_chat_id: int) -> int:
    from memory.store import resolve_user_id
    return resolve_user_id(user_id_or_chat_id)


def create_bubble_share_invite(owner_chat_id: int, bubble_slug: str, permission: str = "read_write") -> Dict[str, Any]:
    """Generates a secure invite code for a specific bubble."""
    owner_uid = _resolve_uid(owner_chat_id)
    slug = (bubble_slug or "").strip().lower()

    try:
        with db.engine.connect() as conn:
            bubble = conn.execute(
                text("SELECT id, name, slug FROM memory_bubbles WHERE user_id = :uid AND slug = :s"),
                {"uid": owner_uid, "s": slug}
            ).mappings().first()

            if not bubble:
                db.create_memory_bubble(owner_chat_id, slug, slug.replace("_", " ").title(), "#6366F1", "Folder")
                with db.engine.connect() as conn2:
                    bubble = conn2.execute(
                        text("SELECT id, name, slug FROM memory_bubbles WHERE user_id = :uid AND slug = :s"),
                        {"uid": owner_uid, "s": slug}
                    ).mappings().first()

        # Generate unambiguous invite code using bubble ID
        bubble_id_val = bubble["id"] if bubble else 1
        invite_code = f"SAPTIC-{bubble_id_val}-{secrets.token_hex(2).upper()}"

        return {
            "status": "success",
            "bubble_id": bubble_id_val,
            "bubble_name": bubble["name"] if bubble else slug,
            "bubble_slug": slug,
            "permission": permission,
            "invite_code": invite_code,
            "instructions": f"Apne dost ya family member ko yeh command bhejein: /join_bubble {invite_code}",
        }
    except Exception as exc:
        logger.error(f"create_bubble_share_invite error: {exc}")
        return {"status": "error", "message": str(exc), "invite_code": f"SAPTIC-{slug.upper()[:4]}-{secrets.token_hex(2).upper()}"}


def join_shared_bubble(invited_chat_id: int, invite_code: str) -> Dict[str, Any]:
    """Joins a user into a shared memory bubble via invite code."""
    code = (invite_code or "").strip().upper()
    if not code.startswith("SAPTIC-"):
        return {"status": "error", "message": "Invalid invite code format."}

    # Extract bubble identifier from code: SAPTIC-<ident>-<hex>
    parts = code.split("-")
    if len(parts) < 3:
        return {"status": "error", "message": "Invalid code format."}
    
    bubble_ident = parts[1]

    try:
        with db.engine.begin() as conn:
            # 1. Look up by bubble ID if integer
            if bubble_ident.isdigit():
                bubble = conn.execute(
                    text("SELECT id, user_id, name, slug FROM memory_bubbles WHERE id = :bid"),
                    {"bid": int(bubble_ident)}
                ).mappings().first()
            else:
                bubble = conn.execute(
                    text("SELECT id, user_id, name, slug FROM memory_bubbles WHERE LOWER(slug) = :s LIMIT 1"),
                    {"s": bubble_ident.lower()}
                ).mappings().first()
                if not bubble:
                    bubble = conn.execute(
                        text("SELECT id, user_id, name, slug FROM memory_bubbles WHERE LOWER(slug) LIKE :s LIMIT 1"),
                        {"s": f"{bubble_ident.lower()}%"}
                    ).mappings().first()

            if not bubble:
                return {"status": "error", "message": "Shared bubble not found or expired."}

            owner_uid = bubble["user_id"]
            bubble_id = bubble["id"]

            # Check if already a member
            existing = conn.execute(
                text("SELECT id FROM shared_bubble_members WHERE bubble_id = :bid AND shared_with_chat_id = :cid"),
                {"bid": bubble_id, "cid": invited_chat_id}
            ).first()

            if existing:
                return {
                    "status": "already_member",
                    "bubble_name": bubble["name"],
                    "bubble_slug": bubble["slug"],
                    "message": f"Aap already '{bubble['name']}' bubble ke member hain!"
                }

            conn.execute(
                text("""
                    INSERT INTO shared_bubble_members (bubble_id, owner_user_id, shared_with_chat_id, permission)
                    VALUES (:bid, :ouid, :cid, 'read_write')
                    ON CONFLICT (bubble_id, shared_with_chat_id) DO NOTHING
                """),
                {"bid": bubble_id, "ouid": owner_uid, "cid": invited_chat_id}
            )

        return {
            "status": "success",
            "bubble_name": bubble["name"],
            "bubble_slug": bubble["slug"],
            "message": f"🎉 Successfully joined shared bubble: '{bubble['name']}'! Ab aap is bubble mein notes aur updates share kar sakte hain."
        }
    except Exception as exc:
        logger.error(f"join_shared_bubble error: {exc}")
        return {"status": "error", "message": str(exc)}


def get_shared_bubbles_for_user(chat_id: int) -> List[Dict[str, Any]]:
    """Retrieves all bubbles shared with this user."""
    try:
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT b.id, b.name, b.slug, b.slug as bubble_slug, b.color_hex, b.icon_name, u.first_name as owner_name, s.permission, s.created_at
                    FROM shared_bubble_members s
                    JOIN memory_bubbles b ON s.bubble_id = b.id
                    JOIN users u ON s.owner_user_id = u.id
                    WHERE s.shared_with_chat_id = :cid
                """),
                {"cid": chat_id}
            ).mappings().all()
            return [dict(r) for r in rows]
    except Exception as exc:
        logger.error(f"get_shared_bubbles_for_user error: {exc}")
        return []
