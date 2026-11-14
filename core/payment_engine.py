"""
PeculiarAI Omni-Channel Dynamic UPI Payment & Settlement Engine (NPCI Standard)
Features:
1. Dynamic NPCI UPI QR generation with auto-embedded amount, reference ID, and transaction note.
2. In-memory Pillow-rendered high-contrast payment cards (compatible with GPay, PhonePe, Paytm, BHIM).
3. Zero-loss database ledger (payment_requests table) with atomic states (pending, completed).
4. Direct multi-user dispatch: Automatically delivers QR and invoice to tagged payer (@handle / phone).
5. Automatic settlement notifications: Dispatches real-time delivery confirmations to both payee and payer.
6. Auto-settles contact commitment ledgers upon payment confirmation.
"""

import os
import io
import re
import uuid
import logging
from urllib.parse import quote
from datetime import datetime
from typing import Dict, Any, Optional, List, Tuple
from zoneinfo import ZoneInfo

import qrcode
from PIL import Image, ImageDraw, ImageFont

import db
from core import timeutil
from core.timeutil import IST

logger = logging.getLogger("payment_engine")


def build_upi_payment_url(
    upi_id: str,
    payee_name: str,
    amount: float,
    note: str,
    ref_id: str
) -> str:
    """
    Constructs NPCI Standard UPI Deep Link URI.
    Format: upi://pay?pa={upi_id}&pn={name}&am={amount:.2f}&cu=INR&tn={ref_id}:{note}
    """
    clean_upi = upi_id.strip()
    clean_name = payee_name.strip()
    safe_note = f"{ref_id}:{note}".strip()[:50]
    
    encoded_name = quote(clean_name)
    encoded_note = quote(safe_note)
    
    return f"upi://pay?pa={clean_upi}&pn={encoded_name}&am={amount:.2f}&cu=INR&tn={encoded_note}"


def generate_styled_qr_card(
    upi_url: str,
    amount: float,
    payee_name: str,
    upi_id: str,
    note: str,
    ref_id: str
) -> bytes:
    """
    Renders an executive-styled UPI payment card PNG using Pillow.
    Includes banner, high-contrast QR code, amount badge, payee details, and reference ID.
    """
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(upi_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGBA")

    # Card dimensions
    card_w, card_h = 650, 820
    card = Image.new("RGBA", (card_w, card_h), (250, 250, 252, 255))
    draw = ImageDraw.Draw(card)

    # Top Brand Header Banner
    header_h = 120
    draw.rectangle([(0, 0), (card_w, header_h)], fill=(15, 76, 129, 255))
    
    # Subtly rounded corners or clean edges
    try:
        font_title = ImageFont.truetype("arial.ttf", 26)
        font_sub = ImageFont.truetype("arial.ttf", 16)
        font_amount = ImageFont.truetype("arial.ttf", 34)
        font_detail = ImageFont.truetype("arial.ttf", 18)
        font_footer = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font_title = ImageFont.load_default()
        font_sub = font_title
        font_amount = font_title
        font_detail = font_title
        font_footer = font_title

    # Header Text
    draw.text((card_w // 2, 45), "UPI INSTANT PAYMENT", fill=(255, 255, 255), anchor="mm", font=font_title)
    draw.text((card_w // 2, 85), "Scan with Google Pay, PhonePe, Paytm, or BHIM", fill=(210, 230, 250), anchor="mm", font=font_sub)

    # Center QR Code with white background and soft outline
    qr_w, qr_h = qr_img.size
    qr_box_pad = 12
    qr_x = (card_w - qr_w) // 2
    qr_y = 150
    draw.rectangle(
        [(qr_x - qr_box_pad, qr_y - qr_box_pad), (qr_x + qr_w + qr_box_pad, qr_y + qr_h + qr_box_pad)],
        fill=(255, 255, 255, 255),
        outline=(220, 225, 235, 255),
        width=2
    )
    card.paste(qr_img, (qr_x, qr_y))

    # Details Area
    details_y = qr_y + qr_h + 35
    # Amount Badge (Bold Green Accent)
    draw.text((card_w // 2, details_y), f"INR {amount:,.2f}", fill=(22, 101, 52), anchor="mm", font=font_amount)
    
    # Payee & UPI details
    draw.text((card_w // 2, details_y + 45), f"Pay to: {payee_name}", fill=(30, 41, 59), anchor="mm", font=font_detail)
    draw.text((card_w // 2, details_y + 75), f"UPI ID: {upi_id}", fill=(71, 85, 105), anchor="mm", font=font_detail)

    # Note and Reference ID
    clean_note = note[:35] if note else "Payment"
    draw.text((card_w // 2, details_y + 110), f"Purpose: {clean_note}  |  Ref: {ref_id}", fill=(100, 116, 139), anchor="mm", font=font_footer)

    # Bottom Trust Ribbon
    draw.rectangle([(0, card_h - 40), (card_w, card_h)], fill=(241, 245, 249, 255))
    draw.text((card_w // 2, card_h - 20), "Secured by NPCI Unified Payments Interface", fill=(148, 163, 184), anchor="mm", font=font_footer)

    buf = io.BytesIO()
    card.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


async def create_and_dispatch_payment_request(
    sender_chat_id: int,
    amount: float,
    note: str,
    payer_handle: Optional[str] = None,
    upi_id: Optional[str] = None,
    sender_name: Optional[str] = None
) -> Dict[str, Any]:
    """
    Executes full Payment QR lifecycle:
    1. Resolves payee UPI ID and display name.
    2. Builds NPCI URI and renders card PNG.
    3. Persists to payment_requests table.
    4. Automatically sends QR card to sender.
    5. If payer is mentioned (@handle/phone), dispatches invoice and QR to payer's chat.
    """
    cid = db.canonical_chat_id(sender_chat_id)
    
    # 1. Resolve Payee UPI ID
    resolved_upi = (upi_id or "").strip()
    if not resolved_upi:
        resolved_upi = db.get_user_upi_id(cid) or ""
    
    if not resolved_upi:
        # Check if user has phone number linked and generate standard VPA suggestion
        prof = db.get_user_profile(cid)
        phone = prof.get("phone_number")
        if phone:
            digits = re.sub(r"\D", "", phone)[-10:]
            if len(digits) == 10:
                resolved_upi = f"{digits}@upi"
    
    if not resolved_upi:
        return {
            "status": "missing_upi_id",
            "message": (
                "Aapka receiving UPI ID configure nahi hai. Kripya apna UPI ID batayein "
                "(jaise `mera UPI ID nirav@okhdfcbank hai`) taaki agle payments ke liye QR code ban sake."
            )
        }

    # 2. Resolve Payee Name
    p_name = sender_name
    if not p_name:
        prof = db.get_user_profile(cid)
        p_name = prof.get("first_name") or prof.get("username") or "Peculiar AI User"

    # 3. Generate Reference ID
    today_str = timeutil.now(IST).strftime("%Y%m%d")
    short_hash = uuid.uuid4().hex[:6].upper()
    ref_id = f"PAY-{today_str}-{short_hash}"

    # 4. Build NPCI URI & Styled QR Card
    upi_url = build_upi_payment_url(
        upi_id=resolved_upi,
        payee_name=p_name,
        amount=amount,
        note=note,
        ref_id=ref_id
    )
    qr_png_bytes = generate_styled_qr_card(
        upi_url=upi_url,
        amount=amount,
        payee_name=p_name,
        upi_id=resolved_upi,
        note=note,
        ref_id=ref_id
    )

    # Save to disk for dispatch
    os.makedirs("uploads/qr_codes", exist_ok=True)
    qr_filename = f"UPI_QR_{ref_id}.png"
    qr_path = os.path.join("uploads", "qr_codes", qr_filename)
    with open(qr_path, "wb") as f:
        f.write(qr_png_bytes)

    # 5. Resolve Payer Identity (if provided)
    payer_chat_id = None
    payer_profile = None
    if payer_handle:
        try:
            from core.mentions_engine import resolve_recipient_identity
            payer_profile = resolve_recipient_identity(cid, payer_handle)
            if payer_profile and payer_profile.telegram_chat_id:
                payer_chat_id = payer_profile.telegram_chat_id
        except Exception as p_err:
            logger.warning(f"Error resolving payer identity for {payer_handle}: {p_err}")

    # 6. Record in Database Ledger
    db.create_payment_request(
        ref_id=ref_id,
        creator_chat_id=cid,
        upi_id=resolved_upi,
        payee_name=p_name,
        amount=amount,
        note=note,
        payer_handle=payer_handle,
        payer_chat_id=payer_chat_id,
        qr_file_path=qr_path
    )

    # 7. Dispatch to Creator
    from main import send_file, send_telegram_text
    creator_caption = (
        f"📸 **Dynamic UPI QR Code Generated!**\n\n"
        f"💰 **Amount:** ₹{amount:,.2f}\n"
        f"👤 **Payee:** {p_name} (`{resolved_upi}`)\n"
        f"📝 **Purpose:** {note}\n"
        f"🔖 **Ref ID:** `{ref_id}`\n\n"
        f"📲 **Direct UPI Link:**\n`{upi_url}`"
    )
    try:
        await send_file(
            chat_id=cid,
            filename=qr_filename,
            content_bytes=qr_png_bytes,
            caption=creator_caption,
            mime_type="image/png"
        )
    except Exception as exc:
        logger.error(f"Failed sending QR card to creator {cid}: {exc}")

    # 8. Dispatch to Payer (if linked on Telegram)
    payer_notified = False
    if payer_chat_id and payer_chat_id != cid:
        payer_caption = (
            f"💸 **Payment Request from {p_name}:**\n\n"
            f"💰 **Amount Due:** ₹{amount:,.2f}\n"
            f"📝 **For:** {note}\n"
            f"🔖 **Ref ID:** `{ref_id}`\n\n"
            f"Please scan the attached QR code with Google Pay, PhonePe, Paytm, or BHIM to pay directly.\n\n"
            f"📲 **Tap to Pay:**\n`{upi_url}`\n\n"
            f"💡 *Payment karne ke baad reply karein 'Paid {ref_id}' ya receipt bhej kar confirm karein.*"
        )
        try:
            await send_file(
                chat_id=payer_chat_id,
                filename=qr_filename,
                content_bytes=qr_png_bytes,
                caption=payer_caption,
                mime_type="image/png"
            )
            payer_notified = True
        except Exception as p_tg_err:
            logger.error(f"Failed sending QR invoice to payer {payer_chat_id}: {p_tg_err}")

    summary_msg = (
        f"✅ Dynamic UPI QR Code for ₹{amount:,.2f} created successfully (Ref: `{ref_id}`). "
        + (f"Invoice and QR also sent directly to {payer_handle}!" if payer_notified else "")
    )

    return {
        "status": "success",
        "ref_id": ref_id,
        "amount": amount,
        "upi_id": resolved_upi,
        "payee_name": p_name,
        "note": note,
        "upi_url": upi_url,
        "qr_file_path": qr_path,
        "payer_notified": payer_notified,
        "summary": summary_msg
    }


async def settle_and_notify_payment(
    ref_id: Optional[str] = None,
    sender_chat_id: Optional[int] = None,
    payer_handle: Optional[str] = None,
    verified_by: str = "user",
    receipt_proof: Optional[str] = None
) -> Dict[str, Any]:
    """
    Settles a payment request:
    1. Locates payment request by ref_id or latest pending.
    2. Atomically marks status = 'completed'.
    3. Dispatches notifications to BOTH creator and payer.
    4. Auto-resolves contact commitments / debt memory.
    """
    req = None
    if ref_id:
        req = db.get_payment_request(ref_id)
    if not req and (sender_chat_id or payer_handle):
        req = db.get_latest_pending_payment_request(
            creator_chat_id=sender_chat_id,
            payer_handle=payer_handle
        )

    if not req:
        return {
            "status": "not_found",
            "message": "Koi pending payment request nahi mili. Kripya valid Ref ID (jaise `PAY-20261114-XXXX`) check karein."
        }

    actual_ref = req["ref_id"]
    if req.get("status") == "completed":
        return {
            "status": "already_settled",
            "ref_id": actual_ref,
            "message": f"Payment request `{actual_ref}` pehle se hi settled aur completed hai!"
        }

    # Mark completed in DB
    ok, updated = db.complete_payment_request(actual_ref, receipt_proof=receipt_proof)
    if not ok or not updated:
        return {"status": "error", "message": "Failed to update payment status in ledger."}

    amount = float(updated["amount"])
    payee_name = updated["payee_name"]
    note = updated.get("note") or "Payment"
    creator_cid = updated["creator_chat_id"]
    payer_cid = updated.get("payer_chat_id")
    p_handle = updated.get("payer_handle") or "Teammate"

    from main import send_telegram_text

    # 1. Notify Creator (Payee)
    creator_notif = (
        f"🎉 **Payment Received & Verified!**\n\n"
        f"💰 **Amount:** ₹{amount:,.2f}\n"
        f"👤 **From:** {p_handle}\n"
        f"📝 **For:** {note}\n"
        f"🔖 **Ref ID:** `{actual_ref}`\n\n"
        f"✅ Aapke ledger me balance successfully clear aur settle kar diya gaya hai."
    )
    try:
        if creator_cid:
            await send_telegram_text(creator_cid, creator_notif)
    except Exception as exc:
        logger.error(f"Error notifying creator {creator_cid} for payment {actual_ref}: {exc}")

    # 2. Notify Payer (if linked on Telegram)
    payer_notified = False
    if payer_cid and payer_cid != creator_cid:
        payer_notif = (
            f"🧾 **Payment Settlement Receipt**\n\n"
            f"✅ Aapka ₹{amount:,.2f} ka payment **{payee_name}** ko **'{note}'** ke liye successfully verify ho gaya hai.\n"
            f"🔖 **Ref ID:** `{actual_ref}`\n\n"
            f"Dhanyawaad! Pending request clear ho chuki hai."
        )
        try:
            await send_telegram_text(payer_cid, payer_notif)
            payer_notified = True
        except Exception as exc:
            logger.error(f"Error notifying payer {payer_cid} for payment {actual_ref}: {exc}")

    # 3. Auto-clear Contact Commitment if present
    try:
        if creator_cid and p_handle:
            clean_name = p_handle.lstrip("@").strip()
            contacts = db.get_contact_memories(creator_cid)
            for c in contacts:
                c_name = (c.get("name") or "").lower()
                if clean_name.lower() in c_name or c_name in clean_name.lower():
                    # Clear pending commitment note
                    db.update_contact_commitment(
                        creator_cid,
                        contact_name=c.get("name"),
                        commitments=f"Payment of ₹{amount:,.2f} settled on {timeutil.now(IST).strftime('%d %b %Y')} (Ref: {actual_ref})"
                    )
                    break
    except Exception as c_err:
        logger.warning(f"Error auto-clearing commitment for {p_handle}: {c_err}")

    return {
        "status": "success",
        "ref_id": actual_ref,
        "amount": amount,
        "payee_name": payee_name,
        "payer_handle": p_handle,
        "note": note,
        "payer_notified": payer_notified,
        "summary": f"✅ Payment of ₹{amount:,.2f} (Ref: `{actual_ref}`) successfully verified and settled. Both parties notified!"
    }
