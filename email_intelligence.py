"""Autonomous Incoming Email Intelligence Engine.

Inspects incoming emails from connected user accounts, intelligently identifies
high-importance emails (interviews/meetings with video links, flight/train ticket
bookings with PNR, credit card / utility bills with due dates, and urgent deliveries/OTPs),
filters out marketing/promotional spam without hallucinations, and generates
instant structured alerts.
"""

import os
import re
import json
from datetime import datetime
from typing import Optional, Dict, Any

# Meeting link patterns
MEET_LINK_REGEX = re.compile(
    r"https?://(?:meet\.google\.com/[a-z0-9\-]+|"
    r"(?:[a-zA-Z0-9_\-]+\.)?zoom\.us/(?:j|my|s)/[0-9a-zA-Z?=&._\-]+|"
    r"teams\.microsoft\.com/(?:l/meetup-join/[a-zA-Z0-9%?=_&/.\-]+|[a-zA-Z0-9?=_&/.\-]+)|"
    r"[a-zA-Z0-9.\-_]*webex\.com/[a-zA-Z0-9?=_&/.\-]+)",
    re.IGNORECASE,
)

# PNR / Booking ID patterns
PNR_REGEX = re.compile(
    r"\b(?:pnr|booking\s*(?:id|ref|reference|no|number)?)\s*[:\-#]?\s*([A-Za-z0-9]{5,12})\b",
    re.IGNORECASE,
)

# Amount patterns (₹, Rs, INR, $)
AMOUNT_REGEX = re.compile(
    r"(?:(?:total\s*(?:amount\s*)?due|amount\s*payable|bill\s*amount|total\s*due)\s*[:\-]?\s*)?"
    r"(?:₹|rs\.?|inr|\$)\s*([\d,]+(?:\.\d{2})?)",
    re.IGNORECASE,
)

# Due Date patterns
DUE_DATE_REGEX = re.compile(
    r"(?:payment\s*)?due\s*date\s*[:\-]?\s*([0-9]{1,2}[-/\s][0-9]{1,2}[-/\s][0-9]{2,4}|"
    r"[0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+[0-9]{2,4})?|"
    r"[A-Za-z]+\s+[0-9]{1,2}(?:st|nd|rd|th)?,?\s+[0-9]{2,4})",
    re.IGNORECASE,
)

# Delivery OTP patterns
DELIVERY_OTP_REGEX = re.compile(
    r"\b(?:delivery\s*otp|delivery\s*pin|share\s*(?:this\s*)?(?:otp|pin)\s*with\s*delivery|"
    r"otp\s*for\s*delivery|otp\s*is|secret\s*code\s*is)\s*[:\-]?\s*(\d{4,8})\b",
    re.IGNORECASE,
)

# Pure promotional / spam signals
PROMOTIONAL_REGEX = re.compile(
    r"\b(?:unsubscribe|view\s*in\s*browser|opt\s*out|promotional|flash\s*sale|"
    r"discount\s*code|special\s*offer|exclusive\s*deal|50%\s*off|flat\s*\d+%\s*off|"
    r"newsletter|weekly\s*digest|shop\s*now|best\s*deals|clearance\s*sale)\b",
    re.IGNORECASE,
)


def _heuristic_analyze(sender: str, subject: str, body: str) -> Dict[str, Any]:
    """Fast, deterministic pattern matching fallback for classifying emails."""
    combined = f"From: {sender}\nSubject: {subject}\n{body}"
    combined_lower = combined.casefold()

    meet_match = MEET_LINK_REGEX.search(combined)
    meet_url = meet_match.group(0) if meet_match else None

    # 1. Job Interview / Meeting
    interview_signals = any(
        w in combined_lower
        for w in (
            "interview", "round 1", "round 2", "technical round", "hr round",
            "screening call", "hiring team", "assessment", "interview scheduled",
            "discussion with", "invitation to interview", "take-home assignment"
        )
    )
    meeting_signals = any(
        w in combined_lower
        for w in ("meeting scheduled", "calendar invitation", "scheduled a meeting", "video call")
    ) or (meet_url is not None and any(w in combined_lower for w in ("meet", "call", "discussion", "sync", "agenda")))

    if interview_signals or meeting_signals:
        company = "Company"
        # Try extracting company from subject e.g. "Interview with Google" or "Google | Technical Interview"
        comp_m = re.search(r"(?:interview\s+(?:with|at)|invitation\s+from|hiring\s+team\s+at)\s+([A-Za-z0-9\s&]+?)(?=\s+for|\s+on|\s*[-|:]|$)", subject, re.I)
        if comp_m:
            company = comp_m.group(1).strip()
        elif "@" in sender:
            dom = sender.split("@")[-1].split(".")[0].capitalize()
            if dom not in ("Gmail", "Yahoo", "Outlook", "Hotmail", "Icloud"):
                company = dom

        # Extract date/time hints
        dt_m = re.search(
            r"(?:on|dated?|schedule[d]?\s+for|at|date:?)\s+([0-9]{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]+(?:\s+[0-9]{2,4})?(?:\s+at\s+[0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm|ist|utc)?)?|[A-Za-z]+\s+[0-9]{1,2}(?:st|nd|rd|th)?,?(?:\s+[0-9]{2,4})?(?:\s+at\s+[0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm|ist|utc)?)?|[0-9]{1,2}[-/.][0-9]{1,2}[-/.][0-9]{2,4}(?:\s+[0-9]{1,2}:[0-9]{2}\s*(?:am|pm)?)?|[0-9]{1,2}(?::[0-9]{2})?\s*(?:am|pm|ist|utc))",
            body,
            re.I,
        )
        dt_str = dt_m.group(1).strip() if dt_m else "Check email details"

        return {
            "is_important": True,
            "category": "interview",
            "company": company,
            "role_or_title": subject.strip()[:100],
            "datetime_str": dt_str,
            "meet_link": meet_url or "Link in email",
            "interviewer": sender.strip()[:80],
            "details": {"meet_link": meet_url, "company": company, "subject": subject},
        }

    # 2. Ticket / Booking confirmation
    booking_signals = any(
        w in combined_lower
        for w in (
            "booking confirmed", "ticket confirmation", "e-ticket", "irctc", "boarding pass",
            "flight confirmed", "train ticket", "bus ticket", "movie ticket", "reservation confirmed"
        )
    )
    pnr_match = PNR_REGEX.search(combined)

    if booking_signals or pnr_match:
        pnr_val = pnr_match.group(1) if pnr_match else "Confirmed"
        carrier = "Travel Provider"
        for candidate in ("IRCTC", "IndiGo", "Air India", "SpiceJet", "MakeMyTrip", "EaseMyTrip", "Cleartrip", "RedBus", "BookMyShow", "Vistara", "Akasa"):
            if candidate.casefold() in combined_lower:
                carrier = candidate
                break

        # Route matching (e.g. DEL to BOM or Delhi -> Mumbai)
        route_m = re.search(r"([A-Za-z\s]+?)\s*(?:to|->|–|-)\s*([A-Za-z\s]+?)(?=\s+on|\s+dated|\s+flight|\s+train|\s*,|\.|$)", body, re.I)
        route_str = f"{route_m.group(1).strip()} ➔ {route_m.group(2).strip()}" if route_m else "Check email for route"

        return {
            "is_important": True,
            "category": "ticket_booking",
            "carrier": carrier,
            "pnr": pnr_val,
            "route": route_str,
            "datetime_str": "See booking details in mail",
            "details": {"pnr": pnr_val, "carrier": carrier, "subject": subject},
        }

    # 3. Credit Card / Bill Payment Due
    bill_signals = any(
        w in combined_lower
        for w in (
            "credit card statement", "total amount due", "payment due date", "bill statement",
            "electricity bill", "water bill", "broadband bill", "invoice amount", "minimum amount due",
            "bill payment reminder"
        )
    )
    due_m = DUE_DATE_REGEX.search(combined)
    amt_m = AMOUNT_REGEX.search(combined)

    if bill_signals or (due_m and amt_m):
        biller = "Bank / Utility"
        for candidate in ("HDFC", "SBI", "ICICI", "Axis Bank", "Kotak", "American Express", "Airtel", "Jio", "Tata Power", "Adani Electricity", "BESCOM", "Torrent Power"):
            if candidate.casefold() in combined_lower:
                biller = candidate
                break

        due_date_str = due_m.group(1).strip() if due_m else "Due Date in Email"
        amount_str = f"₹{amt_m.group(1)}" if amt_m else "See statement"

        return {
            "is_important": True,
            "category": "bill_payment",
            "biller": biller,
            "amount": amount_str,
            "due_date": due_date_str,
            "details": {"biller": biller, "amount": amount_str, "due_date": due_date_str},
        }

    # 4. Critical Delivery / OTP
    delivery_signals = any(
        w in combined_lower
        for w in ("out for delivery", "arriving today", "delivery partner", "package is arriving", "share otp with delivery")
    )
    otp_m = DELIVERY_OTP_REGEX.search(combined)

    if delivery_signals:
        service = "Delivery Service"
        for candidate in ("Amazon", "Flipkart", "Bluedart", "Delhivery", "Swiggy", "Zomato", "Blinkit", "Zepto"):
            if candidate.casefold() in combined_lower:
                service = candidate
                break

        otp_val = otp_m.group(1) if otp_m else None
        return {
            "is_important": True,
            "category": "delivery_urgent",
            "service": service,
            "otp": otp_val,
            "summary": subject.strip()[:100],
            "details": {"service": service, "otp": otp_val},
        }

    # 5. Check if purely promotional
    if PROMOTIONAL_REGEX.search(combined):
        return {
            "is_important": False,
            "category": "promotional",
            "details": {"subject": subject},
        }

    # Default to not important for unknown generic emails to avoid hallucinations
    return {
        "is_important": False,
        "category": "other",
        "details": {"subject": subject},
    }


def _format_alert(data: Dict[str, Any]) -> Optional[str]:
    """Format structured alert message with clean emojis and actionable guidance."""
    if not data or not data.get("is_important"):
        return None

    category = data.get("category")

    if category == "interview":
        comp = data.get("company") or "Company"
        role = data.get("role_or_title") or "Meeting / Discussion"
        dt = data.get("datetime_str") or "Check email for date & time"
        link = data.get("meet_link") or "Link in email"
        sender = data.get("interviewer") or "Recruiter"
        return (
            f"💼 <b>Interview / Meeting Scheduled!</b>\n\n"
            f"🏢 <b>Company/Organizer</b>: {comp}\n"
            f"📌 <b>Topic/Role</b>: {role}\n"
            f"📅 <b>Date & Time</b>: {dt}\n"
            f"🔗 <b>Meeting Link</b>: {link}\n"
            f"👤 <b>From</b>: {sender}\n\n"
            f"💡 <i>Tip: Aap chahein toh 'Add this interview to calendar' bol kar calendar me schedule karwa sakte hain.</i>"
        )

    elif category == "ticket_booking":
        carrier = data.get("carrier") or "Travel Provider"
        pnr = data.get("pnr") or "Confirmed"
        route = data.get("route") or "Details in email"
        dt = data.get("datetime_str") or "Details in email"
        return (
            f"🎫 <b>Ticket / Booking Confirmed!</b>\n\n"
            f"✈️ <b>Service/Provider</b>: {carrier}\n"
            f"🔢 <b>PNR / Booking ID</b>: <code>{pnr}</code>\n"
            f"📍 <b>Route / Venue</b>: {route}\n"
            f"📅 <b>Travel Date</b>: {dt}\n\n"
            f"💡 <i>Booking confirm ho gayi hai! Agar travel reminder lagana ho toh mujhe bata dein.</i>"
        )

    elif category == "bill_payment":
        biller = data.get("biller") or "Biller"
        amount = data.get("amount") or "See statement"
        due_date = data.get("due_date") or "See statement"
        return (
            f"💳 <b>Bill Payment / Due Date Alert!</b>\n\n"
            f"🏦 <b>Biller/Bank</b>: {biller}\n"
            f"💰 <b>Total Amount Due</b>: {amount}\n"
            f"⏳ <b>Payment Due Date</b>: {due_date}\n\n"
            f"⚠️ <i>Late fees se bachne ke liye due date se pehle payment kar lein.</i>"
        )

    elif category == "delivery_urgent":
        service = data.get("service") or "Delivery"
        otp = data.get("otp")
        summary = data.get("summary") or "Package arriving today"
        otp_line = f"🔑 <b>Delivery OTP/PIN</b>: <code>{otp}</code>\n" if otp else ""
        return (
            f"📦 <b>Urgent Delivery / Order Update!</b>\n\n"
            f"🚚 <b>Merchant/Courier</b>: {service}\n"
            f"{otp_line}"
            f"📋 <b>Status</b>: {summary}\n"
        )

    return None


async def analyze_incoming_email(sender: str, subject: str, body: str) -> Dict[str, Any]:
    """
    Intelligently analyze an incoming email.
    Uses Groq LLM (if available) with strict JSON output schema, or deterministic heuristic fallback.
    Returns:
        {
            "is_important": bool,
            "category": str,
            "alert_text": str or None,
            "raw_data": dict
        }
    """
    sender = sender or ""
    subject = subject or ""
    body = body or ""

    # Fast-check if obviously promotional spam with no critical indicators
    combined_sample = f"{sender} {subject} {body[:500]}".casefold()
    has_critical_token = any(
        k in combined_sample
        for k in ("interview", "meet.google.com", "zoom.us", "teams.microsoft.com", "pnr", "booking confirmed", "due date", "statement", "out for delivery")
    )
    if not has_critical_token and any(p in combined_sample for p in ("unsubscribe", "flash sale", "newsletter", "50% off", "promo code")):
        return {
            "is_important": False,
            "category": "promotional",
            "alert_text": None,
            "raw_data": {"subject": subject},
        }

    # Attempt AI classification if GROQ_API_KEY is configured
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        try:
            from llm import _chat_json

            system_prompt = (
                "You are an expert AI email intelligence assistant for a user's personal mailbox. "
                "Analyze the email and determine if it is HIGH IMPORTANCE and requires an instant user notification.\n\n"
                "Allowed categories:\n"
                "- 'interview': Job interview, HR screening, technical interview, client video call with recruiter/company. Contains company name, date/time, and video link (Google Meet, Zoom, Teams).\n"
                "- 'ticket_booking': Flight, train (IRCTC), bus, movie, or hotel booking confirmation with PNR, booking ID, route, date.\n"
                "- 'bill_payment': Credit card statement, electricity bill, mobile recharge/utility bill with total amount due and due date.\n"
                "- 'delivery_urgent': Order out for delivery today with a delivery OTP/PIN or urgent delivery status.\n"
                "- 'promotional': Sales, marketing, discounts, newsletters, social media digests (LinkedIn, Twitter), spam. Set is_important=false.\n\n"
                "Return JSON ONLY in this schema:\n"
                "{\n"
                '  "is_important": boolean,\n'
                '  "category": "interview" | "ticket_booking" | "bill_payment" | "delivery_urgent" | "promotional",\n'
                '  "company": string or null,\n'
                '  "role_or_title": string or null,\n'
                '  "datetime_str": string or null,\n'
                '  "meet_link": string or null,\n'
                '  "interviewer": string or null,\n'
                '  "carrier": string or null,\n'
                '  "pnr": string or null,\n'
                '  "route": string or null,\n'
                '  "biller": string or null,\n'
                '  "amount": string or null,\n'
                '  "due_date": string or null,\n'
                '  "service": string or null,\n'
                '  "otp": string or null,\n'
                '  "summary": string or null\n'
                "}\n"
                "Do NOT hallucinate. If details are not in the email, use null. Set is_important to false for marketing, advertisements, newsletters, or generic notifications."
            )

            email_content = f"From: {sender}\nSubject: {subject}\nBody:\n{body[:2500]}"
            llm_result = await _chat_json(system_prompt, email_content)

            if isinstance(llm_result, dict) and "is_important" in llm_result:
                # Also ensure meet link is caught from body if LLM missed it
                meet_match = MEET_LINK_REGEX.search(body)
                if meet_match and not llm_result.get("meet_link"):
                    llm_result["meet_link"] = meet_match.group(0)

                # Ensure PNR is caught if LLM missed it
                pnr_match = PNR_REGEX.search(body)
                if pnr_match and not llm_result.get("pnr"):
                    llm_result["pnr"] = pnr_match.group(1)

                is_imp = bool(llm_result.get("is_important"))
                if llm_result.get("category") == "promotional":
                    is_imp = False

                llm_result["is_important"] = is_imp
                alert = _format_alert(llm_result) if is_imp else None
                return {
                    "is_important": is_imp,
                    "category": llm_result.get("category", "other"),
                    "alert_text": alert,
                    "raw_data": llm_result,
                }
        except Exception as llm_err:
            print("Email intelligence LLM analysis fallback to heuristic:", repr(llm_err))

    # Fallback to deterministic heuristic analyzer
    heur = _heuristic_analyze(sender, subject, body)
    alert = _format_alert(heur) if heur.get("is_important") else None
    return {
        "is_important": heur.get("is_important", False),
        "category": heur.get("category", "other"),
        "alert_text": alert,
        "raw_data": heur,
    }
