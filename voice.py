import os
import httpx
from dotenv import load_dotenv

load_dotenv()

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


async def transcribe_telegram_voice(file_id):
    """
    Telegram voice note -> (transcript, language name from Whisper).
    Audio sirf memory me rehta hai, disk pe kabhi save nahi hota.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    groq_key = os.getenv("GROQ_API_KEY")
    model = os.getenv("GROQ_STT_MODEL")
    if not model:
        raise RuntimeError("GROQ_STT_MODEL is missing in .env")

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
        )
        r.raise_for_status()
        file_path = r.json()["result"]["file_path"]

        audio = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
        audio.raise_for_status()

        # Telegram ki file .oga hoti hai; Groq ko .ogg naam se bhejte hain
        resp = await client.post(
            GROQ_STT_URL,
            headers={"Authorization": f"Bearer {groq_key}"},
            data={"model": model, "response_format": "verbose_json", "temperature": "0"},
            files={"file": ("voice.ogg", audio.content, "audio/ogg")},
        )

    if resp.status_code != 200:
        print("GROQ STT ERROR:", resp.status_code, resp.text)
    resp.raise_for_status()
    data = resp.json()
    return (data.get("text") or "").strip(), (data.get("language") or "")