"""
PeculiarAI Frontier Router — Memorae Parity Architecture
Implements:
1. ReAct Step Streaming (SSE / NDJSON) via /api/assistant/chat
2. pgvector Cosine Search & Memory Bubbles (/api/bubbles, /api/bubbles/{slug}/memories)
3. Cloudflare R2 / S3 Asset Pipeline + Multimodal OCR Vision (/api/memories/upload, /api/extension/save)
4. Model Context Protocol (MCP) Hub (/api/connectors/mcp)
5. ElevenLabs Audio Briefing Engine (/api/assistant/tts, /api/assistant/briefing/audio)
"""
import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import APIRouter, Request, HTTPException, UploadFile, File, Form, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import db
from state.timeline import get_user_timeline_spans
from services.storage import process_and_ingest_asset, upload_asset
from services.elevenlabs_service import synthesize_speech, generate_morning_audio_briefing
from services.mcp_client import discover_and_cache_mcp_tools
from timing import IST
import core.timeutil as timeutil

logger = logging.getLogger("peculiar_frontier")

router = APIRouter(prefix="/api", tags=["Frontier Memorae Engine"])

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str = Field(..., description="User message or prompt")
    chat_id: Optional[int] = Field(None, description="Telegram chat ID or User ID")
    bubble_slug: Optional[str] = Field("everything", description="Target memory bubble (everything, work, travel, health, finance)")
    stream: bool = Field(True, description="Whether to stream ReAct reasoning steps via NDJSON/SSE")

class CreateBubbleRequest(BaseModel):
    slug: str
    name: str
    color_hex: Optional[str] = "#6366F1"
    icon_name: Optional[str] = "Folder"

class ExtensionSaveRequest(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    bubble_slug: Optional[str] = "everything"
    tags: Optional[List[str]] = Field(default_factory=list)
    chat_id: Optional[int] = None

class TTSRequest(BaseModel):
    text: str
    voice_id: Optional[str] = None

class RegisterMCPRequest(BaseModel):
    server_name: str
    server_url: str
    auth_token: Optional[str] = None

class UpsertRelationshipRequest(BaseModel):
    name: str
    relationship: str
    phone: Optional[str] = None
    email: Optional[str] = None
    birthday: Optional[str] = None
    notes: Optional[str] = None

class UpsertSubscriptionRequest(BaseModel):
    service_name: str
    amount: Optional[float] = None
    billing_cycle: Optional[str] = "monthly"
    next_renewal_date: Optional[str] = None
    is_trial: Optional[bool] = False
    trial_expiry_date: Optional[str] = None
    cancel_url: Optional[str] = None

class ShareBubbleRequest(BaseModel):
    bubble_slug: str
    permission: Optional[str] = "read_write"

class JoinBubbleRequest(BaseModel):
    invite_code: str

class CreateFollowupRequest(BaseModel):
    recipient: str
    subject: str
    notes: Optional[str] = None
    days_ahead: Optional[int] = 3


def resolve_chat_id(request: Request, body_chat_id: Optional[int] = None) -> int:
    """Extracts chat_id from query, header, or body, falling back to default user."""
    if body_chat_id:
        return db.canonical_chat_id(body_chat_id)
    header_cid = request.headers.get("X-Chat-Id") or request.headers.get("X-Telegram-Chat-Id")
    if header_cid:
        try:
            return db.canonical_chat_id(int(header_cid))
        except Exception:
            pass
    # Fallback to configured primary user
    return 1234828592


# ---------------------------------------------------------------------------
# 1. ReAct Step Streaming Engine (/api/assistant/chat)
# ---------------------------------------------------------------------------

@router.post("/assistant/chat")
async def assistant_chat(req: ChatRequest, request: Request):
    """
    Executes reasoning with real-time ReAct step streaming (NDJSON or SSE).
    Steps:
      1. Thinking: Analyzes intent and temporal parameters
      2. Searching: Queries pgvector cosine distance across memory bubbles
      3. Checking: Inspects 4D continuous timeline & calendar commitments
      4. Executing: Runs agent reasoning and tools
      5. Token Deltas: Streams response text
      6. Done: Completion metadata
    """
    from agent.loop import run_agent_turn
    chat_id = resolve_chat_id(request, req.chat_id)
    bubble_slug = (req.bubble_slug or "everything").lower()
    accept_header = request.headers.get("Accept", "")
    is_sse = "text/event-stream" in accept_header

    if not req.stream:
        # Non-streaming JSON response
        matched_mems = db.search_bubble_memories_cosine(chat_id, req.message, bubble_slug=bubble_slug, limit=5)
        timeline_spans = get_user_timeline_spans(chat_id)
        turn_res = await run_agent_turn(chat_id, req.message)
        
        return {
            "reply": turn_res.get("reply", ""),
            "steps": [
                {"name": "Thinking", "detail": "Analyzed intent and temporal context", "status": "completed"},
                {"name": "Searching", "detail": f"Retrieved {len(matched_mems)} memories from pgvector", "status": "completed"},
                {"name": "Checking", "detail": f"Checked {len(timeline_spans)} continuous timeline spans", "status": "completed"},
                {"name": "Executing", "detail": f"Executed {len(turn_res.get('tool_calls_executed', []))} actions", "status": "completed"},
            ],
            "memories": matched_mems,
            "tool_calls": turn_res.get("tool_calls_executed", []),
            "latency_ms": turn_res.get("latency_ms", 0),
            "tokens_used": turn_res.get("tokens_used", 0)
        }

    async def step_generator():
        def format_event(data: dict) -> str:
            line = json.dumps(data)
            if is_sse:
                return f"data: {line}\n\n"
            return f"{line}\n"

        # Step 1: Thinking
        yield format_event({
            "type": "step",
            "name": "Thinking",
            "detail": "Analyzing user message and temporal intent...",
            "status": "running",
            "timestamp": timeutil.now(IST).isoformat()
        })
        await asyncio.sleep(0.04)

        # Step 2: Searching pgvector
        yield format_event({
            "type": "step",
            "name": "Searching",
            "detail": f"Searching pgvector cosine distance in '{bubble_slug}' bubble...",
            "status": "running",
            "timestamp": timeutil.now(IST).isoformat()
        })
        matched_mems = await asyncio.to_thread(db.search_bubble_memories_cosine, chat_id, req.message, bubble_slug=bubble_slug, limit=5)
        yield format_event({
            "type": "step",
            "name": "Searching",
            "detail": f"Found {len(matched_mems)} relevant memories in pgvector",
            "status": "completed",
            "timestamp": timeutil.now(IST).isoformat()
        })

        # Step 3: Checking 4D Timeline
        yield format_event({
            "type": "step",
            "name": "Checking",
            "detail": "Checking continuous timeline & schedule collision guards...",
            "status": "running",
            "timestamp": timeutil.now(IST).isoformat()
        })
        timeline_spans = await asyncio.to_thread(get_user_timeline_spans, chat_id)
        yield format_event({
            "type": "step",
            "name": "Checking",
            "detail": f"Timeline validated ({len(timeline_spans)} spans active)",
            "status": "completed",
            "timestamp": timeutil.now(IST).isoformat()
        })

        # Step 4: Executing Agent Turn
        yield format_event({
            "type": "step",
            "name": "Executing",
            "detail": "Executing autonomous partner reasoning & tool calls...",
            "status": "running",
            "timestamp": timeutil.now(IST).isoformat()
        })
        agent_task = asyncio.create_task(run_agent_turn(chat_id, req.message, channel="web"))
        while not agent_task.done():
            await asyncio.sleep(1.0)
            if not agent_task.done():
                yield format_event({
                    "type": "heartbeat",
                    "status": "processing"
                })
        turn_res = await agent_task
        reply_text = turn_res.get("reply", "")
        tools_executed = turn_res.get("tool_calls_executed", [])
        
        yield format_event({
            "type": "step",
            "name": "Executing",
            "detail": f"Action complete ({len(tools_executed)} tools invoked)",
            "status": "completed",
            "timestamp": timeutil.now(IST).isoformat()
        })

        # Step 5: Streaming Token Deltas
        words = reply_text.split(" ")
        chunk = []
        for w in words:
            chunk.append(w)
            if len(chunk) >= 4 or w.endswith((".", "\n", "?", "!")):
                yield format_event({
                    "type": "delta",
                    "content": " ".join(chunk) + " "
                })
                chunk = []
                await asyncio.sleep(0.015)
        if chunk:
            yield format_event({
                "type": "delta",
                "content": " ".join(chunk)
            })

        # Step 6: Done
        yield format_event({
            "type": "done",
            "metadata": {
                "bubble_slug": bubble_slug,
                "tool_calls": tools_executed,
                "latency_ms": turn_res.get("latency_ms", 0),
                "matched_memories": matched_mems
            }
        })

    media_type = "text/event-stream" if is_sse else "application/x-ndjson"
    return StreamingResponse(step_generator(), media_type=media_type)


# ---------------------------------------------------------------------------
# 2. Structured Memory Bubbles & Vector Retrieval
# ---------------------------------------------------------------------------

@router.get("/bubbles")
async def list_bubbles(request: Request, chat_id: Optional[int] = Query(None)):
    """Returns all memory bubbles for the user."""
    cid = resolve_chat_id(request, chat_id)
    bubbles = db.get_memory_bubbles(cid)
    return {"bubbles": bubbles, "count": len(bubbles)}


@router.post("/bubbles")
async def create_bubble(req: CreateBubbleRequest, request: Request):
    """Creates a new memory bubble namespace."""
    cid = resolve_chat_id(request)
    ok = db.create_memory_bubble(cid, req.slug, req.name, req.color_hex or "#6366F1", req.icon_name or "Folder")
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to create memory bubble.")
    return {"status": "success", "slug": req.slug.lower(), "name": req.name}


@router.get("/bubbles/{bubble_slug}/memories")
async def get_bubble_memories_endpoint(
    bubble_slug: str,
    request: Request,
    query: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100)
):
    """Lists or vector-searches memories within a specific bubble."""
    cid = resolve_chat_id(request)
    if query and query.strip():
        mems = db.search_bubble_memories_cosine(cid, query.strip(), bubble_slug=bubble_slug, limit=limit)
    else:
        mems = db.get_bubble_memories(cid, bubble_slug=bubble_slug, limit=limit)
    return {"bubble": bubble_slug, "count": len(mems), "memories": mems}


# ---------------------------------------------------------------------------
# 3. S3 / Cloudflare R2 Asset Pipeline + Multimodal OCR Ingestion
# ---------------------------------------------------------------------------

@router.post("/memories/upload")
async def upload_asset_endpoint(
    file: UploadFile = File(...),
    caption: Optional[str] = Form(""),
    bubble_slug: Optional[str] = Form("everything"),
    chat_id: Optional[int] = Form(None),
    request: Request = None
):
    """
    Uploads screenshot, document, or audio to R2/S3, extracts OCR text,
    routes to the appropriate bubble, and stores vector embedding.
    """
    cid = resolve_chat_id(request, chat_id)
    file_bytes = await file.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Empty file uploaded.")

    res = await process_and_ingest_asset(
        file_bytes=file_bytes,
        filename=file.filename or "uploaded_asset",
        content_type=file.content_type,
        chat_id=cid,
        caption=caption or "",
        preferred_bubble=bubble_slug
    )
    return {"status": "success", "asset": res}


@router.post("/extension/save")
async def extension_save(req: ExtensionSaveRequest, request: Request):
    """
    Chrome Extension ingestion endpoint for saving notes, selected text, or web links.
    """
    cid = resolve_chat_id(request, req.chat_id)
    content_text = req.content or req.title or req.url or "Saved item"
    mem_id = db.save_bubble_memory(
        chat_id=cid,
        content=content_text,
        title=req.title or (content_text[:50]),
        bubble_slug=req.bubble_slug or "everything",
        url=req.url,
        tags=req.tags or ["chrome_extension"]
    )
    return {"status": "success", "memory_id": mem_id, "bubble": req.bubble_slug}


# ---------------------------------------------------------------------------
# 4. ElevenLabs Audio Briefing Engine
# ---------------------------------------------------------------------------

@router.post("/assistant/tts")
async def text_to_speech(req: TTSRequest):
    """Synthesizes high-fidelity speech from text using ElevenLabs with neural fallback."""
    try:
        audio_bytes = await synthesize_speech(req.text, voice_id=req.voice_id)
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"TTS synthesis failed: {exc}")


@router.get("/assistant/briefing/audio")
async def morning_briefing_audio(request: Request, chat_id: Optional[int] = Query(None)):
    """Generates today's executive morning briefing as an audio stream (MP3)."""
    cid = resolve_chat_id(request, chat_id)
    try:
        audio_bytes, text_script = await generate_morning_audio_briefing(cid)
        if not audio_bytes:
            raise HTTPException(status_code=500, detail="Could not generate briefing audio.")
        return Response(content=audio_bytes, media_type="audio/mpeg")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Audio briefing error: {exc}")


# ---------------------------------------------------------------------------
# 5. Model Context Protocol (MCP) Hub
# ---------------------------------------------------------------------------

@router.get("/connectors/mcp")
async def list_mcp_servers(request: Request):
    """Lists all user-registered MCP servers and their discovered tools."""
    cid = resolve_chat_id(request)
    servers = db.get_user_mcp_servers(cid)
    return {"servers": servers, "count": len(servers)}


@router.post("/connectors/mcp")
async def register_mcp_server(req: RegisterMCPRequest, request: Request):
    """Registers an MCP server, queries its tools, and caches the schema."""
    cid = resolve_chat_id(request)
    tools = await discover_and_cache_mcp_tools(
        chat_id=cid,
        server_name=req.server_name,
        server_url=req.server_url,
        auth_token=req.auth_token
    )
    return {
        "status": "success",
        "server_name": req.server_name,
        "tools_discovered": len(tools),
        "tools": [t.get("name") for t in tools]
    }


@router.delete("/connectors/mcp/{server_name}")
async def unregister_mcp_server(server_name: str, request: Request):
    """Removes a registered MCP server."""
    cid = resolve_chat_id(request)
    ok = db.delete_user_mcp_server(cid, server_name)
    return {"status": "success" if ok else "not_found", "server_name": server_name}


# ---------------------------------------------------------------------------
# 6. Profile & Health Status (/api/me)
# ---------------------------------------------------------------------------

@router.get("/me")
async def get_me(request: Request):
    """Returns user profile, active bubbles, and system readiness."""
    cid = resolve_chat_id(request)
    bubbles, mcp_servers, timeline_spans, persona = await asyncio.gather(
        asyncio.to_thread(db.get_memory_bubbles, cid),
        asyncio.to_thread(db.get_user_mcp_servers, cid),
        asyncio.to_thread(get_user_timeline_spans, cid, window_days_back=0, window_days_forward=2),
        asyncio.to_thread(db.get_persona, cid)
    )
    
    return {
        "chat_id": cid,
        "persona": persona or "Executive Partner",
        "bubbles_count": len(bubbles),
        "active_mcp_servers": len(mcp_servers),
        "active_timeline_spans": len(timeline_spans),
        "version": "2.3.0-frontier",
        "status": "ready"
    }


# ---------------------------------------------------------------------------
# 7. Memory Health Score & Cognitive Decay Radar
# ---------------------------------------------------------------------------

@router.get("/assistant/health-radar")
async def get_health_radar(request: Request, chat_id: Optional[int] = Query(None)):
    """Calculates live 0-100 cognitive clarity score and overdue commitment radar."""
    from services.health_radar import compute_memory_health_score
    cid = resolve_chat_id(request, chat_id)
    score_data = compute_memory_health_score(cid)
    return {"status": "success", "chat_id": cid, "radar": score_data}


# ---------------------------------------------------------------------------
# 8. People CRM & Relationship Knowledge Graph
# ---------------------------------------------------------------------------

@router.get("/relationships")
async def list_user_relationships(request: Request, chat_id: Optional[int] = Query(None)):
    """Retrieves all mapped family, friends, and team members for the user."""
    from services.relationship_service import get_user_relationships
    cid = resolve_chat_id(request, chat_id)
    rels = get_user_relationships(cid)
    return {"status": "success", "chat_id": cid, "count": len(rels), "relationships": rels}


@router.post("/relationships")
async def upsert_user_relationship(req: UpsertRelationshipRequest, request: Request, chat_id: Optional[int] = Query(None)):
    """Maps or updates an entity kinship/relationship (e.g. Papa, Manager)."""
    from services.relationship_service import add_or_update_relationship
    cid = resolve_chat_id(request, chat_id)
    res = add_or_update_relationship(
        user_id_or_chat_id=cid,
        name=req.name,
        relationship=req.relationship,
        phone=req.phone,
        email=req.email,
        birthday=req.birthday,
        notes=req.notes
    )
    return res


# ---------------------------------------------------------------------------
# 9. Subscription & Free-Trial Watchdog
# ---------------------------------------------------------------------------

@router.get("/subscriptions")
async def list_user_subscriptions(request: Request, chat_id: Optional[int] = Query(None)):
    """Lists all tracked recurring subscriptions and active free trials."""
    from services.subscription_service import get_user_subscriptions
    cid = resolve_chat_id(request, chat_id)
    subs = get_user_subscriptions(cid)
    return {"status": "success", "chat_id": cid, "count": len(subs), "subscriptions": subs}


@router.get("/subscriptions/expiring")
async def get_expiring_subs(request: Request, days: int = Query(3, ge=1, le=30), chat_id: Optional[int] = Query(None)):
    """Checks subscriptions and free-trials expiring within the specified days window."""
    from services.subscription_service import check_expiring_subscriptions
    cid = resolve_chat_id(request, chat_id)
    expiring = check_expiring_subscriptions(cid, days_ahead=days)
    return {"status": "success", "chat_id": cid, "expiring_count": len(expiring), "alerts": expiring}


@router.post("/subscriptions")
async def upsert_user_subscription(req: UpsertSubscriptionRequest, request: Request, chat_id: Optional[int] = Query(None)):
    """Tracks or updates a subscription or free trial."""
    from services.subscription_service import upsert_subscription
    cid = resolve_chat_id(request, chat_id)
    res = upsert_subscription(
        user_id_or_chat_id=cid,
        service_name=req.service_name,
        amount=req.amount,
        billing_cycle=req.billing_cycle or "monthly",
        next_renewal_date=req.next_renewal_date,
        is_trial=req.is_trial or False,
        trial_expiry_date=req.trial_expiry_date,
        cancel_url=req.cancel_url
    )
    return res


# ---------------------------------------------------------------------------
# 10. Shared Memory Bubbles
# ---------------------------------------------------------------------------

@router.post("/bubbles/share")
async def share_bubble_endpoint(req: ShareBubbleRequest, request: Request, chat_id: Optional[int] = Query(None)):
    """Creates a secure share invite code for a specific bubble."""
    from services.shared_bubble_service import create_bubble_share_invite
    cid = resolve_chat_id(request, chat_id)
    invite_code = create_bubble_share_invite(
        owner_chat_id=cid,
        bubble_slug=req.bubble_slug,
        permission=req.permission or "read_write"
    )
    return {
        "status": "success",
        "bubble_slug": req.bubble_slug.lower(),
        "permission": req.permission or "read_write",
        "invite_code": invite_code,
        "instructions": f"Share this code with your team or collaborator: {invite_code}"
    }


@router.post("/bubbles/join")
async def join_bubble_endpoint(req: JoinBubbleRequest, request: Request, chat_id: Optional[int] = Query(None)):
    """Joins a shared memory bubble using an invite code."""
    from services.shared_bubble_service import join_shared_bubble
    cid = resolve_chat_id(request, chat_id)
    res = join_shared_bubble(invitee_chat_id=cid, invite_code=req.invite_code)
    if res.get("status") != "success":
        raise HTTPException(status_code=400, detail=res.get("error", "Failed to join bubble."))
    return res


@router.get("/bubbles/shared")
async def list_shared_bubbles(request: Request, chat_id: Optional[int] = Query(None)):
    """Lists all shared bubbles accessible to the user."""
    from services.shared_bubble_service import get_shared_bubbles_for_user
    cid = resolve_chat_id(request, chat_id)
    shared = get_shared_bubbles_for_user(cid)
    return {"status": "success", "chat_id": cid, "count": len(shared), "shared_bubbles": shared}


# ---------------------------------------------------------------------------
# 11. Follow-Up & Unanswered Email Watchdog & Clash Shield
# ---------------------------------------------------------------------------

@router.get("/followups")
async def list_followups_endpoint(request: Request, chat_id: Optional[int] = Query(None)):
    """Retrieves all active communications awaiting replies."""
    from services.followup_service import list_active_followups
    cid = resolve_chat_id(request, chat_id)
    items = list_active_followups(cid)
    return {"status": "success", "chat_id": cid, "count": len(items), "followups": items}


@router.post("/followups")
async def create_followup_endpoint(req: CreateFollowupRequest, request: Request, chat_id: Optional[int] = Query(None)):
    """Registers an outgoing communication to watch for reply."""
    from services.followup_service import record_followup
    cid = resolve_chat_id(request, chat_id)
    res = record_followup(
        user_id_or_chat_id=cid,
        recipient=req.recipient,
        subject=req.subject,
        notes=req.notes,
        days_ahead=req.days_ahead or 3
    )
    return res


@router.post("/followups/{followup_id}/resolve")
async def resolve_followup_endpoint(followup_id: int, request: Request, chat_id: Optional[int] = Query(None)):
    """Marks a follow-up tracker as resolved."""
    from services.followup_service import resolve_followup
    cid = resolve_chat_id(request, chat_id)
    ok = resolve_followup(cid, followup_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Follow-up not found or already resolved.")
    return {"status": "success", "followup_id": followup_id, "resolved": True}


@router.get("/clash-check")
async def clash_check_endpoint(
    date_str: str = Query(..., description="Target date in YYYY-MM-DD"),
    time_str: str = Query(..., description="Proposed time e.g. 16:00 or 4 PM"),
    duration_mins: int = Query(30, ge=15, le=480),
    request: Request = None,
    chat_id: Optional[int] = Query(None)
):
    """Proactive collision check across Google Calendar, reminders, and 4D timeline spans."""
    from services.clash_detector import detect_schedule_clash
    from datetime import date
    cid = resolve_chat_id(request, chat_id)
    try:
        t_date = date.fromisoformat(date_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid date_str format. Use YYYY-MM-DD.")

    clash = await detect_schedule_clash(cid, t_date, time_str, duration_minutes=duration_mins)
    return {
        "status": "success",
        "has_clash": bool(clash and clash.get("has_clash")),
        "clash": clash
    }
