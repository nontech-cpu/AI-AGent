import os
import httpx
from dotenv import load_dotenv

load_dotenv()

import json

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


async def clean_voice_transcript(raw_text):
    """Clean up obvious acoustic mishearings and speech-to-text typos using fast LLM pass."""
    if not raw_text or len(raw_text.strip()) < 3:
        return raw_text
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        return raw_text

    system = (
        "You are an expert Speech-to-Text post-processor for an Indian personal assistant. "
        "User speech often has Indian accents, Hindi/Hinglish/English slang, and acoustic mishearings. "
        "Fix obvious speech recognition errors into what the user was actually saying, in natural Hindi/Hinglish/English. "
        "Examples: "
        "- 'ओस कर देव' / 'वोस्ट करनी' / 'होस्ता में' -> 'होस्ट कर दे' / 'होस्ट करनी है' (meeting host), "
        "- 'पान्च दोजें' -> 'पाँच बजे' (5 baje), "
        "- 'जेवनिंग' -> 'evening', "
        "- 'वाई' -> 'भाई', "
        "- 'आई मतेशाम भूँ' -> 'आज शाम को', "
        "- 'मिटिंग' -> 'मीटिंग'. "
        "Preserve user intent and meaning accurately. If the text is already clear, keep it as is. "
        "Return ONLY a JSON object: {\"clean_text\": \"...\"}."
    )
    payload = {
        "model": os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b",
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": raw_text},
        ],
    }
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            res = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}"},
                json=payload,
            )
        if res.status_code == 200:
            val = res.json()["choices"][0]["message"]["content"]
            parsed = json.loads(val)
            clean = (parsed.get("clean_text") or "").strip()
            if clean and len(clean) >= 2:
                return clean
    except Exception as exc:
        print("clean_voice_transcript error:", repr(exc))
    return raw_text


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
                "Bhai meeting host kar de, Devender ke sath meeting schedule kar, Google Meet link bana, "
                "reminder set kar, 5 baje, aaj shaam 5:00 PM, kal subah 11:00 AM, electricity bill 1200 due 25 Oct. "
                "मीटिंग होस्ट कर दे, गूगल मीट, शेड्यूल्ड मीटिंग, रिमाइंडर, आज शाम पांच बजे, कल सुबह, devender ke sath."
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

    cleaned = await clean_voice_transcript(raw_text)
    return cleaned, (data.get("language") or "")