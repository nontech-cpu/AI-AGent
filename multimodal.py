import os
import io
import base64
import httpx
from dotenv import load_dotenv

load_dotenv()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
VISION_MODEL = "qwen/qwen3.8-27b"


async def download_telegram_file(file_id: str) -> tuple[bytes, str]:
    """
    Downloads a file from Telegram by file_id strictly into memory.
    Returns (file_bytes, file_path).
    Never writes to local disk.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in environment.")

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Step 1: get file path
        r = await client.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
        )
        r.raise_for_status()
        data = r.json()
        if not data.get("ok") or "result" not in data:
            raise RuntimeError(f"Failed to getFile from Telegram: {data}")

        file_path = data["result"]["file_path"]

        # Step 2: download bytes
        file_resp = await client.get(
            f"https://api.telegram.org/file/bot{token}/{file_path}"
        )
        file_resp.raise_for_status()
        return file_resp.content, file_path


async def analyze_image_with_groq(image_bytes: bytes, caption: str = "") -> str:
    """
    Sends an image (as base64) to Groq's Vision LLM.
    Extracts structured details (bills, due dates, meetings, notes, etc.).
    """
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        raise RuntimeError("GROQ_API_KEY is missing in environment.")

    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    prompt = (
        "You are PeculiarAI, a highly capable multimodal personal assistant. "
        "Analyze this image carefully and extract all actionable details.\n\n"
        "Guidelines:\n"
        "- If it is a Bill / Invoice / Receipt: extract the biller/company name, total amount, due date, "
        "and account/reference number.\n"
        "- If it is a Meeting / Ticket / Event invite: extract the title, date, start time, end time, organizer, and location.\n"
        "- If it is a Document / Letter / Form: extract the main subject, key dates, names, and action items.\n"
        "- If it contains general text or an object: describe and transcribe it accurately.\n"
        "- Be factual and concise. Do NOT invent dates or amounts not clearly visible."
    )

    if caption and caption.strip():
        prompt += f"\n\nUser added caption: \"{caption.strip()}\". Give special attention to what the user requested."

    payload = {
        "model": VISION_MODEL,
        "temperature": 0.1,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_image}"
                        },
                    },
                ],
            }
        ],
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            GROQ_API_URL,
            headers={"Authorization": f"Bearer {groq_key}"},
            json=payload,
        )
        if resp.status_code != 200:
            err_text = resp.text[:300]
            print(f"[multimodal] Groq vision failed ({resp.status_code}): {err_text}")
            raise RuntimeError(f"Vision analysis failed: {err_text}")

        res_json = resp.json()
        analysis = res_json["choices"][0]["message"]["content"].strip()
        return analysis


def extract_text_from_pdf(pdf_bytes: bytes, max_pages: int = 10) -> str:
    """
    Extracts text from PDF bytes in memory using pypdf.
    Limits extraction to max_pages for efficiency.
    """
    try:
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        num_pages = len(reader.pages)
        pages_to_read = min(num_pages, max_pages)

        extracted = []
        for i in range(pages_to_read):
            text = reader.pages[i].extract_text()
            if text and text.strip():
                extracted.append(f"--- Page {i+1} ---\n{text.strip()}")

        if not extracted:
            return ""

        result = "\n\n".join(extracted)
        if num_pages > max_pages:
            result += f"\n\n[Note: Document has {num_pages} pages; first {max_pages} pages extracted.]"
        return result
    except Exception as exc:
        print("[multimodal] PDF extraction error:", repr(exc))
        return ""


async def process_telegram_photo(file_id: str, caption: str = "") -> str:
    """
    Full pipeline for Telegram photo:
    Download -> Vision LLM -> Actionable text summary
    """
    img_bytes, _ = await download_telegram_file(file_id)
    return await analyze_image_with_groq(img_bytes, caption)


async def process_telegram_document(
    file_id: str,
    file_name: str,
    mime_type: str,
    caption: str = "",
) -> str:
    """
    Full pipeline for Telegram document (PDF, text, image, etc.):
    Download -> In-memory parse -> Actionable summary
    """
    file_bytes, _ = await download_telegram_file(file_id)
    lower_name = (file_name or "").lower()
    lower_mime = (mime_type or "").lower()

    # Case 1: PDF Document
    if "pdf" in lower_mime or lower_name.endswith(".pdf"):
        text = extract_text_from_pdf(file_bytes)
        if not text:
            return (
                f"I received the PDF '{file_name}', but it seems to contain scanned images "
                f"without selectable text."
            )
        return (
            f"Document Content from '{file_name}':\n\n{text}"
            + (f"\n\nUser request: {caption}" if caption else "")
        )

    # Case 2: Image sent as Document
    if lower_mime.startswith("image/") or any(
        lower_name.endswith(ext) for ext in [".jpg", ".jpeg", ".png", ".webp"]
    ):
        return await analyze_image_with_groq(file_bytes, caption)

    # Case 3: Text / Code / CSV / JSON
    if (
        lower_mime.startswith("text/")
        or any(
            lower_name.endswith(ext)
            for ext in [".txt", ".csv", ".json", ".md", ".log", ".py", ".js"]
        )
    ):
        try:
            content = file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            content = file_bytes.decode("latin-1", errors="replace")

        # Cap text at 10,000 characters
        if len(content) > 10000:
            content = content[:10000] + "\n...[truncated]"

        return (
            f"File '{file_name}' content:\n\n{content}"
            + (f"\n\nUser request: {caption}" if caption else "")
        )

    # Fallback for unsupported formats
    return f"Received file '{file_name}' ({mime_type or 'unknown format'})."
