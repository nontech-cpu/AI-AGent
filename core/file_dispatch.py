"""
Channel-aware artifact storage and real document delivery.
Ensures generated files (PDF, Excel, CSV, DOCX, PPTX, Markdown, JSON, HTML, images, etc.)
are genuinely dispatched to the user on Telegram or stored for authenticated API download,
never returning fake success when delivery fails.
"""
import logging
import os
import time
import uuid
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("peculiar_file_dispatch")

MIME_MAP: Dict[str, str] = {
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".csv": "text/csv; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".ics": "text/calendar; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".svg": "image/svg+xml",
}

_ARTIFACT_TTL_SECONDS = 3600.0
_MAX_ARTIFACTS = 200
# artifact_id -> (created_ts, owner_chat_id, filename, mime_type, content_bytes)
_ARTIFACT_STORE: Dict[str, Tuple[float, int, str, str, bytes]] = {}


def sanitize_filename(raw_name: str, default_name: str = "document", ext: str = "") -> str:
    """Build a safe filesystem/header filename without brittle regexes."""
    base = (raw_name or "").strip() or default_name
    cleaned = "".join(ch if (ch.isalnum() or ch in "._- ") else "_" for ch in base).strip().replace(" ", "_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    cleaned = cleaned.strip("._-") or default_name
    if ext:
        norm_ext = ext if ext.startswith(".") else f".{ext}"
        if not cleaned.lower().endswith(norm_ext.lower()):
            stem, _ = os.path.splitext(cleaned)
            cleaned = f"{stem or default_name}{norm_ext.lower()}"
    return cleaned[:100]


def guess_mime_type(filename: str, fallback: str = "application/octet-stream") -> str:
    _, ext = os.path.splitext(filename or "")
    return MIME_MAP.get(ext.lower(), fallback)


def _prune_artifacts() -> None:
    now = time.time()
    expired = [aid for aid, (ts, *_) in _ARTIFACT_STORE.items() if (now - ts) > _ARTIFACT_TTL_SECONDS]
    for aid in expired:
        _ARTIFACT_STORE.pop(aid, None)
    while len(_ARTIFACT_STORE) >= _MAX_ARTIFACTS:
        oldest = min(_ARTIFACT_STORE.items(), key=lambda item: item[1][0])[0]
        _ARTIFACT_STORE.pop(oldest, None)


def store_artifact(chat_id: int, filename: str, content_bytes: bytes, mime_type: Optional[str] = None) -> Dict[str, Any]:
    """Store generated artifact bytes in memory for authenticated retrieval and return metadata."""
    _prune_artifacts()
    aid = f"art_{uuid.uuid4().hex[:12]}"
    resolved_mime = mime_type or guess_mime_type(filename)
    _ARTIFACT_STORE[aid] = (time.time(), int(chat_id), filename, resolved_mime, content_bytes)
    return {
        "artifact_id": aid,
        "filename": filename,
        "mime_type": resolved_mime,
        "bytes": len(content_bytes),
        "download_url": f"/api/v1/files/{aid}",
    }


def get_artifact(artifact_id: str, requester_chat_id: int) -> Optional[Tuple[str, str, bytes]]:
    """Retrieve a stored artifact only if owned by requester_chat_id and not expired."""
    entry = _ARTIFACT_STORE.get(artifact_id)
    if not entry:
        return None
    ts, owner_chat_id, filename, mime_type, content_bytes = entry
    if (time.time() - ts) > _ARTIFACT_TTL_SECONDS:
        _ARTIFACT_STORE.pop(artifact_id, None)
        return None
    if int(owner_chat_id) != int(requester_chat_id):
        return None
    return filename, mime_type, content_bytes


async def dispatch_generated_file(
    chat_id: int,
    origin_chat_id: Optional[int],
    filename: str,
    content_bytes: bytes,
    caption: str = "",
    mime_type: Optional[str] = None,
    channel: str = "telegram",
) -> Dict[str, Any]:
    """
    Deliver generated file bytes over the active channel (Telegram sendDocument or App artifact download).
    Returns verified delivery status; never fabricates success if Telegram dispatch fails.
    """
    if not content_bytes:
        return {
            "status": "error",
            "error_type": "empty_file",
            "delivered": False,
            "filename": filename,
            "message": f"Generated file '{filename}' was empty.",
            "user_message": f"Failed to generate '{filename}' because the output was empty.",
        }

    resolved_mime = mime_type or guess_mime_type(filename)
    ext = os.path.splitext(filename)[1].lstrip(".").lower() or "file"
    type_label = "excel" if ext in ("xlsx", "xls") else ext

    artifact_meta = store_artifact(int(chat_id), filename, content_bytes, resolved_mime)
    target_chat_id = origin_chat_id or chat_id

    if channel == "telegram":
        if not target_chat_id or int(target_chat_id) <= 0:
            return {
                "status": "error",
                "error_type": "invalid_chat_id",
                "delivered": False,
                "filename": filename,
                "mime_type": resolved_mime,
                "bytes": len(content_bytes),
                "message": f"Cannot send '{filename}' on Telegram without a valid positive chat_id.",
                "user_message": f"I generated '{filename}', but could not send it because the Telegram chat target is invalid.",
            }
        try:
            from main import send_file

            await send_file(
                chat_id=int(target_chat_id),
                filename=filename,
                content_bytes=content_bytes,
                caption=caption or filename,
                mime_type=resolved_mime,
            )
            logger.info(f"Delivered {filename} ({len(content_bytes)} bytes) to Telegram chat {target_chat_id}")
            return {
                "status": "success",
                "delivered": True,
                "delivery_channel": "telegram",
                "type": type_label,
                "filename": filename,
                "mime_type": resolved_mime,
                "bytes": len(content_bytes),
                "files_sent": [filename],
                "artifact_id": artifact_meta["artifact_id"],
                "download_url": artifact_meta["download_url"],
                "message": f"Successfully generated and delivered '{filename}' ({len(content_bytes)} bytes) to Telegram.",
            }
        except Exception as send_err:
            logger.warning(f"Telegram file delivery failed for {filename} -> {target_chat_id}: {send_err}")
            return {
                "status": "error",
                "error_type": "delivery_failed",
                "delivered": False,
                "delivery_channel": "telegram",
                "type": type_label,
                "filename": filename,
                "mime_type": resolved_mime,
                "bytes": len(content_bytes),
                "artifact_id": artifact_meta["artifact_id"],
                "download_url": artifact_meta["download_url"],
                "message": f"Generated '{filename}' ({len(content_bytes)} bytes), but Telegram delivery failed: {send_err}",
                "user_message": f"I generated '{filename}', but couldn't deliver it via Telegram right now. Please try again.",
            }

    # Non-Telegram channels (e.g., mobile/web app): deliver via authenticated artifact attachment URL
    return {
        "status": "success",
        "delivered": True,
        "delivery_channel": channel or "app",
        "type": type_label,
        "filename": filename,
        "mime_type": resolved_mime,
        "bytes": len(content_bytes),
        "files_sent": [filename],
        "artifact_id": artifact_meta["artifact_id"],
        "download_url": artifact_meta["download_url"],
        "message": f"Successfully prepared '{filename}' ({len(content_bytes)} bytes) for download.",
    }

