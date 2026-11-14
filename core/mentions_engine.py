"""
core/mentions_engine.py

Industry-Grade Collaborative Mentions & Cross-Platform Directed Dispatch Engine:
  - Pillar 1: Unique Usernames & Reddit-Style Dynamic Avatars
  - Pillar 2: Cross-Channel Identity Presence Mesh (Telegram, Web/App, WhatsApp)
  - Pillar 3: Mention Lexer & Targeted Scope Decompiler (Zero-Regex, Privacy Partitioning / Isolation Vault)
  - Pillar 4: Omni-Channel Dispatch Engine & File Fan-Out Pipeline
"""

import os
import re
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple

import db
from sqlalchemy import text

logger = logging.getLogger("mentions_engine")


@dataclass
class RecipientProfile:
    handle: str                      # e.g. "devender_contact" or "keshav"
    display_name: str                # e.g. "Devender" or "Keshav"
    target_channel: str              # "telegram", "app", "whatsapp", "unlinked"
    user_id: Optional[int] = None    # App user ID if registered
    telegram_chat_id: Optional[int] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    is_registered_app_user: bool = False

    def to_dict(self) -> dict:
        return {
            "handle": self.handle,
            "display_name": self.display_name,
            "target_channel": self.target_channel,
            "user_id": self.user_id,
            "telegram_chat_id": self.telegram_chat_id,
            "phone_number": self.phone_number,
            "email": self.email,
            "is_registered_app_user": self.is_registered_app_user,
        }


@dataclass
class TargetedDirective:
    recipient: RecipientProfile
    private_message: str
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    dispatch_id: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "recipient": self.recipient.to_dict(),
            "private_message": self.private_message,
            "attachments": self.attachments,
            "dispatch_id": self.dispatch_id,
        }


@dataclass
class DispatchResult:
    status: str                                     # "success", "partial", "failed", "no_mentions"
    sender_chat_id: int
    sender_name: str
    directives: List[TargetedDirective] = field(default_factory=list)
    summary_message: str = ""
    privacy_guarantee: str = "Strict Isolation Vault: Each recipient only receives their own assigned directive."


def extract_mentions(text_content: str) -> List[str]:
    """
    Extracts all @mention tokens and phone number recipients from message preserving order.
    Supports:
      - @devender_contact, @keshav
      - @+919876543210, @9876543210
      - Standalone phone numbers (+919876543210 or 10-digit numbers)
    """
    if not text_content:
        return []
    # 1. Match @mentions (e.g. @devender, @keshav, @+919876543210, @9876543210)
    raw_mentions = re.findall(r"@(\+?[a-zA-Z0-9_]{2,32})", text_content)

    # 2. Also match standalone phone numbers if present
    phone_matches = re.findall(r"(?:^|[\s,;:])(\+?[0-9]{10,13})(?=[\s,;:]|$)", text_content)
    all_tokens = list(raw_mentions)
    for p in phone_matches:
        if p not in all_tokens:
            all_tokens.append(p)

    seen = set()
    result = []
    for m in all_tokens:
        low = m.lower()
        if low not in seen:
            seen.add(low)
            result.append(m)
    return result


def resolve_recipient_identity(sender_chat_id: int, handle: str) -> RecipientProfile:
    """
    Pillar 2: Presence Mesh Resolver.
    Resolves an @mention handle or phone number against:
      1. Registered App Users (users.username or users.phone_number)
      2. Sender's Contact Memories (contact_memories.name or contact_memories.phone)
    Determines active platform: Telegram vs App vs WhatsApp.
    """
    raw_handle = handle.strip().lstrip("@")
    clean_handle = raw_handle.lower()
    # Strip optional '_contact' or '_user' suffix for CRM contact matching
    base_name = re.sub(r"_(contact|user|dev)$", "", clean_handle)

    digits = re.sub(r"\D", "", raw_handle)
    is_phone = len(digits) >= 10

    # 1. Search Global Registered App Users by username or phone
    try:
        with db.engine.connect() as conn:
            u_row = conn.execute(
                text("SELECT id, telegram_chat_id, username, first_name, phone_number FROM users WHERE LOWER(username) = :u"),
                {"u": clean_handle}
            ).mappings().first()
            if not u_row and base_name != clean_handle:
                u_row = conn.execute(
                    text("SELECT id, telegram_chat_id, username, first_name, phone_number FROM users WHERE LOWER(username) = :u"),
                    {"u": base_name}
                ).mappings().first()
            if not u_row and is_phone:
                u_row = conn.execute(
                    text("SELECT id, telegram_chat_id, username, first_name, phone_number FROM users WHERE phone_number LIKE :p"),
                    {"p": f"%{digits[-10:]}%"}
                ).mappings().first()

            if u_row:
                disp_name = u_row["first_name"] or u_row["username"] or raw_handle
                tg_id = u_row["telegram_chat_id"]
                # If tagged with _contact or _tg, or if resolved via phone with Telegram ID, prefer Telegram; otherwise App
                if raw_handle.lower().endswith(("_contact", "_tg")) or (is_phone and tg_id):
                    channel = "telegram" if tg_id else "app"
                else:
                    channel = "app"
                return RecipientProfile(
                    handle=raw_handle,
                    display_name=disp_name,
                    target_channel=channel,
                    user_id=u_row["id"],
                    telegram_chat_id=tg_id,
                    phone_number=u_row["phone_number"],
                    is_registered_app_user=True,
                )
    except Exception as exc:
        logger.warning(f"Error querying registered users for {raw_handle}: {exc}")

    # 2. Search Sender's Personal Contact Memories (by Name OR Phone Number)
    try:
        sender_cid = db.canonical_chat_id(sender_chat_id)
        contacts = db.get_contact_memories(sender_cid)
        for c in contacts:
            c_name = (c.get("name") or "").strip().lower()
            c_phone = re.sub(r"\D", "", c.get("phone") or "")
            name_match = (c_name == clean_handle or c_name == base_name or base_name in c_name or clean_handle in c_name)
            phone_match = bool(is_phone and c_phone and digits[-10:] in c_phone)

            if name_match or phone_match:
                phone = c.get("phone")
                email = c.get("email")

                # Check if this contact matches a registered Telegram user by phone
                matched_tg_id = None
                matched_uid = None
                if phone:
                    clean_phone = re.sub(r"\D", "", phone)[-10:]
                    with db.engine.connect() as conn:
                        p_row = conn.execute(
                            text("SELECT id, telegram_chat_id FROM users WHERE phone_number LIKE :p"),
                            {"p": f"%{clean_phone}%"}
                        ).first()
                        if p_row:
                            matched_uid, matched_tg_id = p_row[0], p_row[1]

                # Only route to Telegram/App if recipient actually exists in Peculiar AI
                channel = "telegram" if matched_tg_id else ("app" if matched_uid else "unlinked")
                return RecipientProfile(
                    handle=raw_handle,
                    display_name=c.get("name") or raw_handle,
                    target_channel=channel,
                    user_id=matched_uid,
                    telegram_chat_id=matched_tg_id,
                    phone_number=phone,
                    email=email,
                    is_registered_app_user=bool(matched_uid or matched_tg_id),
                )
    except Exception as exc:
        logger.warning(f"Error searching contact memories for {raw_handle}: {exc}")

    # 3. Fallback: If phone number but not registered in users
    if is_phone:
        formatted_phone = f"+{digits}" if not raw_handle.startswith("+") else raw_handle
        return RecipientProfile(
            handle=raw_handle,
            display_name=formatted_phone,
            target_channel="unlinked",
            user_id=None,
            telegram_chat_id=None,
            phone_number=formatted_phone,
            is_registered_app_user=False,
        )

    # 4. Fallback: Unlinked Recipient
    disp_title = base_name.capitalize() if base_name else raw_handle
    return RecipientProfile(
        handle=raw_handle,
        display_name=disp_title,
        target_channel="unlinked",
        user_id=None,
        telegram_chat_id=None,
        phone_number=None,
        is_registered_app_user=False,
    )


def _clean_directive_text(raw_text: str) -> str:
    """Cleans connector conjunctions, commas, and trailing punctuation."""
    t = raw_text.strip()
    # Strip leading conjunctions & punctuation
    t = re.sub(r"^([,;:\-–—\s]|and\s+|aur\s+|also\s+|while\s+|then\s+)+", "", t, flags=re.I)
    # Strip trailing conjunctions & punctuation
    t = re.sub(r"([,;:\-–—\s]|and|aur|also|while|then)+$", "", t, flags=re.I)
    return t.strip()


def deconstruct_targeted_directives(
    full_prompt: str,
    recipients: List[RecipientProfile],
    attachments: Optional[List[Dict[str, Any]]] = None
) -> List[TargetedDirective]:
    """
    Pillar 3: Zero-Regex Boundary Lexer & Privacy Partitioning.
    Slices multi-target instructions into discrete scoped packets.
    Devender ONLY gets Devender's task; Rahul ONLY gets Rahul's task.
    """
    if not recipients or not full_prompt:
        return []

    # Locate each mention's start and end index in full_prompt
    mention_spans: List[Tuple[int, int, RecipientProfile]] = []
    for r in recipients:
        h_esc = re.escape(r.handle)
        pattern = rf"(?:@{h_esc}(?!\w)|(?<!\w){h_esc}(?!\w))"
        m = re.search(pattern, full_prompt, re.I)
        if m:
            mention_spans.append((m.start(), m.end(), r))

    # Sort spans by their occurrence position in the text
    mention_spans.sort(key=lambda x: x[0])

    if not mention_spans:
        # If no exact match (e.g. edge case), treat whole message for first recipient
        return [TargetedDirective(recipient=recipients[0], private_message=full_prompt.strip(), attachments=attachments or [])]

    directives: List[TargetedDirective] = []
    total_spans = len(mention_spans)

    for i, (m_start, m_end, recip) in enumerate(mention_spans):
        # Text boundary: from end of current @mention to start of next @mention
        if i < total_spans - 1:
            next_start = mention_spans[i + 1][0]
            slice_text = full_prompt[m_end:next_start]
        else:
            slice_text = full_prompt[m_end:]

        cleaned = _clean_directive_text(slice_text)
        if not cleaned:
            # Fallback if text was before mention (e.g. 'work on project a @devender')
            cleaned = _clean_directive_text(full_prompt[:m_start]) if i == 0 else "Please review the updates."

        # Targeted attachment matching: if attachment name is referenced in this slice, attach it
        recip_attachments = []
        if attachments:
            clean_low = cleaned.lower()
            for att in attachments:
                att_name = (att.get("filename") or att.get("name") or "").lower()
                matched = False
                if att_name and att_name in clean_low:
                    matched = True
                else:
                    # check if significant keyword from filename is in text
                    stem = os.path.splitext(att_name)[0]
                    words = [w for w in re.split(r"[-_.\s]+", stem) if len(w) >= 4]
                    if any(w in clean_low for w in words):
                        matched = True
                if matched:
                    recip_attachments.append(att)
            # If no specific match, attach all shared attachments
            if not recip_attachments:
                recip_attachments = list(attachments)

        directives.append(TargetedDirective(
            recipient=recip,
            private_message=cleaned,
            attachments=recip_attachments,
        ))

    return directives


async def execute_targeted_mentions_dispatch(
    sender_chat_id: int,
    text_content: str,
    attachments: Optional[List[Dict[str, Any]]] = None,
    sender_name: Optional[str] = None
) -> DispatchResult:
    """
    Pillar 4: Omni-Channel Dispatch Engine & File Fan-Out.
    Parses mentions, resolves identities, partitions directives, and sends
    isolated, private packets to each recipient across Telegram, App, and WhatsApp.
    """
    raw_mentions = extract_mentions(text_content)
    if not raw_mentions:
        return DispatchResult(
            status="no_mentions",
            sender_chat_id=sender_chat_id,
            sender_name=sender_name or "User",
            summary_message="No @mentions detected in message."
        )

    # Auto-resolve attachments from recent uploaded document/photo or persistent active context
    active_doc = None
    try:
        from main import RECENT_DOCS
        active_doc = RECENT_DOCS.get(sender_chat_id)
    except Exception:
        pass

    if not active_doc or (not active_doc.get("file_id") and not (active_doc.get("path") and os.path.exists(str(active_doc.get("path"))))):
        try:
            from state.active_context import get_active_document
            db_doc = get_active_document(sender_chat_id)
            if db_doc:
                active_doc = db_doc
        except Exception as act_err:
            logger.warning(f"Failed to fetch active document from context: {act_err}")

    if not attachments:
        if active_doc:
            fname = active_doc.get("filename", "document")
            fpath = active_doc.get("path")
            fid = active_doc.get("file_id")
            if fid or (fpath and os.path.exists(str(fpath))):
                attachments = [{
                    "filename": fname,
                    "path": fpath,
                    "file_id": fid,
                    "caption": fname
                }]
    else:
        # Normalize attachments in case caller passes list of strings or incomplete dicts
        normalized_attachments = []
        for att in attachments:
            if isinstance(att, str):
                normalized_attachments.append({"filename": att, "path": att, "caption": att})
            elif isinstance(att, dict):
                normalized_attachments.append(dict(att))
        attachments = normalized_attachments

        for att in attachments:
            if not att.get("file_id") and active_doc and active_doc.get("file_id"):
                att["file_id"] = active_doc.get("file_id")
            if (not att.get("path") or not os.path.exists(str(att.get("path")))) and active_doc and active_doc.get("path"):
                att["path"] = active_doc.get("path")
            if not att.get("filename") and active_doc and active_doc.get("filename"):
                att["filename"] = active_doc.get("filename")

    # 1. Resolve Recipient Identities
    recipients = [resolve_recipient_identity(sender_chat_id, m) for m in raw_mentions]

    # 2. Deconstruct Directive Scopes (Strict Privacy Partitioning)
    directives = deconstruct_targeted_directives(text_content, recipients, attachments)

    # 3. Sender Display Name
    s_name = sender_name
    if not s_name:
        prof = db.get_user_profile(sender_chat_id)
        s_name = prof.get("first_name") or prof.get("username") or "Your teammate"

    sender_uid = db.resolve_user_id(sender_chat_id)
    delivery_summaries = []

    # 4. Atomic Outbox Dispatch
    for d in directives:
        recip = d.recipient
        priv_msg = d.private_message
        atts = d.attachments

        # Self-mention Guardrail: A user cannot dispatch collaborative tasks/files to themselves
        is_self = False
        if recip.telegram_chat_id and recip.telegram_chat_id == sender_chat_id:
            is_self = True
        elif recip.user_id and sender_uid and recip.user_id == sender_uid:
            is_self = True

        if is_self:
            logger.info(f"Self-mention detected for handle @{recip.handle} by sender {sender_chat_id}. Suppressing self-dispatch.")
            delivery_summaries.append(
                f"• **@{recip.handle}**: ⚠️ Ye aapka hi linked account/number hai! "
                f"Kisi doosre user ya teammate ko bhejne ke liye unka phone number ya @username use karein (jaise @9549408914 ya @8824466229)."
            )
            continue

        # Record in directed_dispatches table (Isolation Vault)
        d_id = db.record_directed_dispatch(
            sender_chat_id=sender_chat_id,
            sender_name=s_name,
            recipient_handle=recip.handle,
            private_message=priv_msg,
            target_channel=recip.target_channel,
            recipient_user_id=recip.user_id,
            recipient_chat_id=recip.telegram_chat_id,
            attachments=atts,
            raw_prompt=text_content
        )
        d.dispatch_id = d_id

        # Dispatch via Target Channel
        delivered = False

        # Channel A: Telegram
        if recip.target_channel == "telegram" and recip.telegram_chat_id:
            try:
                from main import send_telegram_text, send_file
                notification_text = f"📩 **Message from {s_name}:**\n\n\"{priv_msg}\""
                await send_telegram_text(recip.telegram_chat_id, notification_text)

                # Send attachments if any
                for att in atts:
                    file_path = att.get("path")
                    file_id = att.get("file_id")
                    att_name = att.get("filename") or (os.path.basename(file_path) if file_path else "document")
                    if file_id:
                        await send_file(
                            chat_id=recip.telegram_chat_id,
                            filename=att_name,
                            file_id=file_id,
                            caption=att.get("caption") or att_name
                        )
                    elif file_path and os.path.exists(file_path):
                        with open(file_path, "rb") as af:
                            file_bytes = af.read()
                        await send_file(
                            chat_id=recip.telegram_chat_id,
                            filename=att_name,
                            content_bytes=file_bytes,
                            caption=att.get("caption") or att_name
                        )

                db.mark_dispatch_delivered(d_id)
                delivered = True
                delivery_summaries.append(f"• **@{recip.handle}** ({recip.display_name}) via **Telegram**: Delivered ✅")
            except Exception as tg_err:
                logger.error(f"Telegram dispatch error to {recip.handle}: {tg_err}")
                delivery_summaries.append(f"• **@{recip.handle}** via Telegram: Failed ({tg_err})")

        # Channel B: In-App Notification (Web Companion / App Session)
        elif recip.target_channel == "app" or recip.user_id:
            try:
                target_uid = recip.user_id
                if not target_uid and recip.telegram_chat_id:
                    target_uid = db.resolve_user_id(recip.telegram_chat_id)

                if target_uid:
                    db.create_in_app_notification(
                        user_id=target_uid,
                        sender_name=s_name,
                        title=f"New task from {s_name}",
                        body=priv_msg,
                        attachments=atts
                    )
                    db.mark_dispatch_delivered(d_id)
                    delivered = True
                    delivery_summaries.append(f"• **@{recip.handle}** ({recip.display_name}) via **In-App Notification**: Delivered ✅")
                else:
                    delivery_summaries.append(f"• **@{recip.handle}**: Queued for App login (User ID pending)")
            except Exception as app_err:
                logger.error(f"In-App dispatch error to {recip.handle}: {app_err}")
                delivery_summaries.append(f"• **@{recip.handle}** via App: Failed ({app_err})")

        # Channel C: Not in Peculiar AI network / dimension
        else:
            handle_name = recip.display_name if recip.display_name and recip.display_name != recip.handle else recip.handle
            delivery_summaries.append(
                f"• **@{recip.handle}** ({handle_name}): This user is not connected to the Peculiar AI network yet. "
                f"Once they login with their phone or link their Telegram account to the bot, directives and files can be delivered directly to them."
            )

    has_self = any("Ye aapka hi linked" in s for s in delivery_summaries)
    has_other_directives = any("Ye aapka hi linked" not in s for s in delivery_summaries)
    status = "self_mention_error" if (has_self and not has_other_directives) else "success"

    summary = (
        f"🎯 **Targeted Mentions Dispatched:**\n"
        + "\n".join(delivery_summaries)
        + f"\n\n🔒 **Privacy Guard**: Each recipient only received their respective assigned directive."
    )

    return DispatchResult(
        status=status,
        sender_chat_id=sender_chat_id,
        sender_name=s_name,
        directives=directives,
        summary_message=summary
    )
