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
        data_payload = {
            "model": model,
            "response_format": "verbose_json",
            "temperature": "0",
            "prompt": (
                "Hindi, Hinglish, English speech: reminders, calendar meetings, Google Meet, "
                "bills, tasks, appointments, dates, times, notes. "
                "देवनागरी हिंदी: आज, कल, परसों, बजे, मीटिंग, याद दिलाना, सेट करना, रखना."
            ),
        }
        resp = await client.post(
            GROQ_STT_URL,
            headers={"Authorization": f"Bearer {groq_key}"},
            data=data_payload,
            files={"file": ("voice.ogg", audio.content, "audio/ogg")},
        )

    if resp.status_code != 200:
        print("GROQ STT ERROR:", resp.status_code, resp.text)
    resp.raise_for_status()
    data = resp.json()
    raw_text = (data.get("text") or "").strip()
    norm = raw_text.lower().strip(" .!?,")
    # Filter well-known Whisper phantom hallucinations on short noise/silence
    hallucinations = {
        "you", "thank you", "thank you.", "thank you for watching", "thanks for watching",
        "bye", "bye.", "subscribe", "please subscribe", "subtitles by", "translated by",
        "mbc", "amara.org", "watching", "...", "silence", "music"
    }
    if norm in hallucinations or len(raw_text) < 2:
        return "", (data.get("language") or "")
    return raw_text, (data.get("language") or "")