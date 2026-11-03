"""
PeculiarAI Audio Briefing & Voice Synthesis Engine
Implements ElevenLabs API with seamless Neural Voice fallback.
Converts written briefings and agent responses into high-fidelity voice notes & podcasts.
"""
import os
import re
import logging
import asyncio
from typing import Optional, Tuple
import httpx

logger = logging.getLogger("peculiar_audio")

DEFAULT_ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM") # Rachel
DEFAULT_NEURAL_FALLBACK_VOICE = os.getenv("NEURAL_TTS_VOICE", "en-IN-NeerjaNeural")

def clean_text_for_speech(text: str) -> str:
    """
    Cleans up markdown formatting, emojis, URLs, and code blocks for crisp, natural human voice synthesis.
    """
    if not text:
        return ""

    # Remove code blocks
    cleaned = re.sub(r'```[\s\S]*?```', '', text)
    # Remove inline code
    cleaned = re.sub(r'`[^`]+`', '', cleaned)
    # Remove markdown links [text](url) -> text
    cleaned = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', cleaned)
    # Remove raw URLs
    cleaned = re.sub(r'https?://\S+', '', cleaned)
    # Remove bullet asterisks, hashes, underscores
    cleaned = re.sub(r'[*_#~>]', '', cleaned)
    # Replace multiple hyphens or bullets
    cleaned = re.sub(r'^\s*[-•]\s*', '', cleaned, flags=re.MULTILINE)
    # Remove redundant whitespace
    cleaned = re.sub(r'\n+', '. ', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    return cleaned


async def synthesize_elevenlabs(text: str, voice_id: Optional[str] = None) -> Optional[bytes]:
    """Calls ElevenLabs Text-to-Speech API."""
    key = os.getenv("ELEVENLABS_API_KEY") or "sk_73eab67382a2720ed4e74601b83c89d2aaad8f0ef77d2bba"
    v_id = voice_id or DEFAULT_ELEVENLABS_VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{v_id}"

    headers = {
        "xi-api-key": key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg"
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.5,
            "similarity_boost": 0.75
        }
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=payload)
            if resp.status_code == 200 and len(resp.content) > 100:
                logger.info(f"ElevenLabs TTS synthesized {len(resp.content)} bytes of audio.")
                return resp.content
            else:
                logger.warning(f"ElevenLabs returned status {resp.status_code}: {resp.text[:150]}")
    except Exception as exc:
        logger.warning(f"ElevenLabs TTS request error: {exc}")

    return None


async def synthesize_neural_fallback(text: str, voice: Optional[str] = None) -> Optional[bytes]:
    """Generates studio-quality neural TTS using edge-tts (pure Python, zero API keys, no RAM overhead)."""
    try:
        import edge_tts
        chosen_voice = voice or DEFAULT_NEURAL_FALLBACK_VOICE
        communicate = edge_tts.Communicate(text, chosen_voice)
        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
        
        audio_data = b"".join(audio_chunks)
        if audio_data:
            logger.info(f"Neural TTS fallback synthesized {len(audio_data)} bytes using {chosen_voice}.")
            return audio_data
    except Exception as exc:
        logger.error(f"Neural TTS fallback error: {exc}")

    return None


async def synthesize_speech(text: str, voice_id: Optional[str] = None) -> bytes:
    """
    Synthesizes speech with automatic fallback:
    1. Tries ElevenLabs if key is configured.
    2. Gracefully falls back to high-fidelity Neural TTS.
    """
    clean = clean_text_for_speech(text)
    if not clean:
        raise ValueError("Cannot synthesize speech from empty text.")

    # 1. Attempt ElevenLabs
    if os.getenv("ELEVENLABS_API_KEY"):
        audio = await synthesize_elevenlabs(clean, voice_id=voice_id)
        if audio:
            return audio

    # 2. High-fidelity Neural fallback
    audio = await synthesize_neural_fallback(clean)
    if audio:
        return audio

    # 3. Last-ditch ElevenLabs attempt with default key if still None
    audio = await synthesize_elevenlabs(clean, voice_id=voice_id)
    if audio:
        return audio

    raise RuntimeError("All TTS engines failed to generate audio.")


async def generate_morning_audio_briefing(chat_id: int) -> Tuple[Optional[bytes], str]:
    """
    Generates an executive spoken audio briefing for today.
    Returns (audio_bytes, text_script).
    """
    from morning_briefing import generate_briefing_content
    from datetime import datetime
    from timing import IST

    today_str = datetime.now(IST).strftime("%Y-%m-%d")
    text_briefing = generate_briefing_content(chat_id, today_str)
    
    clean_speech = clean_text_for_speech(text_briefing)
    audio_bytes = await synthesize_speech(clean_speech)

    return audio_bytes, text_briefing

