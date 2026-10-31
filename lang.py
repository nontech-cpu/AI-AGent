import re
from contextvars import ContextVar
from sqlalchemy import text as sql

from db import engine
from llm import _chat_json

# Is request/task me bot kis language me reply kare
reply_lang = ContextVar("reply_lang", default="English")

SCRIPTS = [
    ("Hindi (written in Devanagari script)", re.compile(r"[\u0900-\u097F]")),
    ("Malayalam", re.compile(r"[\u0D00-\u0D7F]")),
    ("Tamil", re.compile(r"[\u0B80-\u0BFF]")),
    ("Telugu", re.compile(r"[\u0C00-\u0C7F]")),
    ("Kannada", re.compile(r"[\u0C80-\u0CFF]")),
    ("Bengali", re.compile(r"[\u0980-\u09FF]")),
    ("Gujarati", re.compile(r"[\u0A80-\u0AFF]")),
    ("Punjabi", re.compile(r"[\u0A00-\u0A7F]")),
]

# Sirf wo Hinglish words jo English me nahi aate
HINGLISH_WORDS = {
    "hai", "hain", "hoga", "hogi", "tha", "thi", "kal", "aaj", "parso", "kya", "nahi",
    "nahin", "mera", "meri", "mere", "mujhe", "mujhko", "batao", "bata", "karo", "karna",
    "kare", "kardo", "krde", "krdo", "abhi", "bhej", "bhejo", "dena", "rakho", "yaad",
    "aur", "bhi", "toh", "kaise", "kyu", "kyun", "hum", "tum", "aap", "mein", "wala",
    "wale", "wali", "raha", "rahi", "rahe", "chahiye", "kitna", "kitne", "baje", "kab",
    "kaun", "sab", "kuch", "dikhao", "dekho", "subah", "shaam", "raat", "paisa", "ghar",
}

WHISPER_CODES = {
    "en": "English", "hi": "Hinglish", "ur": "Hinglish", "ml": "Malayalam",
    "ta": "Tamil", "te": "Telugu", "kn": "Kannada", "bn": "Bengali",
    "gu": "Gujarati", "pa": "Punjabi", "mr": "Marathi",
}


def detect_text_language(message):
    """Detects message language and script strictly based on user input characters."""
    has_devanagari = bool(re.search(r"[\u0900-\u097F]", message))
    if has_devanagari:
        return "Hindi (written in Devanagari script)"

    for name, pattern in SCRIPTS[1:]:
        if pattern.search(message):
            return name

    tokens = set(re.findall(r"[a-zA-Z]+", message.lower()))
    if not tokens:
        return None

    # Check for Hinglish
    extended_hinglish = HINGLISH_WORDS | {
        "bhy", "bhai", "mekro", "merko", "mereko", "merse", "hindi", "kro", "baat", "mujhe", "mujh", "tu", "teri", "tera",
        "tere", "k", "ke", "ki", "ko", "se", "ne", "me", "mein", "par", "pe", "ab", "abhi",
        "bna", "bnaa", "banao", "bhejo", "bhej", "de", "dede", "dena", "daal", "daalkr",
        "kr", "krde", "krdo", "karna", "karo", "rhe", "rha", "rhi", "aaya", "aayi", "aaye",
        "pta", "smjh", "smjha", "bol", "bta", "btaa", "bata", "batao", "ka", "ki", "h", "hai",
        "hain", "tha", "thi", "the", "toh", "to", "bhi", "aur", "or", "kuch", "sab", "sarre",
        "sarra", "saare", "dono", "ek", "do", "teen", "chaar", "paanch", "chhe", "kya", "kyu",
        "kyun", "kaise", "kisko", "kaha", "kahan", "kitna", "kitne", "kitni"
    }
    if tokens & extended_hinglish:
        return "Hinglish"

    if len(tokens) >= 2:
        return "English"
    return "Hinglish"


def language_from_whisper(name):
    """Whisper ke detected language ko apne naam me badalta hai."""
    n = (name or "").strip().lower()
    if not n:
        return None
    if len(n) == 2:
        return WHISPER_CODES.get(n)
    if n in ("hindi", "urdu"):
        return "Hinglish"
    return n.title()


def get_language(chat_id):
    with engine.connect() as conn:
        row = conn.execute(
            sql("SELECT language FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        ).first()
    return row[0] if row and row[0] else "English"


def set_language(chat_id, lang):
    with engine.begin() as conn:
        conn.execute(
            sql("UPDATE users SET language = :l WHERE telegram_chat_id = :c"),
            {"l": lang, "c": chat_id},
        )


async def localize(message, lang):
    """Pure passthrough: LLM generates the final response natively, no middleman translation."""
    return message
