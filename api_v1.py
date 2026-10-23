import os
import io
import re
import secrets
import asyncio
from datetime import datetime, date, time, timedelta
from typing import Optional, List, Dict, Any

import httpx
from fastapi import APIRouter, Request, Header, HTTPException, UploadFile, File, Form, Depends, Query
from pydantic import BaseModel, Field

from db import (
    engine, text, ensure_user, has_consent, give_consent,
    get_persona, set_persona, get_categories, set_categories,
    save_memory, list_memories, items_between, items_for_date,
    add_reminders, get_upcoming_reminders, get_google_token,
    delete_google_token, delete_user, export_data,
    get_board_cards, create_board_card, move_board_card, delete_board_card,
    get_smart_lists, create_smart_list, add_smart_list_item, toggle_smart_list_item,
    delete_smart_list_item, delete_smart_list, find_or_create_smart_list,
    get_user_rules, add_user_rule, delete_user_rule,
    get_serendipity_memories, delete_memory_items,
    get_contact_memories, save_contact_memory, delete_contact_memory
)
from categories import CATEGORIES, DEFAULTS
from timing import IST, build_plan, fmt_when
from llm import understand, extract, answer_conversation_memory, _chat_json
from voice import transcribe_audio_bytes
from multimodal import (
    analyze_image_with_groq,
    extract_text_from_pdf,
    summarize_document_content,
    answer_document_query,
)
from google_services import list_calendar_events, create_meeting
from google_auth import build_auth_url

router = APIRouter(prefix="/api/v1", tags=["Mobile App API v1"])

# In-memory document trunk storage for fast mobile Q&A
# doc_id -> {"filename": str, "content": str, "summary": str, "chat_id": int, "created_at": datetime}
DOCUMENT_TRUNK: Dict[str, Dict[str, Any]] = {}


# ---------- Authentication & User Helper ----------

async def get_current_chat_id(
    authorization: Optional[str] = Header(None),
    x_chat_id: Optional[int] = Header(None, alias="X-Chat-Id"),
    chat_id: Optional[int] = Query(None)
) -> int:
    """
    Resolves the authenticated user's chat_id.
    Supports:
      1. X-Chat-Id header
      2. Authorization: Bearer <token_or_chat_id>
      3. Query parameter chat_id
    """
    cid = x_chat_id or chat_id
    if not cid and authorization:
        parts = authorization.split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw = parts[1].strip()
            # If token is numeric (direct chat_id) or a session token
            if raw.isdigit():
                cid = int(raw)
            elif raw.startswith("demo_"):
                cid = 999999999
            else:
                # Check web_verifications SQLite table if token exists
                try:
                    import sqlite3
                    db_path = os.getenv("PECULIARAI_WEB_VERIFY_DB", "peculiarai_web_verify.sqlite3")
                    with sqlite3.connect(db_path) as conn:
                        row = conn.execute(
                            "SELECT telegram_chat_id FROM web_verifications WHERE token = ? AND verified = 1",
                            (raw,)
                        ).first()
                        if row and row[0]:
                            cid = int(row[0])
                except Exception:
                    pass

    if not cid:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized: Please provide X-Chat-Id header or Bearer authorization token."
        )

    try:
        ensure_user(cid)
    except Exception:
        pass
    return int(cid)


def get_db_user_id(chat_id: int) -> Optional[int]:
    """Safely retrieves the integer database user_id for a given telegram_chat_id."""
    try:
        with engine.connect() as conn:
            user_row = conn.execute(
                text("SELECT id FROM users WHERE telegram_chat_id = :cid"),
                {"cid": chat_id}
            ).first()
            return int(user_row[0]) if user_row else None
    except Exception:
        return None


# ---------- Pydantic Schemas ----------

class SessionLoginRequest(BaseModel):
    chat_id: Optional[int] = Field(None, description="Telegram Chat ID or unique User ID")
    phone: Optional[str] = Field(None, description="User phone number (optional)")

class SessionResponse(BaseModel):
    token: str
    chat_id: int
    persona: Optional[str] = None
    has_consent: bool = False
    categories: List[str] = []
    message: str

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="User message text")
    history: Optional[List[Dict[str, str]]] = Field(default=[], description="Recent conversation turns")

class ChatResponse(BaseModel):
    reply: str
    intent: str
    action_taken: bool = False
    action_type: Optional[str] = None
    saved_item: Optional[Dict[str, Any]] = None
    meeting: Optional[Dict[str, Any]] = None

class DocumentQueryReq(BaseModel):
    query: str = Field(..., min_length=1, description="Question about the uploaded document")
    doc_id: Optional[str] = Field(None, description="Specific document ID in digital trunk")

class CrossQueryReq(BaseModel):
    query: str = Field(..., min_length=1, description="Cross-tool question (e.g., meetings + tasks + priorities)")

# --- Phase 2 Schemas ---

class BoardCardCreateReq(BaseModel):
    title: str = Field(..., min_length=1, description="Card title")
    description: Optional[str] = Field("", description="Card details / notes")
    status: Optional[str] = Field("queue", pattern="^(queue|this_week|today|done)$", description="Kanban column")
    priority: Optional[str] = Field("medium", pattern="^(low|medium|high)$", description="Priority level")
    due_date: Optional[str] = Field(None, description="Due date YYYY-MM-DD")

class BoardCardMoveReq(BaseModel):
    status: str = Field(..., pattern="^(queue|this_week|today|done)$", description="Target column status")

class SmartListCreateReq(BaseModel):
    name: str = Field(..., min_length=1, description="List name (e.g., Grocery, Packing)")
    category: Optional[str] = Field("general", description="List category")

class SmartListItemCreateReq(BaseModel):
    title: str = Field(..., min_length=1, description="Item name")
    quantity: Optional[str] = Field(None, description="Quantity / spec (e.g. 2 kg, 1 pack)")

class SmartListItemToggleReq(BaseModel):
    checked: Optional[bool] = Field(None, description="Target checked state (or toggle if null)")

class UserRuleCreateReq(BaseModel):
    rule: str = Field(..., min_length=3, description="Rule text (e.g., Never schedule meetings before 10 AM)")

class SerendipityActionReq(BaseModel):
    action: str = Field(..., pattern="^(keep|delete)$", description="Action to perform on memory item")

class ContactMemoryCreateReq(BaseModel):
    name: str = Field(..., min_length=1, description="Person's name")
    company: Optional[str] = Field(None, description="Company / Organization")
    role_title: Optional[str] = Field(None, description="Job title / role")
    email: Optional[str] = Field(None, description="Email address")
    phone: Optional[str] = Field(None, description="Phone number")
    met_at: Optional[str] = Field(None, description="Event / location where you met")
    context_notes: Optional[str] = Field(None, description="What was discussed / why it mattered")
    commitments: Optional[str] = Field(None, description="Promises, follow-ups or next steps")


# ---------- 1. Authentication Endpoints ----------

@router.post("/auth/session", response_model=SessionResponse, summary="Create/Restore App Session")
async def create_app_session(body: SessionLoginRequest):
    """
    Authenticate mobile app user and issue a session token.
    If chat_id is passed, user is verified or registered directly.
    """
    cid = body.chat_id
    if not cid:
        # Default demo/guest mobile user if none provided
        cid = 100000001

    try:
        ensure_user(cid)
    except Exception:
        pass
    try:
        consent = has_consent(cid)
    except Exception:
        consent = False
    try:
        persona = get_persona(cid) or "General"
    except Exception:
        persona = "General"
    try:
        cats = get_categories(cid)
    except Exception:
        cats = []
    if not cats:
        cats = DEFAULTS.get(persona, list(CATEGORIES.keys()))

    # Persistent mobile session token
    token = f"app_sess_{cid}_{secrets.token_hex(8)}"

    return SessionResponse(
        token=token,
        chat_id=cid,
        persona=persona,
        has_consent=consent,
        categories=cats,
        message="Session active. Connected to PeculiarAI Mobile Backend."
    )


@router.get("/auth/me", summary="Get Current Mobile User Profile")
async def get_current_user_profile(chat_id: int = Depends(get_current_chat_id)):
    """
    Returns user details, active persona, consent, and tracked categories.
    """
    try:
        persona = get_persona(chat_id) or "General"
    except Exception:
        persona = "General"
    try:
        consent = has_consent(chat_id)
    except Exception:
        consent = False
    try:
        cats = get_categories(chat_id) or list(CATEGORIES.keys())
    except Exception:
        cats = list(CATEGORIES.keys())
    return {
        "chat_id": chat_id,
        "persona": persona,
        "consented": consent,
        "categories": cats,
        "all_available_categories": CATEGORIES,
    }



@router.post("/auth/consent", summary="Grant or Revoke Assistant Consent")
async def update_consent(
    action: str = Query("give", pattern="^(give|revoke)$"),
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Enables or revokes AI assistant storage consent.
    """
    if action == "give":
        give_consent(chat_id)
        return {"status": "ok", "consented": True, "message": "Consent granted successfully."}
    else:
        # Revoke & delete data
        delete_user(chat_id)
        return {"status": "ok", "consented": False, "message": "Consent revoked and user data cleared."}


# ---------- 2. Chat & Action Execution Engine ----------

async def _execute_user_action(chat_id: int, text_content: str, history: List[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    Core NLP pipeline:
    Analyzes user command, performs database writes / Google calendar creations, and returns structured result.
    """
    clean = text_content.strip()
    now = datetime.now(IST)

    # 1. Classify intent via Groq LLM
    try:
        r = await understand(clean)
    except Exception as exc:
        print("[api_v1] understand error:", repr(exc))
        r = {"intent": "chat"}

    intent = r.get("intent", "chat")

    # Intent A: Save reminder / task / bill / appointment
    if intent == "save_item" and r.get("item"):
        item = r["item"]
        cat = item.get("category", "general")
        title = item.get("title", "Task")
        amount = item.get("amount")
        due_date = item.get("due_date")
        due_time = item.get("due_time")

        # Normalize dates
        if isinstance(due_date, str):
            try:
                due_date = datetime.strptime(due_date, "%Y-%m-%d").date()
            except Exception:
                due_date = now.date()
        if isinstance(due_time, str):
            try:
                due_time = datetime.strptime(due_time, "%H:%M").time()
            except Exception:
                due_time = None

        item_id = save_memory(chat_id, cat, title, amount, due_date, due_time)
        plan = build_plan(due_date, due_time, now)
        if plan.get("reminders") and item_id:
            add_reminders(item_id, plan["reminders"])

        time_str = f" at {due_time.strftime('%H:%M')}" if due_time else ""
        date_str = f" on {due_date.strftime('%d %b')}" if due_date else ""
        reply_msg = f"Saved ✅ {CATEGORIES.get(cat, cat.title())}: {title}{date_str}{time_str}"
        if amount:
            reply_msg += f" | Amount: {amount}"

        return {
            "reply": reply_msg,
            "intent": "save_item",
            "action_taken": True,
            "action_type": "memory_saved",
            "saved_item": {
                "id": item_id,
                "category": cat,
                "title": title,
                "amount": amount,
                "due_date": str(due_date) if due_date else None,
                "due_time": due_time.strftime("%H:%M") if due_time else None,
            }
        }

    # Intent B: Create Google Calendar Meeting
    if intent == "create_meeting":
        title = r.get("title") or "Meeting"
        start_iso = r.get("start_iso")
        duration = r.get("duration_minutes", 30)
        attendees = r.get("attendees", [])

        db_user_id = get_db_user_id(chat_id)
        token_data = get_google_token(db_user_id) if db_user_id else None

        if token_data and start_iso:
            try:
                event = await create_meeting(
                    user_id=db_user_id,
                    title=title,
                    start_iso=start_iso,
                    duration_minutes=duration,
                    attendees=attendees,
                    create_meet=True
                )
                meet_link = event.get("meet_link") or event.get("html_link")
                reply_msg = f"Meeting '{title}' scheduled in Google Calendar ✅\nTime: {start_iso}"
                if meet_link:
                    reply_msg += f"\nGoogle Meet Link: {meet_link}"
                return {
                    "reply": reply_msg,
                    "intent": "create_meeting",
                    "action_taken": True,
                    "action_type": "google_meeting_created",
                    "meeting": event
                }
            except Exception as exc:
                print("[api_v1] create_meeting error:", repr(exc))

        # If Google not connected or start_iso missing, save as local calendar reminder
        due_date = now.date()
        due_time = time(17, 0)
        if start_iso:
            try:
                dt_obj = datetime.fromisoformat(start_iso)
                due_date = dt_obj.date()
                due_time = dt_obj.time()
            except Exception:
                pass

        item_id = save_memory(chat_id, "appointments", f"Meeting: {title}", None, due_date, due_time)
        return {
            "reply": f"Saved meeting reminder ✅: {title} on {due_date} at {due_time.strftime('%H:%M')}",
            "intent": "create_meeting",
            "action_taken": True,
            "action_type": "local_meeting_saved",
            "saved_item": {"id": item_id, "title": title, "due_date": str(due_date), "due_time": str(due_time)}
        }

    # Intent C: Conversational Assistant Chat (Groq LLM)
    messages = [
        {
            "role": "system",
            "content": (
                "You are PeculiarAI, an elite executive personal assistant inspired by Memorae AI. "
                "You are friendly, proactive, concise, and helpful. You support English, Hindi, and Hinglish. "
                "Help the user track their day, schedule tasks, plan meetings, and answer queries. "
                "Keep responses within 2-3 clear sentences."
            )
        }
    ]
    if history:
        for turn in history[-6:]:
            if isinstance(turn, dict) and turn.get("role") and turn.get("content"):
                messages.append({"role": turn["role"], "content": turn["content"][:2000]})

    messages.append({"role": "user", "content": clean})

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"},
                json={"model": os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b", "temperature": 0.4, "messages": messages},
            )
            resp.raise_for_status()
            reply_text = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print("[api_v1] chat completion error:", repr(exc))
        reply_text = "Main aapki reminders, meetings, documents aur daily agenda manage karne me madad karta hu. Aap bataiye kya karna hai?"

    return {
        "reply": reply_text,
        "intent": intent,
        "action_taken": False
    }


@router.post("/chat/message", response_model=ChatResponse, summary="Send Message to Assistant")
async def chat_message(
    body: ChatRequest,
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Text-based interaction from the mobile app chat screen.
    Parses dates, tasks, bills, meetings, or casual conversation and executes them.
    """
    res = await _execute_user_action(chat_id, body.message, body.history or [])
    return ChatResponse(**res)


# ---------- 3. Voice Note Transcription & Parsing ----------

@router.post("/chat/voice", summary="Upload Voice Note & Parse Actions")
async def chat_voice(
    file: UploadFile = File(...),
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Uploads a mobile voice note (.m4a, .wav, .mp3, .ogg).
    Transcribes audio with Groq Whisper and immediately parses and executes user actions.
    """
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Audio file cannot be empty")

    filename = file.filename or "voice.m4a"
    mime_type = file.content_type or "audio/m4a"

    try:
        transcript, lang = await transcribe_audio_bytes(content, filename, mime_type)
    except Exception as exc:
        print("[api_v1] voice transcription error:", repr(exc))
        raise HTTPException(status_code=500, detail="Voice transcription failed. Check GROQ configuration.")

    if not transcript:
        return {
            "transcript": "",
            "reply": "Aawaz saaf sunai nahi di. Please dobara boliye.",
            "action_taken": False
        }

    # Execute intent on the recognized voice transcript
    action_res = await _execute_user_action(chat_id, transcript)
    return {
        "transcript": transcript,
        "language": lang,
        **action_res
    }


# ---------- 4. Image-to-Action Scanning (OCR & Extraction) ----------

@router.post("/chat/scan", summary="Scan Receipt, Flyer, or Boarding Pass")
async def scan_image(
    image: UploadFile = File(...),
    caption: Optional[str] = Form(""),
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Uploads a photo/receipt/ticket from phone camera.
    Analyzes visual text with Groq Vision LLM and extracts amounts, dates, or tasks.
    """
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Image file cannot be empty")

    try:
        analysis = await analyze_image_with_groq(content, caption or "")
    except Exception as exc:
        print("[api_v1] image analysis error:", repr(exc))
        raise HTTPException(status_code=500, detail="Image scanning failed. Check Vision model.")

    # Check if analysis has bill or meeting to auto-save
    action_res = await _execute_user_action(chat_id, f"Process this scanned document: {analysis}")

    return {
        "analysis": analysis,
        "reply": action_res.get("reply") or analysis[:300],
        "action_taken": action_res.get("action_taken", False),
        "saved_item": action_res.get("saved_item")
    }


# ---------- 5. Document Trunk (PDF/Doc Vault & Q&A) ----------

@router.post("/chat/document", summary="Upload PDF / Document to Vault")
async def upload_document(
    document: UploadFile = File(...),
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Uploads a PDF or text document to the user's digital trunk.
    Extracts text, creates an executive summary, and makes it available for instant Q&A.
    """
    content = await document.read()
    if not content:
        raise HTTPException(status_code=400, detail="Document cannot be empty")

    filename = document.filename or "document.pdf"
    lower_name = filename.lower()

    if lower_name.endswith(".pdf"):
        text_content = extract_text_from_pdf(content)
    else:
        try:
            text_content = content.decode("utf-8", errors="ignore")
        except Exception:
            text_content = ""

    if not text_content:
        raise HTTPException(status_code=400, detail="Could not extract readable text from this document")

    summary = await summarize_document_content(text_content, filename)
    doc_id = secrets.token_hex(6)

    DOCUMENT_TRUNK[doc_id] = {
        "doc_id": doc_id,
        "filename": filename,
        "content": text_content,
        "summary": summary,
        "chat_id": chat_id,
        "created_at": datetime.now(IST).isoformat(),
    }

    return {
        "doc_id": doc_id,
        "filename": filename,
        "summary": summary,
        "message": f"Document '{filename}' added to Document Trunk successfully."
    }


@router.post("/chat/document/query", summary="Ask Question About Uploaded Document")
async def query_document(
    body: DocumentQueryReq,
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Ask any specific question against documents saved in the Document Trunk.
    """
    target_doc = None
    if body.doc_id and body.doc_id in DOCUMENT_TRUNK:
        target_doc = DOCUMENT_TRUNK[body.doc_id]
    else:
        # Search for user's latest uploaded document
        for doc in reversed(list(DOCUMENT_TRUNK.values())):
            if doc["chat_id"] == chat_id:
                target_doc = doc
                break

    if not target_doc:
        raise HTTPException(status_code=404, detail="No document found in your vault. Please upload a PDF first.")

    reply = await answer_document_query(
        filename=target_doc["filename"],
        content=target_doc["content"],
        query=body.query,
        history=[],
        user_lang="en"
    )

    return {
        "doc_id": target_doc["doc_id"],
        "filename": target_doc["filename"],
        "reply": reply
    }


# ---------- 6. Free Weather Service (Open-Meteo) ----------

async def get_free_weather(city: str = "Delhi") -> Optional[Dict[str, Any]]:
    """
    Fetches real-time weather using Open-Meteo API.
    100% Free, no API key or subscription required.
    """
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            geo_res = await client.get(
                f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
            )
            if geo_res.status_code == 200 and geo_res.json().get("results"):
                res = geo_res.json()["results"][0]
                lat, lon, name = res["latitude"], res["longitude"], res.get("name", city)

                w_res = await client.get(
                    f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true&daily=temperature_2m_max,temperature_2m_min,uv_index_max&hourly=uv_index&timezone=auto"
                )
                if w_res.status_code == 200:
                    data = w_res.json()
                    cw = data.get("current_weather", {})
                    daily = data.get("daily", {})
                    max_temp = daily.get("temperature_2m_max", [cw.get("temperature")])[0]
                    min_temp = daily.get("temperature_2m_min", [cw.get("temperature")])[0]
                    uv_max_list = daily.get("uv_index_max", [0.0])
                    uv_max = float(uv_max_list[0]) if uv_max_list else 0.0

                    if uv_max >= 8:
                        uv_alert = "Very High (Sunglasses & high SPF sunscreen recommended)"
                    elif uv_max >= 6:
                        uv_alert = "High (Sunscreen recommended during midday)"
                    elif uv_max >= 3:
                        uv_alert = "Moderate"
                    else:
                        uv_alert = "Low"

                    return {
                        "city": name,
                        "temperature": cw.get("temperature"),
                        "max_temp": max_temp,
                        "min_temp": min_temp,
                        "windspeed": cw.get("windspeed"),
                        "condition_code": cw.get("weathercode", 0),
                        "uv_index_max": uv_max,
                        "uv_peak_hours": "11:30 AM - 02:30 PM",
                        "uv_alert": uv_alert
                    }
    except Exception as exc:
        print("[api_v1] weather fetch error:", repr(exc))
    return None


# ---------- 7. Workspace: The Office (Daily Dashboard) ----------

@router.get("/workspace/office", summary="Get Daily Office Agenda & Focus Data")
async def get_office_workspace(
    city: Optional[str] = Query("Delhi", description="City name for weather briefing"),
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Mobile Home Dashboard ('The Office'):
    Returns today's agenda, weather forecast, active reminders, tasks, and Google meetings.
    """
    now = datetime.now(IST)
    today = now.date()

    # 1. Fetch Today's Memory Items from Database
    raw_today_items = items_for_date(chat_id, today)
    today_items = []
    for row in raw_today_items:
        # row: (category, title, amount, due_time)
        today_items.append({
            "category": row[0],
            "title": row[1],
            "amount": row[2],
            "due_time": row[3].strftime("%H:%M") if row[3] else None
        })

    # 2. Fetch Upcoming Reminders (next 24-48 hours)
    raw_reminders = get_upcoming_reminders(chat_id, now)
    upcoming = []
    for r in raw_reminders[:10]:
        # r: (remind_at, label, category, title, amount, due_time)
        upcoming.append({
            "remind_at": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            "label": r[1],
            "category": r[2],
            "title": r[3],
            "amount": r[4]
        })

    # 3. Check Google Calendar Meetings for Today
    google_meetings = []
    is_google_connected = False
    db_user_id = get_db_user_id(chat_id)
    if db_user_id:
        token_info = get_google_token(db_user_id)
        if token_info:
            is_google_connected = True
            try:
                today_start = datetime.combine(today, time.min, tzinfo=IST).isoformat()
                today_end = datetime.combine(today, time.max, tzinfo=IST).isoformat()
                events = await list_calendar_events(db_user_id, min_time=today_start, max_time=today_end)
                google_meetings = events
            except Exception as exc:
                print("[api_v1] google meetings fetch error:", repr(exc))

    # 4. Fetch Free Local Weather
    weather = await get_free_weather(city or "Delhi")

    return {
        "date": today.isoformat(),
        "formatted_date": today.strftime("%A, %d %B %Y"),
        "weather": weather,
        "google_connected": is_google_connected,
        "today_agenda": today_items,
        "google_meetings": google_meetings,
        "upcoming_reminders": upcoming,
        "stats": {
            "today_tasks_count": len(today_items),
            "meetings_count": len(google_meetings),
            "upcoming_reminders_count": len(upcoming)
        }
    }


# ---------- 8. Workspace: The Park (Memory Bubbles & Vault) ----------

@router.get("/workspace/park", summary="Get Categorized Memory Bubbles")
async def get_park_workspace(chat_id: int = Depends(get_current_chat_id)):
    """
    Mobile Long-Term Vault ('The Park'):
    Returns memories organized as floating 'Memory Bubbles' by category.
    """
    memories = list_memories(chat_id)
    bubbles: Dict[str, List[Dict[str, Any]]] = {
        "bills": [],
        "appointments": [],
        "bookings": [],
        "general": []
    }

    for row in memories:
        # row: (category, title, amount, due_date, due_time)
        cat = row[0] if row[0] in bubbles else "general"
        bubbles[cat].append({
            "category": row[0],
            "title": row[1],
            "amount": row[2],
            "due_date": str(row[3]) if row[3] else None,
            "due_time": row[4].strftime("%H:%M") if row[4] else None
        })

    # Also list uploaded Document Trunk summaries
    user_docs = [
        {"doc_id": d["doc_id"], "filename": d["filename"], "summary": d["summary"], "created_at": d["created_at"]}
        for d in DOCUMENT_TRUNK.values() if d["chat_id"] == chat_id
    ]

    return {
        "memory_bubbles": bubbles,
        "documents_vault": user_docs,
        "total_active_memories": len(memories)
    }


@router.get("/workspace/park/search", summary="Semantic Memory & Task Search")
async def search_memories(
    q: str = Query(..., min_length=1, description="Search keyword or question"),
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Searches across the user's stored memories, notes, and tasks.
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.category, m.title, m.amount, m.due_date, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :cid AND (m.title ILIKE :q OR m.category ILIKE :q) "
                "ORDER BY m.id DESC LIMIT 30"
            ),
            {"cid": chat_id, "q": f"%{q}%"}
        ).fetchall()

    results = []
    for r in rows:
        results.append({
            "category": r[0],
            "title": r[1],
            "amount": r[2],
            "due_date": str(r[3]) if r[3] else None,
            "due_time": r[4].strftime("%H:%M") if r[4] else None
        })

    return {
        "query": q,
        "total_results": len(results),
        "matches": results
    }


# ---------- 9. Free Integrations Hub & Cross-Tool Querying ----------

@router.get("/tools/status", summary="Check Status of All Free Connected Tools")
async def get_tools_status(chat_id: int = Depends(get_current_chat_id)):
    """
    Displays the active connection status of 100% Free Tools.
    """
    # 1. Google Status
    is_google = False
    google_email = None
    db_user_id = get_db_user_id(chat_id)
    if db_user_id:
        token_info = get_google_token(db_user_id)
        if token_info:
            is_google = True
            google_email = token_info.get("google_email")

    has_github = bool(os.getenv("GITHUB_TOKEN"))
    has_notion = bool(os.getenv("NOTION_API_KEY"))
    has_trello = bool(os.getenv("TRELLO_API_KEY") and os.getenv("TRELLO_TOKEN"))

    return {
        "integrations": [
            {
                "id": "google",
                "name": "Google (Calendar & Gmail)",
                "free": True,
                "connected": is_google,
                "details": f"Connected as {google_email}" if is_google else "Not linked"
            },
            {
                "id": "github",
                "name": "GitHub (PRs & Issues)",
                "free": True,
                "connected": has_github,
                "details": "Connected via GITHUB_TOKEN" if has_github else "Add GITHUB_TOKEN in .env (100% Free)"
            },
            {
                "id": "notion",
                "name": "Notion Workspace",
                "free": True,
                "connected": has_notion,
                "details": "Connected via NOTION_API_KEY" if has_notion else "Add NOTION_API_KEY in .env (100% Free)"
            },
            {
                "id": "trello",
                "name": "Trello Boards",
                "free": True,
                "connected": has_trello,
                "details": "Connected via TRELLO_API_KEY" if has_trello else "Add TRELLO_API_KEY in .env (Optional)"
            },
            {
                "id": "outlook",
                "name": "Microsoft Outlook (Calendar & Mail)",
                "free": True,
                "connected": False,
                "details": "Ready to connect via Microsoft Graph (100% Free)"
            },
            {
                "id": "weather",
                "name": "Open-Meteo Weather Service",
                "free": True,
                "connected": True,
                "details": "Active (100% Free & Open Source)"
            }
        ]
    }


@router.get("/tools/google/connect-url", summary="Generate Google Connect Link for Mobile")
async def get_google_connect_url(chat_id: int = Depends(get_current_chat_id)):
    """
    Returns the Google OAuth 2.0 authorization URL for the mobile app WebView.
    """
    try:
        url = build_auth_url(chat_id, source="mobile")
        return {"auth_url": url, "status": "ok"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Google OAuth not configured: {str(exc)}")


@router.post("/tools/google/disconnect", summary="Disconnect Google for Mobile User")
async def disconnect_google(chat_id: int = Depends(get_current_chat_id)):
    """
    Revokes and deletes saved Google OAuth tokens for this user.
    """
    db_user_id = get_db_user_id(chat_id)
    if db_user_id:
        delete_google_token(db_user_id)
    return {"status": "ok", "message": "Google account unlinked successfully."}


@router.post("/tools/cross-query", summary="Unified Cross-Tool AI Query")
async def execute_cross_tool_query(
    body: CrossQueryReq,
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Unified Cross-Tool Querying:
    Queries Google Calendar, stored memories, and active integrations in parallel,
    then generates a unified executive answer.
    """
    query = body.query.strip()
    now = datetime.now(IST)

    # 1. Fetch relevant memories from DB
    with engine.connect() as conn:
        mem_rows = conn.execute(
            text(
                "SELECT category, title, amount, due_date, due_time FROM memory_items m "
                "JOIN users u ON u.id = m.user_id WHERE u.telegram_chat_id = :cid "
                "ORDER BY m.id DESC LIMIT 15"
            ),
            {"cid": chat_id}
        ).fetchall()

    mem_context = "\n".join([
        f"- [{r[0]}] {r[1]} (Amount: {r[2]}, Date: {r[3]}, Time: {r[4]})"
        for r in mem_rows
    ])

    # 2. Fetch upcoming Google Calendar events
    cal_context = "No Google Calendar connected or no events."
    db_user_id = get_db_user_id(chat_id)
    if db_user_id:
        token_info = get_google_token(db_user_id)
        if token_info:
            try:
                events = await list_calendar_events(
                    db_user_id,
                    min_time=now.isoformat(),
                    max_time=(now + timedelta(days=7)).isoformat()
                )
                if events:
                    cal_context = "\n".join([
                        f"- Meeting: {e.get('summary')} at {e.get('start', {}).get('dateTime', 'TBD')}"
                        for e in events[:5]
                    ])
            except Exception as exc:
                print("[api_v1] cross-query cal error:", repr(exc))

    # 3. Fetch GitHub issues/PRs if GITHUB_TOKEN is present
    gh_context = ""
    gh_token = os.getenv("GITHUB_TOKEN")
    if gh_token:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                gh_res = await client.get(
                    "https://api.github.com/user/issues?filter=assigned&state=open",
                    headers={"Authorization": f"Bearer {gh_token}", "User-Agent": "PeculiarAI"}
                )
                if gh_res.status_code == 200:
                    gh_issues = gh_res.json()
                    if gh_issues and isinstance(gh_issues, list):
                        gh_context = "\n".join([f"- GitHub Issue #{it.get('number')}: {it.get('title')}" for it in gh_issues[:4]])
        except Exception as exc:
            print("[api_v1] gh cross-query error:", repr(exc))

    # 4. Fetch Notion docs if NOTION_API_KEY is present
    notion_context = ""
    notion_key = os.getenv("NOTION_API_KEY")
    if notion_key:
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                n_res = await client.post(
                    "https://api.notion.com/v1/search",
                    headers={"Authorization": f"Bearer {notion_key}", "Notion-Version": "2022-06-28"},
                    json={"page_size": 4}
                )
                if n_res.status_code == 200:
                    n_results = n_res.json().get("results", [])
                    if n_results:
                        notion_context = "\n".join([f"- Notion Item: {p.get('url', 'Doc')}" for p in n_results[:3]])
        except Exception as exc:
            print("[api_v1] notion cross-query error:", repr(exc))

    extra_tools_context = ""
    if gh_context:
        extra_tools_context += f"\nContext from GitHub:\n{gh_context}\n"
    if notion_context:
        extra_tools_context += f"\nContext from Notion:\n{notion_context}\n"

    prompt = (
        f"You are PeculiarAI performing a Cross-Tool Query across the user's workspace.\n\n"
        f"User Query: {query}\n\n"
        f"Context from Stored Memories & Tasks:\n{mem_context or 'No saved tasks'}\n\n"
        f"Context from Google Calendar:\n{cal_context}\n"
        f"{extra_tools_context}\n"
        f"Provide a clear, cohesive answer answering the user's question by combining insights across their tools. "
        f"Be direct, structured, and helpful."
    )

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"},
                json={
                    "model": os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b",
                    "temperature": 0.3,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as exc:
        print("[api_v1] cross-query LLM error:", repr(exc))
        answer = "Cross-tool query execution is temporarily unavailable."

    return {
        "query": query,
        "answer": answer,
        "sources_checked": ["Stored Memories & Tasks", "Google Calendar", "Free Weather API"]
    }


# ==============================================================================
# PHASE 2 ENDPOINTS: Task Boards, Smart Lists, Rules, Serendipity, Briefings
# ==============================================================================

# ---------- 10. Dynamic Task Boards (Kanban: Queue, This Week, Today, Done) ----------

@router.get("/boards", summary="Get Kanban Task Board")
async def get_task_board(chat_id: int = Depends(get_current_chat_id)):
    """Returns the user's task cards divided into columns: queue, this_week, today, done."""
    return get_board_cards(chat_id)


@router.post("/boards/cards", summary="Add Card to Task Board")
async def add_task_card(body: BoardCardCreateReq, chat_id: int = Depends(get_current_chat_id)):
    """Creates a new card in the specified Kanban column."""
    due = None
    if body.due_date:
        try:
            due = datetime.strptime(body.due_date, "%Y-%m-%d").date()
        except Exception:
            pass
    card_id = create_board_card(
        chat_id=chat_id,
        title=body.title,
        description=body.description or "",
        status=body.status or "queue",
        priority=body.priority or "medium",
        due_date=due
    )
    if not card_id:
        raise HTTPException(status_code=500, detail="Failed to create board card")
    return {"status": "ok", "card_id": card_id, "message": f"Card '{body.title}' added to {body.status}."}


@router.patch("/boards/cards/{card_id}/move", summary="Move Card Between Columns")
async def move_task_card(card_id: int, body: BoardCardMoveReq, chat_id: int = Depends(get_current_chat_id)):
    """Moves a card to a new column (queue, this_week, today, done)."""
    success = move_board_card(chat_id, card_id, body.status)
    if not success:
        raise HTTPException(status_code=404, detail="Card not found or access denied")
    return {"status": "ok", "card_id": card_id, "new_status": body.status}


@router.delete("/boards/cards/{card_id}", summary="Delete Task Card")
async def delete_card(card_id: int, chat_id: int = Depends(get_current_chat_id)):
    """Deletes a card from the task board."""
    success = delete_board_card(chat_id, card_id)
    if not success:
        raise HTTPException(status_code=404, detail="Card not found or access denied")
    return {"status": "ok", "message": "Card deleted successfully"}


# ---------- 11. Persistent Smart Lists (Checklists) ----------

@router.get("/lists", summary="Get All Smart Lists & Checklists")
async def get_lists(chat_id: int = Depends(get_current_chat_id)):
    """Returns all user checklists (e.g. Grocery, Packing) with their items."""
    return {"lists": get_smart_lists(chat_id)}


@router.post("/lists", summary="Create New Smart List")
async def create_list(body: SmartListCreateReq, chat_id: int = Depends(get_current_chat_id)):
    """Creates a new persistent list (e.g. 'Grocery List')."""
    list_id = create_smart_list(chat_id, body.name, body.category or "general")
    if not list_id:
        raise HTTPException(status_code=500, detail="Failed to create list")
    return {"status": "ok", "list_id": list_id, "name": body.name}


@router.post("/lists/{list_id}/items", summary="Add Item to Smart List")
async def add_list_item(list_id: int, body: SmartListItemCreateReq, chat_id: int = Depends(get_current_chat_id)):
    """Adds a new item or deliverable to a smart list."""
    item_id = add_smart_list_item(chat_id, list_id, body.title, body.quantity)
    if not item_id:
        raise HTTPException(status_code=404, detail="List not found or access denied")
    return {"status": "ok", "item_id": item_id, "title": body.title, "quantity": body.quantity}


@router.patch("/lists/items/{item_id}/toggle", summary="Toggle or Check Item")
async def toggle_item(item_id: int, body: SmartListItemToggleReq, chat_id: int = Depends(get_current_chat_id)):
    """Toggles the completed checkbox of a list item."""
    success = toggle_smart_list_item(chat_id, item_id, body.checked)
    if not success:
        raise HTTPException(status_code=404, detail="Item not found or access denied")
    return {"status": "ok", "item_id": item_id, "message": "Item checked state updated"}


@router.delete("/lists/items/{item_id}", summary="Delete Item from List")
async def delete_item(item_id: int, chat_id: int = Depends(get_current_chat_id)):
    """Removes an item from a list."""
    success = delete_smart_list_item(chat_id, item_id)
    if not success:
        raise HTTPException(status_code=404, detail="Item not found or access denied")
    return {"status": "ok", "message": "Item removed from list"}


@router.delete("/lists/{list_id}", summary="Delete Entire Smart List")
async def delete_entire_list(list_id: int, chat_id: int = Depends(get_current_chat_id)):
    """Deletes an entire list and all its items."""
    success = delete_smart_list(chat_id, list_id)
    if not success:
        raise HTTPException(status_code=404, detail="List not found or access denied")
    return {"status": "ok", "message": "List deleted successfully"}


# ---------- 12. Behavioral Rule Learning (AI Preferences) ----------

@router.get("/user/rules", summary="Get User AI Behavioral Rules")
async def get_rules(chat_id: int = Depends(get_current_chat_id)):
    """Returns active personal rules taught to the AI assistant."""
    return {"rules": get_user_rules(chat_id)}


@router.post("/user/rules", summary="Teach AI a New Behavioral Rule")
async def add_rule(body: UserRuleCreateReq, chat_id: int = Depends(get_current_chat_id)):
    """Teaches the AI a behavioral rule (e.g. 'Never schedule meetings before 10 AM')."""
    rule_id = add_user_rule(chat_id, body.rule)
    if not rule_id:
        raise HTTPException(status_code=500, detail="Failed to save rule")
    return {"status": "ok", "rule_id": rule_id, "rule": body.rule, "message": "Rule saved and active."}


@router.delete("/user/rules/{rule_id}", summary="Delete Behavioral Rule")
async def delete_rule(rule_id: int, chat_id: int = Depends(get_current_chat_id)):
    """Deletes a learned behavioral rule."""
    success = delete_user_rule(chat_id, rule_id)
    if not success:
        raise HTTPException(status_code=404, detail="Rule not found or access denied")
    return {"status": "ok", "message": "Rule deleted successfully"}


# ---------- 13. Serendipity Feed (Clutter Review & Cleaning) ----------

@router.get("/feed/serendipity", summary="Get Memories for Serendipity Feed")
async def get_serendipity_feed(
    min_days: Optional[int] = Query(14, ge=1, description="Minimum age of memories in days"),
    limit: Optional[int] = Query(5, ge=1, le=20),
    chat_id: int = Depends(get_current_chat_id)
):
    """Returns older unreviewed memories to swipe, keep, or clear mind clutter."""
    memories = get_serendipity_memories(chat_id, min_days_old=min_days, limit=limit)
    return {
        "feed": memories,
        "total": len(memories),
        "tip": "Swipe right or choose 'keep' to retain, or 'delete' to permanently clear clutter."
    }


@router.post("/feed/serendipity/{item_id}/action", summary="Keep or Delete Memory Card")
async def handle_serendipity_action(
    item_id: int,
    body: SerendipityActionReq,
    chat_id: int = Depends(get_current_chat_id)
):
    """Action on serendipity memory card: keep or delete permanently."""
    if body.action == "delete":
        deleted_count = delete_memory_items(chat_id, [item_id])
        return {"status": "ok", "action": "deleted", "deleted": deleted_count > 0, "message": "Memory removed permanently."}
    else:
        return {"status": "ok", "action": "kept", "message": "Memory preserved in your vault."}


# ---------- 14. Daily Morning & Weekly Focus Briefings ----------

@router.get("/briefing/morning", summary="Generate Daily Morning Executive Briefing")
async def get_morning_briefing(
    city: Optional[str] = Query("Delhi"),
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Synthesizes an executive daily morning digest:
    Weather + Today's Google Calendar meetings + Active Today tasks + Motivational focus tip.
    """
    now = datetime.now(IST)
    today = now.date()

    # 1. Weather with UV Index
    weather = await get_free_weather(city or "Delhi")
    uv_max = weather.get("uv_index_max", 0.0) if weather else 0.0
    uv_info = f"Max UV: {uv_max} ({weather.get('uv_alert', '')}, Peak: {weather.get('uv_peak_hours', '')})" if weather else ""
    weather_text = f"{weather.get('temperature')}°C in {weather.get('city')}, {uv_info}" if weather else "Pleasant weather"

    # 2. Today's items from DB
    raw_today = items_for_date(chat_id, today)
    agenda_str = ", ".join([f"{r[1]} ({r[0]})" for r in raw_today[:5]]) if raw_today else "No immediate pending tasks"

    # 3. Google Calendar & Adaptive Early Wake-up Check
    cal_str = "No meetings scheduled"
    earliest_event_dt = None
    has_travel = False
    db_user_id = get_db_user_id(chat_id)

    if db_user_id:
        token_info = get_google_token(db_user_id)
        if token_info:
            try:
                today_start = datetime.combine(today, time.min, tzinfo=IST).isoformat()
                today_end = datetime.combine(today, time.max, tzinfo=IST).isoformat()
                events = await list_calendar_events(db_user_id, min_time=today_start, max_time=today_end)
                if events:
                    cal_str = ", ".join([f"{e.get('summary')} at {e.get('start', {}).get('dateTime', 'TBD')}" for e in events[:3]])
                    for ev in events:
                        ev_start_raw = ev.get("start", {}).get("dateTime")
                        if ev_start_raw:
                            try:
                                dt = datetime.fromisoformat(ev_start_raw.replace("Z", "+00:00")).astimezone(IST)
                                if earliest_event_dt is None or dt < earliest_event_dt:
                                    earliest_event_dt = dt
                            except Exception:
                                pass
                        ev_summary = (ev.get("summary") or "").lower()
                        if any(w in ev_summary for w in ("flight", "travel", "trip", "goa", "ticket", "plane", "airport")):
                            has_travel = True
            except Exception:
                pass

    # Check agenda for travel mentions too
    if not has_travel and any(w in agenda_str.lower() for w in ("flight", "travel", "trip", "goa", "ticket", "plane", "airport")):
        has_travel = True

    # Adaptive Briefing Time calculation:
    # Default is 08:00 AM IST. If user has an event before 08:00 AM, send briefing 1 hour before!
    target_send_time = "08:00 AM"
    is_early_wake_up = False
    if earliest_event_dt and earliest_event_dt.time() < time(8, 0):
        early_dt = earliest_event_dt - timedelta(hours=1)
        target_send_time = early_dt.strftime("%I:%M %p")
        is_early_wake_up = True

    travel_checklist = []
    travel_prompt_snippet = ""
    if has_travel:
        travel_checklist = ["Aadhaar Card", "Passport / Photo ID", "Boarding Pass / Flight Ticket", "Phone Charger", "Wallet & Cards"]
        travel_prompt_snippet = "\nTRAVEL ALERT: User is traveling today/tomorrow. Specifically remind them to carry their Aadhaar card, Passport/ID, and Flight Ticket/Boarding Pass!"

    prompt = (
        f"Generate a crisp, motivating 3-sentence Daily Morning Executive Briefing for an ambitious professional.\n"
        f"Date: {today.strftime('%A, %d %B %Y')}\n"
        f"Weather & UV: {weather_text}\n"
        f"Calendar: {cal_str}\n"
        f"Pending Tasks: {agenda_str}\n"
        f"{travel_prompt_snippet}\n"
        f"Tone: Energetic, concise, professional executive assistant. Mention UV sunscreen alert if high. Return in Hinglish or English."
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"},
                json={
                    "model": os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b",
                    "temperature": 0.5,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            briefing_text = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        travel_suffix = " Don't forget your Aadhaar card and boarding pass!" if has_travel else ""
        briefing_text = f"Good morning! Today is {today.strftime('%A, %d %b')}. You have {len(raw_today)} active tasks scheduled. Weather: {weather_text}.{travel_suffix} Stay focused!"

    return {
        "date": today.isoformat(),
        "formatted_date": today.strftime("%A, %d %B %Y"),
        "weather": weather,
        "briefing_text": briefing_text,
        "agenda_count": len(raw_today),
        "meetings_summary": cal_str,
        "target_send_time": target_send_time,
        "is_early_wake_up": is_early_wake_up,
        "travel_checklist": travel_checklist
    }


@router.get("/briefing/weekly", summary="Generate Advanced Weekly Focus Briefing")
async def get_weekly_briefing(chat_id: int = Depends(get_current_chat_id)):
    """
    Weekly Strategy Digest (for Mondays):
    Analyzes upcoming 7 days workload, heaviest days, and deep work slots.
    """
    now = datetime.now(IST)
    today = now.date()
    week_end = today + timedelta(days=7)

    items_week = items_between(chat_id, today, week_end)

    prompt = (
        f"You are PeculiarAI providing a Weekly Focus Strategy Briefing for the upcoming week ({today} to {week_end}).\n"
        f"Total Scheduled Items: {len(items_week)}\n"
        f"Provide 3 bullet points: 1) High priority focus, 2) Deep work recommendation, 3) Weekly encouragement."
    )
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY', '')}"},
                json={
                    "model": os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b",
                    "temperature": 0.4,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            weekly_plan = resp.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        weekly_plan = "Prioritize your key deliverables early in the week and reserve afternoon slots for deep work."

    return {
        "period": f"{today.strftime('%d %b')} - {week_end.strftime('%d %b %Y')}",
        "total_scheduled_items": len(items_week),
        "weekly_strategy": weekly_plan
    }


# ---------- 15. Relationship & Networking Memory (Enfolo Feature) ----------

@router.get("/contacts", summary="Get All Business & Relationship Memories")
async def get_contacts(chat_id: int = Depends(get_current_chat_id)):
    """Returns all professional relationship memories, context notes, and commitments."""
    return {"contacts": get_contact_memories(chat_id)}


@router.post("/contacts", summary="Save Relationship Context & Commitments")
async def save_contact(body: ContactMemoryCreateReq, chat_id: int = Depends(get_current_chat_id)):
    """Saves details about someone you met, discussion context, and commitments made."""
    cid = save_contact_memory(
        chat_id=chat_id,
        name=body.name,
        company=body.company,
        role_title=body.role_title,
        email=body.email,
        phone=body.phone,
        met_at=body.met_at,
        context_notes=body.context_notes,
        commitments=body.commitments
    )
    if not cid:
        raise HTTPException(status_code=500, detail="Failed to save contact memory")
    return {"status": "ok", "contact_id": cid, "message": f"Relationship memory saved for {body.name}."}


@router.post("/contacts/scan-card", summary="Scan Visiting Card / Badge to Relationship Memory")
async def scan_business_card(
    image: UploadFile = File(...),
    notes: Optional[str] = Form(""),
    commitments: Optional[str] = Form(""),
    chat_id: int = Depends(get_current_chat_id)
):
    """
    Scans a business card or conference badge using Groq Vision.
    Extracts name, company, title, email, phone and saves it with discussion notes and commitments.
    """
    content = await image.read()
    if not content:
        raise HTTPException(status_code=400, detail="Image cannot be empty")

    prompt = (
        "Analyze this business card or conference badge. Extract the following JSON strictly: "
        '{"name": "...", "company": "...", "role_title": "...", "email": "...", "phone": "..."}'
    )
    try:
        raw_analysis = await analyze_image_with_groq(content, caption=prompt)
        # Parse JSON from vision LLM
        data = await _chat_json(
            'Extract strictly JSON: {"name": "...", "company": "...", "role_title": "...", "email": "...", "phone": "..."}',
            raw_analysis
        )
        name = data.get("name") or "New Contact"
        cid = save_contact_memory(
            chat_id=chat_id,
            name=name,
            company=data.get("company"),
            role_title=data.get("role_title"),
            email=data.get("email"),
            phone=data.get("phone"),
            met_at="Business Card Scan",
            context_notes=notes or raw_analysis[:300],
            commitments=commitments or ""
        )
        return {
            "status": "ok",
            "contact_id": cid,
            "extracted": data,
            "message": f"Business card saved for {name}."
        }
    except Exception as exc:
        print("[api_v1] scan_business_card error:", repr(exc))
        raise HTTPException(status_code=500, detail="Failed to parse business card")


@router.delete("/contacts/{contact_id}", summary="Delete Relationship Memory")
async def delete_contact(contact_id: int, chat_id: int = Depends(get_current_chat_id)):
    """Deletes a relationship memory."""
    success = delete_contact_memory(chat_id, contact_id)
    if not success:
        raise HTTPException(status_code=404, detail="Contact memory not found or access denied")
    return {"status": "ok", "message": "Contact memory deleted successfully"}

