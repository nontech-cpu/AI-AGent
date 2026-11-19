import os
import httpx
from dotenv import load_dotenv

load_dotenv()

import json

GROQ_STT_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


async def clean_voice_transcript(raw_text):
    """Clean up obvious acoustic mishearings and speech-to-text typos into natural Roman Hinglish."""
    if not raw_text or len(raw_text.strip()) < 3:
        return raw_text

    system = (
        "You are an expert Speech-to-Text post-processor for an Indian personal assistant.\n"
        "User speech often has Indian accents, Hindi/Hinglish/English slang, and acoustic mishearings.\n\n"
        "CRITICAL SCRIPT MANDATE:\n"
        "- ALWAYS output in clean ROMAN script (Hinglish / English). NEVER output in Devanagari Hindi script font.\n"
        "- If the input is in Devanagari Hindi (e.g. 'चार बजे आराव को point कर देना...'), you MUST transliterate and clean it into natural Roman Hinglish (e.g. 'Chaar baje Aarav ko phone kar dena...').\n\n"
        "ACOUSTIC & INTENT CORRECTIONS:\n"
        "- Fix obvious speech recognition errors into what the user was actually saying in natural Roman Hinglish / English:\n"
        "  * 'point kar dena' / 'point karna' when referring to a person -> 'phone kar dena' / 'call kar dena'\n"
        "  * 'aath se payment' / 'aath payment' -> 'payment' / '800 payment'\n"
        "  * 'os kar de' / 'hosta me' -> 'meeting host kar de'\n"
        "  * 'panch doje' -> '5 baje'\n"
        "  * 'aarav', 'rakshit', 'devender', 'ananya' -> capitalize names properly\n"
        "- Preserve all times (e.g. 4 baje, 5 baje), tasks, numbers, and multiple thoughts accurately.\n"
        "Return ONLY a JSON object: {\"clean_text\": \"...\"}."
    )

    try:
        from llm.provider import chat
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": raw_text}
        ]
        res = await chat(messages, response_format={"type": "json_object"}, temperature=0.0, timeout=8.0)
        parsed = json.loads(res.text or "{}")
        clean = (parsed.get("clean_text") or "").strip()
        if clean and len(clean) >= 2:
            return clean
    except Exception as exc:
        print("clean_voice_transcript chat fallback:", repr(exc))

    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        return raw_text

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


async def transcribe_audio_bytes(audio_bytes: bytes, filename: str = "voice.m4a", mime_type: str = "audio/m4a"):
    """
    Transcribe raw in-memory audio bytes using Groq Whisper.
    Supports .m4a, .mp3, .wav, .ogg, .webm.
    """
    groq_key = os.getenv("GROQ_API_KEY")
    model = os.getenv("GROQ_STT_MODEL") or "whisper-large-v3-turbo"
    if not groq_key:
        raise RuntimeError("GROQ_API_KEY is missing in .env")

    data_payload = {
        "model": model,
        "response_format": "verbose_json",
        "temperature": "0",
        "prompt": (
            "Bhai meeting host kar de, Devender ke sath meeting schedule kar, Google Meet link bana, "
            "reminder set kar, chaar baje 4:00 PM Aarav ko phone kar dena, paanch baje 5:00 PM Rakshit ko payment, "
            "kal subah 11:00 AM, electricity bill 1200 due 25 Oct, urgent reminder yaad rakhna."
        ),
    }
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            GROQ_STT_URL,
            headers={"Authorization": f"Bearer {groq_key}"},
            data=data_payload,
            files={"file": (filename, audio_bytes, mime_type)},
        )
    if resp.status_code != 200:
        print("GROQ STT ERROR:", resp.status_code, resp.text)
    resp.raise_for_status()
    data = resp.json()
    raw_text = (data.get("text") or "").strip()
    norm = raw_text.lower().strip(" .!?,")
    hallucinations = {
        "you", "thank you", "thank you.", "thank you for watching", "thanks for watching",
        "bye", "bye.", "subscribe", "please subscribe", "subtitles by", "translated by",
        "mbc", "amara.org", "watching", "...", "silence", "music"
    }
    if norm in hallucinations or len(raw_text) < 2:
        return "", (data.get("language") or "")

    cleaned = await clean_voice_transcript(raw_text)
    return cleaned, (data.get("language") or "")


async def transcribe_telegram_voice(file_id):
    """
    Telegram voice note -> (transcript, language name from Whisper).
    Audio sirf memory me rehta hai, disk pe kabhi save nahi hota.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.get(
            f"https://api.telegram.org/bot{token}/getFile",
            params={"file_id": file_id},
        )
        r.raise_for_status()
        file_path = r.json()["result"]["file_path"]

        audio = await client.get(f"https://api.telegram.org/file/bot{token}/{file_path}")
        audio.raise_for_status()

        return await transcribe_audio_bytes(audio.content, "voice.ogg", "audio/ogg")