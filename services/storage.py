"""
PeculiarAI Cloudflare R2 / S3 Asset Storage Pipeline
Supports Cloudflare R2 (S3-compatible) & AWS S3 with local persistent disk fallback.
Includes Multimodal OCR Vision pipeline to automatically extract text, summarize,
and route assets to pgvector memory bubbles (Travel, Health, Finance, Work, Everything).
"""
import os
import uuid
import mimetypes
import logging
from typing import Dict, Any, Optional, Tuple
from pathlib import Path

logger = logging.getLogger("peculiar_storage")

# Local upload directory fallback
LOCAL_UPLOAD_DIR = Path("static/uploads")
LOCAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

def get_s3_client():
    """Returns a boto3 client configured for Cloudflare R2 or AWS S3 if credentials exist."""
    account_id = os.getenv("R2_ACCOUNT_ID")
    access_key = os.getenv("R2_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID")
    secret_key = os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    
    if not (access_key and secret_key):
        return None

    try:
        import boto3
        from botocore.config import Config

        endpoint_url = None
        if account_id:
            endpoint_url = f"https://{account_id}.r2.cloudflarestorage.com"
        elif os.getenv("S3_ENDPOINT_URL"):
            endpoint_url = os.getenv("S3_ENDPOINT_URL")

        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto" if account_id else os.getenv("AWS_REGION", "us-east-1"),
            config=Config(signature_version="s3v4")
        )
        return client
    except Exception as exc:
        logger.warning(f"Failed to initialize S3/R2 client: {exc}")
        return None


def upload_asset(
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
    user_id: int = 0
) -> Dict[str, str]:
    """
    Uploads an asset to Cloudflare R2 / S3 if configured, or saves to local disk.
    Returns:
        {
            "url": "https://... or /static/uploads/...",
            "key": "unique_file_key",
            "storage_type": "r2" | "s3" | "local",
            "filename": original_filename
        }
    """
    ext = Path(filename).suffix.lower() if filename else ".bin"
    if not content_type:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    unique_id = uuid.uuid4().hex[:12]
    safe_name = "".join(c for c in Path(filename).stem if c.isalnum() or c in "-_")[:40] or "asset"
    key = f"user_{user_id}/{unique_id}_{safe_name}{ext}"

    s3 = get_s3_client()
    bucket = os.getenv("R2_BUCKET_NAME") or os.getenv("AWS_BUCKET_NAME")

    if s3 and bucket:
        try:
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=file_bytes,
                ContentType=content_type
            )
            public_domain = os.getenv("R2_PUBLIC_DOMAIN") or os.getenv("S3_PUBLIC_DOMAIN")
            if public_domain:
                url = f"https://{public_domain.rstrip('/')}/{key}"
            else:
                url = f"https://{bucket}.r2.cloudflarestorage.com/{key}"

            logger.info(f"Uploaded asset to R2/S3: {url}")
            return {
                "url": url,
                "key": key,
                "storage_type": "r2" if os.getenv("R2_ACCOUNT_ID") else "s3",
                "filename": filename
            }
        except Exception as exc:
            logger.error(f"S3/R2 upload failed, falling back to local disk: {exc}")

    # Local fallback
    user_dir = LOCAL_UPLOAD_DIR / f"user_{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    local_path = user_dir / f"{unique_id}_{safe_name}{ext}"

    with open(local_path, "wb") as f:
        f.write(file_bytes)

    local_url = f"/static/uploads/user_{user_id}/{local_path.name}"
    logger.info(f"Saved asset locally: {local_url}")
    return {
        "url": local_url,
        "key": f"local:{local_path.name}",
        "storage_type": "local",
        "filename": filename
    }


async def process_and_ingest_asset(
    file_bytes: bytes,
    filename: str,
    content_type: Optional[str] = None,
    chat_id: int = 0,
    caption: str = "",
    preferred_bubble: Optional[str] = None
) -> Dict[str, Any]:
    """
    End-to-end ingestion pipeline:
    1. Uploads to R2 / local storage.
    2. Runs multimodal OCR Vision if image/doc.
    3. Categorizes into appropriate Memory Bubble (travel, health, finance, work, everything).
    4. Automatically generates 384-dim vector embedding and saves into pgvector.
    """
    import db
    from multimodal import analyze_image_with_groq

    if not content_type:
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"

    # Step 1: Upload asset
    upload_res = upload_asset(file_bytes, filename, content_type, user_id=chat_id)
    asset_url = upload_res["url"]

    ocr_text = ""
    ai_summary = ""
    detected_bubble = preferred_bubble or "everything"

    # Step 2: Multimodal analysis for images
    is_image = content_type.startswith("image/") or Path(filename).suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    if is_image:
        try:
            ocr_res = await analyze_image_with_groq(file_bytes, caption=caption)
            ocr_text = ocr_res.strip()
            ai_summary = ocr_text[:200]
            
            # Autonomous Bubble Routing
            lower_ocr = (ocr_text + " " + caption).lower()
            if any(w in lower_ocr for w in ("flight", "airline", "boarding", "ticket", "seat", "pnr", "train", "hotel", "bus")):
                detected_bubble = "travel"
            elif any(w in lower_ocr for w in ("prescription", "tablet", "mg", "doctor", "clinic", "hospital", "glucose", "sugar", "bp", "medical")):
                detected_bubble = "health"
            elif any(w in lower_ocr for w in ("invoice", "total amount", "gst", "receipt", "bill", "payment", "paid", "due date", "usd", "inr", "rupees")):
                detected_bubble = "finance"
            elif any(w in lower_ocr for w in ("meeting", "project", "client", "sprint", "roadmap", "task", "github", "code")):
                detected_bubble = "work"
            elif not preferred_bubble:
                detected_bubble = "everything"
        except Exception as exc:
            logger.warning(f"OCR analysis error for uploaded asset: {exc}")
            ocr_text = caption or f"Uploaded file: {filename}"

    # Step 3: Ingest into pgvector cognitive memory
    title = caption[:60] if caption else f"Asset: {filename}"
    mem_id = db.save_bubble_memory(
        chat_id=chat_id,
        content=caption or (ocr_text[:300] if ocr_text else f"Asset uploaded: {filename}"),
        title=title,
        bubble_slug=detected_bubble,
        image_url=asset_url,
        ocr_extracted_text=ocr_text,
        ai_summary=ai_summary,
        tags=[detected_bubble, "asset_upload", Path(filename).suffix.lstrip(".")]
    )

    return {
        "memory_id": mem_id,
        "asset_url": asset_url,
        "bubble_slug": detected_bubble,
        "title": title,
        "ocr_extracted_text": ocr_text,
        "storage_type": upload_res["storage_type"]
    }

